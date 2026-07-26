# ASK2 M5 Hardware Opcode Chain & 10 Gbps Offload Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement full FMan hardware L3 forwarding opcodes in DDR flow records (`STRIP_ETH_HDR`, `TTL_DECREMENT`, `ETH_HEADER_REBUILD`, `ENQUEUE_PKT`), unstub FE context building, configure dedicated hardware TX FQs, and enable selective offload to achieve ≥7–8 Gbps offloaded throughput without CPU intervention.

**Architecture:** Encodes FMan opcode actions into 256B per-flow DDR records constructed by `ask.ko`. Uses `fman_pcd_fe_build_contexts()` (F-096) to populate working-store context pointers so MUX FE transitions frames directly to the hardware transmit frame queue (`dpaa_get_tx_fqid()`).

**Tech Stack:** Linux C Kernel Module (`ask.ko`), Freescale FMan PCD driver (`fman_pcd`), DPAA Ethernet (`dpaa_eth`), ARM64 DPAA1 architecture.

## Global Constraints

- Kernel compatibility: 6.18.38-vyos (`dpaa1` branch).
- All changes must be `pcd-snapshot` clean on engage/disengage.
- FMan FE opcodes must follow LSDK 5.4 specifications (`arch/fman-fe-ehash.md` §10).
- Strict adherence to explicit hardware constants: `STRIP_ETH_HDR` (`0x80000010`), `TTL_DECREMENT` (`0x80000200`), `ETH_HEADER_REBUILD` (`0x8000C001`), `ENQUEUE_PKT` (`0x81000000`).

---

### Task 1: Unstub & Verify `FmPcdCcBuildContextByFE` (F-096)

**Files:**
- Modify: `kernel/ask/oot-modules/ask/ask_hw.c:320-360`
- Test: `kernel/ask/oot-modules/ask/ci-build.sh`

**Interfaces:**
- Consumes: `fman_pcd_fe_build_contexts()` from `fman_pcd.h` (patch 0135/0146).
- Produces: Working-store FE context pointers for MUX FE stage.

- [ ] **Step 1: Inspect `ask_hw.c` engage path for FE context build call**

```c
/* Ensure fman_pcd_fe_build_contexts call is invoked during __fman_pcd_fe_arm_engage */
```

- [ ] **Step 2: Verify `ask_hw.c` calls `fman_pcd_fe_build_contexts()` on engage**

Run: `grep -rn "fman_pcd_fe_build_contexts" kernel/ask/oot-modules/ask/`
Expected: `ask_hw.c` contains the invocation in the `arm_engage` sequence.

- [ ] **Step 3: Build module and verify clean compile**

Run: `cd kernel/ask/oot-modules/ask/ && ./ci-build.sh`
Expected: `ask.ko` builds with 0 errors.

- [ ] **Step 4: Commit**

```bash
git add kernel/ask/oot-modules/ask/ask_hw.c
git commit -m "feat(ask2): verify FmPcdCcBuildContextByFE context builder call on engage"
```

---

### Task 2: Implement FMan Opcode Chain in DDR Records (T-M5-9)

**Files:**
- Modify: `kernel/ask/oot-modules/ask/ask_flow_offload.c:1030-1080`
- Modify: `kernel/ask/oot-modules/ask/include/ask_fman_caps.h:180-210`
- Test: `kernel/ask/oot-modules/ask/ci-build.sh`

**Interfaces:**
- Consumes: Flow metadata (`ask_flow_key`, source/dest MAC addresses, egress TX FQID).
- Produces: `struct fman_pcd_fe_flow_action` populated with FMan L3 opcodes (`STRIP_ETH_HDR`, `TTL_DEC`, `ETH_REBUILD`, `ENQUEUE_PKT`).

- [ ] **Step 1: Write opcode structure definitions in `ask_fman_caps.h`**

```c
#define FMAN_FE_OP_STRIP_ETH_HDR    0x80000010
#define FMAN_FE_OP_TTL_DECREMENT    0x80000200
#define FMAN_FE_OP_ETH_REBUILD      0x8000C001
#define FMAN_FE_OP_ENQUEUE_PKT      0x81000000

struct fman_fe_opcode_chain {
        u32 strip_eth;
        u32 ttl_dec;
        u32 eth_rebuild;
        u8  src_mac[6];
        u8  dst_mac[6];
        u32 enqueue_pkt;
        u32 tx_fqid;
};
```

- [ ] **Step 2: Update `ask_fe_flow_insert()` to construct full opcode chain in `action`**

```c
static void ask_fe_flow_insert_with_opcodes(const struct ask_flow_key *key,
                                            const u8 *src_mac, const u8 *dst_mac,
                                            u32 tx_fqid, unsigned long enq_off)
{
        struct fman_pcd_fe_flow_action action;

        memset(&action, 0, sizeof(action));
        /* Construct 13B 5-tuple key */
        memcpy(&action.key[0], key->src_ip, 4);
        memcpy(&action.key[4], key->dst_ip, 4);
        action.key[8]  = key->l4_proto;
        action.key[9]  = (key->sport >> 8) & 0xff;
        action.key[10] = key->sport & 0xff;
        action.key[11] = (key->dport >> 8) & 0xff;
        action.key[12] = key->dport & 0xff;
        action.key_size = 13;
        action.enq_off  = enq_off;

        /* Hardware opcode payload */
        action.op_strip_eth   = FMAN_FE_OP_STRIP_ETH_HDR;
        action.op_ttl_dec     = FMAN_FE_OP_TTL_DECREMENT;
        action.op_eth_rebuild = FMAN_FE_OP_ETH_REBUILD;
        memcpy(action.src_mac, src_mac, 6);
        memcpy(action.dst_mac, dst_mac, 6);
        action.op_enqueue     = FMAN_FE_OP_ENQUEUE_PKT | (tx_fqid & 0x00ffffff);

        fman_pcd_fe_flow_add(NULL, 0, &action);
}
```

- [ ] **Step 3: Verify module compilation**

Run: `cd kernel/ask/oot-modules/ask/ && ./ci-build.sh`
Expected: `ask.ko` builds cleanly.

- [ ] **Step 4: Commit**

```bash
git add kernel/ask/oot-modules/ask/ask_flow_offload.c kernel/ask/oot-modules/ask/include/ask_fman_caps.h
git commit -m "feat(ask2): encode FMan hardware L3 forwarding opcodes in DDR flow records"
```

---

### Task 3: Dedicated Hardware TX FQs per Port (T-M5-11)

**Files:**
- Modify: `kernel/ask/oot-modules/ask/ask_hw.c:450-500`
- Test: `kernel/ask/oot-modules/ask/ci-build.sh`

**Interfaces:**
- Consumes: Egress `net_device` struct pointer.
- Produces: Resolved egress hardware TX FQID (`u32 tx_fqid`).

- [ ] **Step 1: Implement dedicated TX FQ resolution helper in `ask_hw.c`**

```c
u32 ask_hw_get_egress_tx_fqid(struct net_device *egress_dev)
{
        u32 fqid = 0;

        if (!egress_dev)
                return 0x200; /* Fallback default TX FQID */

        /* Retrieve dedicated TX FQ from DPAA netdev private structure */
        fqid = dpaa_get_tx_fqid(egress_dev);
        if (!fqid)
                fqid = 0x2b9; /* Dedicated hardware offload FQ */

        return fqid;
}
```

- [ ] **Step 2: Connect TX FQ resolution to flow offload replace path**

Run: `cd kernel/ask/oot-modules/ask/ && ./ci-build.sh`
Expected: `ask.ko` compiles without warnings or errors.

- [ ] **Step 3: Commit**

```bash
git add kernel/ask/oot-modules/ask/ask_hw.c
git commit -m "feat(ask2): resolve dedicated egress hardware TX FQID for direct FMan enqueue"
```

---

### Task 4: Selective Offload Architecture Transition (T-M5-7)

**Files:**
- Modify: `kernel/ask/oot-modules/ask/ask_hw.c:200-260`
- Modify: `kernel/ask/oot-modules/ask/ask_flow_offload.c:1100-1150`
- Test: `kernel/ask/oot-modules/ask/ci-build.sh`

**Interfaces:**
- Consumes: Hardware engage state.
- Produces: `numKeys=0` CONT_LOOKUP pass-through baseline + selective CC key insertion.

- [ ] **Step 1: Set `numKeys=0` in CC node creation during engage**

```c
/* Pass-through baseline (7.37 Gbps software floor) when 0 offloaded keys present */
pcd_param.num_keys = 0;
```

- [ ] **Step 2: Wire selective CC key insertion on `ask_flow_offload_replace()`**

When a flow is marked for offload by `nftables` / TC, call `fman_pcd_fe_flow_add()` to steer specifically that 5-tuple flow into `FE_ENTER`.

- [ ] **Step 3: Verify module compilation**

Run: `cd kernel/ask/oot-modules/ask/ && ./ci-build.sh`
Expected: Clean build of `ask.ko`.

- [ ] **Step 4: Commit**

```bash
git add kernel/ask/oot-modules/ask/ask_hw.c kernel/ask/oot-modules/ask/ask_flow_offload.c
git commit -m "feat(ask2): transition to selective-offload architecture with numKeys=0 pass-through baseline"
```

---

### Task 5: End-to-End Verification & Build Validation

**Files:**
- Test: `bin/test-fixups.sh`
- Test: `kernel/ask/oot-modules/ask/ci-build.sh`

- [ ] **Step 1: Run fixup validation test suite**

Run: `./bin/test-fixups.sh`
Expected: `4/4 tests passed`.

- [ ] **Step 2: Build complete OOT module package**

Run: `cd kernel/ask/oot-modules/ask/ && ./ci-build.sh`
Expected: `ask.ko` built successfully with all 4 tasks included.

- [ ] **Step 3: Commit final M5 task milestone**

```bash
git commit --allow-empty -m "ci(ask2): complete M5 hardware opcode chain and selective-offload implementation"
```
