// SPDX-License-Identifier: GPL-2.0
/*
 * ask_hw.c — hardware-derived information shared with userspace.
 *
 * PR13 (M2.4) populates the ucode-version fields of ASK_CMD_GET_INFO
 * from the QEF (QorIQ Embedded Firmware) microcode blob that U-Boot
 * loads from the SPI flash "fman-ucode" partition (mtd3 on the Mono
 * Gateway DK) into FMan IRAM at boot, and re-publishes via the device
 * tree property /soc/fman@1a00000/fman-firmware/fsl,firmware so any
 * in-kernel consumer can identify the loaded microcode without
 * touching MMIO.
 *
 * Why this path instead of the spec §12.2 OP_GET_UCODE_VERSION host
 * command? See specs/ask2-rewrite-spec.md §12.8 (PR13 hardware-probe
 * findings): the standard NXP 210.x QEF microcode loaded on this
 * board does not implement a host-command opcode dispatcher — it
 * implements parser/policer/keygen via MURAM-resident config tables
 * programmed by drivers/net/ethernet/freescale/fman/fman_keygen.c,
 * fman_port.c and fman_memac.c. The host-command transport
 * (kernel/flavors/ask/patches/0003-fman-host-command-api.patch) is
 * preserved as future infrastructure for a hypothetical custom ASK2
 * microcode that does implement opcode dispatch, but is not used by
 * v1.0 against stock 210.x.
 *
 * QEF blob layout (verified 2026-05-13 against
 * /proc/device-tree/soc/fman@1a00000/fman-firmware/fsl,firmware on
 * the live Mono Gateway DK, kernel 6.18.28-vyos):
 *
 *   off  size  field
 *   ---  ----  -------------------------------------------------
 *   0x00   4   crc32 (big-endian, over the rest of the blob)
 *   0x04   4   magic 'Q' 'E' 'F' 0x01 (= 0x51454601 BE)
 *   0x08  64   NUL-terminated ASCII description string, e.g.
 *               "Microcode version 210.10.1 for LS1043 r1.0"
 *   0x48   N   binary microcode payload (opaque to the kernel)
 *
 * The version fields are extracted by sscanf() from the description
 * string. This is the same approach the SDK fmlib library uses, and
 * is robust across all known 210.x microcode generations.
 *
 * Copyright 2026 Mono Networks / VyOS LS1046A maintainers.
 */

#include <linux/kernel.h>
#include <linux/module.h>
#include <linux/of.h>
#include <linux/of_address.h>
#include <linux/string.h>
#include <linux/types.h>

#include "include/ask_internal.h"

/* QEF blob structural constants (see comment above). */
#define ASK_QEF_MAGIC          0x51454601u   /* 'Q' 'E' 'F' 0x01 */
#define ASK_QEF_MAGIC_OFFSET   4
#define ASK_QEF_DESC_OFFSET    8
#define ASK_QEF_DESC_LEN       64
#define ASK_QEF_MIN_LEN        (ASK_QEF_DESC_OFFSET + ASK_QEF_DESC_LEN)

/* Cached version, populated on first successful probe. */
static struct ask_hw_ucode_version ask_hw_cached;
static bool ask_hw_cached_valid;

/* ------------------------------------------------------------------------- */
/* QEF blob parsing                                                           */
/* ------------------------------------------------------------------------- */

/*
 * Validate the QEF magic and return the embedded description string.
 * @blob:   pointer to the firmware blob bytes (DT property contents)
 * @len:    length of the blob in bytes
 * @desc:   output buffer of at least ASK_QEF_DESC_LEN bytes; the
 *          description is copied here NUL-terminated on success.
 *
 * Return: 0 on success, -EINVAL on bad magic or short blob.
 */
static int ask_hw_qef_get_description(const u8 *blob, size_t len,
                                      char *desc)
{
        u32 magic;

        if (!blob || len < ASK_QEF_MIN_LEN) {
                ask_pr_warn("hw: firmware blob too short (%zu < %d)\n",
                            len, ASK_QEF_MIN_LEN);
                return -EINVAL;
        }

        magic = ((u32)blob[ASK_QEF_MAGIC_OFFSET]     << 24) |
                ((u32)blob[ASK_QEF_MAGIC_OFFSET + 1] << 16) |
                ((u32)blob[ASK_QEF_MAGIC_OFFSET + 2] <<  8) |
                ((u32)blob[ASK_QEF_MAGIC_OFFSET + 3]);

        if (magic != ASK_QEF_MAGIC) {
                ask_pr_warn("hw: firmware magic mismatch (got 0x%08x, want 0x%08x)\n",
                            magic, ASK_QEF_MAGIC);
                return -EINVAL;
        }

        /*
         * The description region is exactly 64 bytes wide and is
         * always NUL-terminated by the QEF spec. We copy and force a
         * terminator at the last byte as belt-and-braces against a
         * malformed blob.
         */
        memcpy(desc, blob + ASK_QEF_DESC_OFFSET, ASK_QEF_DESC_LEN);
        desc[ASK_QEF_DESC_LEN - 1] = '\0';
        return 0;
}

/*
 * Parse a QEF description string of the form
 *   "Microcode version <family>.<major>.<minor> for <soc> r<rev>"
 * (e.g. "Microcode version 210.10.1 for LS1043 r1.0") into the four
 * version fields exposed by ASK_INFO_ATTR_UCODE_*.
 *
 * The patch field (ASK_INFO_ATTR_UCODE_PATCH) is set to 0 for stock
 * NXP microcode — the QEF format does not encode a fourth version
 * component. It is reserved for any custom microcode that bumps it
 * (the ASK2 hypothetical custom microcode path per spec §12.8).
 *
 * Return: 0 on success, -EINVAL if the string does not match the
 *         expected pattern.
 */
static int ask_hw_parse_desc(const char *desc,
                             struct ask_hw_ucode_version *out)
{
        unsigned int family, major, minor;
        int matched;

        matched = sscanf(desc, "Microcode version %u.%u.%u",
                         &family, &major, &minor);
        if (matched != 3) {
                ask_pr_warn("hw: unrecognised QEF description '%s'\n", desc);
                return -EINVAL;
        }

        if (family > U16_MAX || major > U8_MAX || minor > U8_MAX) {
                ask_pr_warn("hw: QEF version fields out of range: %u.%u.%u\n",
                            family, major, minor);
                return -EINVAL;
        }

        out->family = (u16)family;
        out->major  = (u8)major;
        out->minor  = (u8)minor;
        out->patch  = 0;
        strscpy(out->description, desc, sizeof(out->description));
        return 0;
}

/* ------------------------------------------------------------------------- */
/* DT lookup                                                                  */
/* ------------------------------------------------------------------------- */

/*
 * Walk the device tree to find the FMan firmware blob.
 *
 * The mainline FMan binding places the firmware as a child node of
 * the FMan controller:
 *
 *   /soc/fman@<addr>/fman-firmware {
 *       compatible = "fsl,fman-firmware";
 *       fsl,firmware = <BLOB BYTES>;
 *   };
 *
 * U-Boot fills in fsl,firmware from the SPI "fman-ucode" partition
 * before it boots Linux. We accept the firmware from any FMan on the
 * SoC — this is single-FMan on LS1046A, dual-FMan on LS1043A. The
 * first match wins; v1.0 does not differentiate per FMan because
 * NXP ships the same QEF microcode for every FMan on a given SoC.
 *
 * Return: 0 on success, -ENODEV if no fsl,fman-firmware node is
 *         present, -ENOENT if the node lacks the fsl,firmware
 *         property, -EINVAL if the QEF blob fails sanity checks.
 */
static int ask_hw_probe_ucode_locked(struct ask_hw_ucode_version *out)
{
        struct device_node *np;
        const u8 *blob;
        int blob_len;
        char desc[ASK_QEF_DESC_LEN];
        int rc;

        np = of_find_compatible_node(NULL, NULL, "fsl,fman-firmware");
        if (!np) {
                ask_pr_warn("hw: no fsl,fman-firmware node in device tree\n");
                return -ENODEV;
        }

        blob = of_get_property(np, "fsl,firmware", &blob_len);
        if (!blob || blob_len <= 0) {
                ask_pr_warn("hw: fsl,firmware property missing or empty\n");
                of_node_put(np);
                return -ENOENT;
        }

        rc = ask_hw_qef_get_description(blob, (size_t)blob_len, desc);
        if (rc) {
                of_node_put(np);
                return rc;
        }

        rc = ask_hw_parse_desc(desc, out);
        of_node_put(np);
        if (rc)
                return rc;

        ask_pr_info("hw: FMan microcode %u.%u.%u (\"%s\")\n",
                    out->family, out->major, out->minor, out->description);
        return 0;
}

/* ------------------------------------------------------------------------- */
/* Public API                                                                 */
/* ------------------------------------------------------------------------- */

int ask_hw_ucode_get_version(struct ask_hw_ucode_version *out)
{
        int rc;

        if (!out)
                return -EINVAL;

        if (READ_ONCE(ask_hw_cached_valid)) {
                *out = ask_hw_cached;
                return 0;
        }

        rc = ask_hw_probe_ucode_locked(out);
        if (rc)
                return rc;

        ask_hw_cached = *out;
        WRITE_ONCE(ask_hw_cached_valid, true);
        return 0;
}
EXPORT_SYMBOL_GPL(ask_hw_ucode_get_version);

int ask_hw_init(void)
{
        struct ask_hw_ucode_version v;
        int rc;

        /*
         * Probe at module load so dmesg carries the microcode version
         * as a single "ask: hw: FMan microcode X.Y.Z" breadcrumb. If
         * the probe fails we log it but do not fail module load —
         * userspace will still be able to query ASK_CMD_GET_INFO and
         * receive zero ucode fields plus a -ENODEV trail in dmesg
         * explaining why.
         */
        rc = ask_hw_ucode_get_version(&v);
        if (rc) {
                ask_pr_warn("hw: ucode version probe failed (%d); ASK_CMD_GET_INFO will report zeros\n",
                            rc);
                return 0;
        }
        return 0;
}

void ask_hw_exit(void)
{
        WRITE_ONCE(ask_hw_cached_valid, false);
}