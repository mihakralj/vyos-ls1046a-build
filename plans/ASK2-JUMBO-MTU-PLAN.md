# ASK2 jumbo MTU plan: reach vendor parity (9000+)

Status: **Option B adopted; jumbo acceptance remains open.** Repaired patch 0177 removes the MAC buffer-size clamp; CI run `41887348300` built image `2026.11.04-1827` (kernel `6.18.60-vyos`). The guards allow 1280–9000; this is a configuration range, not a validation claim for every MTU, feature, workload or current image. RX pools remain 4 KiB order-0; contiguous jumbo buffers/page_pool are deferred.

On 2026-11-09, the F-308 gate on DUT1, image `2026.11.08-2332-rolling`, observed the margin return to 256 at MTU 9000, successful full-size DF pings, and approximately 9.8 Gbit/s one-way TCP traffic. Cold versus warm boot was not recorded. CSV writes failed, and the hardware classifier reported PARTIAL using an estimated 1448-byte packet size, so its hardware-share verdict is unreliable for jumbo. The retained iperf JSON reports MSS 8948 and PMTU 9000, but actual packet sizes and full-hardware forwarding were not independently established. See the master plan F-308 row.

On image `2026.11.11-2140`, some unicast, NAT and VLAN-to-VLAN bidirectional 9k cells collapsed in over 50% of runs; a same-rig software control remained steady at about 13.0–13.2 Gbit/s. The cause and current-image status are unresolved, not proof of a new current-image bug. Revalidate with packet sizes, per-direction delivery and hardware share measured.

### Step 0 result (HW, `.185`, image 2026.10.31-0245, offload engaged, cold-ish boot)

With the 0177 buffer clamp neutralised (mEMAC MAXFRM written live to 9026 after `ip link set mtu 9000`), DF pings at 3000, 3600, 4500, 6000 and 8972 passed through the DUT, and `iperf3 -P4` at MTU 9000 with hardware offload engaged ran 9.86 Gbit/s (127 retransmits), `fe_ehash_stats` hits growing (about 650k per 3 s), `rx dma error` and `rx frame physical error` 0, no kernel warnings, no wedge. The CPU RX path can reassemble scatter-gather frames with `sg_fd_to_skb()`; that does not describe the FE-hit path, which bypasses CPU reassembly. This experiment observed selected jumbo forwarding with FE hits, not universal SG safety or current-image acceptance.

The repaired `0177` no longer caps the MAC max frame by `dpaa_bp->size`; the `data/vyos-1x-036/037/043` guard ceilings are raised to 9000. Image `0511` already tested A3 at 9000: 96.9% L1 rate, 0.00291% loss and reported hardware share 1.000 (`suite-ask2-jumbo-0511`); boot type was not recorded in that evidence. The stricter zero-loss target and current-build repeat remain open, as do a 30 min MTU-9000 soak (`MemFree`, buddyinfo), MTU-1500 regression (A1, B1, C1), and feature-specific VLAN/PPPoE jumbo tests.



## 1. Goal and gap

| DUT | Largest working MTU (DF ping, 2026-10-30 battery) | Basis |
|---|---|---|
| OpenWrt + NXP ASK (.70, SDK `fsl_dpa` driver) | at least 9578 | DF pings pass at 3562, 3600, 4000, 6000, 9000, 9578; A3 at 9000: 84.9 % line rate, 0 % loss, 100 % hardware |
| vyos/ASK2 (DUT1, mainline `dpaa_eth` + our patches) | 3562 (3568 fails) in that historical battery, before clamp removal | A3 at 3562: 88.8 % line rate, 100 % hardware; later selected 9000 tests are described above |

Target: ASK2 passes A3 at MTU 9000 with offload engaged (100 % hardware share, 0 % loss, no port wedge), with the 1500 MTU baseline unchanged (A1 3.70 Mpps, B1, C1, C2) and the default memory footprint unchanged.

## 2. Root cause (corrected)

The limit is not in kernel.org. Upstream v6.18 `dpaa_eth.c` sets `max_mtu = fsl_fm_max_frm - 22` (9578 with `fsl_fm_max_frm=9600`), has no MTU clamp against the buffer size, and has an RX scatter-gather path (`sg_fd_to_skb()`), so FMan can spread a jumbo frame over several 4 KiB buffers. The 2026-08-16 qdrant note ("no RX SG in mainline", "frames above about 3700 B cannot be received") is wrong about RX scatter-gather and needs a correcting entry.

The historical limit came from two things in our tree:

1. **`0177-dpaa-memac-max-frame-from-mtu.patch`** added `dpaa_apply_max_frame()`, which formerly clamped the MAC maximum frame length to `dpaa_bp->size`; the repaired patch removes that clamp.
2. **`dpaa_bp->size` is 3584.** `DPAA_BP_RAW_SIZE` is 4096 (F-222 reverted F-203's order-1 buffers to order 0). With `CONFIG_DPAA_ERRATUM_A050385=y`, `dpaa_bp_size()` is `(4096 − 320) & ~255 = 3584` (320 is the assumed arm64 `skb_shared_info` size). Under the former clamp the largest frame was 3584 B including FCS, which is MTU 3566. That matches 3562 passing and 3568 failing. The DT on both boards has no `fsl,erratum-a050385` property, so the 256-byte rounding appears to buy nothing here.

The clamp was added because earlier oversized frames wedged the port (2026-08-16, 2026-10-22 notes). Whether the cause was scatter-gather frames reaching the offload (FE) path or the since-fixed ENQUEUE-MTU bug (F-270) is not established.

## 3. How the vendor does it

Source: OpenWrt `target/linux/layerscape/files/.../sdk_dpaa/` (`dpaa_eth.h`, `dpaa_eth_sg.c`, `dpaa_eth_common.c`); build config `target/linux/layerscape/armv8_64b/config-6.12`; live `.70`.

| Item | Vendor (SDK `fsl_dpa`) | Ours |
|---|---|---|
| Kconfig | `CONFIG_FSL_DPAA_ETH_JUMBO_FRAME=y`, `CONFIG_FSL_FM_MAX_FRAME_SIZE=9600` (Kconfig says the option requires 9600) | stock mainline, `fsl_fm_max_frm=9600` module parameter |
| RX buffer raw size | `(9600 + DPA_MAX_FD_OFFSET + sizeof(skb_shared_info) + 128) & ~(SMP_CACHE_BYTES-1)`, about 10 KB, one buffer per frame | 4096 (`DPAA_BP_ORDER 0`) |
| Allocation | `netdev_alloc_frag(SMP_CACHE_BYTES + DPA_BP_RAW_SIZE)`: page-fragment cache, normally 32 KiB order-3 blocks, about 3 buffers per block | `dev_alloc_pages(0)` |
| Usable buffer size | `dpa_bp_size() = SKB_WITH_OVERHEAD(raw) − SMP_CACHE_BYTES` | 3584 |
| MAC clamp to buffer | none; MTU limit is `dpa_max_frm − 22` | removed from `dpaa_apply_max_frame()` by repaired 0177 |
| A050385 | `fm_has_errata_a050385()` reads DT `fsl,erratum-a050385`; if set the buffer drops to 2048 and the alloc is a plain page. `.70` FMan DT has no such property, so the jumbo path is active | compile-time rounding only; DT property absent |
| Offload of jumbo flows | A3 at 9000 reported 100 % hardware, consistent with jumbo frames traversing the FE hit path in one contiguous buffer | selected 9000 FE-hit observations and historical A3 above; current acceptance open |
| Caveat in ASK source | `ask_flowtable_core.c` (`ft_detours`, A394): a jumbo frame through the offline-port detour arrives in several buffers and a flood of them timed out FMan's host-command channel, so ports with a jumbo MTU are excluded from that detour | n/a |

So the vendor reaches 9000 with large contiguous RX buffers on the same silicon and FMan microcode (210.10.1). It does not use scatter-gather on the main RX path. The same family of risk we hit with F-203/F-222 (large-order page allocation under fragmentation) exists in the vendor, but it refills 32 KiB blocks (one order-3 allocation per three buffers) instead of one order-1 allocation per buffer.

## 4. Options

Option B was adopted, but its selected observations do not close the outstanding acceptance gates.

- **Option A (deferred): vendor-style jumbo buffers.** A possible design would add a module parameter (for example `fsl_dpa.jumbo_rx=1`, default 0, settable from the kernel command line like `fsl_dpaa_fman.fsl_fm_max_frm`). When set, the RX bpool would use a raw size computed like the vendor (`9600 + headroom + shinfo + 128`, cache-line aligned) allocated with a page-fragment cache (`page_frag_alloc`) rather than one `dev_alloc_pages()` call per buffer; DMA map/unmap and `free_pages()` sites would become fragment-aware. Default behaviour (4 KiB order-0) would remain unchanged, limiting the F-222 regression risk to the opt-in mode. Fixing mode at boot would avoid re-seeding a live pool. The old idea of automatically following `dpaa_bp->size` in `dpaa_apply_max_frame()` is superseded by the repaired 0177.
- **Option B (adopted): lift the clamp and retain order-0 buffers.** CPU-bound SG frames use upstream reassembly; FE hits follow a separate path. The October observation supports selected FE jumbo traffic, not every offload or sustained workload. The vendor's A394 note remains a warning about the offline-port detour, not proof that all SG offload fails.
- **Option C: a reserved-memory (`shared-dma-pool`/CMA) region for the jumbo buffers.** Deterministic against fragmentation, at the cost of boot-time reserved memory and a DTS change. Use only if Option A fails the soak test.

## 5. Work plan

| Step | Work | Exit criterion |
|---|---|---|
| 0 | Historical Option B experiment on DUT1: live MAXFRM override and selected DF pings and engaged traffic on 0245, cold-ish boot. The proposed full sweep used `bin/testrig-mtu-steps.sh` and `bin/testrig-udp-sweep.sh` | Selected observations above; not the complete per-mode matrix |
| 1 | Before further silicon-facing changes, run the qdrant gate: at least three focused queries on BMI buffer size and `FMBM_*` fields, SG FD layout, F-203/F-222 history; cross-check `arch/fman-microcode-210-programming-reference.md` and `specs/fman-keygen-flow-key-spec.md` | Gate recorded and contradictions resolved |
| 2 | If reconsidering Option A, check how buffer size reaches FMan (`fman_port` ext buffer size, `FMBM_EBMPI`) and per-interface bpool count (`config_count`): roughly count × raw size per port | Memory budget on the 4 GB board written down |
| 3 | Option B implemented by repaired 0177; Option A Layer-1 patch and parameter are deferred, not a current requirement | Any future alternative builds and passes `bin/test-fixups.sh`, preserving default behaviour |
| 4 | Guards `data/vyos-1x-036`/`037`/`043` allow 1280–9000 without a jumbo-pool parameter | Allowed bounds are distinct from the tested subset |
| 5 | Cold-boot validation, one variable per run: MTU 3600, 4500, 6000, 9000 engaged with verified frame sizes and FE attribution; PPPoE and VLAN jumbo; investigate 2140 bidirectional collapse and repeat on current build. Run a 30 min soak at 800 Mbit/s routed (the F-222 failure case), logging `MemFree` and `/proc/buddyinfo` | No wedge or allocation failures; current A3 at 9000 meets 100 % hardware and zero-loss target |
| 6 | Regression at MTU 1500 under adopted Option B: A1, A2, B1 (16k flows), C1, C2 | Within run-to-run noise of the 2026-10-30 results |
| 7 | Repeat A3 against the vendor DUT and update `plans/ASK2-VENDOR-PERF-BATTERY.md`; retain the 0511 result and its nonzero loss | Comparison row updated; stricter zero-loss and current-build acceptance remain open |

## 6. Risks

- Port wedge (RX-deaf, cold boot required) on an oversized or scatter-gather frame. Always cold-boot before each experiment; never test on eth0.
- Option A, if reconsidered, would cost about 2.5 times the RAM with 10 KB buffers versus 4 KiB (four times with 16 KiB page-per-buffer blocks) and risks large-order allocation failure under fragmentation (F-222). Option B retains order-0; the soak and fragmentation logging still remain required.
- XDP/AF_XDP (`xdp_validate_mtu()`, the 3290 cap) must keep working; any future jumbo pool must not be selected for XDP-attached ports, or the XDP path must refuse jumbo mode.
- `ask.ko` FE records and the F-262/F-270 MTU fields (ENQUEUE MTU 0xffff) must stay correct for frames above 1500.
- Memory-footprint trade-off: the vendor notes small-frame throughput may drop with jumbo buffers; measure if Option A is revisited.
- WGFP's sink releases non-contiguous descriptors rather than processing them; selected routed FE observations do not establish broad WireGuard jumbo safety.

## 7. Out of scope

AF_XDP/VPP MTU (3290), IPv6 egress MTU handling in `ask.ko`, the F-262 PPPoE MTU-shrink case, and the DPAA2 driver.
