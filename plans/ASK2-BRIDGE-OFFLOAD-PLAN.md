# ASK2 L2 bridge HW offload — switchdev FDB → CC DA-match → bridge HMTD, CC-miss → FE_ENTER

**2026-08-27 · dpaa1 · T-M6-2 · Implementation plan (design + staged build, no code yet).**

> **STATUS — PLANNING.** No code written. This plan turns the high-level
> `plans/OFFLOAD-CAPABILITY-PLAN.md` §1.5 sketch into a concrete, silicon-gated
> build. It reuses the CC-tree + HMTD + CC-miss→FE_ENTER substrate the VLAN
> re-architecture (`plans/ASK2-VLAN-REARCH.md`, T-M6-8) proved on silicon (R4c
> coexistence: routed 5.68 G via CC-miss→FE + a CC-key edit path simultaneous, no
> wedge). The bridge lane is the **leanest new consumer** of that substrate: a CC
> leaf that matches on **destination MAC** and enqueues to the egress port's TX
> FQ — no L3 rewrite, no TTL decrement, and (for untagged bridging) no HMTD at
> all. Ships **default-off** behind an `ask_bridge_offload` module param;
> `ASK_CAP_BRIDGE` (already defined, `ask.h:204`) is advertised only when armed.
> Kernel is authoritative: the Linux bridge owns learning, ageing, STP, VLAN
> filtering, and flooding; ASK2 is a fail-closed hardware cache of **known-unicast
> forwarding entries only**.

## 1. Goal and non-goals

**Goal.** Offload the steady-state fast path of a Linux software bridge: a frame
whose **destination MAC is a known, non-local FDB entry reachable on another
bridge port in the forwarding state** is forwarded in FMan silicon (DA match →
enqueue to the egress port TX FQ) with the CPU bypassed, exactly as routed
unicast is today. Everything else stays in the kernel bridge.

**Non-goals / permanently software (fail-closed, `-EOPNOTSUPP`):**
- **BUM traffic** — broadcast, unknown-unicast, multicast/flooded frames. No
  hardware replication in this task (multicast/MDB is T-M6-MC, a separate heavier
  primitive). Flooded frames go to the kernel.
- **Local termination** — frames to the bridge's own MAC (`is_local` FDB entries);
  the kernel must see them.
- **Control planes** — STP/RSTP BPDUs, LLDP, LACP, 802.1X: never offloaded; the
  bridge owns port state and these frames must reach it.
- **Learning / ageing / move** — done by the kernel bridge. ASK2 only mirrors the
  resulting FDB into hardware and removes entries the kernel deletes/ages.
- **Non-forwarding port states** — a port that is STP-blocked/listening/learning
  offloads nothing; only `BR_STATE_FORWARDING` ports carry HW entries.
- **VLAN-aware bridging with tag edits** — deferred (§9). The first cut is
  untagged bridging (and 802.1Q-transparent forwarding where no tag edit is
  needed). Tag push/pop on a bridged frame reuses the VLAN HMTD (T-M6-8) and is a
  later increment.

## 2. Why this is the leanest new capability (established facts)

Three silicon-proven mechanisms already exist in-tree; the bridge lane is
composition, not new silicon research:

1. **KeyGen can extract L2 fields.** `KG_SCH_KN_MACDST` (bit 30, 6 B),
   `KG_SCH_KN_MACSRC` (bit 29, 6 B), `KG_SCH_KN_ETYPE` (bit 26, 2 B),
   `KG_SCH_KN_TCI1/2` (bits 28/27) are all hard-parser known-field bits
   (`arch/fman-microcode-210-programming-reference.md:416-420`,
   `specs/fman-keygen-flow-key-spec.md:292-296`). The **vendor** proves L2 is a
   first-class offloaded class on this exact silicon: live `.106` scheme 11
   `0xe4000000` = `PORT_ID|MACDST|MACSRC|ETYPE` (`kgse_mode 0x80000006`, AC_CC)
   carried **1,225,734 packets — the busiest scheme on the board**
   (`arch/fman-vendor-source-extraction-2026-08-07.md:145-146`). The vendor FMC
   L2 classifier `cdx_ethernet_cc` is `keysize=15` = `PORT_ID(1)+DA(6)+SA(6)+
   ETYPE(2)` (`specs/reference/nxp-ask-fmc/cdx_pcd.xml:53-57,207-219`) and is the
   **last** distribution in each port's `dist_order` — the catch-all L2 lane
   walked after the IP tuple lanes.

2. **CC-tree leaf → enqueue and CC-miss → FE_ENTER coexist on one live port.**
   The `miss_fe_off` production API (`0121h`) copies a non-zero FE_ENTER AD offset
   into the CC tree's trailing miss row **before publish** (F-182 rule: post-attach
   AD writes fault the controller). VLAN R4c measured **routed 5.68 G via
   CC-miss→FE + a CC-key edit path simultaneous, no wedge** on silicon. A bridge
   DA-match key slots into the same topology: DA HIT → bridge forward, DA miss →
   FE_ENTER → the untouched routed/NAT ehash path.

3. **CC leaf enqueue needs no HMTD for a plain L2 forward.** For untagged
   bridging the egress frame is the ingress frame, unchanged, sent to the egress
   port's TX FQ. `cc_write_leaf_ad` (0108/0115/0116) encodes `word0 = target_fqid`
   + a plain BMI enqueue AD (no NADEN, no HM). The VLAN R4c proof used NADEN→HMTD;
   the bridge base case is *strictly simpler* — it drops the HMTD. Only VLAN-aware
   bridging with tag edits reuses the HMTD (§9).

**Consequence: a bridge FDB entry is a CC leaf whose key is the destination MAC
and whose action is a plain enqueue to the egress port TX FQ.** No FE-VM opcode,
no ehash record, no header manipulation, no per-frame DDR. This is the closest
capability to the proven routed template with a different key and a simpler
action, exactly as `OFFLOAD-CAPABILITY-PLAN.md` §1.5 anticipated.

## 3. Topology decision (the load-bearing choice)

```
                                            ┌───────────────────────── bridge member ports (br0: eth3, eth4, ...)
ingress → Parser → KeyGen → per-port CC tree (RCCB grafted)
                              │
                              │  DA-match key HIT (known unicast FDB, egress port FORWARDING)
                              │     word0 = egress-port TX FQ (no-confirm)
                              │     word2 = BMI ENQ            (no NADEN for untagged bridging)
                              ▼
                        enqueue → per-egress no-confirm TX FQ  (CPU bypassed)

                              │  CC MISS (BUM, unknown DA, control, or non-bridged flow)
                              ▼
                        FE_ENTER → routed/NAT ehash (unchanged)  OR  KG-default/PCD FQ → kernel
```

**Chosen model: one per-port CC tree, DA-match leaves for bridge FDB, CC-miss →
FE_ENTER.** This is the *same* unified dispatch the VLAN work landed. Rationale,
decisively backed by the multi-protocol/IPv6 findings:

- **A second match-all (`kgse_mv=0`) KG scheme is impossible** — scheme selection
  is a first-match SI-walk; a `mv=0` scheme matches every frame so the first
  enabled one wins and the second is dead (`specs/ask2-ipv6-dual-lane-key-design.md`
  findings; `specs/ask2-shared-table-multi-protocol-design.md` §17.2). So the L2
  lane cannot be a parallel match-all scheme alongside the routed match-all
  scheme.
- **A distinct non-zero-`kgse_mv` L2 scheme needs an LCV/NetEnv split**, which has
  repeatedly **wedged multi-port silicon** (the two-live-v6-port wedge, shared
  hard-parser PCAC stop hypothesis, `ask2-shared-table-multi-protocol-design.md`
  §16.3). Rejected for the first build.
- **CCOBASE selects a table per *scheme*, not per key field, and the FE-VM has no
  key/parse branch** — so a single match-all scheme cannot drive two differently
  keyed ehash nodes (`ask2-ipv6-dual-lane-key-design.md` findings). That closes
  the "separate L2 ehash node" door and leaves the **CC-tree class** as the clean
  path. The CC comparator already matches variable keys per leaf; adding DA-keyed
  leaves to the existing per-port CC tree needs no new scheme and no LCV split.

**Coexistence contract (unchanged from VLAN R4c, now also carrying DA leaves):**
one CC tree per bridge-member port, containing (a) DA-match leaves for that port's
bridge FDB, and — on ports that also route — (b) the routed/VLAN leaves; the CC
**miss** row is the FE_ENTER AD so any non-bridged, non-VLAN flow still hits the
proven ehash routed/NAT path, and true BUM/unknown-DA misses fall through
FE_ENTER's own miss to the KG-default/PCD FQ → kernel bridge (which floods).

**Key-vs-scheme note.** Whether the bridge DA lives in the *CC comparator key*
(preferred: pure DA match, keysize = 6 or the vendor's 15-byte PORT_ID|DA|SA|
ETYPE) is a CC-key packing choice (§5), independent of the scheme graft. The
existing routed CC scheme's extraction must additionally emit the DA bytes the CC
comparator will match; confirm against the vendor `cdx_ethernet_dist` extraction
(DA+SA+type) and the CC comparator-window open question (§8).

## 4. Kernel authority — switchdev FDB (not `ndo_fdb_add`)

The Linux bridge is the single source of truth. ASK2 subscribes to the switchdev
notifier chains and mirrors **only** offloadable FDB entries into hardware.
`ndo_fdb_add`/`ndo_fdb_del` are the "bridge bypass" path and are **explicitly
discouraged** for switchdev offload (`Documentation/networking/switchdev.rst`);
we do not implement them (optionally `ndo_fdb_dump` later to visualise the HW
table, not required).

**Registration (module-global, once):**
- `register_switchdev_notifier()` (atomic chain) — carries
  `SWITCHDEV_FDB_ADD_TO_DEVICE` / `SWITCHDEV_FDB_DEL_TO_DEVICE`. Runs under RCU;
  **must defer** to a workqueue (deep-copy `addr`, `dev_hold()`, `queue_work()`)
  because CC install sleeps.
- `register_switchdev_blocking_notifier()` (blocking chain) — carries
  `SWITCHDEV_PORT_ATTR_SET` (STP state, bridge port flags, ageing) and
  `SWITCHDEV_PORT_OBJ_ADD/DEL` (VLAN/MDB — reject/ignore for now).
- `register_netdevice_notifier()` — track `NETDEV_CHANGEUPPER` bridge join/leave;
  call `switchdev_bridge_port_offload()` / `_unoffload()` on join/leave.

**Event struct** `struct switchdev_notifier_fdb_info { addr; vid; added_by_user:1;
is_local:1; locked:1; offloaded:1; }`. Admission filter in the work item:
- **skip `is_local`** (terminate on bridge — kernel keeps it),
- **skip `locked`** (802.1X MAB — bridge does not offload these),
- program **both** `added_by_user` (static) and dynamically-learned unicast
  entries whose egress port is a DPAA member in `BR_STATE_FORWARDING`;
- after a successful CC install, fire `SWITCHDEV_FDB_OFFLOADED` (set
  `.offloaded = true`, `call_switchdev_notifiers()`) so `bridge fdb` shows
  `offload` and the bridge tracks HW ownership.

**Port attributes:**
- `SWITCHDEV_ATTR_ID_PORT_STP_STATE` — a port leaving `FORWARDING` must
  immediately drop all its HW DA entries (rebuild the CC tree without them); a
  port entering `FORWARDING` may re-mirror the kernel FDB.
- `SWITCHDEV_ATTR_ID_BRIDGE_AGEING_TIME` — informational; ageing is the kernel's
  job. ASK2 removes an entry only when the kernel sends `FDB_DEL_TO_DEVICE`. To
  keep the kernel's ageing correct for *hardware-forwarded* flows (whose source
  MAC the CPU never sees), the driver must refresh FDB "used" via the same
  mechanism switchdev drivers use (`SWITCHDEV_FDB_ADD_TO_BRIDGE` learning-sync or
  periodic `fdb` update from HW hit counters) — see §8 open item.
- `SWITCHDEV_ATTR_ID_PORT_BRIDGE_FLAGS` — honour `BR_LEARNING`/`BR_FLOOD` etc.:
  if learning is off or a flag combination we cannot honour is set, fail closed
  (no HW entries for that port).

Reference drivers to mirror (API-identical, HW backend differs): DPAA2 switch
`dpaa2-switch.c` (closest Freescale shape), `am65-cpsw-switchdev.c` (compact),
`adin1110.c` (smallest two-chain example).

## 5. Board / driver API extensions required (all software)

1. **CC match-key: add DA (and optionally SA/ETYPE) fields.** The current CC key
   packer `cc_pack_key()` (`0098-fman-pcd-cc-static-install.patch`) and
   `struct fman_pcd_cc_hw_key` have presence bits for EtherType/proto/IPv4/IPv6
   src+dst/L4 ports only — **no MAC DA/SA slot**. Add `FMAN_PCD_CC_HW_F_MAC_DST`
   (and `_MAC_SRC`, `_TCI` for VLAN-aware later) presence bits + byte slots,
   packing the vendor 15-byte `PORT_ID|DA|SA|ETYPE` layout (or a DA-only subset).
   Count-gated fixup, static-asserts + KUnit vectors, mirroring the 14-byte
   routed match-key fixup (`0167`). The routed CC scheme's KG extraction must also
   emit those bytes — confirm the extraction order (MSB-first) places DA/SA/type
   deterministically, and add a self-test vector.

2. **Bridge-forward action = plain CC leaf enqueue (no HMTD for untagged).**
   Reuse `cc_write_leaf_ad`: `word0 = egress-port no-confirm TX FQ`, plain BMI
   enqueue, **no NADEN**. This is *simpler* than the VLAN leaf (which sets
   NADEN→HMTD). Egress TX FQ resolution reuses the existing per-egress no-confirm
   FQ allocator (`ask_hw_resolve_oif_fqid`, F-199 `0x2ba/0x2bb`).

3. **`(hw_port_id, ASK_TABLE_L2)` in the per-port table/shadow registry.** The
   `ASK_TABLE_L2` class and per-port table instance are already designed
   (`ask2-shared-table-multi-protocol-design.md` §7.4, `ASK_TABLE_L2` enum) but
   not built. `ask.ko` keeps a per-port software shadow of the DA key set and
   rebuilds `fman_pcd_cc_hw_spec` via `fman_pcd_cc_static_install` on each FDB
   add/del (whole-tree atomic rebuild — no per-flow dynamic CC add exists;
   caps=0x17, no Host-Command doorbell). `FMAN_PCD_CC_HW_MAX_KEYS` bounds
   concurrent HW FDB entries per port; **fail closed to SW (kernel bridge) past
   the cap** — a full HW table is not an error, it just means the overflow
   entries forward in software.

4. **`miss_fe_off` wired for the bridge tree** — set the miss row to the engaged
   port's FE_ENTER root AD (identical to VLAN R4c) so non-bridge flows still hit
   ehash. No new primitive (`0121h`).

5. **`ask_bridge.c` — replace the stub.** Today it is a lifecycle-only stub
   (`ask_bridge_init/exit`). Fill in: switchdev notifier registration, the FDB
   workqueue + admission filter, per-port DA-shadow + CC rebuild calls, STP-state
   handling, and teardown. Add `ask_bridge_offload` module param (default-off),
   `ASK_CAP_BRIDGE` advertise gate in `ask_genl.c`, and `ask-check` /
   `show flows` / `support-bundle` bridge observability.

## 6. Staged implementation increments (each build + silicon-gated)

Mirrors the NAT/VLAN safe progression: dormant host plumbing first, silicon
de-risk on the `cc_test` harness before any production wiring, then a matrix.

- **B0 — S0 gate + host plumbing (dormant, zero datapath change).**
  Add the CC DA match-key fields (§5.1), the `ASK_TABLE_L2` per-port shadow, the
  `ask_bridge_offload` module param (default-off), and the switchdev notifier
  skeleton that **logs** offloadable FDB events but installs nothing. Gate: builds
  clean; static-asserts + KUnit key vectors pass; routed/NAT/VLAN byte-identical
  (regression oracle untouched); `ask-check` shows bridge dormant.

- **B1 — CC DA-match builder + KUnit.** `fman_pcd_cc_bridge_key_add/remove` (host
  shadow → `fman_pcd_cc_hw_spec` with DA leaves + `miss_fe_off`). Dormant readback
  of a built spec (no live install). Gate: KUnit vectors for DA-only and
  PORT_ID|DA|SA|ETYPE keys; spec byte-exact vs a golden vector.

- **B2 — silicon de-risk (the decisive proof), `cc_test` harness, sacrificial
  port, cold boot.** Hand-arm a CC leaf matching a fixed destination MAC →
  enqueue to the other port's TX FQ, with the miss row = FE_ENTER, and engage ASK
  ehash routing on the same port. Prove on silicon **simultaneously**: (a) an L2
  frame to that DA is forwarded out the egress port with CPU bypassed
  (`pkt_count` climbs, kernel vif RX flat, ErrFD 0, sustains — not a 21-frame
  freeze), AND (b) an untagged routed flow on the same port still hits the ehash
  path (CC miss → FE). This is the single new silicon question (§8.2/§8.3);
  everything downstream is gated on it. Read-only comparator-window check first
  (`hash_probe`/`fe_scaffold` oracle) before arming.

- **B3 — `ask.ko` production switchdev wiring (gated on B2 PASS).** Replace the
  `ask_bridge.c` stub: FDB workqueue installs/removes DA leaves via the B1
  builder + `fman_pcd_cc_static_install`; STP-state and port-flag handling;
  `SWITCHDEV_FDB_OFFLOADED` ack; bridge join/leave via
  `switchdev_bridge_port_offload`. BUM/unknown-DA/local/control all miss → kernel.
  Gate: two-port bridge (eth3↔eth4 in `br0`), a known-unicast flow forwards in HW,
  broadcast/unknown-unicast/BPDU stay in SW, no routing regression.

- **B4 — lifecycle + teardown (binding ordering from VLAN R4c §7c).** FDB del /
  flush → rebuild tree without the entry; STP leave-FORWARDING → drop entries;
  port down / bridge leave / `ask_bridge_offload=N` / module unload / reboot →
  `kg_port_detach_cc` **restoring `next_engine=2` (RSS), not 0**, quiesce (~5 ms
  drain), RCCB→RSS **before** freeing CC MURAM, never churn VyOS config mid-
  teardown; `pcd-snapshot` byte-clean after. Gate: forward+inverse + concurrency
  (CONFIG_DEBUG_LIST/lockdep) + resource (`muram_budget` returns to baseline).

- **B5 — matrix + productization.** Learn/move/delete/age, port down, STP blocked,
  multi-port bridge, FDB churn under load, table-full fallback, ageing
  correctness for HW-forwarded flows (§8 open item), performance vs SW bridge
  (must not regress the ~10 G routed path when both coexist), safety (no BUM/
  control bypass). Only then advertise `ASK_CAP_BRIDGE`, add the `show offload`
  bridge label, and make the default-on/off decision.

### De-risk experiments before B3 (cheap, sacrificial port)
1. Confirm the current `fman_pcd_kg_port_detach_cc()` restores `next_engine=2`
   (0106/0147) — same pre-check the VLAN R4c-1 required; if not, that fix lands
   first.
2. The B2 coexistence proof (DA-match CC + ehash routed, CC-miss→FE, both
   sustained) is the gating go/no-go for the whole production path.

## 7. Per-feature acceptance contract (master-plan §4.6.5 — all must pass)

1. **Semantic:** capture proves a known-unicast bridged frame egresses the correct
   port in HW; broadcast/unknown-unicast/multicast/BPDU/local demonstrably stay in
   software (kernel bridge floods/terminates).
2. **Kernel-authority:** an FDB entry is `offload`-marked only after successful CC
   install; the kernel bridge remains authoritative for learning/ageing/STP/VLAN;
   control frames stay visible to the bridge.
3. **Forward + inverse:** FDB add/del/flush, MAC move (port change), STP state
   change, bridge port add/remove, ageing expiry, interface down/up, config
   removal, module unload, reboot.
4. **Concurrency:** async FDB add/del under lockdep/CONFIG_DEBUG_LIST + CC rebuild
   racing disengage; no poison/double-free/stale-generation/deadlock.
5. **Resource:** force `FMAN_PCD_CC_HW_MAX_KEYS`/MURAM exhaustion → clean fallback
   to SW bridge; `muram_budget` + `pcd-snapshot` return to baseline; no partial
   publication.
6. **Performance:** HW bridge vs SW bridge on the reproducible harness (throughput,
   per-core CPU, error deltas, MTU); must not regress the ~10 G routed path when a
   port both bridges and routes.
7. **Safety:** BUM, unknown-DA, STP-blocked, local, and control frames cannot be
   silently HW-forwarded; no bypass of STP/VLAN/bridge semantics.
8. **Observability:** `ask-check`/`show flows`/`support-bundle` identify the bridge
   feature, owner (switchdev cookie), state, fallback reason, and errors without
   debugfs control writes.
9. **Capability:** only then set `ASK_CAP_BRIDGE` and mark T-M6-2 DONE.

## 8. Unresolved silicon questions (resolve read-only before arming)

1. **CC comparator window for a DA-bearing key.** The project has never directly
   observed what the CC CONT_LOOKUP comparator reads for *any* key (open even for
   the IP 5-tuple, `specs/cc-comparator-compare-window-hypothesis.md`). Whether a
   DA-only (6 B) or PORT_ID|DA|SA|ETYPE (15 B) window matches the KG-emitted
   composite is unverified. Resolve via the `hash_probe`/`fe_scaffold` oracle
   before B2 arming. The vendor's live 15-byte `cdx_ethernet_cc` is strong prior
   evidence the layout works.
2. **DA-match CC + routed ehash coexistence on one live port via CC-miss→FE.**
   Proven for VLAN CC keys (R4c); a **DA-keyed** CC leaf coexisting is a new (small)
   variant — B2 is exactly this proof. Expected to pass since `miss_fe_off` is
   key-agnostic.
3. **Non-IP frame through the CC/enqueue path.** The VLAN/routed proofs were IP
   frames. A pure L2 bridge forward of a non-IP known-unicast frame (e.g. a
   protocol the parser doesn't deep-parse) through a CC DA-match leaf + plain
   enqueue is unexercised. Base case (untagged, no HMTD, no PAHM) should be the
   safest possible path, but must be captured. Control/BUM stays SW regardless.
4. **Ageing of HW-forwarded flows.** The CPU never sees the source MAC of a
   hardware-bridged flow, so the kernel bridge could age out an entry that is
   actively forwarding in HW. Mirror the in-tree switchdev pattern: either
   periodically refresh the kernel FDB `used` timestamp from HW hit counters, or
   emit `SWITCHDEV_FDB_ADD_TO_BRIDGE` learning-sync. Decide in B5; until then a
   conservative option is to only offload **static** (`added_by_user`) FDB entries
   (no ageing concern) and treat dynamic entries as SW — a smaller but safe first
   ship.
5. **Static-CC whole-tree rebuild under FDB churn.** Bridge FDBs churn more than
   routed flows. Whole-tree atomic reinstall under live traffic must not fault the
   walk (F-182/R4b class). Bounded rebuild rate + the proven pre-live miss-row
   write; measure churn in B5.

## 9. VLAN-aware bridging (later increment, reuses T-M6-8 HMTD)

Untagged bridging (§1) needs no HMTD. VLAN-aware bridging — a bridged frame that
must have a tag pushed/popped between ingress and egress bridge ports — reuses the
**exact** VLAN CC-leaf → NADEN → combined-HMTD path the VLAN re-architecture
shipped (`ASK2-VLAN-REARCH.md`), except the HMTD does only the tag edit + enqueue
(no L3 rewrite / no TTL decrement, since it's bridged not routed). This is a clean
follow-on once §1 base bridging and T-M6-8 tagged-forward both land; sequence it
after B5 and after multicast (T-M6-MC) if bridge+VLAN filtering is required. Do
not build it into the base case.

## 10. Provenance
- Capability scope + lean-model recommendation: `plans/OFFLOAD-CAPABILITY-PLAN.md`
  §1.5, §2, §3; master task **T-M6-2** (`plans/ASK2-MASTER-PLAN.md` §4.6.5 Phase
  M6-E, gates §4.6.5).
- CC-tree + HMTD + CC-miss→FE substrate (silicon-proven): `plans/ASK2-VLAN-REARCH.md`
  (R3b/R4b/R4c), board patches `0098`/`0108`/`0115`/`0116`/`0121h`.
- L2 EKFC fields + vendor L2 evidence:
  `arch/fman-microcode-210-programming-reference.md:416-420`,
  `specs/fman-keygen-flow-key-spec.md:292-296`,
  `arch/fman-vendor-source-extraction-2026-08-07.md:145-146`,
  `specs/reference/nxp-ask-fmc/cdx_pcd.xml:53-57,207-219`.
- Scheme/dispatch constraints (no 2nd match-all, CCOBASE scheme-only, no FE
  branch): `specs/ask2-ipv6-dual-lane-key-design.md`,
  `specs/ask2-shared-table-multi-protocol-design.md` §7.4/§16.3/§17.2.
- switchdev FDB kernel authority (not `ndo_fdb_add`):
  `Documentation/networking/switchdev.rst`, `include/net/switchdev.h`,
  `net/bridge/br_switchdev.c`; reference drivers `dpaa2-switch.c`,
  `am65-cpsw-switchdev.c`, `adin1110.c`.
- Stub to replace: `kernel/ask/oot-modules/ask/ask_bridge.c`; capability bit
  `ASK_CAP_BRIDGE` `kernel/ask/oot-modules/ask/include/uapi/linux/ask/ask.h:204`.
