/*
 * FMan Controller Microcode 210.10.1 — FE-VM Action Interpreter & Opcode Engine
 *
 * Reconstructed from disassembly of microcode words w8645–w9520.
 *
 * Cross-references:
 *   - decomp/fe-action-interpreter.md (Opcode dispatch island)
 *   - decomp/en-exthash-lookup.asm (Flow record linking)
 *   - decomp/fman-ehash-process.md (Hardware offload pipeline)
 *   - decomp/out/fman-210.10.1-full.asm
 */

#include <stdint.h>
#include <stdbool.h>
#include <string.h>

/* Internal Context (IC) Layout (base r26 = 0xd000) */
#define IC_AD_BASE           0x08
#define IC_FLOW_HASH         0x0C
#define IC_PR_SHIM_OFF       0x30
#define IC_PR_PPPOE_OFF      0x38
#define IC_TIMESTAMP_LO      0x44
#define IC_MGMT_INDEX        0xB8  /* [0xd0b8]: Per-task management index */
#define IC_TASK_FLAGS        0xC0
#define IC_CURRENT_NIA       0xC4
#define IC_ENQ_SCRATCH       0xD4

/* FE-VM Action Opcode Definitions */
#define OPC_ENQUEUE_PKT      0x01
#define OPC_ENQ_ONLY         0x03
#define OPC_STRIP_ETH        0x11
#define OPC_STRIP_ALL_VLAN   0x12
#define OPC_INSERT_L2_HDR    0x41
#define OPC_INSERT_VLAN_HDR  0x42

/* Known Protocol EtherTypes (w9345, w9348) */
#define ETHERTYPE_IPV4       0x0800
#define ETHERTYPE_IPV6       0x86DD

/* 256-byte Flow Record Structure in DDR */
struct fman_flow_record {
    uint16_t flags;            /* [15:11]=reserved, [10:6]=opc_offset, [5:0]=param_offset */
    uint16_t record_flags;     /* [15]=invalid, [13]=timestamp_en, [12]=stats_en */
    uint32_t stats_counter;    /* Hit count / packet counter */
    uint8_t  match_key[14];    /* KeyGen extracted key (PORT_ID|SIP|DIP|PROTO|SPORT|DPORT) */
    uint8_t  opcodes[16];      /* Inline action opcode byte stream */
    uint8_t  params[64];       /* Action parameter block */
};

/* L2 Header Replacement Scratchpad in MURAM (w9350) */
struct l2_rebuild_scratch {
    uint8_t  dmac[6];
    uint8_t  smac[6];
    uint16_t ethertype;
};

/* Parameter Block for ENQUEUE_PKT */
struct enqueue_param {
    uint16_t mtu;
    uint8_t  hdr_expand_size;
    uint8_t  bpid;
    uint32_t target_fqid;
};

/*
 * FE-VM Action Interpreter Loop (w8660–w9520)
 *
 * Executes the inline bytecode script attached to a matched DDR flow record.
 */
void fman_fevm_execute_actions(uint8_t *ic, uint8_t *frame, struct fman_flow_record *record)
{
    /* Step 1: Derive Opcode and Parameter Cursors (w8660–w8682) */
    uint16_t flags = record->flags;
    uint32_t param_offset  = (flags & 0x3F) << 2;
    uint32_t opcode_offset = ((flags >> 6) & 0x1F) << 2;

    uint8_t *param_ptr  = (uint8_t *)record + param_offset;
    uint8_t *opcode_ptr = (uint8_t *)record + opcode_offset;

    /* Per-task management index tracking (IC+0xb8) */
    uint32_t mgmt_index = *(uint32_t *)(ic + IC_MGMT_INDEX);

    bool terminate = false;
    while (!terminate) {
        /* Fetch next action opcode */
        uint8_t opcode = *opcode_ptr++;

        switch (opcode) {
        case 0x00: /* END / NOP */
            terminate = true;
            break;

        case OPC_INSERT_L2_HDR: { /* 0x41: w9328–w9354 */
            /* Read scratch parameters for L2 rebuild */
            struct l2_rebuild_scratch *scratch = (struct l2_rebuild_scratch *)param_ptr;
            param_ptr += sizeof(struct l2_rebuild_scratch);

            /* Select EtherType based on frame IP version */
            uint8_t ip_ver = (frame[14] >> 4) & 0x0F;
            uint16_t ethertype = (ip_ver == 6) ? ETHERTYPE_IPV6 : ETHERTYPE_IPV4;

            /* Write rewritten Ethernet header */
            memcpy(frame, scratch->dmac, 6);
            memcpy(frame + 6, scratch->smac, 6);
            *(uint16_t *)(frame + 12) = __builtin_bswap16(ethertype);
            break;
        }

        case OPC_STRIP_ALL_VLAN: { /* 0x12 */
            /* 
             * STRIP_ALL_VLAN fallthrough body:
             * Frame length -= 4 bytes, frame pointer += 4 bytes.
             * Preserves PCP/priority bits into task context.
             */
            uint32_t *frame_len = (uint32_t *)(ic + 0x04);
            *frame_len -= 4;
            /* Move Ethernet header past the 4-byte 802.1Q tag */
            memmove(frame + 4, frame, 12);
            frame += 4;
            break;
        }

        case OPC_INSERT_VLAN_HDR: { /* 0x42 */
            /* Push 802.1Q VLAN tag and update checksum-fixup tail */
            uint16_t vlan_tci = *(uint16_t *)param_ptr;
            param_ptr += 2;

            uint32_t *frame_len = (uint32_t *)(ic + 0x04);
            *frame_len += 4;
            frame -= 4;
            memmove(frame, frame + 4, 12);
            *(uint16_t *)(frame + 12) = __builtin_bswap16(0x8100);
            *(uint16_t *)(frame + 14) = __builtin_bswap16(vlan_tci);
            break;
        }

        case OPC_ENQUEUE_PKT: { /* 0x01: w9291–w9435 */
            struct enqueue_param *enq = (struct enqueue_param *)param_ptr;
            param_ptr += sizeof(struct enqueue_param);

            /* Materialize ENQ descriptor (0x02010000) into IC[0xd4] */
            *(uint32_t *)(ic + IC_ENQ_SCRATCH) = 0x02010000;

            /* Set target FQID */
            uint32_t target_fqid = __builtin_bswap32(enq->target_fqid);
            *(uint32_t *)(ic + IC_CURRENT_NIA) = target_fqid;

            terminate = true;
            break;
        }

        case OPC_ENQ_ONLY: { /* 0x03: w9040–w9067 */
            /* Materialize ENQ_ONLY descriptor (0x02010000) */
            *(uint32_t *)(ic + IC_ENQ_SCRATCH) = 0x02010000;
            terminate = true;
            break;
        }

        default:
            /* Unrecognized opcode: stop execution and fall back to software */
            terminate = true;
            break;
        }

        /* 
         * Common Epilogue (w9067, w9111, w9241):
         * Reset task management index IC[0xb8] = 0 and update MURAM workspace.
         */
        *(uint32_t *)(ic + IC_MGMT_INDEX) = 0;
    }
}
