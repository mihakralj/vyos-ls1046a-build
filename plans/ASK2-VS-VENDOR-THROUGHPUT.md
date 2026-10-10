# ASK2 vs vendor NXP ASK — routed throughput (A6)

This is the A6 gate scoreboard for `plans/archive/ASK2-REWRITE-PLAN-2026-10-09.md` Phase 1. Re-run and
replace the "current" tables whenever either side changes; keep the history
table.

Last measured 2026-10-06.

## Setup

| | ASK2 | NXP ASK (vendor) |
|---|---|---|
| Board | `.185` | `.106` |
| Software (current) | VyOS image `2026.10.06-0526-rolling`, `dpaa1` `dc8591ae` (patches 0215–0219, RX buffer pool 640/CPU, RX data at 256), kernel 6.18.54-vyos | Vendor's own NXP ASK on OpenWrt (Mono 25.12.5, kernel 6.12.103) |
| Offload proof | ehash hit counters: ~17.5M hits per unidir run, ~34M per bidir run | Kernel RX packets in the window: 1–4 per run |
| Raw data | `/mnt/builds/ask2-review/oracle/a6-ask2-185-dc8591ae.csv` (current); earlier: `a6-ask2-185-align256.csv` (test DTB), `a6-ask2-185-bpool640.csv`, `a6-ask2-185.csv` | `/mnt/builds/ask2-review/oracle/a6-vendor-owrt-106.csv` |

Method (binding, `ASK2-REWRITE-PLAN.md` §8): `oracle/baseline3.sh`, iperf3 `-Z`,
40 s per run, unidir `-P 8`, bidir two concurrent `-P 16` clients, tuned Dells
(dell1 `.112` ↔ DUT ↔ dell2 `.113`, rig `bin/testrig-combo-matrix.sh`), routed
with no NAT on every combo, median of 3 runs. Throughput is aggregate Gbit/s.
Retransmits are TCP retransmits per 40 s run, summed over all streams.
ASK2 warm boot (`sudo reboot`) into the image; vendor warm boot.

## Unidirectional (current)

| Combo | ASK2 Gbit/s | ASK2 retransmits | Vendor Gbit/s | Vendor retransmits |
|---|---|---|---|---|
| port→port v4 | 9.35 | 10 | 9.34 | 10 |
| port→port v6 | 9.23 | 55 | 9.22 | 2 |
| vlan→port v4 | 9.33 | 115 | 9.32 | 1 |
| vlan→port v6 | 9.22 | 2 | 9.19 | 5 |
| vlan→vlan v4 | 9.34 | 7 | 9.34 | 0 |
| vlan→vlan v6 | 9.20 | 57 | 9.20 | 45 |

## Bidirectional (current)

| Combo | ASK2 Gbit/s (range) | ASK2 retransmits (range) | Vendor Gbit/s | Vendor retransmits | ASK2 vs vendor |
|---|---|---|---|---|---|
| port↔port v4 | 17.82 (17.63–17.89) | 600k (235k–931k) | 17.67 | 339k | +0.8 % |
| port↔port v6 | 17.48 (17.38–17.51) | 556k (136k–556k) | 17.32 | 294k | +0.9 % |
| vlan↔port v4 | 12.77 (12.72–12.85) | 8.33M (8.30M–8.64M) | 12.84 | 9.77M | −0.5 % |
| vlan↔port v6 | 12.55 (12.43–12.70) | 8.29M (8.10M–8.52M) | 12.53 | 8.89M | +0.2 % |
| vlan↔vlan v4 | 17.79 (17.72–17.80) | 460k (234k–566k) | 17.61 | 323k | +1.0 % |
| vlan↔vlan v6 | 17.35 (17.24–17.48) | 538k (425k–884k) | 17.20 | 210k | +0.9 % |

## History (bidir median Gbit/s / retransmits)

| Combo | 2026-10-05 `1e8865d5` | 2026-10-06 `7ba747e1` (0219 + pool 640) | 2026-10-06 `dc8591ae` (+ RX data at 256) | Vendor |
|---|---|---|---|---|
| port↔port v4 | 13.59 / 10.70M | 15.55 / 705k | 17.82 / 600k | 17.67 / 339k |
| port↔port v6 | 13.35 / 10.00M | 15.09 / 3.01M | 17.48 / 556k | 17.32 / 294k |
| vlan↔port v4 | 10.27 / 14.96M | 12.06 / 4.90M | 12.77 / 8.33M | 12.84 / 9.77M |
| vlan↔port v6 | 9.72 / 15.04M | 11.69 / 4.44M | 12.55 / 8.29M | 12.53 / 8.89M |
| vlan↔vlan v4 | 12.53 / 13.71M | 15.49 / 671k | 17.79 / 460k | 17.61 / 323k |
| vlan↔vlan v6 | 12.66 / 14.13M | 15.26 / 1.28M | 17.35 / 538k | 17.20 / 210k |

On 2026-10-05 port→port unidir also showed a steady ~710k retransmits per
run; that is gone on the current image.

## Reading the results

- **A6 throughput passes.** Unidir is at line rate and bidir matches or beats
  the vendor on all six combos (−0.5 % to +1.0 %).
- **Two fixes closed the gap.** The RX buffer pool fix (`7ba747e1`, 128 → 640
  buffers/CPU) stopped the BMI out-of-buffer discards (`fmbm_rodc`) that
  accounted for nearly every retransmit. The RX data alignment fix (`dc8591ae`,
  data at 256 instead of 272, vendor DT `buffer-layout <0x60 0x40>` parity)
  removed a ~15.5 Gbit/s byte-rate ceiling caused by partial-cache-line DMA
  writes on every received frame.
- **Retransmits on port↔port and vlan↔vlan are still 1.4–2.6× the vendor's**
  at equal throughput. Not a gate item; still open.
- **vlan↔port loses heavily on both stacks** (~8–10M retransmits): the known
  MAC RX FIFO overflow on this traffic mix. ASK2 now loses slightly less than
  the vendor. The earlier bimodal 18.75 Gbit/s run did not recur.
- **Ruled out along the way:** per-port BMI resources, FMan global DMA/FPM
  defaults (mainline and SDK identical), and FE record cost (ASK2 records do
  less per-frame work than the vendor's).
