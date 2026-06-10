# Scope brief — Drive the DPAA1 true-ZC AF_XDP RX productive oracle (`xsk_zc_rx_redirect > 0`)

**Status:** ✅ **FUNCTIONALLY RESOLVED 2026-06-10** — true-ZC RX is HW-validated: oracle `xsk_zc_rx_redirect` 0→7→8 reproducible (ISO `2026.06.10-0124-rolling`, kernel `6.18.34-vyos`, DUT `192.168.1.190`). GAP 1 closed (`0102b` BMI readback: `FMBM_EBMPI[0]` bpid 3→5 in silicon), GAP 1b closed (`0103g` NULL-`xdp.rxq` crash fixed, i40e `MEM_TYPE_XSK_BUFF_POOL` rxq registration), dispatch-placement fixed (`0103f`). Crash-free + reversible (serial-capture clean). **Only optimization remains:** GAP 2 = bulk-flow steering onto the XSK default FQ for a *high-rate* ZC throughput number (not gate-3-blocking — copy-mode already meets capacity). See spec §6.1.18. Original brief preserved below for the GAP-2 follow-up.
**Owner:** (assign)
**Branch:** `dpaa1` (do **not** disrupt `main`).
**Authoritative spec:** `specs/dpaa1-afxdp-modernization-spec.md` §6.1.10–§6.1.17 (M3-3 step 7). This brief is a self-contained pointer; the spec is the source of truth. Cross-ref `plans/COMPLETION-PLAN.md` §4.3.

---

## 1. One-paragraph goal

Make the FMan RX BMI DMA ingress frames **directly into the XSK UMEM's BMan buffer pool**, so the driver can recognise those frames, recover the owning `xdp_buff`, and `xdp_do_redirect()` them into the XSKMAP — i.e. **true zero-copy RX**. The single success oracle is the ethtool counter **`xsk_zc_rx_redirect`** (the 22nd `xsk_*` counter) climbing above 0 on `eth3`/`eth4` while an `XDP_ZEROCOPY` socket is bound and traffic flows. Today it reads **0**. Everything needed to make it non-zero is already compiled into the kernel; two debug gaps keep it dormant.

This is **NOT gate-3 (capacity) blocking** — copy-mode AF_XDP already meets the ~3.5 Gbps capacity target (§6.1.8a/§6.1.8b). This task closes the *true-ZC* optimisation, the last item of M3-3 step 7.

---

## 2. What is already DONE (do not redo)

All patches are in-tree and CI-built. Mechanism split (Recognise / Recover / reProgram) per §6.1.10–§6.1.17:

| Patch | Role | State |
|-------|------|-------|
| `0093`–`0096` | Diagnostic counters `xsk_zc_eligible` / `xsk_zc_rx_armed` / `xsk_fill_guard_block` / `xsk_zc_rx_recovered` (read-side of all 3 mechanisms) | DUT-validated dormant |
| `0102` | Exported WRITE primitive `fman_port_set_rx_bpool(port, old_bpid, new_bpid)` (mechanism 3 reProgram). v2 operates on persistent `port->ext_buf_pools` (NOT the freed `port->cfg`) | landed; attach-time `-EINVAL` resolved |
| `0103a` | Recover sw-ring reverse-map: `dpaa_xsk_chunk_record/lookup`, sorted `dma_addr_t → head-index` `bsearch` array, 21st counter `xsk_zc_recover_lookup`. Needed because **kernel 6.18.31+ has NO `xsk_buff_recv()` retrieve-by-DMA primitive** | landed dormant |
| `0103b` | Productive coupled reprogram-WRITE (attach) + Recover-redirect hook (`af_xdp_pool_rx_hook` via `priv->qmgmt_ops->rx_hook`), 22nd counter `xsk_zc_rx_redirect` | landed; **DUT entry-gate-validated, oracle still 0** |

**Proven on hardware (ISO `vyos-2026.05.30-2044-rolling-LS1046A-vpp-arm64`, kernel `6.18.33-vyos`, DUT `192.168.1.190`):**
- Entry gate §6.1.12 preconditions **(1)+(2) MET**: `xsk_zc_rx_armed=2` (reprogram has a distinct target), `xsk_fill_guard_block=0` (FILL-ring single-producer invariant holds under 30 s load).
- The reprogram-WRITE fires **crash-free and fully reversible**: every XDP DRV-mode attach/detach bounces eth3 (`fman_port_disable/enable` → link down → 10gbase-r re-sync ~6 s) and eth3 **fully recovers** IP reachability (3/3 ping). dmesg crash grep (`IVCI|list_add|lockup|Oops|BUG|call trace|panic`) = **zero hits**.

So: the dangerous part (live FMan RX-port BPID reprogram) already works safely. What's missing is purely making frames actually arrive in the XSK pool **and** confirming the BMI re-commit reached silicon.

---

## 3. The two gaps to close (the actual work)

```mermaid
flowchart TD
    A["XDP_ZEROCOPY socket binds eth3"] --> B["0103b attach: fman_port_disable → fman_port_set_rx_bpool(dpaa_bp->bpid → xsk_bpid) → fman_port_enable"]
    B --> G1{"GAP 1: did the BMI FMBM_REBM/bpool register BPID actually change in silicon?"}
    G1 -->|"unconfirmed"| X1["fd->bpid never == xsk_bpid[band]<br/>xsk_zc_eligible stays 0"]
    C["traffic flood into eth3"] --> G2{"GAP 2: does the flow land on the XDP default FQ (queue 0)?"}
    G2 -->|"no — PCD classifies DUT-IP traffic into stack FQs"| X2["XSK path drains only ~1pps RA/ND"]
    X1 --> Z["xsk_zc_rx_redirect = 0"]
    X2 --> Z
    G1 -->|"confirmed flips"| Y1["Recognise hits"]
    G2 -->|"yes"| Y2["frames reach rx_default_dqrr → rx_hook"]
    Y1 --> W["Recognise → Recover (reverse-map) → xdp_do_redirect → XSKMAP"]
    Y2 --> W
    W --> OK["xsk_zc_rx_redirect > 0 ✅"]
```

### GAP 1 — confirm `fman_port_set_rx_bpool()` BMI re-commit is *effective* on a live post-init RX port
The `0102` v2 fix removed the attach-time `-EINVAL` (root cause: `set_bpools()` read the freed `port->cfg`, since `fman_port_init()` ends with `kfree(port->cfg); port->cfg = NULL;`). **But no register readback has confirmed the live port's BPID actually flips** in the BMI `FMBM_REBM` / external-bpool registers after the `disable → write → enable` bracket.

**Task:** a register-readback debugging pass. Before/after a ZC bind on eth3 (RX BMI hw port id `0x10`), read the FMan1 BMI RX-port external-buffer-pool registers and prove the programmed BPID changes from the kernel page-pool BPID (`priv->dpaa_bp->bpid`) to the XSK BPID (`priv->xsk_bpid[band]`).
- **Do NOT hardcode/guess register offsets.** Derive the exact `FMBM_REBM`/`FMBM_EBMPI` bpool register layout from `set_ext_buffer_pools()` in `drivers/net/ethernet/freescale/fman/fman_port.c` plus the LS1046A RM (FMan BMI section). FMan1 CCSR base + per-port BMI window are computed from the hw port id `0x10`.
- Use the established `/dev/mem` reader pattern (DUT has `STRICT_DEVMEM`/`IO_STRICT_DEVMEM` both unset). Templates under `/tmp/kilo/*.py` on the DUT (e.g. the FMPL reader `fmpl-status-read.py`) show the `mmap` + `I2C_SLAVE_FORCE`-style idiom; adapt for BMI.
- If the BPID does **not** flip: the bug is in `0102`/`0103b` (e.g. `set_ext_buffer_pools()` not re-running, or operating on the wrong cfg copy) — fix in the patch files. If it **does** flip: GAP 1 is closed, move to GAP 2.

### GAP 2 — steer real traffic onto the XDP default FQ (queue 0)
The bulk flood used in §6.1.17 targeted the DUT IP `10.99.1.1` and was **PCD-classified into the normal stack FQs**, not the default FQ that the XDP/XSK path drains. Only ~1 pps background IPv6 RA/ND reached the XSK socket. So even with GAP 1 closed, `fd->bpid` is only set to the XSK BPID for frames the FMan steers to the reprogrammed pool — you must get a high-rate flow to that pool/FQ.

**Task:** make a high-rate flow reach the XSK default FQ. Two viable routes (pick one):
1. **PCD steering rule** — install an FMan PCD classification/KeyGen rule that routes the test 5-tuple into the default RX FQ (queue 0). The PCD subsystem is now in-tree (`fman_pcd_*`, §6.1.13) — this is the same substrate the policer/HM use.
2. **Peer-initiated flood independent of the DUT IP stack** — generate L2/non-terminating traffic from the peer (`10.99.1.2`, directly-connected 10G) that the FMan delivers to the default FQ rather than punting up the stack. (e.g. traffic not addressed to a DUT-terminated socket.)

### Test-harness constraint (must design around)
A **DRV-mode XDP redirect prog on eth3 hijacks the entire RX path**, so an `iperf3` source running *on the same eth3* dies the instant the probe attaches (`delta_pps 190206 → 1`). The traffic generator and the XSK consumer **cannot coexist on one interface** with the current probe. Use a **peer-initiated** flow (from `10.99.1.2`), or a second interface, or combine with the GAP-2 PCD steer.

---

## 4. Acceptance criteria

1. **GAP 1 closed:** a documented register readback shows eth3 RX BMI port `0x10` external-bpool BPID changing `priv->dpaa_bp->bpid → priv->xsk_bpid[band]` across a ZC bind, and reverting on unbind.
2. **Oracle fires:** under a sustained ZC bind + steered flow, `ethtool -S eth3 | grep xsk_zc_rx_redirect` climbs > 0, and `xsk_zc_eligible` + `xsk_zc_recover_lookup` also climb (per-FD recognise + reverse-map hit). `/usr/local/bin/xsk-zc-check` renders the productive-ZC verdict.
3. **Safety preserved:** dmesg crash grep stays zero across bind/unbind; eth3 recovers IP reachability after detach (the §6.1.17 reversibility result must not regress).
4. **No regression** to copy-mode capacity (§6.1.8a/b) on `default`/`vpp`.

---

## 5. Build / deploy / test loop

- **Build:** `bin/dev-build.sh kernel` (native arm64, fastest) or full CI ISO via the `build-image` skill (`gh workflow run "VyOS LS1046A build (self-hosted)" --ref dpaa1`). **CRITICAL:** `dev-build.sh kernel` does `rm -rf $KSRC` + fresh re-extract of `linux-6.18.34` + re-applies **all** board patches every run — kernel-source edits in the work tree are wiped; **the `kernel/common/patches/board/*.patch` files are the only durable source of truth.** Every kernel build path MUST pass `LOCALVERSION=-vyos` (vermagic `6.18.34-vyos`).
- **Deploy to DUT:** `add system image <url>` (never `install image`). For ZC iteration the `vpp` flavor is the validated vehicle (`xsk_*` counters exercised under VPP AF_XDP); `default` also carries the counters.
- **DUT access:** mgmt SSH `ssh -i ~/.ssh/vyos_key vyos@192.168.1.190`; raw `ethtool`/`tc`/`ping`/`python3 /dev/mem` need `sudo` (key NOPASSWD). Serial relay `telnet 192.168.1.16:5555` for boot/recovery.
- **Probe tools (already in repo / on DUT):**
  - `bin/dpaa1-xsk-bind-probe.py eth3 0 4096 --hold N --xskmap` — bind an `XDP_ZEROCOPY` socket + install the XSKMAP redirect prog.
  - `/usr/local/bin/xsk-zc-check` — reads the `xsk_*` suite, renders the §6.1.12/§6.1.17 verdict (dormant / armed / productive / fault). Exit 0/1/2.
  - `bin/dpaa1-xdp-rxcap.py` — XDP_DROP driver-only RX capacity reference.
- **Lab:** peer is `10.99.1.2` (directly-connected 10G, lxc201). eth3 = left SFP+ = MAC9/f0000, BMI hw port id `0x10`; eth4 = `0x11`.

---

## 6. Guard-rails

- Specs (`specs/dpaa1-afxdp-modernization-spec.md`) > `plans/`. Update §6.1.17 (and §6.1.15) with findings; record root cause + register evidence in **qdrant** (`qdrant-find` first, `qdrant-store` after).
- **Do not guess register bits** — extract exact encodings from `fman_port.c` + the LS1046A RM / NXP SDK refs (`/tmp/kilo/sdk_*`, `fsl_fman_kg.h`).
- No auto-commit/push without explicit user request — stage for review.
- This is debug, not a forward-port: if you find yourself adding a new `fman_pcd_*` accessor, stop — the accessor (`0102`) and its caller (`0103b`) already exist; the gap is BMI-effectiveness + steering, not missing API.

---

## Addendum — 2026-06-09 late session (0103f + 0102b + 0103g landed)

GAP 1 and the dispatch-placement defect are **resolved in code**; a NEW
crash was found and fixed on hardware:

1. **Dispatch placement defect (root cause of the stuck oracle):** the
   committed `0103b` rx_hook dispatch sat at ~2901 in `rx_default_dqrr()`,
   AFTER the `dpaa_bpid2pool(fd->bpid)` NULL-guard at ~2855 — XSK-bpid FDs
   resolved to no kernel pool and were consumed/dropped before the hook ever
   saw them. **`0103f`** moves the dispatch right after
   `priv = netdev_priv(net_dev)`.
2. **`0102b`** adds the GAP-1 `FMBM_EBMPI` register-readback `dev_info` at
   reprogram time.
3. **NEW crash (HW serial capture):** with 0103f live, the FIRST Recovered
   frame NULL-derefs at `__xsk_map_redirect+0x6c` (lr `xdp_do_redirect`,
   via `af_xdp_pool_rx_hook`) — `xsk_buff_alloc()` leaves `xdp.rxq` NULL
   (driver-owned field) and `xsk_rcv_check` reads `xdp->rxq->dev` (offset 0
   ⇒ fault at address 0). Dual-CPU interleaved Oops → instant reset, NO
   pstore record — serial capture was mandatory.
   **Fix `0103g`:** per-band `struct xdp_rxq_info xsk_zc_rxq[]` registered
   at ZC attach with `MEM_TYPE_XSK_BUFF_POOL` + `xsk_pool_set_rxq_info()`
   (i40e idiom — stamps `xdp.rxq` into every pool buffer once); the
   reprogram-WRITE is gated on successful registration; unreg at detach.
   `MEM_TYPE_XSK_BUFF_POOL` also routes `xsk_rcv()` down the true-ZC
   branch instead of the copy fallback.

**Test procedure refinements (hard-won):**

- The XDP prog on eth3 eats ping replies — a DUT-side
  `ping 10.99.1.2` during the hold drives ICMP echo-replies into eth3 RX
  (no peer access needed), but the ping itself reports 100% loss while
  redirect works. Judge by `ethtool -S eth3 | grep xsk_zc` (needs `sudo`).
- Run a serial-relay logger (raw socket to `192.168.1.16:5555`) for the
  WHOLE test window — this crash class leaves no pstore.
- CI stages board patches via an EXPLICIT `cp` list in
  `bin/ci-setup-kernel.sh` — new `kernel/common/patches/board/*.patch`
  files are silently ignored until a `cp` line is added.

**Status:** `0103f`+`0102b`+`0103g` committed on `dpaa1`, CI run
`27242942698` building the validation ISO. Remaining after the crash fix
is verified: GAP 2 (steering under flood — PCD/RSS FQs vs the default FQ)
and the oracle-positive confirmation.
