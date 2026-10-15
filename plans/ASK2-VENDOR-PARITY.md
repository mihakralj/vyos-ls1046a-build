# ASK2 vs NXP ASK: offload feature parity

Throughput is Gbit/s of iperf3 `-Z` payload on the dell rig (X710, 10G DAC),
from `bin/testrig-offload-quick.sh`: 30 s per cell, 8 streams, steady state
from 10 s. A cell is the mean of 3 runs where its Direction column says
"mean of 3". Most current bidir rows use ASK2's static-rig re-run,
`oracle/static185-bidir-r{1,2,3}.csv`, against the earlier static vendor
baseline, `oracle/rep-bidir-106-{1,2,3}.csv`; these two batches were not
alternated. NAT66 and VLAN translate v4 now use the completed five-pair
alternating follow-up, `oracle/paired-bidir-static20261014-paired/`, as
labelled in their rows. The earlier alternating bidir run with unintended DHCP clients
on ASK2 is retained below as diagnostic history, not the current baseline.
NAT across VLANs and PPPoE also use three alternating runs:
`oracle/natvlan-{185,106}-{1,2,3}.csv` and
`oracle/pppoe-{185,106}-{1,2,3}.csv` (the earlier notes are dated 2026-10-15).
The fragmentation probe is a single N=6000 run per case,
`oracle/frag-{185,106}.txt` and `frag-{185,106}-warm.txt`. Other throughput
rows use one run unless noted (expect about ±1 % run-to-run). "eth3→eth4" is
dell1 → DUT eth3 → DUT eth4 → dell2 and "eth4→eth3" is the reverse, so each
port is measured as ingress and as egress; each is one client. "bidir" is one
client with `iperf3 --bidir` (8 streams each way, both directions' streams
connect before data flows); Gbit/s is the sum of both directions. NXP ASK is
`.106`, ASK2 is `.185` (image `2026.10.14-0217`). Every cell below was checked as hardware-forwarded (per-record hit counts on ASK2,
frames that never reach the kernel on `.106`). Dates are 2026. "—" means not
offloaded.

A cell holding two numbers is written `v4 <Gbit/s> / v6 <Gbit/s>`: the label
always comes before its number, so `v4 9.28 / v6 9.15` is IPv4 9.28 and IPv6
9.15.

**Pass rule: ASK2 slower than NXP ASK in any measured cell is a FAIL** and must
be investigated (repeat the cell at least 3 times alternating boards, then find
the cause), whatever the size of the gap. The Verdict column applies it per
family. A FAIL decided from a single run is marked "repeat pending"; every other
row is a mean of 3 or has the repeats noted in its verdict. Overlapping ranges
or a statistically unresolved difference do not override this rule: a lower
ASK2 mean remains FAIL pending investigation.

## Closed defect: software traffic starved behind offloaded traffic (ASK2 only)

When a port's transmit side is saturated by hardware-forwarded traffic,
packets the kernel forwards out of the same port are held for seconds.
Measured 2026-10-14 on image 0217: dell2→dell1 at 9.2 Gbit/s in hardware
(egress eth3), dell1→dell2 pings came back after 0.06–11.7 s (5.4 s average,
in a sawtooth: replies released in batches), and 5 of 10 new TCP connections
timed out at 3 s. Idle, the same path answers in 0.72 ms and connects in
3 ms. The vendor under the same load and rig: 0.85 ms pings, 8/8 connects in
3–4 ms.

A capture at dell2, the DUT and dell1 shows where the delay is: dell2 sends
the SYN-ACK at once, the DUT kernel receives it on eth4 and hands it to the
eth3 driver within 0.1 ms, and dell1 receives it 4–10 s later together with
the SYN-ACKs of later connections. So the frames wait in the DPAA/QMan
transmit path behind the hardware path's no-confirm TX queue. It is not the
egress congestion group: eth3 never entered congestion. An earlier probe read
a 3.66 GB byte count on that queue with a frame count of 0; the frame count
says it was a stale counter, not a backlog.

**Root cause (found and hardware-validated on image 2058, 2026-10-14).**
`dpaa_alloc_offload_tx_fq()` (F-199) allocates the hardware path's TX queue
zeroed and never sets its work queue, so it is initialised on WQ0. QMan serves
WQ0 and WQ1 with absolute strict priority; the kernel's best-effort TX queues
sit on WQ6 (`dpaa_assign_wq()`), which is only served while WQ0 is empty. A
line-rate offloaded flow keeps WQ0 non-empty, so every kernel-forwarded frame
to that port waits until it drains. That matches the sawtooth and the
batch release. The original PR14z23 design specified WQ 6 for this queue; the
F-199 rewrite on `dpaa_setup_egress()` dropped it. The vendor does not have
the problem because its hardware path goes through a pool-channel queue on
WQ5 and then the port's 16 TX queues.

**Fix.** `bin/kernel-fixups/F_199.py` now sets `dpaa_fq->wq = 6` in the
allocator, so offloaded and kernel traffic share one work queue and QMan's
deficit round robin serves every queue in turn. The existing F-199 marker
no longer silently skips an older WQ0 allocator: a count-gated upgrade fixes
already-applied trees, and a missing or duplicate anchor fails the build.
The no-confirm context `0x9a000000c0000000`, FQD-local tail-drop, RTNL locking,
teardown and normal kernel TX queue setup are unchanged.

Source validation on 2026-10-14: five host regressions passed (fresh apply,
idempotence, stale-tree upgrade and drift refusal), as did the repository's
fixup gate. The actual ARM64 `dpaa_eth.o` compiled on the resolver-selected
`6.18.54-vyos`, using the shipped production config and the existing F-052
prerequisites. Disassembly contains the WQ6 byte store in the allocator;
rerunning F-199 leaves the generated C byte-identical. This is targeted driver
compilation, not a full image. The initial full-stack replay stopped at the
pre-existing `0207-fman-pcd-kg-port-attach-cc-l2.patch` context drift in
`fman_pcd_kg.c`.

Full-build follow-up on 2026-10-14: fresh upstream defaults resolve
`6.18.55`, matching the running board. Patch 0207 was regenerated from the
stack-described pre-0207 tree, before any fixup mutations. Its 120 added
lines are unchanged; only blob IDs, line numbers and the trailing context
changed to account for patch 0191's intervening O/H attach function.
The complete board stack and fixes then applied with `git apply --3way`,
without skipped patches or a raw-patch fallback. All production fixups and
normal file injections completed; WQ6 and all three tripwire layers were
checked. The full `6.18.55-vyos` kernel/module build and Debian packaging
completed with the shipped production configuration and certificate inputs.
The final driver's disassembly confirms the WQ6 byte store. ASK was rebuilt
against this kernel, signed, and checked against the exact certificate
embedded in `vmlinux`; its vermagic is `6.18.55-vyos`. The full signed ISO
`vyos-2026.10.14-2058-rolling-LS1046A-arm64.iso` was built in an isolated copy
of the normal production build state and published to lxc200, without
modifying the existing build workspace. The ISO's kernel matches the freshly
built Debian package; its ASK module and DTB match the validated payloads.
ISO9660, the U-Boot FAT32 partition, the unique image version and the served
minisign signature were checked. Both `latest.iso` and `latest.iso.minisig`
now point at 2058. ISO SHA-256:
`3d490160b23f269224d7bd4b542fad6455a02a3e1c2c908d7e00914c04f176ca`.

The operator installed 2058 on `.185` (kernel `6.18.55-vyos`, build
`#1+b2026.10.14204727`). Pinned URL:
`http://192.168.1.137:8080/iso/vyos-2026.10.14-2058-rolling-LS1046A-arm64.iso`.

**Post-fix hardware result (2026-10-14, dell2-run `starvation-check.py`,
50 s P8 IPv4 routed `iperf3 -Z`, ping plus 10 fresh TCP connects on 5204):**

| Board | Egress | Bulk Gbit/s | HW ratio | Ping min/avg/max ms | Loaded TCP connects |
|---|---|---:|---:|---|---|
| ASK2 2058 | eth3 | 9.409 | 1.06 | 0.502 / 0.543 / 0.569 | 10/10, 0.52–1.11 ms |
| ASK2 2058 | eth4 | 9.411 | 1.07 | 0.483 / 0.544 / 0.611 | 10/10, 0.54–1.47 ms |

Pre-fix ASK2 averaged 4134–4469 ms ping with 3–6 of 10 connects. Starvation
is **fixed**: latency under a hardware-forwarded line-rate flow is at or below
the vendor control (0.52–0.57 ms) with no loss of bulk throughput. No leftover
listeners remained on dell2.

`dpaa_eth` is built in, so a module-only update could not validate this fix.
The operator-installed 2058 kernel passed the original gate in both
directions: line-rate hardware forwarding, loaded ping below the vendor's
0.85 ms, and 10/10 loaded TCP connects in a few ms. The pre-fix measurements
below are retained as diagnostic history, not an open hardware FAIL.

### Reproduced on the static rig, both egress ports

Fresh pre-fix IPv4 measurements on 2026-10-14, after removing DHCP from the
traffic ports, still reproduce the defect on **both** ASK2 ports. Existing
kernels were left running; no reboot, image installation or power-cycle
was performed. Each bulk run is 50 s, P8, with hardware proof from the quick
harness. Opposite-direction ping and ten fresh TCP handshakes to a dedicated
listener run after the bulk flow has warmed up. A handshake timeout is 3 s.
All idle handshakes succeeded, with medians 0.69–0.81 ms.

| Board | Saturated egress | Bulk Gbit/s | Loaded ping avg / max (ms) | Loaded TCP handshakes | Verdict |
|---|---|---:|---:|---|---|
| ASK2 | eth3 | 9.406 | 4133.920 / 9417.806 | 3/10; 7 timeouts; successful range 0.229–1147.323 ms | FAIL |
| ASK2 | eth4 | 9.290 | 4469.324 / 9750.206 | 6/10; 4 timeouts; successful range 1.198–3000.299 ms | FAIL |
| NXP ASK | eth3 | 9.291 | 0.566 / 0.670 | 10/10, 0.586–1.507 ms | Baseline |
| NXP ASK | eth4 | 9.288 | 0.522 / 0.638 | 10/10, 0.580–1.410 ms | Baseline |

ASK2's hardware proof ratios were 1.06 and 1.08; NXP's were 1.00.
The raw ASK2 ping logs contain 12 transmitted/received packets despite the
requested `-c 10`, with the replies released in a batch; the table uses the
actual reported RTT statistics, not an assumed ten-packet denominator.
NXP sent/received ten. None of these results is a post-fix verdict.

The bounded probe is stored on **both** Dells at
`~/testrig/starvation-check.py`; orchestration runs on dell2 and uses its key
for dell1/DUT SSH. The temporary TCP listeners exited cleanly in both root
and `n106` namespaces. Re-run each egress after the operator deploys the
fixed kernel, with a fresh output directory:

```bash
python3 ~/testrig/starvation-check.py --kind ask2 --egress eth3 \
  --out "$HOME/testrig/out/starvation-$(date +%Y%m%dT%H%M%S)-ask2-eth3"
```

Use `--egress eth4` for the other port and `--kind vendor` for the reference.
Raw CSV, iperf JSON, ping logs, individual handshake timings and controls:
`~/testrig/out/starvation-static20261014/` on dell2, collected to
`oracle/starvation-static20261014/`.

Impact: an offloaded download that saturates a LAN port delays every new
connection, ARP/ND and other kernel traffic to that port by seconds. It also
breaks bidir tests built from two clients: the client that starts second cannot
connect until the first test ends, so its run happens afterwards and the
"bidir" total is really two one-way runs. The bidir rows below use one
`--bidir` client, which avoids that start-up stall.

## Routing and NAT

Measured 2026-10-14 on both boards with the same harness and rig, no NAT
configured for the routed rows. 64 B rows use `testrig-pktgen.sh` (single
primed flow). Raw rows: `oracle/table1-oneway-*.csv`, `tables-uni-*.csv`,
`tables-bidir-*.csv`.

| Feature | Direction | NXP ASK | ASK2 | Verdict |
|---|---|---|---|---|
| Routed | eth3→eth4 | v4 9.28 / v6 9.15 | v4 9.41 / v6 9.28 | PASS |
| Routed | eth4→eth3 | v4 9.40 / v6 9.15 | v4 9.41 / v6 9.27 | PASS |
| Routed | bidir (mean of 3, static rig) | v4 17.70 / v6 17.72 | v4 18.06 / v6 17.81 | PASS v4 +2.1 %, v6 +0.5 %; batches not alternated |
| NAT (v4 NAT44 masquerade, v6 NAT66) | eth3→eth4 | v4 9.28 / v6 9.15 | v4 9.41 / v6 9.28 | PASS |
| NAT (v4 NAT44 masquerade, v6 NAT66) | eth4→eth3 | v4 9.28 / v6 9.28 | v4 9.40 / v6 9.28 | PASS |
| NAT (v4 NAT44 masquerade, v6 NAT66) | bidir (v4 mean of 3; v6 mean of 5 paired, static rig) | v4 17.86 / v6 17.68 | v4 18.02 / v6 17.66 | v4 PASS +0.9 %; v6 FAIL −0.09 % (five alternating pairs; ranges overlap) |
| Port translation (PAT) | | in the NAT numbers | in the NAT numbers | |
| 64 B, highest rate at ≤ 0.001 % loss | one way | not stable at 2.79 Mpps (4 runs: 0.00005 %, 0.046 %, 0.047 %, 0.0003 %); clean at 1.84 Mpps | 2.64 Mpps, 0.00001 % (3 confirm runs) | PASS |
| 64 B, 3.14 Mpps offered (3 runs) | one way | loss 0.011–0.053 % | loss 0.00001–0.046 % | PASS |
| 64 B, unpaced flood (14.7 Mpps offered) | one way | 3.40 Mpps delivered | 4.12 Mpps delivered | PASS |
| Software traffic next to offloaded traffic (ping and TCP connects one way while the other way runs at line rate in hardware) | | ping 0.85 ms avg, max 1.09 ms; 8/8 connects in 3–4 ms | 2058: ping 0.543–0.544 ms avg, max 0.611 ms; 10/10 connects on each egress in 0.52–1.47 ms; bulk 9.409–9.411 Gbit/s | PASS (F-199 WQ6 fix; see the closed-defect section) |
| Fragmenting 1500 B packets into a 1492 B PPPoE link (6000 datagrams, flow already offloaded) | eth4→eth3 | v4 and v6 fragmented in hardware, but the output is corrupt: 0 of 6000 datagrams pass the receiver's UDP check | v4 and v6 fragmented in hardware, 6000/6000 intact | PASS (ASK2 correct, vendor corrupt; details in the PPPoE section) |
| Offload start delay (one 200 Mbit/s flow, 5 trials per cell, `bin/testrig-offload-delay.py`) | | immediate: hardware takes over within the handshake, 7–14 frames reach the kernel (TCP/UDP, v4/v6) | `offload_delay_ms=0` (live override on 2058; source default changed to 0 on 2026-10-15, not yet deployed): 0.001 s / 3 kernel frames (TCP v4, v6), 0.0 s / 1 frame (UDP v4, v6), measured by tcpdump on the DUT. With 500 ms it was 1.0–1.06 s and 17.5k (TCP) / 20.9k (UDP) frames | PASS for the start delay; but see "Short TCP flows linger in conntrack" (new FAIL) |
| Per-flow counters | | yes (`cmm` query) | yes (`ynl dump-flows`, matches the FMan record exactly) | PASS |

ASK2 is symmetric between the two ports within 0.1 %. The vendor's same cell
differs by up to 1.3 % between directions (routed v4 9.28 / 9.40, NAT66
9.15 / 9.28), so a one-way gap of about 1 % in the vendor's favour could be the
port pairing, not the stack; every one-way cell is at or above the vendor
anyway. Raw rows: `oracle/tables-rev-185.csv`, `tables-rev-106.csv`.

### Earlier bidir run: DHCP-contaminated ASK2 configuration

These historical numbers used three runs per cell, alternating boards
(Gbit/s, mean with min–max). ASK2 still had unintended DHCP clients on its
traffic ports; the static-rig re-run supersedes these ASK2 numbers.

| Cell | NXP ASK | ASK2 | ASK2 vs NXP |
|---|---|---|---|
| Routed v4 | 17.70 (16.86–18.22) | 17.25 (16.72–17.90) | −2.5 % |
| Routed v6 | 17.72 (17.36–17.92) | 17.63 (17.14–17.93) | −0.5 % |
| NAT44 | 17.86 (17.60–18.01) | 17.57 (16.49–18.14) | −1.6 % |
| NAT66 | 17.66 (17.32–17.92) | 17.75 (17.59–17.97) | +0.5 % |
| VLAN translate v4 | 18.07 (18.05–18.09) | 17.61 (16.72–18.12) | −2.6 % |
| VLAN translate v6 | 17.84 (17.77–17.91) | 17.74 (17.63–17.97) | −0.5 % |
| VLAN↔port v4 | 12.88 (12.63–13.03) | 12.96 (12.74–13.10) | +0.6 % |
| VLAN↔port v6 | 12.79 (12.77–12.80) | 12.74 (12.54–12.92) | −0.4 % |

Every min–max range overlaps, so three runs cannot call any single gap real.
The pattern across runs is what matters. The vendor's DUT busy share never
exceeded 0.1 % in 24 runs. ASK2 sat at 0.4–0.5 % in 14 runs and at 2–14 % in
10 of 24. This was initially interpreted as software forwarding during the
steady window, but later conntrack samples showed all data flows offloaded
and identified user-time DHCP-script activity instead. Taking the
18 Gbit/s-class cells (not VLAN↔port), the 7 ASK2 runs
above 2 % busy averaged 2.8 % below the vendor with a median of 287 k
retransmits; the 11 runs at or below 2 % averaged 0.2 % below it with a
median of 80 k. That is a correlation across 18 runs, not a proven cause:
the shortfall coincided with higher DUT busy share, not proven software
fallback. The vendor shows its own dips (routed v4 16.86 in one run) with no
software load, so the vendor's noise is about ±3 % here. These contaminated
comparisons are diagnostic history, not product-default benchmarks.

The rig adds spread of its own. The vendor's per-second series for its run 3
(`json-quick/*-bidir.json`, the last board to run a cell overwrites the file)
steps between plateaus every 10 s with 0.1 % DUT busy: routed v4 16.6, then
18.4, then 15.7 Gbit/s in the three 10 s blocks, which averaged 16.86 for the
steady window. Unidir runs on both boards also show a one-second dip at
exactly 10 s and 20 s (9.41 to 8.12 Gbit/s). The cause is not isolated (the
dells' iperf3 and NIC are the candidates), so part of every bidir gap above
may be the rig, and the 3-run means cannot separate it from the DUT. The
per-second series of the ASK2 runs were overwritten, so a clean A/B needs
per-second capture of both boards (`SAMPLE=1` in
`bin/testrig-offload-quick.sh` records ASK2's flows and CPU each second).

The original leads were: which flows are in software after 10 s (conntrack
`[OFFLOAD]` against `[HW_OFFLOAD]` counts at 3, 10 and 20 s, with per-second
iperf3 intervals); whether those flows are the ones starved behind offloaded
traffic (the known defect above, which would predict a fix from `wq = 6`);
and `offload_delay_ms=0` as a single-variable A/B on the start window.

### Bidir v4 with the DUT's DHCP clients stopped

Those leads were followed. Conntrack showed every data flow in hardware for the
whole steady window (`hw=16`, the only `sw` entry is the iperf3 control
connection), so no flow was falling back to software. The DUT's CPU showed
3–4 s bursts of about one busy core at random times, and the ASK2 DUT showed
the same bursts with no traffic at all. They are user-time CPU from
short-lived processes: the DUT config has `address dhcp` on eth0–eth4, the rig
has no DHCP server, and every `dhclient@ethN.service` keeps retrying and runs
`dhclient-script`, which spawns `python3`, `cli-shell-api`,
`vyos-frr-render.py` and three `vyos-hostsd-client` calls (2–4 s of one core
every 10–20 s per port). The vendor (OpenWrt, static rig ports) has no such
churn. The throughput dips follow these bursts loosely (1–3 s after a burst
averages 16.7–17.2 Gbit/s against 17.8 outside bursts); the mechanism by which
a CPU burst slows hardware-forwarded flows is not established.

Steering is not the cause for these cells: vendor-like RPS (eth3=2, eth4=1,
flows 0) against the default (`e e 32768`) moved the 6-rep means by +0.2 % to
+1.3 %, inside the noise (unicast-v4 17.761 → 17.795, nat-v4 17.677 → 17.823,
vlan-v4 17.605 → 17.835).

Stopping `dhclient@eth1..eth4` on the ASK2 DUT (runtime only; eth0 DHCP stays)
removes the spawns (zero in 75 s) and the DUT busy share is 0.4 % in every
rep. Six reps per cell, Gbit/s, `BIDIR=1`, 8 streams each way, run
`oracle/quiet-run.log`, `rpsq-nodhcp-r*.csv` and `rpsq-v106-r*.csv`:

| Cell (bidir) | ASK2 default, DHCP running | ASK2, DHCP stopped | NXP ASK |
|---|---|---|---|
| Routed v4 | 17.76 (17.31–17.98) | 18.03, median 18.04 (17.86–18.10, sd 0.09) | 17.96, median 18.10 (17.14–18.18, sd 0.40) |
| NAT44 | 17.68 (17.14–17.99) | 17.87, median 17.98 (17.15–18.09, sd 0.36; one 17.147 outlier, 95 k retransmits) | 17.92, median 18.04 (17.18–18.17, sd 0.37) |
| VLAN translate v4 | 17.61 (17.42–17.85) | 18.01, median 18.01 (17.90–18.08, sd 0.07) | 17.89, median 18.08 (16.80–18.21, sd 0.54) |

ASK2 against NXP ASK with the clients stopped: means +0.4 % (routed), −0.3 %
(NAT44), +0.6 % (VLAN translate); medians −0.3 %, −0.3 %, −0.4 %. Welch t is
+0.42, −0.27 and +0.51, so none of the three differs from the vendor. The
vendor's own reps include one low outlier per cell (17.14, 17.18, 16.80), and
ASK2's spread is much tighter (sd 0.07–0.09 on routed and VLAN). Verdict:
**tie** for all three rows, not a clear PASS: the medians sit 0.3–0.4 % under
the vendor's, which is below the rig's ±0.5 % resolution, so a strict reading
of the pass rule still marks the NAT44 mean (−0.3 %) FAIL. "Tie" here describes
statistical uncertainty, not a waiver of the pass rule. Removing the unintended
DHCP clients improved the v4 means; it does not prove that every remaining gap
is unrelated to forwarding.

**The DHCP clients were a rig misconfiguration, now fixed.** The traffic
interfaces use static addresses, and the vendor board has no DHCP client. `.185` still
carried `address dhcp` on eth1–eth4 from the image's default config, with no
DHCP server anywhere in the rig (eth1 and eth2 are not even cabled), so four
clients retried forever. On 2026-10-14 the four `address dhcp` lines were
deleted from `.185`'s config (committed and saved, backup
`/config/config.boot.pre-rig-static-20261014`); only eth0, the management
port, keeps DHCP as an explicit exception; management was not converted to
static addressing. Earlier ASK2 runs with these clients enabled are not valid
static-rig comparisons. The six-rep DHCP-stopped experiment already excluded
this interference. Current measurements are in "Static-rig re-run".

What a started client does, measured with `bin/testrig-trigger-probe.py` (a
60 s `iperf3 --bidir` run, one trigger every 10 s, `START=13.4` or 15.4 so the
samples after a trigger stay off the rig's own dip grid; median about 18.2
Gbit/s, run on dell2, DUT quiet):

| Trigger on the DUT | Throughput 2 s after, against the run's median |
|---|---|
| `true` (control) | −0.1 to −0.7 Gbit/s (the rig's own dips) |
| `ip route add/del blackhole` v4 and v6, `ip addr add/del` on lo (2 runs each) | −0.8 to +0.5 Gbit/s, the same as the control; one exception: `route6-1` fell to about 16 Gbit/s for 10 s after its fourth trigger and did not repeat in `route6-2` (the rig's bimodal 16 / 18 Gbit/s plateaus) |
| `systemctl start dhclient@eth1 eth2` (no cable) | −2.2 / −2.5 Gbit/s for about 2 s |
| `systemctl start dhclient@eth3 eth4` | −2.1 Gbit/s for about 2 s |
| `systemctl start dhclient@eth1..eth4` | −4.0 / −4.4 Gbit/s for about 2 s |
| `systemctl stop dhclient@…` | −0.1 to −1.2 Gbit/s |

Starting clients coincided with reproducible throughput dips even on
ports with no link, so the observation cannot be explained by DHCP frames
crossing the traffic links. The route and address probes did not reproduce
the same pattern; the unreplicated IPv6 outlier remains unresolved. The mechanism is not
isolated, and no longer needs to be for the rig: with static addresses nothing
starts a client. Raw output `oracle/trigger-*.json`.

### Static-rig re-run

Measured 2026-10-14 after deleting and saving the DHCP configuration on
eth1–eth4. Scripts and results live on dell2 under `~/testrig/`; dell2 runs
local commands directly and originates SSH to dell1 and the DUT with its
existing key. Azure only transfers scripts and collects results. Current
steering readback is RPS mask `e` on every eth3/eth4 RX queue,
`rps_flow_cnt=0` on every queue, and global `rps_sock_flow_entries=32768`.
The older shorthand `e e 32768` did not distinguish the global table from
per-queue RFS capacity. No DHCP or link-flap probes run
alongside this battery.

All eight cells completed three runs each. The initial results below used the
earlier vendor baseline; completed paired follow-ups supersede the relevant
cells in the current Routing/NAT and VLAN tables. The vendor baseline below is the earlier
three-run static batch, not a new alternating measurement.

| Feature | Direction | NXP ASK, mean of 3 | ASK2, current mean | Verdict |
|---|---|---|---|---|
| Routed | bidir | v4 17.70 / v6 17.72 | v4 18.06 / v6 17.81 (3 reps) | PASS +2.1 % / +0.5 % |
| NAT44 / NAT66 | bidir | v4 17.86 / v6 17.66 | v4 18.02 / v6 17.34 (3 reps) | v4 PASS +0.9 %; v6 FAIL −1.8 % |
| Translate VID | bidir | v4 18.07 / v6 17.84 | v4 18.00 / v6 17.86 (3 reps) | v4 FAIL −0.4 %; v6 PASS +0.1 % |
| Strip tag + add tag | bidir | v4 12.88 / v6 12.79 | v4 12.98 / v6 12.83 (3 reps) | PASS +0.7 % / +0.3 % |

All completed runs are hardware-forwarded. DUT busy is 0.4–0.8 %, not the
earlier 2–14 % bursts. NAT66 remains slower despite removing the DHCP clients:
ASK2's three values are 17.403, 17.589 and 17.021 Gbit/s, compared with the
vendor's 17.319–17.916 range. VLAN translate v4 is also below the vendor
mean under the strict pass rule. Both require fresh alternating comparisons;
removing the rig interference does not automatically make them PASS.
Raw CSV, log and per-run JSON: `oracle/static185-bidir-r*.csv`,
`static185-bidir.log`, `json-static185-r{1,2,3}/`.

The follow-up uses `bin/testrig-parity-bidir.sh` on dell2, five repetitions
per board of `nat-v6` and `vlan-v4`. Each cell alternates boards and reverses
the board order each round. Results and JSON have separate paths per board,
cell and round, and ASK2's per-second conntrack/CPU sampler is enabled.
The runner refuses configured or active DHCP clients on eth1–eth4 and records
the live steering controls before the batch. A slower mean remains FAIL,
even when ranges overlap.

Completed: all five alternating pairs for each cell, all 20 rows hardware
forwarded. NAT66: ASK2 mean 17.662 / NXP 17.677
Gbit/s, FAIL −0.09 % under the strict pass rule (ranges 17.589–17.742 and
17.477–17.802). VLAN translate v4: ASK2 17.834 / NXP 17.283, PASS +3.19 %
(ranges 17.250–18.071 and 16.392–17.966). Low runs on either board remain
in the means; none were discarded. These five-pair means supersede the
three-run comparisons in the current tables.
The first pair was NAT66 ASK2 17.742 / NXP 17.678 (+0.4 %) and VLAN v4
ASK2 18.071 / NXP 16.853 (+7.2 %, one low vendor run), illustrating why a
single pair cannot close a FAIL. ASK2's first-pair one-second conntrack
samples showed 16 hardware-offloaded data flows throughout both steady
windows. Across all ten ASK2 runs, each sampled steady window retained at
least 16 HW-flagged entries for the bulk port. The sampler also counted old
unflagged conntrack entries, so its total entry count is not a count of
currently software-forwarded data flows. Raw results:
`oracle/paired-bidir-static20261014-paired/`, with driver log
`oracle/paired-static20261014.log`.

## VLAN

Same method as above. The VLAN sits on eth3 in both tag cells (strip tag
eth3→eth4, add tag eth4→eth3); the other pairing (add tag eth3→eth4, strip tag
eth4→eth3, VLAN on eth4) is not measured. VLAN↔port bidir is strip tag in
one direction and add tag in the other at the same time; both boards lose
frames at the MAC receive FIFO on this traffic mix (the vendor more than ASK2:
5.7 M / 6.3 M retransmits against 4.2 M / 3.5 M, v4 / v6), which caps both near
13 Gbit/s.

| Feature | Direction | NXP ASK | ASK2 | Verdict |
|---|---|---|---|---|
| Strip tag (VLAN in, untagged out) | eth3→eth4 | v4 9.38 / v6 9.13 | v4 9.39 / v6 9.25 | PASS |
| Add tag (untagged in, VLAN out) | eth4→eth3 | v4 9.26 / v6 9.13 | v4 9.40 / v6 9.26 | PASS |
| Strip tag + add tag (VLAN↔port) | bidir (mean of 3, static rig) | v4 12.88 / v6 12.79 | v4 12.98 / v6 12.83 | PASS v4 +0.7 %, v6 +0.3 %; batches not alternated |
| Translate VID (VLAN in, other VLAN out) | eth3→eth4 | v4 9.26 / v6 9.13 | v4 9.38 / v6 9.26 | PASS |
| Translate VID | eth4→eth3 | v4 9.26 / v6 9.13 | v4 9.38 / v6 9.25 | PASS |
| Translate VID | bidir (v4 mean of 5 paired; v6 mean of 3, static rig) | v4 17.28 / v6 17.84 | v4 17.83 / v6 17.86 | v4 PASS +3.19 % (five alternating pairs); v6 PASS +0.1 % |
| NAT across VLANs (VLAN 10 in, masquerade, VLAN 20 out) | eth3→eth4 (mean of 3) | v4 9.26 / v6 9.13 | v4 9.38 / v6 9.25 | PASS +1.3 % / +1.3 % (ranges 9.255–9.258 and 9.128–9.133 against 9.380–9.384 and 9.249–9.256; no overlap; all runs hardware, proof ratio 1.00 vendor) |
| QinQ / 802.1ad | | supported, not measured | — | |
| DSCP → VLAN priority | | supported, not measured | — (pushed tags carry PCP 0) | |

## PPPoE

The vendor session is a runtime `pppd` client on `.106` eth3 named `pppoe10`
against a second `pppoe-server` in dell1's `n106` namespace
(`bin/testrig-pppoe-vendor.sh up|down|status`; nothing persistent changes).
Same credentials, same concentrator addresses (10.99.50.1 / `fd99:50::1`) and the
same route layout as the ASK2 session, so the two boards see identical traffic.
The vendor's own firewall (fw4) needed a runtime accept rule for `pppoe10`; the
script adds and removes it. RPS is set on dell1's DUT-facing NIC for both boards
(that NIC cannot RSS-hash PPPoE frames).

Three runs per cell alternating between the boards (2026-10-15 15:09–15:30,
`oracle/pppoe-{185,106}-{1,2,3}.csv`); every run was forwarded in hardware on
both boards (proof ratio 1.00 on `.106`, 1.06–1.13 on ASK2). Values are the mean
of 3 with the range under the table.

| Feature | Direction | NXP ASK | ASK2 | Verdict |
|---|---|---|---|---|
| PPPoE decap (PPPoE in on eth3, plain IP out on eth4) | eth3→eth4 | v4 9.28 / v6 9.11 | v4 9.32 / v6 9.23 | PASS: v4 +0.5 % (ranges overlap), v6 +1.3 % |
| PPPoE encap (plain IP in on eth4, PPPoE out on eth3) | eth4→eth3 | v4 9.23 / v6 9.14 | v4 9.37 / v6 9.15 | PASS: v4 +1.5 %, v6 tie +0.03 % (ranges overlap) |
| NAT over PPPoE (masquerade out of the session) | eth4→eth3 | v4 9.27 | v4 9.36 | PASS +1.0 % (ranges overlap) |
| PPPoE over a VLAN | | supported, not measured | — | not covered on either board |
| PPPoE relay | | supported, not measured | — | |

Ranges (Gbit/s, min–max of 3). ASK2: decap v4 9.236–9.360, v6 9.229–9.235;
encap v4 9.365–9.373, v6 9.103–9.229; NAT 9.356–9.365. NXP ASK: decap v4
9.230–9.360, v6 9.109–9.113; encap v4 9.229–9.230, v6 9.099–9.226; NAT
9.225–9.356.

Both boards jump between two levels from run to run on the same cell: v4 near
9.23 or 9.36, v6 near 9.10 or 9.23 (a step of about 1.3 %, the size of 20 bytes
in a 1538-byte frame). It is not a feature of one board: the vendor shows it in
decap v4, encap v6 and NAT, ASK2 in decap v4 and encap v6, and the iperf3 JSON
shows the same MSS on both. The cause is not isolated; it falls under the rig
level shifts already noted for the bidir rows. The means above therefore carry
about ±0.7 % of rig noise, and a ±0.5 % verdict (decap v4, encap v6) is a tie,
not a win.

### Fragmenting into the PPPoE MTU

dell2 sends 6000 UDP datagrams at 500 pps with a 1500-byte IP packet each
(`bin/testrig-pppoe-frag.py ask2|vendor`), through the DUT into the 1492-byte
session toward dell1, which counts the datagrams that arrive intact. The
hardware proof is the DUT's kernel RX count (`ethtool -S`, `rx packets [TOTAL]`
on eth3 + eth4), not the interface statistics, which include hardware-forwarded
frames. A "warm" case first sends 1500 small datagrams on the same 5-tuple so
the flow is offloaded before the oversize datagrams start. On ASK2 the FMan
fragmentation block (`0x1A54300`) is a second proof.

| Case | NXP ASK | ASK2 | Verdict |
|---|---|---|---|
| Control, 1000 B IP packet (no fragmentation), v4 and v6 | offloaded | offloaded | tie |
| IPv4, DF clear, warm flow | hardware, kernel RX 5 of 12000; **0 of 6000 intact** | hardware, kernel RX 3; **6000/6000 intact**; FMan +2 frags per frame | PASS |
| IPv6, warm flow | hardware, kernel RX 6; **0 of 6000 intact** | hardware, kernel RX 5; **6000/6000 intact**; FMan v6 frames +1000, frags +2000 in a 1000-datagram run | PASS |
| IPv4, DF clear, new flow | hardware from the first packet (kernel RX 8); 1 of 6000 intact | kernel fragments during the 1.0 s start delay (kernel RX 1009, `FragCreates` +1004), then hardware; 6000/6000 intact | covered by the start-delay FAIL, no loss |
| IPv4, DF set | host path, ICMP frag-needed 6000/6000, 0 delivered | identical | tie |
| IPv6, new flow with an oversize first packet | kernel drops it and sends Packet Too Big 6000/6000, 0 delivered | identical | tie (see below) |

Vendor corruption: the vendor's hardware fragments reach dell1 and reassemble
(`IpReasmOKs` rises by 6000), but the first fragment's UDP header is all zeros,
so the UDP layer rejects the datagram (`UdpInErrors`). Seen in v4 and v6 on
every run. A real TCP or UDP stream through this path on the vendor would not
get its oversize packets delivered. ASK2's fragments are intact.

Two rig traps that wasted earlier runs. First, the DF-set case makes the DUT
send ICMP frag-needed, and dell2 then caches a path MTU of 1492 and
pre-fragments every later datagram; the DUT sees only IP fragments, which no
stack offloads, and conntrack re-fragments them (`FragCreates` +2 per
datagram). The probe flushes dell2's route cache before every case and aborts
if a cached `mtu` remains. Second, a cold IPv6 flow whose first packet is
oversize never gets offloaded, since the kernel drops that packet and conntrack
never confirms the flow; only a warm flow tests v6 fragmentation. This is
why the v6 cold case ties on both boards.

## Bridging

| Feature | NXP ASK | ASK2 |
|---|---|---|
| Learned-MAC bridging (known-unicast IPv4/IPv6) | supported, not measured | 9.41 one way at 1.5 % CPU (software 2.22 at 32.9 %), `-P 4`, 15 s |
| Static FDB entries | supported, not measured | 20/20 frames in hardware |
| Bridge firewall before offload | yes (hook after the filter) | yes (same hook point) |
| MAC move, STP, isolation, ageing | yes | yes; idle records gone within 25 s at a 10 s ageing time |
| VLAN-aware bridge, same tagging (access↔access, trunk↔trunk, tagged frames on a non-filtering bridge) | supported, not measured | in hardware (B7a, board-tested 2026-10-14: all rows pass; throughput not measured per VLAN mode) |
| VLAN-aware bridge, strip/add (access↔trunk) | supported, not measured | — |
| Routing through a bridge (IRB) | supported, not measured | — |
| Routed and bridged on one port | supported | — (one table per port) |
| PPPoE through a bridge | supported, not measured | — |

## Multicast and tunnels

| Feature | NXP ASK | ASK2 |
|---|---|---|
| IPv4 multicast | supported, not measured | — |
| IPv6 multicast | supported, not measured | — |
| 6in4 tunnel | supported, not measured | — |
| 4in6 tunnel | supported, not measured | — |

## QoS

| Feature | NXP ASK | ASK2 |
|---|---|---|
| Ingress policer | per-queue policer, not measured | caps 10/25/50/80 Mbit/s measured 8.8/21.9/43.7/70.3 (payload); ports with offload off only |
| Rate limit on offloaded traffic | supported (`QM_FF_RATE`), not measured | — |
| Egress shaping and scheduling (CEETM: shaper, weighted and class queues) | supported, not measured | — |
| DSCP → queue mapping | supported, not measured | — |
| Exception-path rate limit (traffic punted to the CPU) | supported (`QM_EXPT_RATE`), not measured | — |

## IPsec

None of this is built yet: `ASK2-IPSEC-OFFLOAD-PLAN.md` is "DRAFT, NOT
STARTED" and `ask_xfrm.c` / `ask_caam.c` / `ask_op.c` are lifecycle stubs. The
vendor offloads ESP through FMan → SEC (CAAM) → FMan with SAs pushed by `cmm`
(`cdx/cdx_dpa_ipsec.c`, `cdx/control_ipsec.c`, `FPP_CMD_IPSEC_*`).

| Feature | NXP ASK | ASK2 |
|---|---|---|
| ESP tunnel mode, IPv4 | supported, not measured | — |
| ESP tunnel mode, IPv6 | supported, not measured | — |
| AES-CBC with HMAC-SHA1/256/384/512 | supported, not measured | — |
| AES-GCM / CCM / CTR / GMAC | supported, not measured | — |
| Legacy algorithms (3DES, DES, HMAC-MD5, AES-XCBC) | supported, not measured | — |
| NAT traversal | supported, not measured | — |
| ESN and anti-replay | supported, not measured | — |
| SA lifetime, rekey, expiry | supported, not measured | — |
| Fragmenting encrypted packets | supported, not measured | — |
| SEC crypto rate policer | supported, not measured | — |

## WireGuard

Neither stack offloads WireGuard: ChaCha20-Poly1305 runs in software on the
A72 cores (an ASK2 fast path is only planned, `ASK2-WIREGUARD-FASTPATH-PLAN.md`),
so these rows compare the software crypto and forwarding cost of the two
kernels. The DUT terminates the tunnel; the encrypted leg runs over one port
(eth3 to dell1, or eth4 to dell2) and the plain leg over the other. One tunnel,
4 TCP streams, MTU 1420, 30 s runs, and a new tunnel for every rep: the serial
transmit worker lands on a different CPU with each new `wg0`, so a single
tunnel is bimodal (the vendor measured 1.36 to 1.68 Gbit/s on one cell), and
the figure is the median of the reps with the spread kept in the CSV. "up" is
the DUT encrypting (plain in, tunnel out), "down" is the DUT decrypting, and
"bidir" is both at once; the inner family is v4 or v6. Run it with
`bin/wg-parity.sh 106|185` (`WG_PORT` in `bin/testrig-wireguard.sh` picks the
port) and build the table with `bin/wg-parity-table.py`.

Median of 3 reps in Gbit/s, measured 2026-10-14 on image
`2026.10.14-0217` (ASK2) and the vendor `.106`, ASK2 first and then the
vendor, 14:06 to 14:53 (the first battery was killed by an agent-host reboot
at 06:37 and restarted). Min–max is in the spread table below.

| Tunnel on | Direction | NXP ASK | ASK2 | Verdict |
|---|---|---|---|---|
| eth3 | up (DUT encrypts) | v4 1.39 / v6 1.27 | v4 1.69 / v6 1.63 | PASS (v4 +21 %, v6 +28 %) |
| eth3 | down (DUT decrypts) | v4 1.21 / v6 1.22 | v4 1.54 / v6 1.42 | PASS (v4 +27 %, v6 +17 %) |
| eth3 | bidir | v4 1.42 / v6 1.42 | v4 1.42 / v6 1.47 | v4 FAIL (−0.1 %, a tie, ranges overlap); v6 PASS (+3.7 %, ranges overlap) |
| eth4 | up (DUT encrypts) | v4 1.34 / v6 1.30 | v4 1.57 / v6 1.62 | PASS (v4 +17 %, v6 +25 %) |
| eth4 | down (DUT decrypts) | v4 1.31 / v6 1.29 | v4 1.48 / v6 1.50 | PASS (v4 +12 %, v6 +17 %) |
| eth4 | bidir | v4 1.57 / v6 1.44 | v4 1.41 / v6 1.37 | FAIL (v4 −9.9 % median, −6.7 % mean over 10 reps per board, ranges overlap, see "eth4 bidir v4" below; v6 −5.3 % on 3 reps, ranges overlap) |

Spread (min / median / max of 3 reps, Gbit/s), vendor | ASK2:

| Tunnel on | Direction | v4 NXP ASK | v4 ASK2 | v6 NXP ASK | v6 ASK2 |
|---|---|---|---|---|---|
| eth3 | up | 1.37 / 1.39 / 1.45 | 1.56 / 1.69 / 1.71 | 0.85 / 1.27 / 1.30 | 1.57 / 1.63 / 1.66 |
| eth3 | down | 1.21 / 1.21 / 1.53 | 1.52 / 1.54 / 1.54 | 1.16 / 1.22 / 1.45 | 1.41 / 1.42 / 1.43 |
| eth3 | bidir | 1.20 / 1.42 / 1.64 | 1.39 / 1.42 / 1.46 | 1.33 / 1.42 / 1.57 | 1.33 / 1.47 / 1.53 |
| eth4 | up | 0.91 / 1.34 / 1.41 | 1.53 / 1.57 / 1.61 | 0.86 / 1.30 / 1.31 | 1.17 / 1.62 / 1.65 |
| eth4 | down | 1.30 / 1.31 / 1.52 | 1.47 / 1.48 / 1.50 | 1.27 / 1.29 / 1.51 | 1.38 / 1.50 / 1.60 |
| eth4 | bidir | 1.33 / 1.57 / 1.66 (10 reps) | 1.36 / 1.41 / 1.57 (10 reps) | 1.16 / 1.44 / 1.56 | 1.30 / 1.37 / 1.47 |

DUT busy share, range over the reps: ASK2 75–98 % in every cell (up 90–95 %,
down 85–94 %, bidir 80–90 %); the vendor 59–93 % (up 59–88 %, down 68–84 %,
bidir 73–93 %).

Reading the numbers:

- ASK2 wins all 8 up and down cells (+12 % to +28 %) and its reps are
  tight (a spread of about ±3 % apart from one low eth4 up v6 rep at 1.17).
  The vendor is erratic in the same cells (eth3 up v6 ranges 0.85–1.30, eth4
  up v4 0.91–1.41), which is the bimodal serial-transmit-worker placement
  seen before; ASK2 shows it much less.
- Bidir is where ASK2 loses. The first 3-rep run showed eth4 v4 at −13 %
  with non-overlapping ranges; 10 reps per board (below) give −6.7 % on the
  mean and −9.9 % on the median with overlapping ranges. Still a FAIL under
  the pass rule, but the gap is mostly the vendor's lucky CPU placements,
  not a uniform per-packet cost. The eth3 v4 bidir cell is a tie with wide
  vendor spread (1.20–1.64) and cannot be called either way with 3 reps.
- ASK2 is CPU-bound in up and down (one core near 100 %), as expected for
  software ChaCha20-Poly1305. In bidir it is not: see below.
- Eth3 and eth4 agree within the spread for ASK2. IPv6 is 3–8 % behind IPv4
  in up and down.

### eth4 bidir v4: 10 reps and CPU profile

10 reps per board, 4 streams, a fresh tunnel for each rep, alternating
ASK2 ×5, vendor ×5, ASK2 ×5, vendor ×5, 2026-10-14 16:22–16:41 (log
`oracle/wgbidir10-run.log`; the per-board CSVs only hold the last block of
5, so the log is the record). Gbit/s:

| Board | min / median / mean / max | Busiest core | Retransmits (median) | DUT busy (mean) |
|---|---|---|---|---|
| NXP ASK | 1.333 / 1.569 / 1.532 / 1.664 | cpu0–cpu3, varies by rep | 327,475 | 87.0 % |
| ASK2 | 1.361 / 1.413 / 1.430 / 1.573 | cpu0 in 10 of 10 | 1 | 82.3 % |

The vendor's throughput follows which CPU the serial WireGuard stage lands
on (the peer's `internal_id % ncpus`; a new `wg0` rotates it):

| Vendor serial stage on | Reps | Gbit/s (this run) | Earlier reps (battery and profile runs) |
|---|---|---|---|
| cpu0 | 2 | 1.488, 1.497 | 1.488, 1.480 |
| cpu1 | 3 | 1.385, 1.345, 1.333 | 1.153 |
| cpu2 | 2 | 1.640, 1.663 | 1.650 |
| cpu3 | 3 | 1.664, 1.656, 1.647 | 1.662, 1.653 |

On ASK2 the busiest core was cpu0 in every rep (cpu2 with `threaded=0`) and
it does not follow the NAPI thread (see the pin runs below), so the vendor's
placement effect has no ASK2 counterpart to compare like for like. Against
the vendor by serial-stage CPU: ASK2 1.430 against cpu0 1.49 is −4 %,
against cpu1 1.35 is +6 %, against cpu2/cpu3 1.65 is −13 %.

Profile findings (`PROF=1`, `bin/wg-prof-summary.py`; ASK2 6 reps, vendor 3
reps, files `oracle/wgprof-*`):

- ASK2 is not CPU-bound in bidir. The busiest core is 91–97 % but the box is
  only 79–93 % busy (mean 82 %), the busiest thread is `napi/wg0-0` at
  42–44 % of a core, and the load is uneven (cpu0 about 84 %, the other
  cores about 70 %). The vendor's best reps run at 92 % busy. Scaling ASK2
  to that would give about 1.64, so the unexplained ~18 % idle CPU was the
  lead; it is explained by the RPS/RFS default (see the RPS A/B below).
- CPU per Gbit/s is the same on both boards (about 2.2–2.3 cores per
  Gbit/s), so there is no per-packet cost difference; the gap is how much
  of the CPU each board manages to use.
- The vendor retransmits massively in bidir (100k–550k per rep) and ASK2
  almost never (0–12). The vendor throughput is therefore partly goodput
  traded against drops in its WireGuard queues; ASK2 backpressures cleanly.
- `threaded=1` on `wg0` (the ASK2 default; the vendor kernel has it off) is
  not the cause: `WG_THREADED=0` on ASK2 (4 reps, `oracle/wgprof-t0-*`) gave
  1.337, 1.424, 1.207, 1.470 (mean 1.36), with the serial stage pegging cpu2
  at 99 % and retransmits up to 12,469. Not an improvement.
- Placement of `napi/wg0-0` is not the lever on ASK2 (`WG_NAPI_CPU=N` pins
  the thread; 3 reps each, `oracle/wgnapi{0..3}-185-eth4.csv`): pinned to
  cpu0 1.468, cpu1 1.442, cpu2 1.440, cpu3 1.417 (mean of all 12: 1.442
  against 1.430 unpinned). The busiest core stayed cpu0 in every rep, and
  the per-CPU split did not move.
- 8 streams (`STREAMS=8`, 4 reps per board, `oracle/wgp8-*`, profiles in
  `json-wg-p8-*`): ASK2 1.309–1.365 (mean 1.344, busy 75–85 %), vendor
  1.206–1.634 (mean 1.466) with the same placement pattern (cpu3 1.634,
  cpu2 1.631, cpu0 1.395, cpu1 1.206). More streams do not use ASK2's idle
  CPU.
- ASK2's CPU roles are fixed across reps and pin positions (`/proc/stat`
  over the 20 s steady window, 8-stream and pin runs): cpu2 spends about 47 %
  in softirq and only about 20 % in system time (it is the CPU the DPAA
  receive softirq for the single encrypted UDP flow lands on); cpu0 about
  50 % system plus 31 % softirq with the least idle (12–20 %); cpu1 and cpu3
  about 55 % system and 10 % softirq with 24–36 % idle. The vendor has the
  same kind of fixed softirq CPU (cpu1, 70–79 % softirq), and its throughput
  is lowest when the serial stage lands on that same CPU. The ASK2 serial
  stage never collides with it, but cpu0 carries the extra load.
- UDP bidir is not usable as a cross-check: iperf3 3.22 returns zero with
  `--bidir -u` through the tunnel (`unable to read from stream socket`), on
  both boards.
- Interrupt capture (`dut_prof` saves `/proc/interrupts`; 2 reps,
  `oracle/wgirq-run.log`, `json-wg-irq-185`, 1.434 and 1.381): QMan portal
  IRQs are even across CPUs (about 4.3–5.4k/s each, cpu0 slightly higher),
  so the portal assignment is not the cause. Function-call IPIs are not
  even: cpu1, cpu2 and cpu3 each receive about 2.1–3.1k/s and cpu0 only about
  0.5k/s. cpu0 is the dispatcher that wakes work on the other cores.
- The dispatch comes from the packet-steering configuration, and the two
  boards differ:

  | Board | `rps_cpus` per rx queue | RFS (`rps_sock_flow_entries`) |
  |---|---|---|
  | ASK2 eth3, eth4 | `e` (cpus 1–3; cpu0 excluded) on all 48 queues | 32768 (on) |
  | ASK2 eth0–eth2 | 0 | 32768 |
  | Vendor eth3 | 2 (cpu1) on all 64 queues | 0 (off) |
  | Vendor eth4 | 1 (cpu0) on all 64 queues | 0 (off) |

  ASK2's value comes from VyOS `offload rps`
  (`vyos/ifconfig/ethernet.py` lines ~510–538: `rps_cpus &= ~1`, plus RFS).
  The vendor value comes from OpenWrt `/etc/init.d/packet_steering`.
- RPS A/B on ASK2 (`/tmp/wgrps.sh`, runtime sysfs writes, 5 reps each,
  eth4 bidir v4, 4 streams, fresh tunnel per rep, profiles in
  `json-wg-rps-<config>-185`, log `oracle/wgrps-run.log`; the EXIT trap
  restored `e e 32768` and this was verified on the board afterwards):

  | Config (eth3 / eth4 / flows) | Mean | Min–max | Busy | Busiest core |
  |---|---|---|---|---|
  | `off` (0 / 0 / 0) | 1.346 | 1.323–1.378 | 90.0 % | 97.9 % (cpu0 every rep) |
  | **`vendorlike` (2 / 1 / 0)** | **1.593** | 1.523–1.678 | 90.9 % | 93.2 % (cpu0, cpu1, cpu2) |
  | `all` (f / f / 32768) | 1.462 | 1.377–1.542 | 83.0 % | 91.7 % (cpu0 every rep) |
  | current product default (`e` / `e` / 32768), 10 reps | 1.430 | 1.361–1.573 | 82.3 % | cpu0 in 10 of 10 |
  | vendor, 10 reps | 1.532 | 1.333–1.664 | 87.0 % | n/a |

  The vendor-like steering gives +11 % over the product default and +4 % over
  the vendor's own mean, with all four CPUs doing work (busy rises from 82 %
  to 91 %). The cause of the eth4 bidir v4 FAIL is therefore the steering
  default, not the offload engine or WireGuard itself: excluding cpu0 and
  enabling RFS makes cpu0 the dispatcher and leaves about 18 % of the box
  idle. Removing RPS altogether is worse (1.346), so a steering mask is
  needed; the vendor's per-port single-CPU mask works best. A single run of 5
  reps per config is enough to rank the configs (the spread between
  `vendorlike` and the default is larger than the in-config spread) but not to
  fix the exact optimum; 10 reps and more mask choices are still open.

### RPS A/B at 10 reps (2026-10-15, image 2058, delay 0)

eth4 bidir, 4 streams, fresh tunnel per rep, `bin/wg-rps-ab.sh` on dell2, logs `~/testrig/out/wgrps10.log`,
`wgrps-realdefault.log`. Gbit/s, mean of 10 (min–max):

| Config | v4 | v6 |
|---|---|---|
| `vendorlike` (2 / 1 / 0), 4 blocks of 10 | 1.861, 1.848, 1.831\*, 1.857\* (mean 1.849) | 1.791, 1.776, 1.801\*, 1.793\* (mean 1.790) |
| product default (`e` / `e` / 32768), 10 reps | 1.541 (1.510–1.630) | 1.502 (1.470–1.556) |
| vendor .106, 10 reps | 1.516 (1.192–1.666) | 1.484 (1.254–1.628) |

\* These two blocks were meant to be the default but ran in the `vendorlike` state: the harness saved the original
RPS state through `ssh -n`, which feeds an empty stdin, so the save file was empty and the restore after the first
block did nothing. They are valid as two more `vendorlike` blocks. The default was re-run afterwards with the
script fixed (it now refuses to continue if the save file is empty) and the DUT was verified back at `e e 32768`.

Result: the RPS configuration IS the lever (an earlier conclusion in this session that it no longer mattered came
from the mislabelled blocks and is withdrawn). `vendorlike` is +20 % over the product default and +22 % over the vendor
on v4 and +21 % over the vendor on v6, with no overlap with the vendor's range. The product default is only +1.6 % (v4)
and +1.2 % (v6) over the vendor, inside the vendor's spread, so the default does not clearly pass. The eth4 bidir
FAIL is closed only if the shipping steering changes to the per-port single-CPU mask; that is a `vyos-1x` change
(`ethernet.py` ~510–538, patch 031) and still needs the owner's decision. The starvation fix and delay 0 did not
change the picture for the default (1.541 against 1.430 before, +8 %, and the vendor's own mean was 1.532 earlier).

Open items for this section: add the neighbouring masks (eth3=4 eth4=2, eth3=8 eth4=4, eth3=1 eth4=2) to see whether
the result depends on the exact CPU choice; extend the eth3 bidir v4/v6 cells to 10 reps to decide the ties; mirror the multi-tunnel run
(`testrig-wireguard-multi.sh`, eth4 only today) to eth3. Changing the
product default is a `vyos-1x` change (`rps_cpus &= ~1` and RFS in
`ethernet.py`) and needs a decision from the owner after the wider cells are
measured.

The steering A/B on routed, NAT and VLAN v4 was completed: changing RPS did
not explain their earlier deficits. Those runs contained unintended DHCP
clients, not demonstrated software-flow fallback; see the static-rig re-run.

## Scale and robustness

| Feature | NXP ASK | ASK2 |
|---|---|---|
| New connections per second (plus 20k held) | 20k/s (external tuned-ASK benchmark, other rig) | 1,024 sessions: 4,841/s at 5k offered (0 failed), 5,648/s at 10k (33 % failed), 73/s at 20k; before F-264 and `offload_delay_ms` |
| 8,000 concurrent sessions (bidir 64 B line rate) | 8k-session PDR in the external benchmark | FMan lookup engine wedged (10-10, before F-264) |
| Flow install rate | ≥ 2,370/s | ~510–550/s (before F-264) |
| Churn soak | not measured | bridge: 100 cycles × 64 learned MACs, 0 errors; routed: 1,123 cycles, 0 FMan stalls |

## Not counted

- **Wi-Fi, RTP relay / VoIP / socket offload** (the vendor's 3-tuple tables):
  permanently out of scope (`OFFLOAD-CAPABILITY-PLAN.md` §1.14).
- **NAT46 / NAT64:** neither stack can do it in hardware.
- **IPsec transport mode:** the vendor does not offload it on LS104x
  (`cmm/src/module_ipsec.c:467`).
- **IP reassembly:** the vendor's shipped `cdx_pcd.xml` comments the fragment
  distributions out, so both stacks pass fragments to software.
- **GRE over IPv6:** the vendor accepts the tunnel, but its record builder
  only handles 6in4 and 4in6 (`create_tunnel_insert_hm()` in `cdx_ehash.c`),
  so it is not offloaded.
- **MACVLAN, ICC, packet capture:** the vendor's `cmm` has them, but `cdx.ko`
  registers no handler, so the vendor does not offload them either.

## Short TCP flows linger in conntrack with `offload_delay_ms=0` (2026-10-15)

The start delay was a design choice that also protected conntrack: a short TCP flow that ends inside the first ~1 s
never reaches hardware, so the kernel sees its FIN and the entry ages out. Removing the delay offloads every flow from
its first packet, and the hardware swallows the FIN/RST.

Measured with 300 sequential TCP connections (20 KB each, dell1 to dell2, `shortconn.py`):

| Board / setting | Result 5 s after the run | Result 125 s after the run |
|---|---|---|
| .185 `offload_delay_ms=500` | 300 entries, none `[HW_OFFLOAD]` (flows stayed in the kernel) | not tracked |
| .185 `offload_delay_ms=0` | 300 entries, all `[HW_OFFLOAD]` | 300 entries, all `ESTABLISHED`, timeout ~7,300 s |
| .106 vendor | 300 entries, 286 `TIME_WAIT` | 295, all closing states; ages out in ~2 minutes |

So the vendor lets the close handshake reach conntrack and ASK2 at delay 0 does not. The same lingering already
happens in ASK2 for any long flow that ends while it is offloaded; the delay only hid it for short flows. At 20k
connections/s the table would fill (`nf_conntrack_max`) within minutes, so this is a FAIL for the connection-rate and
8k-session rows once they are re-measured.

Why a software reaper is not the fix (traced in 6.18.48 `flow_offload_fixup_ct`): when the flowtable tears an offloaded
TCP flow down as idle, it restores the conntrack entry with the timeout of the state conntrack last saw (ESTABLISHED,
since the FIN never arrived). The kernel cannot tell a closed flow from a quiet one. Shortening the timeout would let a
live idle connection's entry expire. A routed flow is re-created by `tcp_loose`, but a NAT44/NAT66 flow loses its
mapping and the connection breaks. A reaper is therefore only safe for non-NAT flows, which is not the case that
matters on a gateway. Linux hardware offload (mlx5 conntrack offload) handles this by sending SYN/FIN/RST to software;
the vendor does the same with the `cdx_sp.xml` TCP soft parser (`tcp.flags & 7` goes to the host queue).

The real fix is that punt. The vendor compiler already produced the TCP block: `specs/reference/ask2-sp/tcp_ctl_punt.xml`
compiles with `fmc --sp_only` on .106 to 32 bytes at base 0x20 (label 0x20 on `HEADER_TYPE_TCP`):
`00 00 B7 8A 00 DA 00 07 00 00 00 00 00 00 40 2E 00 00 09 4F 00 02 00 50 00 01 1B FF 00 01 1B FE`. The soft-parser hook
was silent on .185 in F-244 to F-246 (`specs/ask2-soft-parser-lcv-scheme-select.md` §6p-6q), but every one of those
loads wrote parser memory while the parser was running, which the RM forbids (spec §6r).

### F-266: TCP control punt, implemented, default off, not yet board-tested

`bin/kernel-fixups/F_266.py` loads that block by the RM rule and ask.ko arms it per port:

- Kernel: `fman_pcd_tcp_ctl_punt_arm(fm, hw_port_id)` / `_disarm()` and `fman_port_sp_attach()`. The first armed port
  writes the 8 code words at parser byte `0x40` with IRQs off, PEN cleared and PSTAT idle, then reads them back and
  restores PEN. Each port's TCP HXS (`pmda[10].ssa`) goes `0x80000000` -> `0x80000420` by read-modify-write with
  readback. The last disarm restores both. Any timeout or readback mismatch fails the arm and leaves the port as it
  was.
- ask.ko: `echo 1 > /sys/module/ask/parameters/tcp_ctl_punt` arms every port engaged with the ROUTED profile (bridge
  ports are left alone; bridged flows have no conntrack entry to linger). Engage arms, disengage and module unload
  disarm, writing 0 disarms everything. Default 0, so a build with F-266 behaves exactly as before until it is set.

Behaviour when armed: TCP frames with FIN, SYN or RST go to the port's default RX queue before KeyGen, so the kernel
and conntrack see the handshake and the close while data stays in hardware. Costs:

- Punted frames get no RSS; a SYN flood on one port lands on one CPU (vendor: same).
- No hardware L4 checksum result for punted frames; the kernel checks them in software.
- A FIN can overtake the last in-flight data frames by microseconds (vendor: same).
- Clearing PEN pauses parsing on all ports, eth0 included, for a few microseconds at the first arm and the last
  disarm.
- Covers TCP over IPv4 and IPv6. UDP has no close to see and keeps relying on timeouts.

Host checks done: fixup idempotent and anchor-gated (`tests/kernel_fixup_tcp_punt.py`, 12 tests), kernel and ask.ko
compile at W=1 with no new warnings, `bin/test-fixups.sh` 4/4.

**Candidate prepared 2026-10-15:** the locally built, signed
`vyos-2026.10.15-0439-rolling-LS1046A-arm64.iso` is published to lxc200.
It preserves 2058's validated WQ6 baseline and includes the reviewed F-266
restore-retry fix. `tcp_ctl_punt` remains default 0; the ASK module and
rebuilt VyOS CLI both default the offload delay to 0. The VyOS package was
rebuilt from 2058's upstream `ce2baa317` revision with the current patch
stack, rather than introducing a new upstream revision during this test.
Kernel Debian version is `6.18.55-1+b2026.10.15043954`; vermagic remains
`6.18.55-vyos`. This is an uncommitted-worktree candidate, not a CI build
or a release proven by Git HEAD alone.

ISO SHA-256:
`2bdd7b4ce2a1d31507df9552d140587176a4fcdbf0e0391f86f0efed770a5fd8`.
The ISO signature, embedded kernel, exact ASK payload and its signature
against the certificate embedded in `vmlinux`, both zero defaults, installer
DTB/U-Boot support, and identical ISO9660/FAT32 boot payloads were verified.
Both `latest.iso` and `latest.iso.minisig` point to 0439; the served sidecar
verifies against the candidate. Pinned URL:
`http://192.168.1.137:8080/iso/vyos-2026.10.15-0439-rolling-LS1046A-arm64.iso`.

At publication `.185` still runs 2058: the candidate has **not** been
installed or board-tested. The conntrack FAIL remains open, and the agent
does not run the image installer. The isolated copied-chroot build required
generating kdump's crash initramfs with `MODULES=most`: its `MODULES=dep`
root-device autodetection cannot resolve `/` in that chroot. The kernel
package is fully configured and the original kdump post-install hook is
restored and executable; no host, CI-script or DUT policy was changed.

Board test (operator installs the candidate, then cold-boot .185 because FMan state survives a warm reboot):

1. Engage eth3 and eth4, then `echo 1 > /sys/module/ask/parameters/tcp_ctl_punt`. dmesg shows the F-266 load with
   FMPR_RPIMAC PEN 1 -> 0 -> 1, `0x80000000 -> 0x80000420` per port, and the armed message. `rx_errors` stay 0.
2. `shortconn.py`, 300 x 20 KB connections. Pass: 125 s later the entries are in closing states or gone, none
   `ESTABLISHED` with a ~7,300 s timeout.
3. Bulk TCP still shows `[HW_OFFLOAD]` at ~9.4 Gbit/s; idle NAT44 and NAT66 flows preserve their mappings and resume;
   eth0 SSH is unaffected. Repeat close handling for IPv4 and IPv6, including an RST close.
4. Write 0 (and separately, disengage): `pcd-snapshot diff` clean, `pmda[10].ssa` back to `0x80000000`, code words
   restored.

If step 1 succeeds and step 2 still lingers, the routine is not executing: the PEN cycle is ruled out as the missing
soft-parser gate and the next suspects are the HXS attach and HPNIA handling on mainline. Delay stays 0 either way.

**Board result 2026-10-15 (0439 on .185, cold boot): do not publish.**

- Image assembly defect. `ask.ko` (04:45:23) was added after `modules.dep` was generated (04:42:42), so
  `modprobe ask` failed and nothing engaged. 2058 is not affected. `depmod -a` on the board (persists on eMMC)
  plus a cold power-cycle recovered it. A publishable ISO must come from a build that runs `depmod` after `ask.ko`
  is installed (the CI path), not from the hand-assembled copied-chroot build.
- Missing `ask.ko` took all networking down, eth0 included (`ping: Network is unreachable` on the live console).
  `board/scripts/vyos-offload-ask disengage` died for ports that were never engaged, failing the whole ethernet
  commit, and it ran a global `conntrack -F` on every commit touching eth0/eth1/eth2. Fixed in the working tree:
  disengage is a no-op when `/sys/module/ask` is absent; a port not listed in `fe_arm` gets only the idempotent
  port-scoped genl disengage (the kernel handler always returns 0) and no flush; if `fe_arm` is missing or
  unreadable, only the port-scoped disengage runs, never the global flush, and a failure fails the commit. The
  fail-closed "still armed" check is kept. Stub-tested in 7 cases and installed on .185 (sha256 `1b0982a2…dd75`).
- Step 1 PASS: the punt arms on eth3 and eth4 (`Armed ports: 0x10 0x11`), `rx_errors` 0.
- Step 2 FAIL: after 300 x 20 KB IPv4 connections, all 300 entries were still `ESTABLISHED` (~7316 s) at +125 s,
  the same as before F-266.
- Discriminating capture on the DUT (tcpdump on eth3/eth4, TCP-flag filter, 20 short connections, all completed):
  20 SYN and 20 SYN-ACK per direction reach the kernel, which is expected because the flows are not yet offloaded.
  No FIN or RST of any of the 20 flows reaches the kernel, and their conntrack entries stay `ESTABLISHED` (7305 s).
  So the soft-parser routine does not divert control segments of offloaded flows: either the code never runs on
  those frames (HXS slot 10 attach / `SW_PRS_EN` not taking effect) or its HPNIA enqueue is overridden by the
  FE_ENTER classification. Steps 3 and 4 were not run.

Missing before a new ISO is published:

1. Commit the helper fix (outage blocker, independent of F-266).
2. Rebuild through the CI path so `modules.dep` includes `ask.ko`; verify `modprobe ask` on first boot.
3. F-266 either works (step 2 pass, then steps 3 and 4) or ships dormant. It is default off, so a dormant F-266
   does not block the image as long as `tcp_ctl_punt=0` leaves `pcd-snapshot diff` clean against 2058.
4. The conntrack-linger FAIL stays open; next experiment: read back `pmda[10].ssa` and the parser code words on
   the armed ports, then check whether frames that HIT FE_ENTER ever pass the TCP HXS soft-parser stage.

**Rebuild 2026.10.15-0516 (deployed to .185).** It fixes items 1 and 2 in the image without a CI run.
`data/hooks/97-ask-modules.chroot` now runs `depmod -a` for every kernel that ships `extra/ask.ko` and fails the
build if `ask.ko` is not in `modules.dep`; hooks re-run on a persisted chroot, so this covers CI and local builds.
The ISO was reassembled on the 0439 workspace: the kernel, `ask-modules` and `vyos-1x` packages are reused
byte-identical (their sources are unchanged since 0439), and only the helper and hook 97 changed. Checks: the hook
logged `ask.ko indexed`, the F-217 kernel-skew guard passed, and the squashfs ships `extra/ask.ko` in `modules.dep`
plus the fixed helper (sha256 `1b0982a2…`). ISO sha256 `1a3fec21aca678c2dbd66a4197d096e72a0feae4d5d03dcd44513685abb250a5`;
the signature verifies on the HTTP-served bytes, and `latest.iso` and `latest.iso.minisig` both point to 0516.
The agent installed it over serial at the operator's explicit request (`add system image`, defaults accepted,
U-Boot env re-asserted). After a warm reboot, `.185` runs 0516: `ask` loaded at first boot without a manual
`depmod`, `Armed ports: 0x10 0x11`, `tcp_ctl_punt` 0, eth0 pings the gateway, `systemctl is-system-running`
reports `running`.

**Cold-boot validation 2026-10-15 (0516 on .185, Hubitat plug cycle).** Power OFF/ON (both HTTP 200, 3 s off);
serial shows full BL2/DDR init (true cold boot), login ~95 s. Post-boot: `ask` loaded at boot (hook-97 `depmod`
fix holds cold), `Armed ports: 0x10 0x11`, eth0/eth3/eth4 up, eth0 pings the gateway (0.27 ms),
`systemctl is-system-running` = `running`, no ASK errors in dmesg. Only boot error is `fsl-fman-port
1a86000.port … error -22` = OH port `fman0_oh_0x6` lacking a `qman-channel-id` (upstream/DTS-inherent, benign,
no interface affected; AGENTS.md S10).

**F-266 reassessment 2026-10-15 (0516 on .185): prior FAIL verdict is likely invalid; reclassify to
implemented + arms-clean + end-to-end test pending.** Every verifiable layer is correct: attach slot
`FMAN_PCD_TCP_PUNT_HXS=10` matches the standard FMan HXS ordering for TCP (ETH=0, IPv4=5, IPv6=6 per F-205; TCP=10);
our `tcp_ctl_punt.xml` tcpschema is byte-identical to the vendor working `cdx_sp.xml`; `HPNIA=0x500002` =
`NIA_ENG_BMI | AC_ENQ_FRAME` (direct enqueue to the port default FQ) per the programming reference §5.1, overriding
`FMBM_RFPNE` per-frame. Live on 0516: `echo 1 > …/tcp_ctl_punt` loads the 8 code words (PEN 1→0→1, 0 idle polls)
and attaches both ports (`ssa 0x80000000 → 0x80000420`, readback OK); disarm restores cleanly. The earlier Step 2
FAIL was on 0439, where the `depmod` bug kept `ask.ko` unloaded so the punt was never armed; and `.185` currently
forwards no transit traffic (eth3/eth4 ARP empty, `VYOS_FLOWTABLE_ASK1` counters 0), so no offloaded flow existed
to close. A valid test needs the shortconn transit harness (hosts on 10.99.1.x via eth3 and 10.99.2.x via eth4:
establish+offload a TCP flow, close it, confirm the FIN reaches conntrack); not available this session. Disposition:
ship F-266 **dormant** (default 0, provably zero PCD writes when off — `ask_hw_tcp_punt_apply()` early-returns on
every path when the param is off), documented as validation-pending. Still open: the transit-harness end-to-end
test.

**Committed + CI-built + deployed 2026-10-15.** The depmod/disengage outage fixes, the F-266 implementation
(dormant), the F-199 refinement, patch 0207 regeneration, test tooling and docs committed to `dpaa1` as
`6445c74a` (fixup gate 4/4, F-266 test 12/12). CI build `38688982292` on `dpaa1` is green and produced the signed
ISO `vyos-2026.10.15-0551-rolling` (minisig verifies against the repo pubkey). Note: the `publish` job runs only on
`main`/`nxp-sdk*`, so a `dpaa1` build never creates a GitHub release or updates the `version.json` feed — a public
release needs a reviewed `dpaa1 -> main` merge. The CI ISO was staged on lxc200 for operator install-testing:
`latest.iso` and `latest.iso.minisig` both repoint to 0551, served sidecar sha matches the versioned sidecar,
HTTP 200 at `http://192.168.1.137:8080/iso/latest.iso`. DEFERRED pending operator go-ahead: the `dpaa1 -> main`
merge + public release, because F-266 is still end-to-end-unvalidated (ships dormant) and a release writes the
fielded-board update feed.

## Suggested order

1. Build and board-test F-266's default-off TCP control punt, with the
   source's zero-delay defaults aligned between ask.ko and VyOS. The
   operator installs the candidate, then cold-boots `.185`; validate TCP
   closure, idle NAT mappings, bulk forwarding and reversible teardown
   before considering the conntrack FAIL closed. F-199 starvation is
   already closed on 2058 and remains a regression gate, not unfinished work.
2. Investigate the remaining NAT66 bidir FAIL: five alternating static-rig
   pairs give ASK2 17.662 / NXP 17.677 Gbit/s (−0.09 %). VLAN translate v4
   passed the same paired follow-up (+3.19 %). Preserve all runs and the
   strict pass rule; removing DHCP clients does not waive it.
3. Re-run the comparison suite (`bin/testrig-compare.sh`): connection rate,
   8k sessions, install rate.
4. WireGuard bidir: the cause is the RPS steering default. At 10 reps the
   per-port single-CPU mask gives eth4 v4/v6 1.849/1.790 against the vendor
   1.516/1.484 and the product default 1.541/1.502 (see "RPS A/B at 10
   reps"). Decide whether to ship that mask (`vyos-1x` `ethernet.py`), then
   extend eth3 to 10 reps.
5. Multicast. (PPPoE, PPPoE fragmentation and NAT across VLANs are
   measured on both boards.)
6. B7b, then PPPoE over VLAN and QinQ (not covered on either board; the
   vendor session script needs a VLAN sub-interface variant).
7. Tunnels.
8. IPsec ESP (needs the SEC path; `ASK2-IPSEC-OFFLOAD-PLAN.md`).
9. Egress QoS.
