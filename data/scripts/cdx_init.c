/*
 * cdx_init.c — Minimal CDX interface table initializer for LS1046A ASK stack
 *
 * Opens /dev/fm0-pcd and /dev/cdx_ctrl, constructs the port/fman info
 * structures, and sends CDX_CTRL_DPA_SET_PARAMS ioctl to register all
 * ethernet and OH ports with the CDX fast-path engine.
 *
 * This replaces dpa_app for the initial CDX setup. dpa_app combines
 * FMC PCD programming + CDX init; this tool does only CDX init,
 * which is sufficient for software fast-path (CMM → FCI → CDX).
 *
 * Cross-compile:
 *   aarch64-linux-gnu-gcc -static -o cdx_init cdx_init.c
 *
 * Usage (on device, as root):
 *   ./cdx_init
 *
 * Port mapping (LS1046A Mono Gateway):
 *   MAC2  cell-index=1  1G  → eth1 (left RJ45)
 *   MAC5  cell-index=4  1G  → eth2 (center RJ45)
 *   MAC6  cell-index=5  1G  → eth0 (right RJ45)
 *   MAC9  cell-index=8→0  10G → eth3 (left SFP+)  [SDK remaps ≥8 by -8]
 *   MAC10 cell-index=9→1  10G → eth4 (right SFP+)  [SDK remaps ≥8 by -8]
 *   OH@2  dpa-fman0-oh@2    → OH port 1 (PCD classifier)
 *   OH@3  dpa-fman0-oh@3    → OH port 2 (PCD classifier)
 *
 * Copyright 2025 Mono Technologies Inc.
 * SPDX-License-Identifier: GPL-2.0+
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <fcntl.h>
#include <unistd.h>
#include <errno.h>
#include <sys/ioctl.h>

/* ================================================================
 * Struct definitions — must match kernel cdx_ioctl.h exactly
 * ================================================================ */

#define CDX_IOC_MAGIC           0xbe
#define CDX_CTRL_PORT_NAME_LEN  32
#define TABLE_NAME_SIZE         64

/* Port distribution info */
struct cdx_dist_info {
    uint32_t type;
    void *handle;
    uint32_t base_fqid;
    uint32_t count;
};

/* Port information */
struct cdx_port_info {
    uint32_t fm_index;
    uint32_t index;
    uint32_t portid;
    uint32_t type;       /* 0=OH, 1=1G, 10=10G */
    uint32_t max_dist;
    struct cdx_dist_info *dist_info;
    char name[CDX_CTRL_PORT_NAME_LEN];
};

/* Classification table information */
struct table_info {
    char name[TABLE_NAME_SIZE];
    uint32_t dpa_type;
    uint32_t port_idx;
    uint32_t type;
    uint32_t num_keys;
    struct {
        uint32_t num_sets;
        uint32_t num_ways;
    };
    uint32_t key_size;
    void *id;
};

#define CDX_EXPT_MAX_EXPT_LIMIT_TYPES 4

struct cdx_expt_ratelimit_info {
    void *handle;
    uint32_t limit;
};

#define INGRESS_FLOW_POLICER_QUEUES 8
#define SEC_POLICER_QUEUES          0
#define INGRESS_ALL_POLICER_QUEUES  (INGRESS_FLOW_POLICER_QUEUES + SEC_POLICER_QUEUES)

struct cdx_ingress_policer_info {
    void *handle;
    uint8_t  profile_id;
    uint16_t policer_on;
    uint32_t cir_value;
    uint32_t pir_value;
    uint32_t cbs;
    uint32_t pbs;
};

/* FMan information */
struct cdx_fman_info {
    void *fm_handle;
    struct table_info *tbl_info;
    void *pcd_handle;
    void *muram_handle;
    struct cdx_port_info *portinfo;
    uint64_t physicalMuramBase;
    struct cdx_expt_ratelimit_info expt_rate_limit_info[CDX_EXPT_MAX_EXPT_LIMIT_TYPES];
    struct cdx_ingress_policer_info ingress_policer_info[INGRESS_ALL_POLICER_QUEUES];
    uint32_t index;
    uint32_t max_ports;
    uint32_t num_tables;
    uint32_t fmMuramMemSize;
    uint32_t expt_ratelim_mode;
    uint32_t expt_ratelim_burst_size;
};

/* IPR (IP reassembly) info */
struct cdx_ipr_info {
    uint32_t timeout;
    uint32_t max_frags;
    uint32_t min_frag_size;
    uint32_t max_contexts;
    uint32_t ipr_ctx_bsize;
    uint32_t ipr_frag_bsize;
};

/* Main ioctl structure */
struct cdx_ctrl_set_dpa_params {
    struct cdx_fman_info *fman_info;
    struct cdx_ipr_info *ipr_info;
    uint32_t num_fmans;
};

#define CDX_CTRL_DPA_SET_PARAMS \
    _IOWR(CDX_IOC_MAGIC, 1, struct cdx_ctrl_set_dpa_params)

/* ================================================================
 * Port definitions for Mono Gateway LS1046A
 * ================================================================ */

#define NUM_PORTS  7   /* 2x OH + 3x 1G + 2x 10G (MAC9+MAC10) */
#define NUM_FMANS  1

/*
 * Port table:
 * For eth ports (type != 0): kernel calls find_osdev_by_fman_params()
 *   to map (fm_index, index, type) → netdev name.
 *   - index = MAC cell-index within FMan
 *   - type = 1 for 1G, 10 for 10G
 * For OH ports (type == 0): kernel uses name directly.
 *   - name must be "dpa-fman<N>-oh@<M>" matching SDK OH driver registration
 */
static struct {
    uint32_t fm_index;
    uint32_t index;    /* MAC cell-index (eth) or 0 (OH) */
    uint32_t portid;   /* CDX port ID for flow routing */
    uint32_t type;     /* 0=OH, 1=1G, 10=10G */
    const char *name;  /* Initial name (kernel overwrites for eth ports) */
} port_defs[NUM_PORTS] = {
    /* OH ports FIRST — kernel creates /proc/oh* entries */
    { 0, 0, 8, 0,  "dpa-fman0-oh@2" },  /* OH port 1 (PCD classifier) */
    { 0, 0, 9, 0,  "dpa-fman0-oh@3" },  /* OH port 2 (PCD classifier) */
    /* 1G RJ45 ports — cell-index from fsl-ls1046a.dtsi */
    { 0, 1, 1, 1,  "MAC2"           },  /* eth1 (left RJ45) - cell-index=1 */
    { 0, 4, 4, 1,  "MAC5"           },  /* eth2 (center RJ45) - cell-index=4 */
    { 0, 5, 5, 1,  "MAC6"           },  /* eth0 (right RJ45) - cell-index=5 */
    /* 10G SFP+ — MAC9 DTS cell-index=8, SDK remaps to 0 (mac.c: if >=8, -=8) */
    { 0, 0, 6, 10, "MAC9"           },  /* eth3 (left SFP+) - SDK cell-index=0 */
    /* 10G SFP+ — MAC10 DTS cell-index=9, SDK remaps to 1 (mac.c: if >=8, -=8) */
    { 0, 1, 7, 10, "MAC10"          },  /* eth4 (right SFP+) - SDK cell-index=1 */
};

/* ================================================================ */

int main(int argc, char *argv[])
{
    int pcd_fd, cdx_fd, ret;
    struct cdx_fman_info fman;
    struct cdx_ipr_info ipr;
    struct cdx_ctrl_set_dpa_params params;
    struct cdx_port_info ports[NUM_PORTS];
    int verbose = 0;

    if (argc > 1 && strcmp(argv[1], "-v") == 0)
        verbose = 1;

    printf("cdx_init: CDX interface table initializer for LS1046A\n");

    /* ---- Step 1: Open /dev/fm0-pcd ---- */
    pcd_fd = open("/dev/fm0-pcd", O_RDWR);
    if (pcd_fd < 0) {
        perror("open /dev/fm0-pcd");
        fprintf(stderr, "  Is the SDK FMan PCD driver loaded?\n");
        return 1;
    }
    printf("  /dev/fm0-pcd opened (fd=%d)\n", pcd_fd);

    /* ---- Step 2: Open /dev/cdx_ctrl ---- */
    cdx_fd = open("/dev/cdx_ctrl", O_RDWR);
    if (cdx_fd < 0) {
        perror("open /dev/cdx_ctrl");
        fprintf(stderr, "  Is the CDX module loaded? (insmod cdx.ko)\n");
        close(pcd_fd);
        return 1;
    }
    printf("  /dev/cdx_ctrl opened (fd=%d)\n", cdx_fd);

    /* ---- Step 3: Build port info array ---- */
    memset(ports, 0, sizeof(ports));
    for (int i = 0; i < NUM_PORTS; i++) {
        ports[i].fm_index = port_defs[i].fm_index;
        ports[i].index    = port_defs[i].index;
        ports[i].portid   = port_defs[i].portid;
        ports[i].type     = port_defs[i].type;
        ports[i].max_dist = 0;        /* No KG distributions (no FMC PCD) */
        ports[i].dist_info = NULL;
        strncpy(ports[i].name, port_defs[i].name, CDX_CTRL_PORT_NAME_LEN - 1);
        ports[i].name[CDX_CTRL_PORT_NAME_LEN - 1] = '\0';

        if (verbose)
            printf("  port[%d]: fm=%u idx=%u portid=%u type=%u name=%s\n",
                   i, ports[i].fm_index, ports[i].index,
                   ports[i].portid, ports[i].type, ports[i].name);
    }

    /* ---- Step 4: Build FMan info ---- */
    memset(&fman, 0, sizeof(fman));
    fman.index       = 0;                                /* FMan 0 */
    fman.max_ports   = NUM_PORTS;
    fman.num_tables  = 0;                                /* No CC tables */
    fman.portinfo    = ports;                             /* userspace ptr */
    fman.tbl_info    = NULL;                              /* no tables */
    /*
     * pcd_handle is passed as a void* but the kernel interprets it as
     * a file descriptor number. fget((unsigned long)pcd_handle) in
     * cdxdrv_get_fman_handles() translates it to the kernel file struct.
     */
    fman.pcd_handle  = (void *)(uintptr_t)pcd_fd;
    fman.fm_handle   = NULL;        /* kernel fills from PCD file */
    fman.muram_handle = NULL;       /* kernel fills from PCD file */

    printf("  FMan info: index=%u ports=%u tables=%u pcd_fd=%d\n",
           fman.index, fman.max_ports, fman.num_tables, pcd_fd);

    /* ---- Step 5: Build IPR info ---- */
    memset(&ipr, 0, sizeof(ipr));
    ipr.timeout        = 50;    /* 50ms reassembly timeout */
    ipr.max_frags      = 16;
    ipr.min_frag_size  = 64;
    ipr.max_contexts   = 256;
    ipr.ipr_ctx_bsize  = 1600;
    ipr.ipr_frag_bsize = 1600;

    /* ---- Step 6: Build params ---- */
    memset(&params, 0, sizeof(params));
    params.fman_info = &fman;
    params.ipr_info  = &ipr;
    params.num_fmans = NUM_FMANS;

    /* ---- Step 7: Send ioctl ---- */
    printf("  Sending CDX_CTRL_DPA_SET_PARAMS ioctl...\n");
    ret = ioctl(cdx_fd, CDX_CTRL_DPA_SET_PARAMS, &params);
    if (ret < 0) {
        int err = errno;
        fprintf(stderr, "  CDX_CTRL_DPA_SET_PARAMS ioctl FAILED: %s (errno=%d)\n",
                strerror(err), err);
        fprintf(stderr, "  Check dmesg for DPA_ERROR messages\n");
        close(cdx_fd);
        close(pcd_fd);
        return 2;
    }

    printf("  CDX_CTRL_DPA_SET_PARAMS ioctl SUCCESS (ret=%d)\n", ret);
    printf("\n");
    printf("CDX interface table initialized with %d ports.\n", NUM_PORTS);
    printf("CMM can now send flow commands via FCI netlink.\n");

    /* ---- Cleanup ---- */
    close(cdx_fd);
    close(pcd_fd);
    return 0;
}