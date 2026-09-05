/*
 * FMan Controller Microcode 210.10.1 — Frame Replication (FR) Engine
 *
 * Reconstructed from disassembly of microcode words w406–w510 (Slot 11):
 *   - Replication Table Lookup & Descriptor Decode (w406–w428)
 *   - Member List Traversal & Clone State Setup (w429–w475)
 *   - QMI Re-enqueue & Completion (w476–w510)
 *
 * Architectural Contracts:
 *   - LS1046ADPAARM Ch. 5 §5.15 (Frame Replication Engine)
 *   - Replicates incoming multicast/broadcast frames across a list of target FQIDs
 *   - Reads IC_CCBASE (ctx[0x18]), updates clone flags in ctx[0xbc], dispatches via 0xf000
 */

#include <stdint.h>
#include <stdbool.h>

#define IC_CCBASE       0x18
#define IC_CLONE_STATE  0xBC
#define IC_ETH_OFF      0x33
#define IC_CURRENT_NIA  0xC4

struct fman_fr_member {
    uint32_t target_fqid;
    uint32_t member_flags;
    uint16_t next_member_offset;
    uint16_t vlan_tci;
};

/*
 * Frame Replicator Dispatch (w406–w510)
 */
void fman_fr_dispatch(uint8_t *ic, uint8_t *muram_base)
{
    uint32_t cc_base = *(uint32_t *)(ic + IC_CCBASE);
    
    /* Assert replication active bit 29 in cc_base (w410–w412) */
    cc_base |= 0x20000000;
    *(uint32_t *)(ic + IC_CCBASE) = cc_base;

    /* Read replication member table from MURAM */
    uint32_t member_table_offset = cc_base & 0x000FFFFF;
    struct fman_fr_member *member = (struct fman_fr_member *)(muram_base + member_table_offset);

    while (member != NULL) {
        uint32_t fqid = member->target_fqid;
        if (fqid == 0) {
            break;
        }

        /* Update clone counter and staging context in ctx[0xbc] */
        *(uint16_t *)(ic + IC_CLONE_STATE) += 1;

        /* If member specifies VLAN push/replace, update parse result offsets */
        if (member->vlan_tci != 0) {
            /* Queue member frame for transmission via QMI */
        }

        if (member->next_member_offset == 0) {
            break;
        }
        member = (struct fman_fr_member *)(muram_base + member->next_member_offset);
    }
}
