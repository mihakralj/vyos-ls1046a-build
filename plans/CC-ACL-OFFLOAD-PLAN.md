# Static ACL / ntuple / tc-flower hardware backend: remaining work

**2026-10-15 · dpaa1 · Companion to `plans/OFFLOAD-CAPABILITY-PLAN.md` and `plans/ASK2-MASTER-PLAN.md` §4.6.** History (Phase 0, the CC match-walker investigation and Phase 1) is in `plans/archive/CC-ACL-OFFLOAD-PLAN-2026-10-15.md`.

## 0. Scope

Every other ASK2 capability is a flow accelerator that mirrors an already-tracked kernel object. This plan covers the different problem of static, stateless classification rules: `ethtool -N` steering, standalone `tc filter ... flower` drop/redirect, and nft netdev-hook `flags offload` chains (which lower through the same `flow_offload` path as tc-flower).

## 1. Settled facts

- **There is no RM-style masked CC match-table walker in microcode 210.10.1** (verdict 2026-09-05, `decomp/findings.md` entries 10–16). The only content-matching substrate is the enhanced external-hash machine: CRC-64 bucket, DDR record chain, full-key equality against `record+8`. Masked, prefix and range rules cannot be expressed and must fail closed to software. Do not resurrect the CC-tree builder (`cc_write_group0()`/`cc_pack_key()`), the legacy ASK 1.x CC-node patches (`0014`/`0019`/`0026`/`0056`/`0057`) or the excluded FE-VM patches (`0127`–`0138`).
- **Done: `ethtool -N` exact-match steering on ehash records** (patch `0194-dpaa-ethtool-ntuple-ehash-bridge.patch`). `fman_pcd_fe_flow_action` has `rx_fqid` (`hit_fqid = rx_fqid ?: tx_fqid ?: target_fqid`). The key is `PORT_ID=0x00|SIP|DIP|PROTO|SPORT|DPORT`, the bridge engages once with per-key `flow_del`, and validation is exact-only with rollback. It was silicon-verified 2026-09-06 on `.185` (image 0234): a FQID 514/512 differential on the forwarded path and two-port same-tuple isolation. Test only with forwarded traffic. To-self input traffic bypasses the ehash via the parser default-result gate (`pr_word & 0x0044`) and shows zero HITs.
- **Governing rule** (master §4.6.1): the kernel's `tc`/`nft`/`ethtool` rule state is the single source of truth. ACL rules are not a new ASK2-owned state model; the driver mirrors and removes them on the kernel's callbacks.

## 2. Open: Phase 2, static drop ACL (tc-flower / nft netdev-hook)

The drop action is a record whose FE chain ends in the **EXIT FE object** (the 4-byte discard terminal; FE pool sizes: EXT_HASH 28 / HM 16 / ENQ 16 / TRANSITION 8 / EXIT 4). The vendor's per-flow MUX→ENQ context pattern becomes a MUX→EXIT discard chain. The descriptor-level miss-side DROP (`miss_action_type 3` → `PRE_BMI_DISCARD_FRAME`) drops misses, not matches, so it is not the ACL primitive. The errata A006675/SW006 caution applies to CC-leaf ADs, not to this FE-record path. Re-verify against qdrant before implementing (AGENTS.md S0).

1. **Emitter.** `fman_pcd_fe_flow_add()` gains a drop terminal: an action flag selects EXIT-chain record emission instead of the ENQUEUE_PKT terminal. The record format (key@8, flags, OPC/PARAM, ctx pointer) does not change.
2. **Classifier split.** ACL and flow-acceleration flower rules both arrive at `dpaa_setup_tc` → `ask_flow_offload_setup_tc` (patch `0145`). Separate them explicitly: a static rule (no conntrack/flowtable origin) with an exact full 5-tuple match and a `drop` (or queue-redirect) action goes to the ACL path. Everything else stays on its existing path or returns `-EOPNOTSUPP`.
3. **Hook.** Exact-match flower entries become drop-terminal records through the same `fman_pcd_fe_flow_add()` path the ntuple bridge uses. Masks and prefixes fail closed.
4. **nft.** Verify experimentally that an nft `ingress`-hook `flags offload` rule needs no extra driver code once tc-flower works.
5. **Acceptance.** Install a drop-terminal record for a known forwarded tuple. The matched flow must vanish with zero delivery and an error-free discard; a mismatched flow must pass. Instruments: the stats-flagged counters and the FQID differential. Also check engage/disengage, with ASK flow offload engaged on the same port (shared table) and without it.

Sequencing: after the existing-feature acceptance and the feature-parity blockers in master §3. There is no vendor oracle for this capability (vendor CC usage is flow dispatch for `cmm`), so it is not a parity blocker.

## 3. Non-goals

- **Per-rule hardware hit counters.** `CC_ENTRY_STATS_EN` exists in silicon, but there is no software path (`AllocStatsObjs`/MURAM blocked). `tc -s filter` and `ethtool -n` report no hardware counts.
- **CIDR, prefix, range or partial masks.** Not expressible on full-key ehash records; they stay in software.
- **Software-path iptables/nftables** without `flags offload`; they are unaffected.

## 4. References

- `decomp/findings.md` 2026-09-05/06 entries 10–25: the walker verdict, the zero-HIT root causes (F-243 family byte, wrong FE table, PR default-result gate), and the forwarded-path verification.
- `kernel/common/patches/board/0194-dpaa-ethtool-ntuple-ehash-bridge.patch`, `0145` (flow-offload handler registration).
- `kernel/ask/oot-modules/ask/ask_flow_offload.c`: the single ehash record builder.
- `specs/fman-keygen-flow-key-spec.md`, `arch/fman-microcode-210-programming-reference.md`.
- `plans/archive/CC-TREE-REBUILD-PLAN.md`: the abandoned `KGSE_CCBS` graft, kept for its register-level diagnosis technique.
