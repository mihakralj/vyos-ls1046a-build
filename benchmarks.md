# VyOS LS1046A (ASK2) Benchmarks

Measured forwarding, packet-rate, and flow-scaling results for **VyOS on the NXP QorIQ LS1046A Mono Gateway DK** with the **ASK2** FMan FE-VM hardware offload dataplane (`S1`, dual 10 GbE SFP+ transit ports `eth3` ↔ `eth4`, 4× Cortex-A72 @ 1.6 GHz, 8 GiB DDR4, FMan microcode `210.10.1`).

- **Test rig:** Two Intel X710 10 GbE SFP+ traffic generators directly attached to DUT `eth3` and `eth4` over 10G DACs (no switch in the dataplane; management SSH isolated on `eth0`).
- **Kernel:** `Linux 6.18.65-1-vyos` (`dpaa1` branch).
- **Boot note:** All tables below are single-pass (`REPS=1`) runs on fresh image boots without an intermediate cold power-cycle between preceding functional checks and the benchmark runs.

---

## 1. Full 16×6 Throughput Matrix (`iperf3`, TCP)

**Tested:** `2026-11-27` · **Build:** `VyOS 2026.11.27-0124-rolling` (`Linux 6.18.65-1-vyos #1+b2026.11.270124`, CI `45922328245`, `REPS=1`)

Measures steady-state TCP goodput (Gbit/s) across all 16 supported ASK2 forwarding topologies — IPv4 and IPv6, upload, download, and simultaneous bidirectional (`--bidir`) — at both MTU 1500 and jumbo MTU 9000, including NAT44/NAT66, 802.1Q VLAN translation and tag/untag (`vlan-port`), PPPoE, L2 bridging, WireGuard (`WGFP` fastpath with IPv4 and IPv6 outer transport), and composite ISP WAN stacks (`nat+ppp+vlan`, `port-forward`, `wg+nat+pp+vl`).

- **Verification:** **96/96 cells hardware-offloaded** (`HW` for routed/NAT/VLAN/PPPoE/bridge/WAN rows; `TX+RX` WGFP fastpath for WireGuard rows; `0` software fallbacks, `0` kernel errors, `ask-check` `47/49 OK`). Non-WireGuard rows hold DUT CPU busy at ~`0.8–3%`; WireGuard rows are CPU-bound (~`100%` across 4 cores) on software ChaCha20-Poly1305 encryption.

| Flow type | Upload v4 | Upload v6 | Download v4 | Download v6 | Bidir v4 | Bidir v6 | Verdict |
|---|---:|---:|---:|---:|---:|---:|---|
| `unicast` | 9.41 | 9.29 | 9.41 | 9.29 | 17.7 | 18.1 | 6/6 HW |
| `nat` | 9.41 | 9.29 | 9.41 | 9.29 | 18.1 | 18.1 | 6/6 HW |
| `vlan-vlan` | 9.39 | 9.26 | 9.39 | 9.26 | 18.1 | 18.0 | 6/6 HW |
| `vlan-port` | 9.39 | 9.26 | 9.39 | 9.26 | 16.1 | 15.8 | 6/6 HW |
| `pppoe` | 9.36 | 9.23 | 9.37 | 9.23 | 18.2 | 17.9 | 6/6 HW |
| `bridge` | 9.39 | 9.27 | 9.41 | 9.28 | 18.2 | 18.2 | 6/6 HW |
| `unicast-9k` | 9.90 | 9.88 | 9.90 | 9.88 | 18.7 | 18.8 | 6/6 HW |
| `nat-9k` | 9.90 | 9.87 | 9.90 | 9.88 | 18.8 | 18.8 | 6/6 HW |
| `vlan-9k` | 9.90 | 9.87 | 9.90 | 9.87 | 18.8 | 18.7 | 6/6 HW |
| `vlan-port-9k` | 9.90 | 9.88 | 9.89 | 9.87 | 14.6 | 14.4 | 6/6 HW |
| `bridge-9k` | 9.90 | 9.88 | 9.90 | 9.88 | 18.9 | 18.7 | 6/6 HW |
| `wireguard` | 4.70 | 4.61 | 4.73 | 4.64 | 4.60 | 4.53 | 6/6 TX+RX |
| `wireguard6` | 4.58 | 4.53 | 4.74 | 4.67 | 4.57 | 4.49 | 6/6 TX+RX |
| `port-forward` | 9.41 | 9.28 | 9.42 | 9.29 | 18.2 | 17.9 | 6/6 HW |
| `nat+ppp+vlan` | 9.34 | 9.21 | 9.34 | 9.21 | 16.0 | 15.8 | 6/6 HW |
| `wg+nat+pp+vl` | 4.66 | 4.51 | 4.65 | 4.62 | 4.53 | 4.52 | 6/6 HW |

---

## 2. TRex NDR / PDR / MRR Packet-Rate Matrix (UDP, Bidirectional Symmetric)

**Tested:** `2026-11-27` · **Build:** `VyOS 2026.11.27-0330-rolling` (`Linux 6.18.65-1-vyos #1+b2026.11.270330`, CI `45934489997`, commit `380a10b3`, `REPS=1`)

Measures RFC 2544-style lossless and bounded-loss forwarding rates for routed IPv4 UDP across 64 B, 512 B, and 1518 B Ethernet frames (including 4 B FCS) with 1 and 1,024 bidirectional 5-tuple flows using dual-host TRex v3.08 STL (`net_i40e` over `vfio-pci` on both generators). All rates are **both directions summed** (20 Gbit/s L1 aggregate ceiling).

- **Metrics:**
  - **NDR** (No-Drop Rate): highest rate at `≤ 0.001%` packet loss (10 s search trials at 0.5% precision, 20 s confirmation).
  - **PDR** (Partial-Drop Rate): highest rate at `≤ 0.5%` packet loss.
  - **MRR** (Maximum Receive Rate): delivered rate at `100%` offered line rate.
  - **Offer%**: configured per-port line-rate percentage; **20G%**: delivered L1 share of 20 Gbit/s dual-10G wire rate.
- **Verification:** `2048/2048` FMan ehash hardware hit records (`100%` HW), ~`4.48B` packets (~`2.0 TB`) forwarded, `0` SYNC timeouts, `ask-check` `47/49 OK`, clean `dmesg`. At 64 B, spreading 1,024 flows across all 16 per-port egress FQs lifts NDR from `2.52` to `5.81 Mpps` (`6.07 Mpps` MRR ceiling); at 1518 B, ASK2 reaches `19.60 Gbit/s` L2 (`19.86 Gbit/s` L1, `99.29%` of 20G wire speed).

```text
Frame  Flows  Metric   Mpps  L2 Gbps  L1 Gbps   Offer%    20G%      Drop%
  64B      1     NDR  2.524    1.292    1.696    8.48%   8.48%   0.01145%
  64B      1     PDR  4.874    2.496    3.275   16.41%  16.38%   0.08351%
  64B      1     MRR  4.945    2.532    3.323  100.00%  16.62%  83.37398%

  64B   1024     NDR  5.812    2.976    3.905   19.53%  19.53%   0.00031%
  64B   1024     PDR  6.037    3.091    4.057   20.31%  20.29%   0.02231%
  64B   1024     MRR  6.075    3.110    4.082  100.00%  20.41%  71.60945%

 512B      1     NDR  2.876   11.778   12.239   61.22%  61.19%   0.00189%
 512B      1     PDR  3.683   15.086   15.675   78.52%  78.37%   0.18564%
 512B      1     MRR  3.642   14.917   15.500  100.00%  77.50%  22.47183%

 512B   1024     NDR  2.986   12.229   12.707   63.56%  63.54%   0.00146%
 512B   1024     PDR  3.731   15.281   15.878   79.69%  79.39%   0.27066%
 512B   1024     MRR  3.576   14.648   15.220  100.00%  76.10%  23.89665%

1518B      1     NDR  1.426   17.323   17.551   87.78%  87.75%   0.00062%
1518B      1     PDR  1.594   19.360   19.615   98.44%  98.07%   0.36427%
1518B      1     MRR  1.614   19.600   19.858  100.00%  99.29%   0.68761%

1518B   1024     NDR  1.414   17.173   17.399   87.00%  87.00%   0.00092%
1518B   1024     PDR  1.605   19.490   19.747   99.22%  98.73%   0.48773%
1518B   1024     MRR  1.610   19.551   19.808  100.00%  99.04%   0.95233%
```

*Note:* On `64B 1-flow` and both `512B` NDR rows, the 20 s confirmation trial landed slightly above the strict `0.001%` cutoff (`0.00146%–0.01145%`) after a single step-down; `64B 1024-flow` and both `1518B` NDR rows satisfied `≤ 0.001%`.

---

## 3. 64 B Flow-Scaling Ladder & Collapse Ceiling (UDP, 3 Mpps / 90 M Packets)

**Tested:** `2026-11-27` · **Build:** `VyOS 2026.11.27-0330-rolling` (`Linux 6.18.65-1-vyos #1+b2026.11.270330`, CI `45934489997`, commit `380a10b3`, `REPS=1`)

Tests control-plane admission speed, kernel workqueue health, and silicon forwarding stability across increasing active bidirectional UDP 5-tuple counts. Each step installs `N` unique 64 B bidirectional flows during a hold phase (`100–250 kpps/port`), measures time from first packet to 100% hardware offload (`full after`), peak 1-minute load average (`load max`), peak kernel worker threads (`kworkers`), peak D-state tasks (`stuck max`), packet loss over a sustained **30 s trial at 3 Mpps (`90 M` packets)**, and natural hardware teardown time (`drain`).

- **Shipping default budget (`2,048` – `131,072` bidirectional flows, `ask.max_flows=262144` directions):** Every step offloaded in **`6–16 s`** with `load max ≤ 1.59`, `54–58` kworkers, `0–2` stuck tasks, `~0%` trial loss (`0`–`0.0065%` dual-TRex start-edge floor, `kpath_pps < 10 pps` = 100% HW), and `32–37 s` clean natural drain.
- **Extended bisect & collapse hunt (`262,144` – `1,048,576` flows, `ask.max_flows` raised above default):**
  - **Lossless ceiling (`≤ 0.01%` trial loss at 3 Mpps):** **`303,104` bidirectional flows (`606,208` HW ehash records)** — 100% offloaded in `35 s`, `load max 3.15`, `0.0044%` loss.
  - **Deep bucket-chain degradation (`311,296` – `786,432` flows):** ASK2 still offloads **100% of all `786,432` flows (`1,572,864` HW records)** with `kpath_pps = 8 pps` (zero SW spill) and `3,489 MiB` RAM free; loss rises gradually (`0.013%` → `0.70%` → `4.93%`) as per-bucket chains deepen across the silicon-fixed 32,768 buckets/port.
  - **Actual admission collapse (`1,048,576` flows = `2,097,152` directions):** Stalls at **`837,356 / 1,048,576`** (~`1.67 M` HW ehash records) when `dma_pool_alloc()` inside `fman_pcd_ehash_add_key()` exhausts the LS1046A 32-bit `ZONE_DMA` (`1.47 GiB` managed; `GFP_KERNEL|GFP_DMA` page allocation failure, while `3.26 GiB` remains free in `ZONE_NORMAL`). The remaining `~211k` flows fall back cleanly to software (`172k` kernel pps, `25.13%` trial loss) with `sync_timeouts=0`, no panic or wedge, and full natural teardown back to `0` records in `111 s` (`ask-check` was `47/49 OK` through `786,432` flows; the intentional `1,048,576` over-limit run logs the expected `warn_alloc` `GFP_DMA` trace).

```text
64 B flow ladder, VyOS/ASK2 hardware offload (2026-11-27, VyOS 2026.11.27-0330-rolling)
Trial: 30 s at 3 Mpps (90 M packets).

-- Shipping default budget (ask.max_flows=262144 directions / 131072 flows) --
  flows  offloaded            full after  load max  kworkers  stuck max  trial loss   drain
   2048     2048 / 2048              6 s      0.24        56          1    0.0024 %     33s
   8192     8192 / 8192              7 s      0.31        56          1    0.0032 %     32s
  16384    16384 / 16384             8 s      0.45        54          1    0.0034 %     34s
  24576    24576 / 24576             8 s      0.51        56          2    0.0034 %     32s
  32768    32768 / 32768             8 s      0.71        58          2    0.0065 %     34s
  40960    40959 / 40960          never*      0.45        58          0    0.0004 %     34s
  49152    49152 / 49152            10 s      0.86        54          2    0.0040 %     35s
  57344    57343 / 57344          never*      1.21        54          1           0     34s
  65536    65536 / 65536            10 s      0.83        55          1    0.0030 %     35s
  98304    98304 / 98304            12 s      0.53        56          1    0.0029 %     34s
 122880   122880 / 122880           16 s      1.59        57          1    0.0003 %     37s
 131072   131072 / 131072           16 s      1.32        56          1    0.0036 %     34s
 262144   262144 / 262144           27 s      1.41        56          1    0.0044 %     41s
 294912   294912 / 294912           32 s      2.92        58          1    0.0065 %     43s
 303104   303104 / 303104           35 s      3.15        59          1    0.0044 %     43s  <- max <=0.01% loss
 311296   311296 / 311296           34 s      3.26        58          2    0.0131 %     42s
 327680   327680 / 327680           38 s      3.22        58          1    0.0141 %     44s
 393216   393216 / 393216           42 s      3.34        56          2    0.0616 %     49s
 524288   524288 / 524288           81 s      4.24        60          2    0.7016 %     57s
 786432   786432 / 786432          174 s      7.18        62          2    4.9273 %     80s  <- 1.57M HW records
1048576   837356 / 1048576         never     12.68        65          7   25.1341 %    111s  <- admission collapse
```

*\*Note:* At `40,960` and `57,344`, `N - 1` flows offloaded (`99.998%` HW) because the reverse generator won the `CT_NEW` race on UDP destination port `1719`, matching VyOS's default `VYOS_CT_HELPER` `ras_udp` (H.323 RAS) rule; Linux `nf_flow_table` intentionally keeps flows with an attached conntrack helper (`helper=RAS`) in software (~`73 pps` at `40,960`, ~`52 pps` at `57,344`). Steps `≥ 65,536` switch to a 2D port grid (`1025..1280` × `10000+`) that never crosses helper ports `1719`/`5060`/`5061`, and trial loss remained `0.0004%` and `0`.
