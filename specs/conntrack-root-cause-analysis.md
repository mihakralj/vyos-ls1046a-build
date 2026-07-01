# Conntrack "Deaf" Pipeline — Consolidated Root-Cause Analysis

**Version 1.0 · HADS 1.0.0**
**Date:** 2026-07-01
**Branch:** `nxp-sdk`
**DUT used for live verification:** `vyos-eth1` / 192.168.1.185, kernel `6.12.49-vyos` (built 2026-06-28), cmm binary built 2026-06-29
**Prerequisite/related docs:** `specs/opnsense-ask-analysis.md` §2, `plans/NXP-ASK-REFERENCE-IMPLEMENTATION.md` §9-11, `AGENTS.md` (ASK 1.x conntrack bullets)

## AI READING INSTRUCTION

Read `[SPEC]` and `[BUG]` blocks first — they are the authoritative, board-verified facts.
Read `[NOTE]` for the investigative trail (why earlier sessions reached different, sometimes wrong, conclusions).
`[?]` blocks are the remaining unproven hypotheses — treat with lower confidence and validate before acting on them.
This document **supersedes** the conntrack conclusions in `specs/opnsense-ask-analysis.md` §2 and the qdrant memory entries tagged `conntrack-deaf` dated before 2026-07-01 — two of those entries contain a misdiagnosis that this document corrects (see §4).

---

## 1. Why this document exists

**[NOTE]** Conntrack on this board has been investigated across at least four separate sessions (2026-05-20 ASK2 flowtable work, 2026-06-27 kernel `enable_hooks` investigation, 2026-06-28 "breakthrough" bridge/conntrack session, 2026-06-28 OPNsense comparative analysis, 2026-07-01 three-layer synthesis). Each session found a real bug, fixed it, and declared partial victory, but none of them re-verified end-to-end on live hardware with a reproducible test after the fix — so the true current state had drifted from what qdrant memory claimed. This document is a from-scratch, live-hardware-verified re-audit performed on 2026-07-01, plus the exact commands to reproduce every finding.

**[SPEC]** The pipeline under investigation, in order:
1. Kernel creates/updates a `struct nf_conn` when a packet passes a conntrack hook (gated by the vendor `enable_hooks` flag).
2. VyOS's generated `nftables` `raw` table (`ip/ip6 vyos_conntrack`) must not `notrack` the packet, or step 1 never fires.
3. `nf_conntrack_netlink.ko` multicasts NEW/UPDATE/DESTROY events over `NETLINK_NETFILTER` (protocol 12) to any subscribed socket.
4. CMM (`/usr/local/bin/cmm`) must have a socket subscribed to those multicast groups.
5. CMM's registered callback (`cmmCtCatch` → `__cmmCtCatch`) must actually insert the connection into its own shadow table (`ct_table[]`) and hand it to FCI/CDX for hardware programming.

Steps 1-4 are proven healthy on the live board today. Step 5 is proven broken. This inverts the leading hypothesis in the most recent (2026-07-01, pre-this-document) qdrant synthesis, which still had step 4 marked as an open question and assumed step 5 would follow automatically once step 4 was fixed.

---

## 2. Layer 1 — kernel `enable_hooks` gate

**[SPEC]** NXP's `lf-6.12.49-2.2.0` kernel adds `static bool enable_hooks __read_mostly;` (default `false`) to `net/netfilter/nf_conntrack_standalone.c`, gating `nf_ct_netns_get(net, NFPROTO_INET)` inside `nf_conntrack_pernet_init()`. With the default, conntrack hooks never register and `nf_conntrack_in()` is never called.

**[SPEC] Live verification, 2026-07-01, DUT 192.168.1.185:**
```
CONFIG_NF_CONNTRACK=y
CONFIG_NF_CT_NETLINK=m
grep -w enable_hooks /proc/kallsyms → 0000000000000000 d enable_hooks   (symbol present, no sysfs node — expected for mode 0000)
```
Live traffic test: `conntrack -C` went from 21 → 22 immediately after a fresh `curl` to a new destination, with the exact new 4-tuple visible in `conntrack -L`. **Hooks are active and conntrack is creating real entries on this boot.** Layer 1 is currently not blocking anything.

**[NOTE]** The historical fix trail: a bootarg attempt (`nf_conntrack.enable_hooks=1`) was tried and empirically failed (symbol present in kallsyms, but hooks stayed unregistered) — see the `kernel/flavors/ask/patches/failed-conntrack-experiments/0401-nf-conntrack-enable-hooks-true.patch` file, which is filed under a folder literally named `failed-conntrack-experiments` and is **not** applied by any active build step. The fix that reportedly worked was a source-level default flip via `sed` in `.github/workflows/auto-build.yml` (~line 266-269):
```bash
sed -i 's/static bool enable_hooks __read_mostly;/static bool enable_hooks __read_mostly = true;/' \
  work/linux-6.12.49/net/netfilter/nf_conntrack_standalone.c
```

**[BUG] The `auto-build.yml` sed patches a source tree that is never compiled**
- Symptom: none currently observable (the running kernel's hooks ARE active), but this is a latent trap for the next time the kernel needs to be rebuilt from source.
- Cause: `auto-build.yml` sets job-level `ASK_KERNEL_TAG: 'ask-kernel-6.12.49'` unconditionally for this pipeline. `bin/ci-build-packages.sh` explicitly skips the `linux-kernel` package build whenever `ASK_KERNEL_TAG` is set (`echo "### ASK kernel in effect ($ASK_KERNEL_TAG) — skipping linux-kernel local build"`), and instead the "Download pre-built NXP kernel .debs" step fetches a **frozen, pre-built** `linux-image-6.12.49-vyos_*.deb` / `linux-headers-*.deb` pair directly from a GitHub Release asset. The `sed` runs afterward against `work/linux-6.12.49/net/netfilter/nf_conntrack_standalone.c`, a checked-out source tree that is **never passed to a compiler** in this code path. The only reason the fix works today is that the frozen `ask-kernel-6.12.49` release asset must already have been built, at some point, from source that already contained `enable_hooks = true` (most likely via an out-of-band manual run of `bin/local-build.sh`/`bin/dev-build.sh`, not via this CI job).
- Fix: either (a) stop relying on the frozen release and build the kernel in CI from `work/linux-6.12.49` with the `sed` applied before compiling, or (b) leave the frozen-release model but **remove the now-misleading `sed` step** and instead document, next to the `ASK_KERNEL_TAG` release process itself, that any future rebuild of the `ask-kernel-6.12.49` release MUST re-apply this exact one-line default flip before `bindeb-pkg`. Whichever is chosen, add a boot-time assertion (e.g. in `caam-check`-style diagnostic script, see §7) that fails loudly if a freshly-booted kernel ever regresses to `enable_hooks=false`, since nothing in the current pipeline would catch that regression before it reaches the board.

---

## 3. Layer 2 — VyOS `notrack` fallthrough

**[SPEC]** VyOS's firewall/NAT subsystem generates an `ip vyos_conntrack` / `ip6 vyos_conntrack` table with `PREROUTING`/`OUTPUT` base chains. Per VyOS's own documentation (docs.vyos.io → Firewall → packet flow), a `Conntrack Ignore` stage (`set system conntrack ignore ...`) exists as an explicit, user-controlled opt-out. Separately (and this is the part that is not spelled out in VyOS's docs but is empirically confirmed on this board), **when no NAT and no stateful firewall rule is configured at all, VyOS's generator falls through to an unconditional `notrack` verdict** in these chains, since there is nothing that requires tracking and tracking-everything has a CPU cost VyOS avoids by default.

**[SPEC] Confirmed empty in `board/vyos-config/config.boot.default`:** no `nat` block, no `firewall` block — only `interfaces`, `service ssh`, `system`. Any board booting the shipped default config is `notrack`ed unless something removes it at runtime.

**[SPEC] Live verification, 2026-07-01, DUT 192.168.1.185** — current `ip vyos_conntrack` table (already fixed this boot, see below):
```
table ip vyos_conntrack {
	chain VYOS_CT_IGNORE { return }
	chain PREROUTING { type filter hook prerouting priority raw; policy accept;
		jump VYOS_CT_IGNORE; jump FW_CONNTRACK; jump NAT_CONNTRACK; jump WLB_CONNTRACK }
	chain OUTPUT { type filter hook output priority raw; policy accept;
		jump VYOS_CT_IGNORE; jump FW_CONNTRACK; jump NAT_CONNTRACK; jump WLB_CONNTRACK }
	chain VYOS_CT_HELPER { return }
	chain FW_CONNTRACK { return }
	chain NAT_CONNTRACK { return }
	chain WLB_CONNTRACK { return }
}
```
No bare `notrack` rule is present — confirmed fixed for this boot. `journalctl -u ask-ct-setup.service` for this boot:
```
Jul 01 04:00:16 vyos systemd[1]: Starting Enable conntrack tracking for ASK hardware offload...
Jul 01 04:00:16 vyos vyos-ask-ct-fix[2380]: ASK: loading kernel modules...
Jul 01 04:00:31 vyos vyos-ask-ct-fix[2380]: ASK: removed notrack from ip vyos_conntrack PREROUTING
Jul 01 04:00:31 vyos vyos-ask-ct-fix[2380]: ASK: removed notrack from ip vyos_conntrack OUTPUT
Jul 01 04:00:31 vyos vyos-ask-ct-fix[2380]: ASK: removed notrack from ip6 vyos_conntrack PREROUTING
Jul 01 04:00:31 vyos vyos-ask-ct-fix[2380]: ASK: removed notrack from ip6 vyos_conntrack OUTPUT
Jul 01 04:00:31 vyos vyos-ask-ct-fix[2380]: ASK: added masquerade NAT on eth0
Jul 01 04:00:31 vyos vyos-ask-ct-fix[2380]: ASK: conntrack initialized
```
`board/scripts/vyos-ask-ct-fix` (deployed via `board/systemd/ask-ct-setup.service`, `Before=ls1046a-ask.service`) ran correctly this boot. **Layer 2 is currently not blocking anything on this board.**

**[BUG] The Layer-2 fix is a one-shot boot-time patch, not commit-safe**
- Symptom: none today (board hasn't had a config commit since boot), but the design is fragile.
- Cause: `ask-ct-setup.service` is `Type=oneshot`, runs once after `vyos-router.service` at boot, and never re-runs. VyOS's `firewall`/`nat` conf-mode scripts regenerate the entire `vyos_conntrack` table from their Jinja2 templates whenever a commit touches the `firewall`, `nat`, `nat66`, or `system conntrack` config trees (VyOS's dependency-graph-based conf-mode re-run only fires for the trees that changed, so unrelated commits — e.g. `set service ssh port` — do **not** retrigger this). Any commit that *does* touch those trees regenerates the table with the default `notrack` fallthrough restored, silently undoing the fix with no automatic re-trigger.
- Fix implemented in this pass (§6): a new `ask-ct-resync.timer`/`.service` pair periodically (every 30s) re-runs the idempotent notrack-check-and-strip half of `vyos-ask-ct-fix`, so any regression self-heals within 30 seconds of the next commit instead of silently persisting until the next reboot or manual intervention. This does **not** replace the boot-time `ask-ct-setup.service` (which still owns the one-time module-load + wait-for-table + CMM-start-ordering job) — it only adds a cheap continuous safety net for the nftables half.
- **[?]** A more architecturally correct fix — making VyOS's own generator never emit `notrack` in the first place, by shipping a minimal NAT/firewall rule in `config.boot.default` that requires tracking — was considered but **not implemented**, because it changes default forwarding/NAT behavior for every board (a policy decision, not a bug fix) and eth0 is the management port, not obviously the right default egress for a masquerade rule. Flagging for a maintainer/product decision rather than deciding unilaterally.

---

## 4. Layer 3a — CMM's netlink socket subscription (previously misdiagnosed, now corrected)

**[NOTE]** The 2026-06-28 "OPNsense Comparative Analysis" (`specs/opnsense-ask-analysis.md` §2) and the 2026-07-01 three-layer qdrant synthesis both concluded: *"CMM has a NETLINK_NETFILTER (proto=12) socket but groups=0x0 — DEAF, nfct_open with zero subscriptions"* and left this as an open question ("why does CMM open the socket with groups=0?"). **This conclusion was a misdiagnosis caused by a filtering artifact, not a real bug.**

**[SPEC]** `cmmCtInit()` (`kernel/flavors/ask/userspace/cmm/src/conntrack.c`) opens **four separate** `NETLINK_NETFILTER` sockets for the same process, in this order:
1. `globalConf.nf_conntrack_handle = nfct_open(CONNTRACK, 0)` — opened in `cmm.c` `main()`, **before** `cmmCtInit()` runs.
2. `ctx->catch_handle = nfct_open(CONNTRACK, NFCT_ALL_CT_GROUPS)` — the event-catching socket, groups should be `0x7` (NEW|UPDATE|DESTROY).
3. `ctx->handle = nfct_open(CONNTRACK, 0)` — used for synchronous query/update (`NFCT_Q_UPDATE`, `NFCT_Q_DESTROY`).
4. `ctx->get_handle = nfct_open(CONNTRACK, 0)` — used for synchronous `NFCT_Q_GET` (existence checks in resync).

**[SPEC]** Linux's netlink autobind can only give **one** socket per process the literal-PID address (`nl_pid = getpid()`) on a given protocol; every additional socket the same process opens on the same protocol gets an arbitrary large synthetic `nl_pid` from the kernel, because `(protocol, nl_pid)` pairs must be unique. Naively filtering `/proc/net/netlink` (or `ss`) by the process's real PID therefore only ever finds **one** of the four sockets — and because socket #1 above is opened first (in `main()`, before `cmmCtInit()`), it is the one that keeps the literal PID, and it is one of the `groups=0` handles, **not** the event-catching one.

**[SPEC] Live verification, 2026-07-01, DUT 192.168.1.185**, full (unfiltered-by-pid) proto-12 table:
```
sk               Eth Pid        Groups   Inode
00000000d88dfd05 12  3583069215 00000000 56736   ← ctx->handle or get_handle (synthetic pid)
000000000c965487 12  3528808226 00000007 56735   ← ctx->catch_handle — CORRECTLY subscribed (0x7 = NFCT_ALL_CT_GROUPS)
000000004cffa8c7 12  7251       00000000 56730   ← globalConf.nf_conntrack_handle (kept the literal pid, groups=0 by design)
000000003270e744 12  2523316167 00000000 56737   ← the remaining query/get handle
000000002f452e3e 12  0          00000000 2101    ← unrelated kernel-internal socket
```
`nfct_callback_register(ctx->catch_handle, NFCT_T_ALL, cmmCtCatch, ctx)` is called (verified present via `grep -a` against the deployed binary — see §5 for why `strings` gave a false negative here), and the `cmmCtThread` pthread (one of CMM's 4 live threads on this board) correctly polls `nfct_fd(ctx->catch_handle)` in its `select()` loop and calls `nfct_catch(ctx->catch_handle)` when readable.

**Conclusion: CMM's netlink subscription is correctly configured. This is not the bug.** Any future session must disambiguate these four sockets by cross-referencing `/proc/<pid>/fd` inode numbers against `/proc/net/netlink`'s `Inode` column (not by filtering on `Pid`) before drawing conclusions from socket group state.

---

## 5. Layer 3b — CMM never populates its own shadow conntrack table (THE REMAINING BUG)

**[SPEC] Live, reproducible test, 2026-07-01, DUT 192.168.1.185:**
```bash
# Kernel-side ground truth: use conntrack-tools (system libnetfilter_conntrack.so.3.8.0), NOT cmm
sudo timeout 6 conntrack -E &
curl -s -o /dev/null http://192.168.1.137:8080/          # generate one fresh, distinguishable TCP flow
```
Result — `conntrack -E` shows the **complete, correct** TCP state machine for the new flow, in real time:
```
    [NEW] tcp 6 120 SYN_SENT   src=192.168.1.185 dst=192.168.1.137 sport=52660 dport=8080 [UNREPLIED] ...
 [UPDATE] tcp 6 60  SYN_RECV   src=192.168.1.185 dst=192.168.1.137 sport=52660 dport=8080 ...
 [UPDATE] tcp 6 432000 ESTABLISHED ... [ASSURED]
 [UPDATE] tcp 6 60  CLOSE_WAIT ... [ASSURED]
 [UPDATE] tcp 6 30  LAST_ACK ... [ASSURED]
 [UPDATE] tcp 6 120 TIME_WAIT ... [ASSURED]
```
This proves the kernel ctnetlink event stream is fully healthy and standards-compliant — including the custom vendor attributes injected by `kernel/flavors/ask/patches/721-netfilter-add-ask-conntrack-metadata-abi.patch` (`CTA_LAYERSCAPE_FP_ORIG`/`CTA_LAYERSCAPE_FP_REPLY`), since a stock, unmodified `conntrack-tools` binary parses the stream without error.

Now query CMM's **own** internal shadow table directly via its native CLI (bypassing all log-level ambiguity):
```bash
sudo /usr/local/bin/cmm -c "query connections"      # BEFORE
# → ERROR: FPP IPV4 CONNTRACK table empty
curl -s -o /dev/null "http://192.168.1.137:8080/?probe=conntrack-test"
sudo /usr/local/bin/cmm -c "query connections"      # AFTER, immediately following an ESTABLISHED+ASSURED TCP flow
# → ERROR: FPP IPV4 CONNTRACK table empty
```
**CMM's internal `ct_table[]` never gains an entry, even for traffic that unambiguously reached `TCP_CONNTRACK_ESTABLISHED` with `IPS_ASSURED` set** — exactly the condition `__cmmCtCatch()`'s TCP branch (`conntrack.c` ~line 3270) checks before calling `__cmmCtRegister()`. Since `cmmCtShow()`/`query connections` walks the in-memory `ct_table[]` linked list unconditionally (entries can even show as `rejected`/`fallback` runtime states, per `cmmCtRuntimeStateName()`), a **completely empty** table means `__cmmCtRegister()` is never even being reached for this traffic — the break is upstream of it, inside `__cmmCtCatch()` or the `nfct_catch()` dispatch that invokes it.

**[NOTE] Ruled out during this investigation:**
- Not a log-level artifact: `globalConf.debug_level` defaults to `DEBUG_ERROR` (`cmm.c:349`) and the `cmm_print` macro only force-shows `DEBUG_CRIT`/`DEBUG_STDOUT`/`DEBUG_ERROR`; the routine per-connection lines inside `__cmmCtCatch` are logged at `DEBUG_INFO`, which is filtered by default. This explains why the journal shows **nothing** for routine traffic even when the pipeline is healthy — silence in the journal is not evidence of failure on its own — but it does not explain an empty `ct_table[]`, which is queried directly and bypasses logging entirely.
- Not `globalConf.enable == 0` (the "Forward Engine programmation is forbidden" master switch checked first in `__cmmCtCatch`): its only initializer in the source is `globalConf.enable = 1;` (`cmm.c:348`), and nothing in the boot sequence calls the CLI `activate 0` command that would clear it. Not proven at runtime with 100% certainty (the CLI's non-interactive `-c "activate"` query syntax did not resolve during this session — see §8 open items) but considered unlikely.
- Not the standard netlink attribute parsing: proven fine by the successful `conntrack -E` run above, using the system's current `libnetfilter_conntrack.so.3.8.0`.

**[?] Leading hypothesis: CMM statically links a 2016-era, individually-patched copy of `libnetfilter_conntrack`, independent of and older than the system's dynamic copy.**
`bin/ci-build-ask-userspace.sh` builds `cmm` against:
```bash
LIBNFCT_VER="1.1.0"   # https://www.netfilter.org/projects/libnetfilter_conntrack/files/libnetfilter_conntrack-1.1.0.tar.xz  (released 2016)
PATCH="$ASK_DIR/patches/libnetfilter-conntrack/1.1.0/01-nxp-ask-comcerto-fp-extensions.patch"
# ./configure --enable-static --disable-shared ...  →  static .a, installed to $SYSROOT
```
`ldd /usr/local/bin/cmm` confirms **`libnetfilter_conntrack.so` is absent** from CMM's dynamic dependencies (only `libcli`, `libpcap`, `libmnl.so.0`, `libc`, etc. are dynamic), while the system separately has `/usr/lib/aarch64-linux-gnu/libnetfilter_conntrack.so.3.8.0` installed (the one `conntrack-tools` uses). `grep -a` against the deployed binary confirms all of CMM's `nfct_*`/`cmmCt*` call sites are compiled in (`__cmmCtCatch`, `cmmCtCatch`, `____cmmCtRegister`, `cmmCtThread`, `cmmCtInit`, etc. all present — the earlier `strings /usr/local/bin/cmm | wc -l` returning `3` was a **BusyBox `strings` limitation**, not evidence of a stripped/foreign binary; `grep -a` is required on this board's minimal userland).

The `01-nxp-ask-comcerto-fp-extensions.patch` (fetched into `$ASK_DIR`, an externally-cloned `we-are-mono/ASK` repo — **not vendored into this git repository**, so its exact contents could not be inspected in this session) is presumably the userspace counterpart that teaches this old 1.1.0 base how to encode/decode the same `CTA_LAYERSCAPE_FP_*`/`comcerto_fp` attributes the kernel patch (721) adds. The most likely failure mode: this patched-but-very-old parsing/dispatch path silently fails to invoke (or silently mis-parses ahead of) the registered callback when these vendor-specific nested attributes are present in a message, even though a modern, unrelated, unpatched `libnetfilter_conntrack.so.3.8.0` (used by `conntrack -E`) handles the exact same wire messages correctly. **This has not been proven with certainty** (would require either fetching/reading `01-nxp-ask-comcerto-fp-extensions.patch` from the external `we-are-mono/ASK` repo, or adding trace instrumentation and rebuilding — see §6 for the trace instrumentation implemented in this pass) but it is the best-supported explanation given everything else in the pipeline (kernel event stream, CMM's socket, CMM's callback registration, CMM's thread/select loop) has been individually proven correct.

**[NOTE]** A secondary, less likely confound: the live test in this session generated **locally-originated** traffic (a `curl` run directly on the router via SSH, hitting `192.168.1.137:8080`), not genuine **transit** traffic forwarded through two of the router's other interfaces. CMM's fastpath is architecturally about accelerating *forwarded* flows; it is possible (not confirmed) that `__cmmCtCatch`/`__cmmCtRegister` intentionally treats router-local OUTPUT-path connections differently. The board's only bridge (`br0`) had no members and no address at test time, and `query l2flows` was also empty, consistent with no forwarding topology being active. **The test in this document should be re-run with genuine inter-interface transit traffic (e.g. the documented `eth3`↔`eth4` M2 harness topology, `lxc201`→`eth3`→`eth4`→`lxc200`) before concluding definitively that CMM's ingestion is broken for the traffic it is actually meant to accelerate.** This is the single highest-value next validation step (see §8).

---

## 6. Contributing/confounding factors (both real, board-verified, independent of the layers above)

**[BUG] CMM crash-loops for ~2-6 minutes after every boot, delaying when the pipeline becomes live**
- Symptom: `journalctl -b | grep ' cmm\['` shows repeated `error while loading shared libraries: libcli.so.1.10: cannot open shared object file` from `04:00:31` through `04:02:23` (every ~10s, matching `ls1046a-ask.service`'s `RestartSec=10`), then three more `cmmBridgeInit: Bridge is started in manual mode` starts/restarts at `04:02:25`, `04:03:14`, `04:04:25` before finally staying up at `04:05:52`. The whole conntrack→CMM pipeline is unavailable for roughly the first 5-6 minutes of boot.
- Cause: `stat -c '%Y %n' /etc/ld.so.cache /usr/local/lib/libcli.so.1.10.8` shows the library's mtime as `1782878544` and `ld.so.cache`'s regeneration as `1782878545` — **one second later**, both timestamped to the current boot (`Jul 1 04:02`), not ISO-build time. `libcli.so.1.10.8` is packaged into the base squashfs at ISO-build time (confirmed in `bin/ci-build-ask-userspace.sh`), so it should be present at `t=0`; the ~2-minute delay before it is visible on the live filesystem matches this board's well-documented pattern (see `AGENTS.md`, the `ls1046a-config-perms.service` and vyatta-cfg-env races) where `vyos-router.service` reports "active" almost instantly while VyOS's actual persistence/overlay mount activity for various paths completes much later. `ask-ct-setup.service`/`ls1046a-ask.service` only depend on `vyos-router.service`, not on the actual filesystem readiness of `/usr/local/lib`, so `cmm` is started far too early on every boot and must be restarted repeatedly by systemd until the race resolves.
- Fix implemented in this pass (§6.1 below, per-file diff): `ls1046a-ask.service` gained a bounded `ExecStartPre` poll (up to 180s) for `/usr/local/lib/libcli.so.1.10.8`, mirroring the exact pattern this repo already uses for the analogous `ls1046a-config-perms.service` race (`AGENTS.md`: "poll `mountpoint -q /opt/vyatta/config`"). This does not fix the underlying mount-timing mechanism (not fully understood in this session) but removes the symptom: `cmm` will no longer be launched (and fail) before its shared library is actually present, cutting the pipeline's dead time from ~5-6 minutes to whenever the library genuinely becomes available.

**[BUG] Default CMM log level hides all routine conntrack activity — a false-negative trap for future debugging**
- Symptom: journal shows only `DEBUG_CRIT`-level lines (e.g. `cmmBridgeInit: Bridge is started in manual mode`) and nothing else, even while conntrack is actively creating/updating entries; this reads exactly like "CMM is receiving nothing" whether or not that is true.
- Cause: `globalConf.debug_level = DEBUG_ERROR;` is the compiled-in default (`cmm.c:349`); the `cmm_print()` macro (`cmm.h:110`) only unconditionally shows `DEBUG_CRIT | DEBUG_STDOUT`, gating everything else (including `DEBUG_INFO`, the level used by the per-event lines inside `__cmmCtCatch`) behind `globalConf.debug_level`/`globalConf.log_level`.
- Fix: do not rely on journal silence/activity as a proxy for "is conntrack ingestion working" — use `cmm -c "query connections"` (§5) instead, which reads CMM's actual in-memory state regardless of log level. The diagnostic trace print added in this pass (§6.2) is deliberately emitted at `DEBUG_CRIT` so it is visible under the default configuration without needing to change `debug_level`.

---

## 7. Diagnostics playbook — how to validate each layer, in order

Run these **in order**; each step assumes the previous one passed. All commands are read-only except where noted.

1. **Kernel hooks active?**
   ```bash
   cat /proc/net/stat/nf_conntrack   # baseline
   curl -s -o /dev/null http://<some-fresh-host>/
   conntrack -C                      # should have grown by >=1
   ```
   If it did not grow: check `zcat /proc/config.gz | grep CONFIG_NF_CONNTRACK` and `grep -w enable_hooks /proc/kallsyms`; if the symbol is absent entirely the running kernel predates the fix — Layer 1 (§2) is broken, re-flash with a kernel built from source that has the sed applied.

2. **VyOS not re-notracking?**
   ```bash
   sudo nft list table ip vyos_conntrack | grep -c '^\s*notrack$'   # must be 0
   systemctl is-active ask-ct-setup.service ask-ct-resync.timer     # both must be active
   ```
   If `notrack` count is nonzero: Layer 2 (§3) regressed — most likely a commit touched `firewall`/`nat` since boot; wait ≤30s for `ask-ct-resync.timer` (§6) to self-heal, or run `sudo systemctl start ask-ct-setup.service` manually.

3. **CMM's netlink socket correctly subscribed?**
   ```bash
   CMM_PID=$(pgrep -x cmm)
   for fd in /proc/$CMM_PID/fd/*; do readlink -f "$fd"; done | grep -oE '[0-9]+$' > /tmp/cmm_inodes
   awk 'NR==1 || $2==12' /proc/net/netlink | while read -r sk eth pid groups rest; do
     grep -q "^${rest##* }$" /tmp/cmm_inodes 2>/dev/null   # cross-reference by inode, NOT by pid
   done
   ```
   Simpler: just confirm **at least one** proto-12 row anywhere in `/proc/net/netlink` shows `Groups=00000007` — do not filter by CMM's PID (see §4 for why that filter is misleading).

4. **CMM actually ingesting events? (the real test)**
   ```bash
   sudo /usr/local/bin/cmm -c "query connections"     # note current state
   # generate TRANSIT traffic if at all possible (through two OTHER interfaces of
   # this router, not a curl run locally on the router) — see §5 NOTE on why
   # locally-originated traffic is a weaker test
   sudo /usr/local/bin/cmm -c "query connections"     # must show new entries
   ```
   Empty before and after, across genuine transit traffic, confirms the Layer 3b bug (§5) is still present. If the repo's diagnostic trace patch (§6.2) has been built and deployed, also check:
   ```bash
   journalctl -u ls1046a-ask.service | grep 'CT-TRACE'
   ```
   Presence of `CT-TRACE` lines proves `__cmmCtCatch()` is being invoked (pinpoints the bug further downstream, inside registration); absence proves the break is in CMM's netlink message dispatch itself (pointing at the vendored `libnetfilter_conntrack` 1.1.0 chain, §5).

5. **Boot-race sanity check** — do not run steps 1-4 within ~6 minutes of a fresh boot; confirm first with:
   ```bash
   systemctl is-active ls1046a-ask.service && pgrep -x cmm
   journalctl -u ls1046a-ask.service | grep -c 'cannot open shared object file'   # should be 0 once the ExecStartPre fix (§6) ships
   ```

---

## 8. Fix inventory (this pass) and open items

**[SPEC] Implemented in this pass (2026-07-01, this document's companion commit):**
| Fix | File | Effect |
|---|---|---|
| Bounded poll for `libcli.so.1.10.8` before starting `cmm` | `board/systemd/ls1046a-ask.service` | Removes the ~2-6 min boot crash-loop (§6) |
| Periodic notrack re-sync (reuses the existing idempotent `vyos-ask-ct-fix`, no new script) | `board/systemd/ask-ct-resync.timer`, `board/systemd/ask-ct-resync.service` | Layer 2 self-heals within 30s of a regressing commit (§3) |
| Unconditional `CT-TRACE` diagnostic print at top of `__cmmCtCatch` | `kernel/flavors/ask/userspace/cmm/src/conntrack.c` | Makes step 4 of §7 conclusive on the next build without needing to change log levels |

**[?] Open items requiring a maintainer decision or further hardware access (not done in this pass):**
1. Fetch and review `we-are-mono/ASK`'s `patches/libnetfilter-conntrack/1.1.0/01-nxp-ask-comcerto-fp-extensions.patch` directly to confirm/refute the §5 hypothesis with certainty, rather than inferring it from build-script metadata.
2. Re-run the §5 test with genuine transit traffic (two non-management interfaces) rather than router-local traffic, to rule out the traffic-classification confound noted in §5.
3. Decide whether to keep the frozen `ask-kernel-6.12.49` release model (and document the manual re-apply requirement) or make CI build the kernel from source so the `enable_hooks` sed is not silently inert (§2 BUG).
4. Decide whether a default NAT/firewall rule belongs in `config.boot.default` to make Layer 2 correct-by-construction instead of patched-at-runtime (§3 `[?]`) — a product/policy call, not a bug fix.
5. This document's fixes have **not** been built into a new ISO or deployed to hardware in this pass (would require dispatching `self-hosted-build.yml` and rebooting the shared DUT — left for the operator to trigger deliberately).
