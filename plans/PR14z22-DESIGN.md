# PR14z22 — diagnose CC key mismatch on M2 acceptance gate

## State at 2026-05-23 20:00Z (post PR14z21)

PR14z20 (revert of KGSE_MODE NIA-RMW) deployed via CI run 26341257545,
ISO downloaded with `gh run download` (publish gated to `main`), staged at
`lxc200:/srv/tftp/iso/latest-ask.iso`, installed via
`add system image http://192.168.1.137:8080/iso/latest-ask.iso` and
booted as Default.

Live-silicon regdump captured DURING iperf3 -P 8 -t 10 confirms:
- `kgse_mode = 0x80500002` on every active scheme (PR14z20 silicon-truth preserved)
- `kgse_ccbs = 0x0005ac00` on scheme 3 (eth3 ingress, data direction)
- `kgse_ccbs = 0x0005ad00` on scheme 4 (eth4 ingress, ACK direction)
- `kgse_spc` actively counting — scheme 3 gained ~37M classifications in 10s
- 4 distinct REPLACE cookies installed (per-direction × per-stream pair)
- PR14z18 auto-unbind correctly fires on last-cookie destroy

## The remaining failure

| Metric | Value | Target | Status |
|---|---|---|---|
| Throughput | ~3.7 Gbps single flow / ~7 Gbps -P8 | ≥ 2 Gbps | ✅ PASS |
| eth3 rx_packets delta (10s -P8) | 46 (control plane only) | — | — |
| eth3 rx_dropped delta | 8,832,662 (NET_RX_DROP after netif_receive_skb) | — | — |
| eth4 tx_packets delta | 8,832,662 | — | — |
| softirq | 52.91% avg, **93.99% on CPU0** | ≤ 5% | ❌ FAIL |

`rx_dropped++` is only incremented by `dpaa_eth.c::dpaa_rx()` when
`netif_receive_skb()` returns `NET_RX_DROP`. That means **every data
packet IS reaching the kernel net stack via the dpaa NAPI softirq path**,
then being consumed by the in-kernel nf_flow_table ingress hook (which
returns NET_RX_DROP after redirecting the skb to eth4 egress).

The silicon CC tree is being walked (37M `kgse_spc` increments for ~8M
unique packets ≈ 4-5 CC traversals per packet, consistent with the
tree's 4 cookie keys all missing). All packets MISS the CC lookup and
fall through to `miss_action.forward_fq.fqid = base_fqid` =
`dpaa_get_rx_default_fqid()` — the kernel's RX_DEFAULT FQ polled by
NAPI / softirq.

## Root-cause hypothesis

The silicon-extracted 16-byte CC key bytes do not match the
`entry.key[]` layout that `ask_hw_flow_insert_v4_tcp()` supplies via
`fman_pcd_cc_node_add_key()`.

Patch 0048 lines 5217-5247 lays out:
```
entry.key[0..3]   = key->src_ip
entry.key[4..7]   = key->dst_ip
entry.key[8..11]  = 0x00000000   (SPI guard)
entry.key[12..13] = key->sport
entry.key[14..15] = key->dport
```

This matches the documented `KGSE_EKFC = 0x00180206` extract set
(`IPSRC1 | IPDST1 | IPSEC_SPI | L4PSRC | L4PDST`), but the EXACT byte
order silicon emits has only been validated by code-reading the QorIQ
RM 8.7.4 Table 8-107, not against live silicon.

## Diagnostic plan (PR14z22)

### Step 1 — confirm the miss-fallthrough hypothesis

Add a single-line follow-up patch that changes the cc_v4_tcp miss-action
from `FORWARD_FQ(base_fqid)` to `DROP`:

```c
keys.miss_action.type = FMAN_PCD_ACTION_DROP;
/* keys.miss_action.forward_fq.fqid = base_fqid;  // diag: drop misses */
```

**Predicted outcomes:**

| Outcome | Interpretation |
|---|---|
| Throughput → ~0 Gbps, ARP also fails | All packets are MISSing the CC tree — CC key bytes ≠ silicon-extracted bytes. Proceed to step 2. |
| Throughput stays ≥ 2 Gbps, eth4 tx delta matches | HIT path WAS working; softirq came from another source. Investigate QMan portal IRQ scheduling or dpaa_eth NAPI poll budget. |

This is a **destructive control-plane test** — must be run with a watchdog
reboot ready (panic=60) and only with iperf3 from lxc201 → lxc202, never
from an SSH'd-in session that depends on the same flowtable path.

### Step 2 — read silicon key bytes via /dev/mem

Extend `bin/ask-pcd-regdump.py` to dump the first CC node's installed
key bytes from MURAM. The address is `CC base + node_offset + key_idx *
key_size`. Compare against `tcpdump -i eth3 -nn host 10.99.1.2 -c 1` 5-tuple
hex.

### Step 3 — try alternative key orderings

If keys mismatch, the candidate orderings to try (with PR14z14 documenting
[SIP|DIP|SPI|SPORT|DPORT] as one possibility):

- **A**: [SIP|DIP|SPI|SPORT|DPORT] (current, 16B, bit-position order MSB-first)
- **B**: [SPI|SIP|DIP|SPORT|DPORT] (numeric KG_SCH_KN_* descending: 0x00100000→0x00080000→0x00000200→0x4→0x2 makes IPSRC1 first, then IPDST1, then IPSEC_SPI, then L4PSRC, then L4PDST — same as A)
- **C**: [SIP|DIP|SPORT|DPORT|SPI] (LSB-first? unlikely per RM)
- **D**: With L4PROTO prepended (1B at offset 8) — patch 0048 line 5232 explicitly says proto is NOT extracted, but worth verifying

### Step 4 — if all key orderings fail

The cc_node_create extract spec may need configuration. Currently:
```c
extract.type   = FMAN_PCD_CC_EXTRACT_KEY;
extract.offset = 0;
extract.size   = 16;
```

`FMAN_PCD_CC_EXTRACT_KEY` tells silicon "use the KG-emitted key
verbatim" — i.e. consume the bytes the KG scheme generated. But the
KG scheme on the kernel side uses `KGSE_HC = 0` (hash mode), so what
silicon emits to the CC stream might be the HASH RESULT, not the
extracted key bytes. The `fman_pcd_kg_graft_cc()` path should disable
hash mode and switch the scheme to direct-key-emit mode when CC is
attached.

## Action

This document is committed as a checkpoint. Next session should:
1. `qdrant-find` for "PR14z22 CC key mismatch silicon-truth" to recover this state
2. Build a temporary patch 0053 that changes miss_action to DROP, dispatch CI, deploy
3. Run iperf3 + capture eth3/eth4 deltas + dmesg
4. Based on outcome, proceed to step 2/3/4 above

