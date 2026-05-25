# PR14o — Root cause analysis and design for HW path activation

**Status:** in progress, post-PR14n M2 gate run (2026-05-17)

## What PR14n verified

- `flow_indr_dev_register()` callback fires correctly when an nft flowtable with `flags offload` is installed and the dpaa netdev has no `ndo_setup_tc` (the indr path).
- On our dpaa1 setup `ndo_setup_tc` IS present (via patch 0002), so nft uses the NDO path, not the indr path. Both paths converge in `ask_flow_offload_setup_tc()`.
- `BIND eth3` + `BIND eth4` show up in dmesg when an nft flowtable with `flags offload; devices = {eth3,eth4}` is installed.

## What PR14n exposed — FLOW_CLS_REPLACE never fires

After PR14n was confirmed working at BIND time, an iperf3 traffic run showed:

- `/proc/net/nf_conntrack` flows acquire the `[OFFLOAD]` mark (set when the SW flowtable adopts them).
- Zero `ask: flow_offload: REPLACE …` events in dmesg during 30 s @ 6.4 Gbps.
- CPU stays at 61 % (SW-fastpath forwarding), well above the ≤5 % M2 target.
- `tc filter add dev eth3 ingress flower skip_sw …` returns `RTNETLINK: Operation not supported`.

So the issue is downstream of BIND: either the kernel never queues an offload work item for these flows, or `nf_flow_offload_alloc()` silently fails before invoking our cb.

## Three layered problems found

### (1) `flow_offload_work_add()` silently swallows alloc failure

`net/netfilter/nf_flow_table_offload.c:971`:

```c
static void flow_offload_work_add(struct flow_offload_work *offload)
{
    struct nf_flow_rule *flow_rule[FLOW_OFFLOAD_DIR_MAX];
    int err;

    err = nf_flow_offload_alloc(offload, flow_rule);
    if (err < 0)
        return;                       /* SILENT */
    err = flow_offload_rule_add(offload, flow_rule);
    if (err < 0)
        goto out;
    set_bit(IPS_HW_OFFLOAD_BIT, &offload->flow->ct->status);
out:
    nf_flow_offload_destroy(flow_rule);
}
```

`nf_flow_offload_rule_alloc()` calls `flowtable->type->action()` which for inet is
`nf_flow_rule_route_inet → nf_flow_rule_route_ipv4 → nf_flow_rule_route_common`.
The common path calls `flow_offload_eth_src/eth_dst` which need a resolved neighbour;
without one those fail and the entire alloc returns NULL.

For the eth3 → eth4 forward test, the neighbour for the next-hop (host
`192.168.1.137` on eth4) is resolved (kernel SW-fastpath proves it), so this
shouldn't be the killer. But there are five other failure sites in
`nf_flow_rule_route_common` (decap_tunnel, encap_tunnel, eth_src, eth_dst,
flow_offload_redirect). Any one of them silently aborts the offload.

The kernel does not emit a single error message on this path. We need our own
diagnostic.

### (2) Patch 0002 doesn't handle TC_SETUP_BLOCK for tc-flower skip_sw

`tc filter add dev eth3 ingress flower skip_sw …` is the canonical
acceptance test for HW offload. Today it returns ENOTSUPP because the
dpaa patch dispatches only `TC_SETUP_FT` and `TC_SETUP_BLOCK` through the
backend, but only after `nf_flow_table_offload_cmd` has set
`binder_type = FLOW_BLOCK_BINDER_TYPE_CLSACT_INGRESS`. A direct tc-flower
add hits `__tcf_block_find → tc_setup_offload_action → __tc_setup_cb_call`
which goes through `block->cb_list`, requiring the netdev to have already
registered a TC_SETUP_BLOCK handler that the standard tc-flower core path
calls. The current dpaa patch path is reached but the `flow_block_offload`
that arrives has `command=BLOCK_BIND, binder_type=CLSACT_INGRESS,
extack=…` which we already accept. The actual failure is in our
`ask_flow_offload_setup_tc()` — it returns 0 on BIND and the cb is
installed, but tc-flower then iterates the block's cb_list and calls each
cb with `TC_SETUP_CLSFLOWER + FLOW_CLS_REPLACE` … which our cb handles.
So tc-flower **should** work today. The ENOTSUPP must come from
somewhere earlier — most likely tc itself rejecting the add before
reaching the driver. Verify with `tc -s -d filter add … && echo ok` and
straceing.

### (3) BIND on indr path shows `dir=0` (ASK_DIR_UNKNOWN)

`ask_flow_offload_classify_dir()` walks `dev->dev.parent->of_node` to find
the FMan MAC node. On the indr path, the netdev's `dev.parent` may be the
dpaa platform device (not the MAC), so the walk doesn't terminate. This
is purely cosmetic today (PR14j defers the real KG bind to
`FLOW_CLS_REPLACE`), but ugly in logs.

### Bonus — VyOS pre-condition

VyOS's `vyos_conntrack` PREROUTING chain ends with a literal `notrack` (handle 25)
that disables conntrack for all forwarded traffic unless firewall/NAT rules
promote it. With no conntrack, `nft flow add @ft1` is a no-op and the
entire test was invisible. After `nft delete rule ip vyos_conntrack
PREROUTING handle 25`, conntrack saw the iperf3 flows.

## PR14o action items

### A. Add per-step instrumentation to surface alloc failure

Add a one-shot `pr_warn_ratelimited()` in `ask_flow_offload_setup_tc_block_cb()`
that fires when `FLOW_CLS_REPLACE` arrives, so we can definitively say
whether our cb is reached. This is independent of `pr_debug`/dyndbg, so it
will work in production builds without operator action.

### B. Switch `ask_pr_dbg("REPLACE …")` to `pr_info()` for now

Until M2 passes, we need every REPLACE/DESTROY event visible in dmesg by
default. Demote back to `pr_dbg` after the gate is green.

### C. Verify whether `nf_flow_offload_alloc()` actually fails

If our cb is genuinely never called, the alloc is the suspect. Two ways
to prove it without patching the kernel:

  1. Run iperf3, then `cat /sys/kernel/debug/tracing/trace_pipe` with
     `tracepoint:nf_flow_offload:*` enabled.
  2. Compile a tiny eBPF kprobe on `nf_flow_offload_rule_alloc` ret value.

For now, document this as the next diagnostic step. The fix, if it IS
alloc, is to walk `flow->tuplehash[dir].tuple` and figure out which
sub-step failed. The kernel's silence is the bug we can't fix from
ask.ko alone — but we can patch `nf_flow_table_offload.c` to call
`net_warn_ratelimited()` on the silent return path.

### D. Document VyOS notrack pre-req in `plans/ASK2-IMPLEMENTATION.md`

State plainly:

  > For M2 acceptance gate, the DUT must have conntrack enabled on the
  > forward path. VyOS defaults to `notrack` in `vyos_conntrack PREROUTING`
  > unless a firewall or NAT ruleset promotes conntrack. The test harness
  > `bin/verify-ask-flow-offload.sh` should add a `set firewall …` rule
  > (or `nft delete rule ip vyos_conntrack PREROUTING handle <N>`) before
  > the iperf3 burst, and restore it after.

### E. Fix `ask_flow_offload_classify_dir()` for indr path

Add a fallback that finds the FMan MAC by name match (`eth0`–`eth4` →
known FMan MAC indices). Cosmetic; do it in the same PR.

### F. Extend patch 0002 to log dpaa_setup_tc invocations

Add a single `pr_info_once()` in `dpaa_setup_tc()` that fires the first
time it's called for each `(net_dev, type)` pair. This gives us a "yes
the dpaa driver IS receiving the call" trace, which we currently don't
have.

## Out of scope for PR14o

- Actually wiring KG to the silicon for REPLACE — that work was done in
  PR14j and is gated behind getting REPLACE to fire at all.
- Tc-flower direct path beyond the diagnostic; once REPLACE works via
  nft flowtable, tc-flower-skip_sw will work too.
- Re-architecting the in-tree `nf_flow_table_offload.c` silent error
  swallow. We propose a one-line `net_warn_ratelimited()` patch in
  patches/0036, but keep it minimal.