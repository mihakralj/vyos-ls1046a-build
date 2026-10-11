# ASK Dataplane Regression Tests

This directory imports the complete DUT test directory from
[we-are-mono/ASK](https://github.com/we-are-mono/ASK/tree/b6638492706edefb211a21c26ed79a78410d1967/tools/tests),
together with the original runner, testing guide and required support packages.
The test scenarios retain their original assertions and GPL licensing;
the runner, shared transport and documentation are adapted for this repository.
The upstream snapshot is commit `b6638492706edefb211a21c26ed79a78410d1967`;
the unchanged capacity module's Git blob matches that snapshot.

See [testing.md](testing.md) for the `dell1`/`dell2`, `dut1`/`dut2` wiring,
dependency setup, safe invocation and existing image/TFTP tools.

## Files

| File or directory | Purpose |
|---|---|
| [run_test.py](run_test.py) | Collect locally or stage/launch one hardware worker on `dell2` |
| [bench.json](bench.json) | DUT management, data NICs, namespaces and endpoint addresses |
| [conftest.py](conftest.py) | Shared DUT SSH and LAN/WAN fixtures |
| [ask_orch/](ask_orch/) | Existing orchestration code, SSH profiles, capability gates and artifacts |
| [askd_agent/](askd_agent/) | Existing framed protocol and observation operations; temporary stdio agents |
| [dut/](dut/) | Original staged DUT helper programs; excluded from test discovery |
| [golden/](golden/) | Original expected results and kernel-log allowlist |
| [requirements.txt](requirements.txt) | Pinned Python dependencies |
| [_harness_tests.py](_harness_tests.py) | Host-only adapter regressions; run explicitly |

## Coverage Boundaries

| Group | Required backend | Current rig |
|---|---|---|
| Portable cases in [smoke.py](smoke.py) | Existing dual-stack routing and SSH | Applicable to `dut1` and `dut2`; no offload claim |
| `flowtable_*` | Native Linux-flowtable/CDX adapter, status API and KASAN | Compatibility-skipped on both current images |
| Multicast, reassembly and mixed profiles | Native-flowtable/CDX features and KASAN | Compatibility-skipped; retain feature-specific checks |
| `ipsec_*`, `cdx_ioctl_*` and DPA tests | CDX, KASAN and the selected debug/feature interfaces | ASK2 is not CDX; vendor production image lacks instrumentation |
| [abi_snapshot.py](abi_snapshot.py) | Imported ioctl constants and committed golden snapshot | Host-only; does not inspect the running DUT's ABI |
| Original agent/boot smoke cases | KASAN, compatible agent and complete retained boot history | Separate from portable checks |
| Wi-Fi cases | Wi-Fi hardware, matching signed probe/driver binaries and instrumentation | Not part of this wired rig |

The original directory contains 549 collected cases. Nine portable checks were
added without deleting those scenarios. Host adapter checks are separate from
hardware coverage and are not counted as DUT proofs.

Changing a DUT address is not a backend port. In particular, `/proc/cdx_flowtable`,
native generation cookies, CDX ioctl layouts and allocator fault controls have
no assumed ASK2 equivalents. Missing capabilities remain visible as skips;
`--release` makes selected skips fail. New ASK2 ports should reuse the numbered
packet, lifecycle, rollback and resource-reuse scenarios while asserting real
ASK2 state and preserving independent delivery/offload evidence.

## Verification Status

Only tests marked `ask2` run by default; the imported upstream scenarios,
`smoke.py` and `abi_snapshot.py` do not exercise ASK2 (they need CDX, KASAN and
the DUT UART agent), so they and the host adapter checks are filtered out unless
`ASK_INCLUDE_NON_ASK=1`. [ask2_offload.py](ask2_offload.py) holds 36 cases: ask.ko
loaded with both ports armed, plus IPv4/IPv6 UDP and TCP unicast forwarding
proven by FMan ehash per-record packet counts. On 2026-10-10 34 pass live (the 2 planned-feature gates, bridge and IPsec, fail by design) on
`dut1` (`.185`) through `dell2`, and the traffic tests fail with the ports disengaged
(`vyos-offload-ask --port 0x10|0x11 family 0`; re-engage with `family 3`).
`dell2` needed `python3.14-venv`, `git`, `ethtool`, `conntrack`, `nftables`,
`dnsmasq-base`, `ppp` and `python3-scapy`; `dell1` got the same tools except the
venv package.
The launcher needs an SSH agent holding `vyos_key` and `admin_key`.
