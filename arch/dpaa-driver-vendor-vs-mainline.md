# DPAA Ethernet Driver — Vendor SDK vs Mainline (ASK2 base)

Last verified 2026-08-18 against the NXP LSDK kernel `lf-6.18.20-2.0.0`
(`/mnt/build/linux`, which carries both driver stacks side by side) and this
repository's mainline-based build (kernel 6.18.44).

## Scope

This document compares the two DPAA1 Ethernet **driver datapaths** on the
LS1046A — the netdev / RX-TX / BMan-QMan plumbing — *not* the classification
or offload layer above them. For the offload-stack (ASK 1.x `cdx.ko` vs ASK2
`ask.ko`) comparison see `ask1-vs-ask2-module-comparison.md`; for the
classification engine see `fman-pcd-api-reference.md` and
`fman-microcode-210-programming-reference.md`.

The question this answers: at the driver level, is ASK2's mainline `dpaa`
driver a superset of the vendor `sdk_dpaa` driver, or does the vendor driver
still do datapath things mainline does not?

## The two drivers are separate implementations, not variants

They live side by side in the NXP LSDK kernel under
`drivers/net/ethernet/freescale/` and are mutually exclusive at Kconfig
(`sdk_dpaa/Kconfig`: `depends on … !FSL_DPAA_ETH`). They share no code.

| | Vendor `sdk_dpaa` + `sdk_fman` (ASK 1.x base) | Mainline `dpaa` + `fman` (ASK2 base) |
|---|---|---|
| DPAA netdev driver LOC | ~12,100 | ~4,560 |
| FMan driver LOC | ~86,400 | ~11,070 |
| Kconfig | `FSL_SDK_DPAA_ETH`, `FSL_SDK_FMAN`, `FSL_SDK_QMAN`, `FSL_SDK_BMAN` | `FSL_DPAA_ETH`, `FSL_FMAN` |
| Kernel modules | `fsl_dpa`, `fsl_mac`, `fsl_advanced`, `fsl_proxy`, `fsl_oh` (5) | `fsl_dpa` (1) |
| MAC layer | `fsl_mac` + `mac-api.c` (SDK "NCSW" wrapper) | `mac.c` + PHYLINK + PCS-Lynx |
| FMan config model | `fm_ext.h` / `fm_port_ext.h` / `lnxwrp_*` NCSW library | thin `fman.h` / `fman_port.h` |
| Copyright/licence | Freescale 2008–2013 / NXP 2019 (`BSD-3 OR GPL-2.0`) | mainline rewrite (`BSD-3-Clause OR GPL-2.0-or-later`) |

The vendor stack is still maintained by NXP (recent `sdk_dpaa` commits April
2026: separate ingress CGR for high-priority traffic, a mixed-traffic TCP GRO
fix, NAPI refactors), but it is frozen in the old NCSW-wrapper architecture.
Mainline is the leaner upstream rewrite that continues to get datapath fixes
(`fman.c` KeyGen-failure resource-leak fix June 2026; `qman.c`
`qman_destroy_fq` race fix December 2025).

## Driver-level datapath capability comparison

Reference counts are `grep` occurrence counts across each driver's `.c` files
— coarse but directionally accurate — followed by the concrete source anchors.

| Datapath feature | Vendor `sdk_dpaa` | Mainline `dpaa` (ASK2) |
|---|---|---|
| XDP / AF_XDP | none (0 refs) | full: `XDP_PASS/DROP/TX/REDIRECT`, `bpf_prog_run_xdp`, `xdp_do_redirect`, `ndo_bpf` (`dpaa_eth.c:2602-2670`) |
| GRO | in-driver, parser-assisted: `NETIF_F_GRO`, `qman_portal_napi_gro_receive`, protocol-aware gating (`dpaa_eth_sg.c:560,628`; `dpaa_eth.c:333-376`) | none in driver; plain `netif_receive_skb()` (`dpaa_eth.c:2827`), defers to generic-stack GRO |
| CEETM egress shaping | in-driver: `dpaa_eth_ceetm.c` (~2,100 LOC, ~400 refs) | absent (0 refs) |
| Offline / OH ports | first-class netdevs: `offline_port.c` (~850 LOC) | absent |
| Proxy / advanced init | `dpaa_eth_proxy.c`, `dpaa_eth_base.c` (`fsl_advanced`, `fsl_proxy`) | absent; single `dpaa_load` probe |
| IEEE-1588 / PTP | heavy legacy: `dpaa_1588.c` (~580 LOC, ~220 hwtstamp refs) | lean modern `ndo_hwtstamp_get/set` (added 2025, ~39 refs) |
| Scatter-gather | separate `dpaa_eth_sg.c` (~1,230 LOC) | integrated; `build_skb`, mainline SG |
| Congestion (CGR) | ~80 refs; recently split ingress CGR (Apr 2026) | ~66 refs, upstream-standard |
| Checksum offload | present | present (slightly more refs) |

## What the vendor driver does that mainline (ASK2's base) does not

These are genuine datapath capabilities the vendor `sdk_dpaa` driver carries
and mainline dropped:

1. **In-driver GRO.** The vendor RX path calls
   `qman_portal_napi_gro_receive()` and gates GRO per protocol using the FMan
   parser result (`dpaa_eth.c:333-376`, `dpaa_eth_sg.c:560,628`). Mainline
   delivers frames with a plain `netif_receive_skb()` and relies on the
   generic stack for GRO. For pure kernel-forwarded throughput the vendor's
   tighter, parser-assisted GRO can be more efficient.

2. **In-driver CEETM egress shaping.** The vendor driver *is* the QoS shaper
   (`dpaa_eth_ceetm.c`, ~2,100 LOC). Mainline has none. This is the clearest
   regression: this project had to re-implement CEETM out-of-tree as new files
   (`qman_ceetm.c` / `dpaa_ceetm.c`) to recover what the vendor driver already
   shipped inside the netdev driver.

3. **Offline / host-command (OH) ports.** The vendor stack exposes
   offline-parsing ports as netdevs (`offline_port.c`); mainline has no concept
   of them.

4. **Fuller legacy IEEE-1588.** `dpaa_1588.c` is a heavier 1588 implementation
   than mainline's leaner `ndo_hwtstamp` interface.

## What mainline (ASK2's base) does that the vendor driver cannot

1. **XDP / AF_XDP.** The vendor driver has zero XDP. This is the single
   biggest driver-level differentiator and the entire reason ASK2 can offer
   per-interface VPP AF_XDP coexistence in one image (see
   `plans/DUAL-DATAPLANE.md`). The vendor NCSW architecture cannot provide it.

2. **Modern Linux integration.** PHYLINK / PCS-Lynx, a single clean module,
   SPDX headers, and active upstream bugfix tracking. The vendor stack is the
   frozen `lnxwrp_*` / `fm_ext.h` NCSW wrapper — maintained but not
   modernizable.

3. **Smaller, auditable, mergeable base.** ~4,560 vs ~12,100 LOC for the
   netdev driver; ~11,070 vs ~86,400 LOC for FMan.

## Verdict

At the driver level ASK2 did **not** take "the best of both worlds." It took
the mainline `dpaa`/`fman` driver wholesale and accepted losing three real
vendor-driver datapath features — in-driver GRO, in-driver CEETM egress
shaping, and offline/OH ports — in exchange for XDP/AF_XDP support and a clean,
single-image, upstream-tracked base.

That is a deliberate and defensible trade: XDP coexistence was non-negotiable
for the VPP dataplane story, and the vendor driver could never provide it. But
it is not a pure superset. The clearest concrete regression is **CEETM**, which
the vendor driver ships in ~2,100 lines inside the driver and which mainline
forced this project to rebuild out-of-tree. **In-driver GRO** is the second:
mainline's reliance on generic-stack GRO is slightly less efficient than the
vendor's parser-assisted path for kernel-forwarded traffic. Neither is fatal,
but both are cases where the vendor `sdk_dpaa` driver still does more at the
datapath level than ASK2's mainline base.
