/*
 * FMan Controller Microcode 210.10.1 — Parser Error & Frame Epilogue Engine
 *
 * Reconstructed from disassembly of microcode words w12133–w12850.
 *
 * Cross-references:
 *   - LS1046ADPAARM Ch. 5 §5.5 (Hardware Parser Results)
 *   - decomp/naming-map.md §8 (struct fman_prs_result)
 *   - decomp/findings.md (CORRECTION: w12849 shared status check exit)
 *   - decomp/out/fman-210.10.1-full.asm
 */

#include <stdint.h>
#include <stdbool.h>

/* Parser Error Flags in Frame Descriptor (FD Status Word) */
#define FM_FD_ERR_PRS_GROSS        0x00080000 /* Gross frame error / cycle limit */
#define FM_FD_ERR_L4_CKSUM         0x00010000 /* TCP/UDP checksum failed */
#define FM_FD_ERR_NO_SCHEME        0x00004000 /* No matching KeyGen scheme */

/* Internal Context Parse Result Layout (0xd020–0xd03f) */
struct fman_prs_result {
    uint8_t  lpid;
    uint8_t  shimr;
    uint16_t l2r;
    uint16_t l3r;
    uint8_t  l4r;
    uint8_t  cplan;
    uint8_t  nxthdr;
    uint8_t  cksum;
    uint16_t flags_frag_off;
    uint8_t  route_type;
    uint8_t  rhp_ip_valid;
    uint8_t  shim_off[2];
    uint8_t  ip_pid_off;
    uint8_t  eth_off;
    uint8_t  llc_snap_off;
    uint8_t  vlan_off[2];
    uint8_t  etype_off;
    uint8_t  pppoe_off;
    uint8_t  mpls_off[2];
    uint8_t  ip_off[2];
    uint8_t  gre_off;
    uint8_t  l4_off;
    uint8_t  nxthdr_off;
};

/*
 * Frame Epilogue: Parse Result Offset Normalization (w12143–w12170)
 *
 * Prepares the 32-byte parse result offsets before final enqueue to QMI/BMI.
 * Offsets equal to 0xFF (header not present) are preserved as 0xFF; valid
 * offsets are adjusted relative to the frame annotation boundary.
 */
void fman_frame_epilogue_normalize(struct fman_prs_result *pr, int8_t frame_delta)
{
    uint8_t *offset_array = &pr->shim_off[0];
    int num_offsets = 16; /* shim_off[0] through nxthdr_off */

    for (int i = 0; i < num_offsets; i++) {
        if (offset_array[i] != 0xFF) {
            /* Adjust offset by frame manipulation delta (e.g. after VLAN push/pop) */
            offset_array[i] = (uint8_t)((int16_t)offset_array[i] + frame_delta);
        }
    }
}

/*
 * Shared Status Check & Error Routing (w12551–w12850)
 *
 * Inspects FM_CTL status register [0xf808] and routes corrupted or errored
 * frames to the port's designated Error Frame Queue.
 */
uint32_t fman_check_errors_and_resolve_target(uint32_t fd_status,
                                             uint32_t normal_fqid,
                                             uint32_t err_fqid)
{
    /* Check for parsing gross errors or checksum failures */
    if (fd_status & (FM_FD_ERR_PRS_GROSS | FM_FD_ERR_L4_CKSUM | FM_FD_ERR_NO_SCHEME)) {
        /* Route directly to Error FQ (e.g. 0x291) */
        return err_fqid;
    }

    /* Normal frame disposition */
    return normal_fqid;
}
