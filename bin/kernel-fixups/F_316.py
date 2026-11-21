#!/usr/bin/env python3
"""F-316: per-FQ tail-drop for the F-199 offload no-confirm TX FQs.

F-199 gives each egress port one no-confirm TX FQ with a 4 MiB FQD tail-drop
(DPAA_FQ_TD). Every FE-offloaded flow to that port shares it, so at 9000 MTU
one direction's BBR data holds the FQ at the threshold and the other
direction's ACKs, which egress the same port, are tail-dropped: the reverse
direction stalls for seconds (A/B on .185: lost ACKs were ~90 % of the FQ's
rejected enqueues, and only in the stall seconds).

ask.ko now gives each flow its own FQ from a per-port set. To keep the port's
aggregate bound where it is (and under the RX buffer pool), the per-flow FQs
need a smaller threshold than DPAA_FQ_TD. This adds:
  * struct dpaa_fq.td_bytes (0 = DPAA_FQ_TD), used by dpaa_fq_init()'s
    tail-drop branch; a set FQ (td_bytes != 0) also gets ICS_CRED 0x4000,
  * the set FQs share the port channel's WQ, and with ICS_CRED 0 QMan
    rotates FQs every frame: FMan TX stalls near 770 kfps and receiver GRO
    falls from ~9 to ~3 (1500 MTU, 8 BBR streams: 15.6 vs 17.5 Gbit/s
    legacy). 0x4000 (16 KiB per visit) restores 17.3-17.4 for +0.3 ms
    kernel-path RTT; 0x7FFF (no DRR, drain to empty) adds ~3 ms,
  * exported dpaa_alloc_offload_tx_fq_td(dev, td_bytes, *fqid), the F-199/
    F-295 allocator body with the threshold stored before dpaa_fq_init(),
  * dpaa_alloc_offload_tx_fq(dev, *fqid) kept as the td_bytes = 0 wrapper
    (WGFP and ask.ko's legacy FQ are unchanged).

Needs F-199 and F-295. Idempotent (marker); every anchor must match exactly
once. Host check: bin/test-dpaa-offload-fq.py.
"""

from pathlib import Path
import sys

ETH_H = Path("drivers/net/ethernet/freescale/dpaa/dpaa_eth.h")
ETH_C = Path("drivers/net/ethernet/freescale/dpaa/dpaa_eth.c")
OFF_H = Path("include/linux/fsl/dpaa_flow_offload.h")
MARK = "F-316"

EDITS = (
    (ETH_H, "struct field",
     "\tu8 wq;\n\tenum dpaa_fq_type fq_type;\n",
     "\tu8 wq;\n"
     "\tu32 td_bytes;\t/* F-316: tail-drop when td_enable; 0 = DPAA_FQ_TD */\n"
     "\tenum dpaa_fq_type fq_type;\n"),
    (ETH_C, "tail-drop threshold",
     "\t\t\tqm_fqd_set_taildrop(&initfq.fqd, DPAA_FQ_TD, 1);\n",
     "\t\t\t/* F-316: per-FQ threshold for the offload FQ set */\n"
     "\t\t\tqm_fqd_set_taildrop(&initfq.fqd, dpaa_fq->td_bytes ?\n"
     "\t\t\t\t\t    dpaa_fq->td_bytes : DPAA_FQ_TD, 1);\n"
     "\t\t\tif (dpaa_fq->td_bytes) {\n"
     "\t\t\t\t/* F-316: 16 KiB intra-WQ DRR credit per visit */\n"
     "\t\t\t\tinitfq.we_mask |= cpu_to_be16(QM_INITFQ_WE_ICSCRED);\n"
     "\t\t\t\tinitfq.fqd.ics_cred = cpu_to_be16(0x4000);\n"
     "\t\t\t}\n"),
    (ETH_C, "allocator signature",
     "int dpaa_alloc_offload_tx_fq(struct net_device *dev, u32 *fqid)\n{\n",
     "int dpaa_alloc_offload_tx_fq_td(struct net_device *dev, u32 td_bytes,\n"
     "\t\t\t\tu32 *fqid)\n{\n"),
    (ETH_C, "threshold bound",
     "\tif (!dev || !fqid)\n\t\treturn -EINVAL;\n\n\t/* Serialize priv->dpaa_fq_list",
     "\t/* F-316: qm_fqd_set_taildrop() fails silently above this */\n"
     "\tif (!dev || !fqid || td_bytes > DPAA_FQ_TD)\n\t\treturn -EINVAL;\n\n"
     "\t/* Serialize priv->dpaa_fq_list"),
    (ETH_C, "allocator threshold",
     "\tdpaa_fq->fq_type = FQ_TYPE_TX_NO_CONFIRM;\n",
     "\tdpaa_fq->fq_type = FQ_TYPE_TX_NO_CONFIRM;\n"
     "\tdpaa_fq->td_bytes = td_bytes;\t/* F-316 */\n"),
    (ETH_C, "wrapper",
     "EXPORT_SYMBOL_GPL(dpaa_alloc_offload_tx_fq);\n",
     "EXPORT_SYMBOL_GPL(dpaa_alloc_offload_tx_fq_td);\n"
     "\n"
     "/* F-316: the single per-port FQ at DPAA_FQ_TD (WGFP, ask.ko legacy FQ). */\n"
     "int dpaa_alloc_offload_tx_fq(struct net_device *dev, u32 *fqid)\n"
     "{\n"
     "\treturn dpaa_alloc_offload_tx_fq_td(dev, 0, fqid);\n"
     "}\n"
     "EXPORT_SYMBOL_GPL(dpaa_alloc_offload_tx_fq);\n"),
    (OFF_H, "declaration",
     "int dpaa_alloc_offload_tx_fq(struct net_device *dev, u32 *fqid);\n#else\n",
     "int dpaa_alloc_offload_tx_fq(struct net_device *dev, u32 *fqid);\n"
     "/* F-316: same, with a per-FQ tail-drop threshold (0 = DPAA_FQ_TD). */\n"
     "int dpaa_alloc_offload_tx_fq_td(struct net_device *dev, u32 td_bytes,\n"
     "\t\t\t\tu32 *fqid);\n"
     "#else\n"
     "static inline int dpaa_alloc_offload_tx_fq_td(struct net_device *dev,\n"
     "\t\t\t\t\t      u32 td_bytes, u32 *fqid)\n"
     "{\n"
     "\treturn -ENODEV;\n"
     "}\n"),
)


def main():
    try:
        files = {p: p.read_text() for p in (ETH_H, ETH_C, OFF_H)}
        if all(MARK in files[p] for p in files):
            for path, label, _, new in EDITS:
                if files[path].count(new) != 1:
                    raise ValueError(f"partially applied: {label}")
            print("### F-316: already applied")
            return 0
        if any(MARK in files[p] for p in files):
            raise ValueError("partially applied: marker in some files only")
        for path, label, old, new in EDITS:
            count = files[path].count(old)
            if count != 1:
                raise ValueError(f"{label}: found {count} anchors, expected 1")
            files[path] = files[path].replace(old, new, 1)
        for path, text in files.items():
            path.write_text(text)
    except (OSError, ValueError) as error:
        print(f"### F-316: FATAL: {error}", file=sys.stderr)
        return 1
    print("### F-316: per-FQ offload tail-drop applied (7 blocks)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
