/*
 * FMan Controller Microcode 210.10.1 — IP Fragmentation & Reassembly Engine
 *
 * Reconstructed from disassembly of microcode words:
 *   - IPF Entry & Header Parsing: w530–w545 (Slot 17 / HCOR 0x11)
 *   - IPR Timeout & Status Window: w583–w585 (Slot 16)
 *   - Secondary Fragment Reassembly: w11911–w11950
 *
 * Architectural Contracts:
 *   - LS1046ADPAARM Ch. 5 §5.14 (IP Fragmentation and Reassembly Engine)
 *   - Read Parse Result offsets: IC_PR_L4R (0xd026), IC_PR_L4_OFF (0xd03e), IC_PR_IP_OFF (0xd03b)
 *   - Updates fragment identification and offset fields in ctx[0xb4]
 */

#include <stdint.h>
#include <stdbool.h>

#define IC_FLOW_HASH       0x0C
#define IC_PR_L4R          0x26
#define IC_PR_IP_OFF       0x3B
#define IC_PR_L4_OFF       0x3E
#define IC_IPF_STATE       0xB4

/* Flag constants */
#define IP_MF_FLAG         0x2000 /* More Fragments */
#define IP_OFFSET_MASK     0x1FFF /* Fragment Offset (in 8-byte units) */

struct fman_ipf_context {
    uint32_t flow_hash;
    uint8_t  l4_protocol;
    uint8_t  ip_offset;
    uint8_t  l4_offset;
    uint16_t frag_offset;
    bool     more_fragments;
};

/*
 * IPF Dispatch Handler (w530–w545)
 */
void fman_ipf_dispatch(uint8_t *ic, uint8_t *frame)
{
    uint8_t l4r = ic[IC_PR_L4R];
    uint8_t ip_off = ic[IC_PR_IP_OFF];
    uint8_t l4_off = ic[IC_PR_L4_OFF];
    uint32_t flow_hash = *(uint32_t *)(ic + IC_FLOW_HASH);

    /* Test L4 header presence (w534–w536) */
    if ((l4r & 0xE0) == 0x40) {
        /* Compute fragment header displacement relative to outer IP */
        uint8_t header_len = l4_off - ip_off + 8;
        uint32_t frag_desc = ((uint32_t)header_len << 16) | 0x80;
        
        *(uint32_t *)(ic + IC_IPF_STATE) = flow_hash;
        *(uint32_t *)(ic + IC_FLOW_HASH) = frag_desc;
    }
}

/*
 * IPR Timeout Handler (w583–w585)
 * Dispatches reassembly timeout frames via the FM_CTL status window at 0xf800.
 */
void fman_ipr_timeout_dispatch(uint8_t *ic)
{
    /* Handoff to FM_CTL action dispatcher for expired reassembly contexts */
    uint32_t action = *(uint32_t *)(ic + 0xC4);
    if (action == 0) {
        action = 0x1E; /* DISCARD timeout fragment */
    }
    *(uint32_t *)(ic + 0xC4) = action;
}
