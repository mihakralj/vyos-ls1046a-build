"""F-329 (G10 Phase 4): Relocate port-wide ingress policer to HWP->PLCR->downstream.

Relocates the 0104 tc matchall ingress policer from the RSS KeyGen scheme tail
(which ASK2's engaged AC_CC scheme bypasses) to the post-parser NIA
(HWP -> PLCR -> downstream):

  1. include/linux/fsl/fman_pcd.h:
       - add `u32 next_nia` and `bool drop_yellow` to `struct fman_pcd_plcr_hw_profile`.
       - declare `fman_pcd_plcr_destroy_all()`, `fman_pcd_plcr_update_next_nia()`,
         `fman_pcd_plcr_read_nias()`, and `fman_pcd_plcr_read_counters()`.
  2. drivers/net/ethernet/freescale/fman/fman_pcd.c:
       - reap any surviving policer profiles in `fman_pcd_release()`.
  3. drivers/net/ethernet/freescale/fman/fman_pcd_kg.c:
       - release `pcd->lock` BEFORE calling `fman_port_set_kg_direct_scheme()`
         in `fman_pcd_kg_port_attach_cc_dual()`, `_dual_ekfc()`, and `_l2()`
         to preserve the global lock order (`fman->cc_port_lock -> pcd->lock`).
  4. drivers/net/ethernet/freescale/fman/fman_port.h & fman_port.c:
       - define `NIA_ENG_PLCR 0x004C0000`.
       - track per-port `plcr_attached`, `plcr_profile_id`, `plcr_saved_rfpne`
         under `fman->cc_port_lock`.
       - coordinate `fman_port_use_kg_hash()`, `fman_port_set_cc_base()`,
         `fman_port_set_kg_direct_scheme()`, `fman_port_clear_kg_direct_scheme()`,
         and `fman_port_verify_fe_detached()` (with live HW PEGNIA/PEYNIA
         readback via FMPL_PAR_R) so ASK2 engage/disengage updates PEGNIA
         atomically (via selective PWSEL) while keeping `fmbm_rfpne == NIA_ENG_PLCR`.
       - implement & export `fman_port_plcr_attach()`, `fman_port_plcr_detach()`,
         and `fman_port_plcr_read_counters()`.
  5. drivers/net/ethernet/freescale/dpaa/dpaa_eth.h & dpaa_eth.c:
       - use per-port physical FMPL slot `port_id` (eliminating multi-port slot-0
         collisions across eth3/eth4).
       - enforce tc police semantics (`drop_yellow = true`, `pbs_bytes = mtu`
         when `peakrate` is set, full rate/burst/avrate/overhead/overflow validation).
       - drive `fman_port_plcr_attach()` / `fman_port_plcr_detach()` instead of
         KeyGen RSS scheme attach/detach.
       - wire `TC_CLSMATCHALL_STATS` (`dpaa_setup_tc_matchall_stats()`) with
         seeded baseline, reset-safe hardware packet/drop (`yellow+red`) deltas,
         and idle-safe `lastused`.

Note on soft-parser interactions: F-266 (TCP SYN/FIN/RST and TTL<=1 control-plane
punt) intentionally overrides `$nia = 0x500002` inside HWP ahead of `fmbm_rfpne`
(matching NXP `cdx_sp.xml`). Ports using F-323 soft-parser G4/G5b class-dispatch
override `$nia` to `0x480300|sid` inside HWP and bypass `fmbm_rfpne` (`HWP->PLCR`).

Must run after F-328. Idempotent; count-gated anchors.
"""

import sys

FMAN_PCD_H = "include/linux/fsl/fman_pcd.h"
FMAN_PCD_C = "drivers/net/ethernet/freescale/fman/fman_pcd.c"
FMAN_PCD_KG_C = "drivers/net/ethernet/freescale/fman/fman_pcd_kg.c"
FMAN_PORT_H = "drivers/net/ethernet/freescale/fman/fman_port.h"
FMAN_PORT_C = "drivers/net/ethernet/freescale/fman/fman_port.c"
DPAA_ETH_H = "drivers/net/ethernet/freescale/dpaa/dpaa_eth.h"
DPAA_ETH_C = "drivers/net/ethernet/freescale/dpaa/dpaa_eth.c"
MARK = "F-329"

FMAN_PCD_H_EDITS = [
    (
        "extend fman_pcd_plcr_hw_profile with next_nia and drop_yellow",
        "struct fman_pcd_plcr_hw_profile {\n"
        "\tbool trtcm;\n"
        "\tbool color_aware;\n"
        "\tu64 cir_bps;\n"
        "\tu32 cbs_bytes;\n"
        "\tu64 pir_bps;\n"
        "\tu32 pbs_bytes;\n"
        "};\n",
        "struct fman_pcd_plcr_hw_profile {\n"
        "\tbool trtcm;\n"
        "\tbool color_aware;\n"
        "\tbool drop_yellow;\t/* F-329: discard yellow frames too */\n"
        "\tu32 next_nia;\t\t/* F-329: 24-bit pass NIA; 0 = BMI enqueue */\n"
        "\tu64 cir_bps;\n"
        "\tu32 cbs_bytes;\n"
        "\tu64 pir_bps;\n"
        "\tu32 pbs_bytes;\n"
        "};\n",
    ),
    (
        "declare fman_pcd_plcr_destroy_all, update_next_nia, read_nias, and read_counters",
        "void fman_pcd_plcr_destroy(struct fman_pcd *pcd, u8 port_id, u8 profile_id);\n",
        "void fman_pcd_plcr_destroy(struct fman_pcd *pcd, u8 port_id, u8 profile_id);\n"
        "/* F-329 (G10 Phase 4): reap, live pass-NIA update, & HW readback. */\n"
        "void fman_pcd_plcr_destroy_all(struct fman_pcd *pcd);\n"
        "int fman_pcd_plcr_update_next_nia(struct fman_pcd *pcd, u8 port_id,\n"
        "\t\t\t\t  u8 profile_id, u32 next_nia);\n"
        "int fman_pcd_plcr_read_nias(struct fman_pcd *pcd, u8 port_id,\n"
        "\t\t\t    u8 profile_id, u32 *pegnia, u32 *peynia);\n"
        "int fman_pcd_plcr_read_counters(struct fman_pcd *pcd, u8 port_id,\n"
        "\t\t\t\tu8 profile_id, u32 *green, u32 *yellow,\n"
        "\t\t\t\tu32 *red);\n",
    ),
]

FMAN_PCD_C_EDITS = [
    (
        "reap surviving policer profiles in fman_pcd_release",
        "\tWARN_ON(!list_empty(&pcd->plcr_profiles));\n",
        "\t/* F-329: reap any surviving policer profiles on PCD unbind. */\n"
        "\tfman_pcd_plcr_destroy_all(pcd);\n"
        "\tWARN_ON(!list_empty(&pcd->plcr_profiles));\n",
    ),
]

FMAN_PCD_KG_C_EDITS = [
    (
        "unlock pcd->lock before fman_port_set_kg_direct_scheme in attach_cc_dual",
        "\t{\n"
        "\t\tstruct fman_port *rxport = fman_port_lookup_rx(fman, hw_port_id);\n"
        "\n"
        "\t\tif (rxport)\n"
        "\t\t\t(void)fman_port_set_kg_direct_scheme(rxport, id);\n"
        "\t}\n"
        "\n"
        "\tmutex_unlock(lock);\n"
        "\treturn 0;\n"
        "}\n"
        "EXPORT_SYMBOL_GPL(fman_pcd_kg_port_attach_cc_dual);\n",
        "\t/* F-329: release pcd->lock before taking fman->cc_port_lock. */\n"
        "\tmutex_unlock(lock);\n"
        "\t{\n"
        "\t\tstruct fman_port *rxport = fman_port_lookup_rx(fman, hw_port_id);\n"
        "\n"
        "\t\tif (rxport)\n"
        "\t\t\t(void)fman_port_set_kg_direct_scheme(rxport, id);\n"
        "\t}\n"
        "\n"
        "\treturn 0;\n"
        "}\n"
        "EXPORT_SYMBOL_GPL(fman_pcd_kg_port_attach_cc_dual);\n",
    ),
    (
        "unlock pcd->lock before fman_port_set_kg_direct_scheme in attach_cc_dual_ekfc",
        "\t{\n"
        "\t\tstruct fman_port *rxport = fman_port_lookup_rx(fman, hw_port_id);\n"
        "\n"
        "\t\tif (rxport)\n"
        "\t\t\t(void)fman_port_set_kg_direct_scheme(rxport, id);\n"
        "\t}\n"
        "\n"
        "\tmutex_unlock(lock);\n"
        "\treturn 0;\n"
        "}\n"
        "EXPORT_SYMBOL_GPL(fman_pcd_kg_port_attach_cc_dual_ekfc);\n",
        "\t/* F-329: release pcd->lock before taking fman->cc_port_lock. */\n"
        "\tmutex_unlock(lock);\n"
        "\t{\n"
        "\t\tstruct fman_port *rxport = fman_port_lookup_rx(fman, hw_port_id);\n"
        "\n"
        "\t\tif (rxport)\n"
        "\t\t\t(void)fman_port_set_kg_direct_scheme(rxport, id);\n"
        "\t}\n"
        "\n"
        "\treturn 0;\n"
        "}\n"
        "EXPORT_SYMBOL_GPL(fman_pcd_kg_port_attach_cc_dual_ekfc);\n",
    ),
    (
        "unlock pcd->lock before fman_port_set_kg_direct_scheme in attach_cc_l2",
        "\t{\n"
        "\t\tstruct fman_port *rxport = fman_port_lookup_rx(fman, hw_port_id);\n"
        "\n"
        "\t\tif (rxport)\n"
        "\t\t\t(void)fman_port_set_kg_direct_scheme(rxport, id);\n"
        "\t}\n"
        "\n"
        "\tmutex_unlock(lock);\n"
        "\treturn 0;\n"
        "}\n"
        "EXPORT_SYMBOL_GPL(fman_pcd_kg_port_attach_cc_l2);\n",
        "\t/* F-329: release pcd->lock before taking fman->cc_port_lock. */\n"
        "\tmutex_unlock(lock);\n"
        "\t{\n"
        "\t\tstruct fman_port *rxport = fman_port_lookup_rx(fman, hw_port_id);\n"
        "\n"
        "\t\tif (rxport)\n"
        "\t\t\t(void)fman_port_set_kg_direct_scheme(rxport, id);\n"
        "\t}\n"
        "\n"
        "\treturn 0;\n"
        "}\n"
        "EXPORT_SYMBOL_GPL(fman_pcd_kg_port_attach_cc_l2);\n",
    ),
]

FMAN_PORT_H_EDITS = [
    (
        "declare fman_port_plcr_attach/detach/read_counters",
        "#endif /* __FMAN_PORT_H */\n",
        "/* F-329 (G10 Phase 4): HWP -> PLCR -> downstream RX port policer. */\n"
        "struct fman_pcd;\n"
        "struct fman_pcd_plcr_hw_profile;\n"
        "int fman_port_plcr_attach(struct fman_port *port, struct fman_pcd *pcd,\n"
        "\t\t\t  u8 profile_id,\n"
        "\t\t\t  const struct fman_pcd_plcr_hw_profile *hw);\n"
        "void fman_port_plcr_detach(struct fman_port *port, struct fman_pcd *pcd);\n"
        "int fman_port_plcr_read_counters(struct fman_port *port,\n"
        "\t\t\t\t struct fman_pcd *pcd,\n"
        "\t\t\t\t u32 *green, u32 *yellow, u32 *red);\n"
        "\n"
        "#endif /* __FMAN_PORT_H */\n",
    ),
]

FMAN_PORT_C_EDITS = [
    (
        "define NIA_ENG_PLCR in fman_port.c",
        "#define NIA_ENG_HWK\t\t\t\t\t0x00480000\n",
        "#define NIA_ENG_HWK\t\t\t\t\t0x00480000\n"
        "#define NIA_ENG_PLCR\t\t\t\t\t0x004C0000\t/* F-329 */\n",
    ),
    (
        "add per-port policer tracking fields to struct fman_port",
        "\tbool cc_engaged;\t/* protected by fman->cc_port_lock */\n",
        "\tbool cc_engaged;\t/* protected by fman->cc_port_lock */\n"
        "\tbool plcr_attached;\t/* F-329: HWP->PLCR->downstream active */\n"
        "\tu8 plcr_profile_id;\t/* F-329: physical FMPL slot (= port_id) */\n"
        "\tu32 plcr_saved_rfpne;\t/* F-329: logical downstream NIA */\n",
    ),
    (
        "coordinate fman_port_use_kg_hash with attached parser-exit policer",
        "void fman_port_use_kg_hash(struct fman_port *port, bool enable)\n"
        "{\n"
        "\tif (enable)\n"
        "\t\t/* After the Parser frames go to KeyGen */\n"
        "\t\tiowrite32be(NIA_ENG_HWK, &port->bmi_regs->rx.fmbm_rfpne);\n"
        "\telse\n"
        "\t\t/* After the Parser frames go to BMI */\n"
        "\t\tiowrite32be(NIA_ENG_BMI | NIA_BMI_AC_ENQ_FRAME,\n"
        "\t\t\t    &port->bmi_regs->rx.fmbm_rfpne);\n"
        "}\n",
        "void fman_port_use_kg_hash(struct fman_port *port, bool enable)\n"
        "{\n"
        "\tstruct fman *fman = port->fm;\n"
        "\tu32 nia = enable ? NIA_ENG_HWK :\n"
        "\t\t\t   (NIA_ENG_BMI | NIA_BMI_AC_ENQ_FRAME);\n"
        "\n"
        "\tmutex_lock(&fman->cc_port_lock);\n"
        "\tif (port->plcr_attached) {\n"
        "\t\tstruct fman_pcd *pcd = fman_get_pcd(fman);\n"
        "\t\tint err = -ENODEV;\n"
        "\n"
        "\t\tif (enable && port->cc_engaged)\n"
        "\t\t\tnia |= NIA_KG_CC_EN;\n"
        "\t\tif (pcd)\n"
        "\t\t\terr = fman_pcd_plcr_update_next_nia(pcd, port->port_id,\n"
        "\t\t\t\t\t\t\t    port->plcr_profile_id,\n"
        "\t\t\t\t\t\t\t    nia);\n"
        "\t\tif (!err)\n"
        "\t\t\tport->plcr_saved_rfpne = nia;\n"
        "\t\telse\n"
        "\t\t\tdev_warn(port->dev,\n"
        "\t\t\t\t \"fman_port: policer pass-NIA update failed (%d)\\n\",\n"
        "\t\t\t\t err);\n"
        "\t} else {\n"
        "\t\tiowrite32be(nia, &port->bmi_regs->rx.fmbm_rfpne);\n"
        "\t}\n"
        "\tmutex_unlock(&fman->cc_port_lock);\n"
        "}\n",
    ),
    (
        "coordinate fman_port_set_cc_base with attached parser-exit policer",
        "\trfpne = ioread32be(&port->bmi_regs->rx.fmbm_rfpne);\n"
        "\tif ((rfpne & 0x007C0000) == NIA_ENG_HWK) {\n"
        "\t\tif (cc_muram_off)\n"
        "\t\t\trfpne |= NIA_KG_CC_EN;\n"
        "\t\telse\n"
        "\t\t\trfpne &= ~NIA_KG_CC_EN;\n"
        "\t\tiowrite32be(rfpne, &port->bmi_regs->rx.fmbm_rfpne);\n"
        "\t} else if (cc_muram_off) {\n"
        "\t\tdev_warn(port->dev,\n"
        "\t\t\t \"fman_port: CC base set but parser NIA 0x%08x is not KeyGen - CC will not dispatch\\n\",\n"
        "\t\t\t rfpne);\n"
        "\t}\n",
        "\trfpne = ioread32be(&port->bmi_regs->rx.fmbm_rfpne);\n"
        "\tif (port->plcr_attached) {\n"
        "\t\tstruct fman_pcd *pcd = fman_get_pcd(fman);\n"
        "\n"
        "\t\tif ((port->plcr_saved_rfpne & 0x007C0000) == NIA_ENG_HWK) {\n"
        "\t\t\tu32 next_nia = port->plcr_saved_rfpne;\n"
        "\t\t\tint uerr = -ENODEV;\n"
        "\n"
        "\t\t\tif (cc_muram_off)\n"
        "\t\t\t\tnext_nia |= NIA_KG_CC_EN;\n"
        "\t\t\telse\n"
        "\t\t\t\tnext_nia &= ~NIA_KG_CC_EN;\n"
        "\t\t\tif (pcd)\n"
        "\t\t\t\tuerr = fman_pcd_plcr_update_next_nia(pcd,\n"
        "\t\t\t\t\t\t\t\t     port->port_id,\n"
        "\t\t\t\t\t\t\t\t     port->plcr_profile_id,\n"
        "\t\t\t\t\t\t\t\t     next_nia);\n"
        "\t\t\tif (uerr) {\n"
        "\t\t\t\tdev_err(port->dev,\n"
        "\t\t\t\t\t\"fman_port: policer pass-NIA update failed (%d)\\n\",\n"
        "\t\t\t\t\tuerr);\n"
        "\t\t\t\tmutex_unlock(&fman->cc_port_lock);\n"
        "\t\t\t\treturn uerr;\n"
        "\t\t\t}\n"
        "\t\t\tport->plcr_saved_rfpne = next_nia;\n"
        "\t\t} else if (cc_muram_off) {\n"
        "\t\t\tdev_warn(port->dev,\n"
        "\t\t\t\t \"fman_port: CC base set but policer pass NIA 0x%08x is not KeyGen\\n\",\n"
        "\t\t\t\t port->plcr_saved_rfpne);\n"
        "\t\t}\n"
        "\t} else if ((rfpne & 0x007C0000) == NIA_ENG_HWK) {\n"
        "\t\tif (cc_muram_off)\n"
        "\t\t\trfpne |= NIA_KG_CC_EN;\n"
        "\t\telse\n"
        "\t\t\trfpne &= ~NIA_KG_CC_EN;\n"
        "\t\tiowrite32be(rfpne, &port->bmi_regs->rx.fmbm_rfpne);\n"
        "\t} else if (cc_muram_off) {\n"
        "\t\tdev_warn(port->dev,\n"
        "\t\t\t \"fman_port: CC base set but parser NIA 0x%08x is not KeyGen - CC will not dispatch\\n\",\n"
        "\t\t\t rfpne);\n"
        "\t}\n",
    ),
    (
        "coordinate fman_port_set_kg_direct_scheme and clear with attached policer",
        "\tval = ioread32be(fpne);\n"
        "\tval = (val & ~0x1Fu) | NIA_KG_DIRECT | ((u32)scheme_id & 0x1Fu);\n"
        "\tiowrite32be(val, fpne);\n"
        "\n"
        "\tdev_info(port->dev,\n"
        "\t\t \"fman_port: KG direct-scheme addressing set, scheme %u (fpne 0x%08x)\\n\",\n"
        "\t\t scheme_id, val);\n"
        "\treturn 0;\n"
        "}\n"
        "EXPORT_SYMBOL_GPL(fman_port_set_kg_direct_scheme);\n"
        "\n"
        "/**\n"
        " * fman_port_clear_kg_direct_scheme() - inverse of\n"
        " * fman_port_set_kg_direct_scheme() (F-162)\n"
        " * @port: the FMan RX or O/H port\n"
        " */\n"
        "void fman_port_clear_kg_direct_scheme(struct fman_port *port)\n"
        "{\n"
        "\tu32 __iomem *fpne;\n"
        "\tu32 val;\n"
        "\n"
        "\tif (!port)\n"
        "\t\treturn;\n"
        "\tif (port->port_type == FMAN_PORT_TYPE_RX)\n"
        "\t\tfpne = &port->bmi_regs->rx.fmbm_rfpne;\n"
        "\telse if (port->port_type == FMAN_PORT_TYPE_OH)\n"
        "\t\tfpne = &port->bmi_regs->oh.fmbm_ofpne;\n"
        "\telse\n"
        "\t\treturn;\n"
        "\n"
        "\tval = ioread32be(fpne);\n"
        "\tval &= ~(NIA_KG_DIRECT | 0x1Fu);\n"
        "\tiowrite32be(val, fpne);\n"
        "}\n",
        "\tif (port->port_type == FMAN_PORT_TYPE_RX) {\n"
        "\t\tstruct fman *fman = port->fm;\n"
        "\n"
        "\t\tmutex_lock(&fman->cc_port_lock);\n"
        "\t\tif (port->plcr_attached) {\n"
        "\t\t\tstruct fman_pcd *pcd = fman_get_pcd(fman);\n"
        "\t\t\tint uerr = -ENODEV;\n"
        "\n"
        "\t\t\tval = (port->plcr_saved_rfpne & ~0x1Fu) |\n"
        "\t\t\t      NIA_KG_DIRECT | ((u32)scheme_id & 0x1Fu);\n"
        "\t\t\tif (pcd)\n"
        "\t\t\t\tuerr = fman_pcd_plcr_update_next_nia(pcd,\n"
        "\t\t\t\t\t\t\t\t     port->port_id,\n"
        "\t\t\t\t\t\t\t\t     port->plcr_profile_id,\n"
        "\t\t\t\t\t\t\t\t     val);\n"
        "\t\t\tif (uerr) {\n"
        "\t\t\t\tmutex_unlock(&fman->cc_port_lock);\n"
        "\t\t\t\treturn uerr;\n"
        "\t\t\t}\n"
        "\t\t\tport->plcr_saved_rfpne = val;\n"
        "\t\t} else {\n"
        "\t\t\tval = ioread32be(fpne);\n"
        "\t\t\tval = (val & ~0x1Fu) | NIA_KG_DIRECT |\n"
        "\t\t\t      ((u32)scheme_id & 0x1Fu);\n"
        "\t\t\tiowrite32be(val, fpne);\n"
        "\t\t}\n"
        "\t\tmutex_unlock(&fman->cc_port_lock);\n"
        "\t} else {\n"
        "\t\tval = ioread32be(fpne);\n"
        "\t\tval = (val & ~0x1Fu) | NIA_KG_DIRECT | ((u32)scheme_id & 0x1Fu);\n"
        "\t\tiowrite32be(val, fpne);\n"
        "\t}\n"
        "\n"
        "\tdev_info(port->dev,\n"
        "\t\t \"fman_port: KG direct-scheme addressing set, scheme %u (fpne 0x%08x)\\n\",\n"
        "\t\t scheme_id, val);\n"
        "\treturn 0;\n"
        "}\n"
        "EXPORT_SYMBOL_GPL(fman_port_set_kg_direct_scheme);\n"
        "\n"
        "/**\n"
        " * fman_port_clear_kg_direct_scheme() - inverse of\n"
        " * fman_port_set_kg_direct_scheme() (F-162)\n"
        " * @port: the FMan RX or O/H port\n"
        " */\n"
        "void fman_port_clear_kg_direct_scheme(struct fman_port *port)\n"
        "{\n"
        "\tu32 __iomem *fpne;\n"
        "\tu32 val;\n"
        "\n"
        "\tif (!port)\n"
        "\t\treturn;\n"
        "\tif (port->port_type == FMAN_PORT_TYPE_RX) {\n"
        "\t\tstruct fman *fman = port->fm;\n"
        "\n"
        "\t\tfpne = &port->bmi_regs->rx.fmbm_rfpne;\n"
        "\t\tmutex_lock(&fman->cc_port_lock);\n"
        "\t\tif (port->plcr_attached) {\n"
        "\t\t\tstruct fman_pcd *pcd = fman_get_pcd(fman);\n"
        "\t\t\tu32 next_nia = port->plcr_saved_rfpne &\n"
        "\t\t\t\t       ~(NIA_KG_DIRECT | 0x1Fu);\n"
        "\t\t\tint uerr = -ENODEV;\n"
        "\n"
        "\t\t\tif (pcd)\n"
        "\t\t\t\tuerr = fman_pcd_plcr_update_next_nia(pcd,\n"
        "\t\t\t\t\t\t\t\t     port->port_id,\n"
        "\t\t\t\t\t\t\t\t     port->plcr_profile_id,\n"
        "\t\t\t\t\t\t\t\t     next_nia);\n"
        "\t\t\tif (!uerr)\n"
        "\t\t\t\tport->plcr_saved_rfpne = next_nia;\n"
        "\t\t\telse\n"
        "\t\t\t\tdev_warn(port->dev,\n"
        "\t\t\t\t\t \"fman_port: policer direct-scheme clear failed (%d)\\n\",\n"
        "\t\t\t\t\t uerr);\n"
        "\t\t} else {\n"
        "\t\t\tval = ioread32be(fpne);\n"
        "\t\t\tval &= ~(NIA_KG_DIRECT | 0x1Fu);\n"
        "\t\t\tiowrite32be(val, fpne);\n"
        "\t\t}\n"
        "\t\tmutex_unlock(&fman->cc_port_lock);\n"
        "\t\treturn;\n"
        "\t} else if (port->port_type == FMAN_PORT_TYPE_OH) {\n"
        "\t\tfpne = &port->bmi_regs->oh.fmbm_ofpne;\n"
        "\t} else {\n"
        "\t\treturn;\n"
        "\t}\n"
        "\n"
        "\tval = ioread32be(fpne);\n"
        "\tval &= ~(NIA_KG_DIRECT | 0x1Fu);\n"
        "\tiowrite32be(val, fpne);\n"
        "}\n",
    ),
    (
        "verify live HW PEGNIA/PEYNIA in fman_port_verify_fe_detached and add plcr attach/detach/counters",
        "int fman_port_verify_fe_detached(struct fman_port *port)\n"
        "{\n"
        "\tu32 rccb, rfpne;\n"
        "\n"
        "\tif (!port || port->port_type != FMAN_PORT_TYPE_RX)\n"
        "\t\treturn -EINVAL;\n"
        "\trccb = ioread32be(&port->bmi_regs->rx.fmbm_rccb);\n"
        "\trfpne = ioread32be(&port->bmi_regs->rx.fmbm_rfpne);\n"
        "\tif (rccb || ((rfpne & 0x007c0000U) == NIA_ENG_HWK &&\n"
        "\t\t     (rfpne & (NIA_KG_CC_EN | NIA_KG_DIRECT | 0x1fU)))) {\n"
        "\t\tdev_err(port->dev, \"fman_port: FE disarm readback rccb=0x%08x rfpne=0x%08x\\n\",\n"
        "\t\t\trccb, rfpne);\n"
        "\t\treturn -EIO;\n"
        "\t}\n"
        "\treturn 0;\n"
        "}\n"
        "EXPORT_SYMBOL_GPL(fman_port_verify_fe_detached);\n",
        "int fman_port_verify_fe_detached(struct fman_port *port)\n"
        "{\n"
        "\tstruct fman *fman;\n"
        "\tu32 rccb, rfpne, eff_rfpne = 0, pegnia = 0, peynia = 0;\n"
        "\tbool attached;\n"
        "\tint read_rc = 0;\n"
        "\n"
        "\tif (!port || port->port_type != FMAN_PORT_TYPE_RX)\n"
        "\t\treturn -EINVAL;\n"
        "\tfman = port->fm;\n"
        "\tmutex_lock(&fman->cc_port_lock);\n"
        "\trccb = ioread32be(&port->bmi_regs->rx.fmbm_rccb);\n"
        "\trfpne = ioread32be(&port->bmi_regs->rx.fmbm_rfpne);\n"
        "\tattached = port->plcr_attached;\n"
        "\tif (attached) {\n"
        "\t\tstruct fman_pcd *pcd = fman_get_pcd(fman);\n"
        "\n"
        "\t\tread_rc = pcd ? fman_pcd_plcr_read_nias(pcd, port->port_id,\n"
        "\t\t\t\t\t\t\tport->plcr_profile_id,\n"
        "\t\t\t\t\t\t\t&pegnia, &peynia) :\n"
        "\t\t\t  -ENODEV;\n"
        "\t\teff_rfpne = pegnia & 0x00ffffffU;\n"
        "\t} else {\n"
        "\t\teff_rfpne = rfpne;\n"
        "\t}\n"
        "\tmutex_unlock(&fman->cc_port_lock);\n"
        "\tif (rccb || read_rc ||\n"
        "\t    (attached && (rfpne & 0x007c0000U) != NIA_ENG_PLCR) ||\n"
        "\t    ((eff_rfpne & 0x007c0000U) == NIA_ENG_HWK &&\n"
        "\t     (eff_rfpne & (NIA_KG_CC_EN | NIA_KG_DIRECT | 0x1fU))) ||\n"
        "\t    (attached && ((peynia & 0x007c0000U) == NIA_ENG_HWK &&\n"
        "\t     (peynia & (NIA_KG_CC_EN | NIA_KG_DIRECT | 0x1fU))))) {\n"
        "\t\tdev_err(port->dev,\n"
        "\t\t\t\"fman_port: FE disarm readback rccb=0x%08x rfpne=0x%08x pegnia=0x%08x peynia=0x%08x rc=%d\\n\",\n"
        "\t\t\trccb, rfpne, pegnia, peynia, read_rc);\n"
        "\t\treturn -EIO;\n"
        "\t}\n"
        "\treturn 0;\n"
        "}\n"
        "EXPORT_SYMBOL_GPL(fman_port_verify_fe_detached);\n"
        "\n"
        "/* F-329 (G10 Phase 4): install a per-port FMPL policer between HWP and\n"
        " * the port's downstream engine (RSS KeyGen or ASK2 CC/ehash) by flipping\n"
        " * fmbm_rfpne to NIA_ENG_PLCR (relative 0 -> FMPL_PMR -> @profile_id) and\n"
        " * programming PEGNIA = VALID | saved_rfpne. */\n"
        "int fman_port_plcr_attach(struct fman_port *port, struct fman_pcd *pcd,\n"
        "\t\t\t  u8 profile_id,\n"
        "\t\t\t  const struct fman_pcd_plcr_hw_profile *hw)\n"
        "{\n"
        "\tstruct fman *fman;\n"
        "\tstruct fman_pcd_plcr_hw_profile copy;\n"
        "\tu32 next_nia;\n"
        "\tint err;\n"
        "\n"
        "\tif (!port || !pcd || !hw || port->port_type != FMAN_PORT_TYPE_RX)\n"
        "\t\treturn -EINVAL;\n"
        "\tfman = port->fm;\n"
        "\tmutex_lock(&fman->cc_port_lock);\n"
        "\tif (port->plcr_attached && port->plcr_profile_id != profile_id) {\n"
        "\t\tmutex_unlock(&fman->cc_port_lock);\n"
        "\t\treturn -EBUSY;\n"
        "\t}\n"
        "\tnext_nia = port->plcr_attached ? port->plcr_saved_rfpne :\n"
        "\t\t   ioread32be(&port->bmi_regs->rx.fmbm_rfpne);\n"
        "\tif ((next_nia & 0x007C0000U) == NIA_ENG_PLCR) {\n"
        "\t\tmutex_unlock(&fman->cc_port_lock);\n"
        "\t\treturn -EINVAL;\n"
        "\t}\n"
        "\tcopy = *hw;\n"
        "\tcopy.next_nia = next_nia;\n"
        "\terr = fman_pcd_plcr_install(pcd, port->port_id, profile_id, &copy);\n"
        "\tif (err) {\n"
        "\t\tmutex_unlock(&fman->cc_port_lock);\n"
        "\t\treturn err;\n"
        "\t}\n"
        "\t/* Profile + FMPL_PMR + FMPL_GCR are live: now steer HWP -> PLCR. */\n"
        "\tiowrite32be(NIA_ENG_PLCR, &port->bmi_regs->rx.fmbm_rfpne);\n"
        "\tport->plcr_attached = true;\n"
        "\tport->plcr_profile_id = profile_id;\n"
        "\tport->plcr_saved_rfpne = next_nia;\n"
        "\tmutex_unlock(&fman->cc_port_lock);\n"
        "\tdev_info(port->dev,\n"
        "\t\t \"fman_port: ingress policer attached (slot %u, pass NIA 0x%08x)\\n\",\n"
        "\t\t profile_id, next_nia);\n"
        "\treturn 0;\n"
        "}\n"
        "EXPORT_SYMBOL_GPL(fman_port_plcr_attach);\n"
        "\n"
        "void fman_port_plcr_detach(struct fman_port *port, struct fman_pcd *pcd)\n"
        "{\n"
        "\tstruct fman *fman;\n"
        "\tu8 prof_id;\n"
        "\n"
        "\tif (!port || !pcd || port->port_type != FMAN_PORT_TYPE_RX)\n"
        "\t\treturn;\n"
        "\tfman = port->fm;\n"
        "\tmutex_lock(&fman->cc_port_lock);\n"
        "\tif (!port->plcr_attached) {\n"
        "\t\tmutex_unlock(&fman->cc_port_lock);\n"
        "\t\treturn;\n"
        "\t}\n"
        "\t/* Restore HWP next-engine FIRST so no new frames enter PLCR, wait\n"
        "\t * briefly for any in-flight frame to exit PLCR, then tear down. */\n"
        "\tiowrite32be(port->plcr_saved_rfpne, &port->bmi_regs->rx.fmbm_rfpne);\n"
        "\tprof_id = port->plcr_profile_id;\n"
        "\tport->plcr_attached = false;\n"
        "\tport->plcr_profile_id = 0;\n"
        "\tport->plcr_saved_rfpne = 0;\n"
        "\tusleep_range(200, 500);\n"
        "\tfman_pcd_plcr_destroy(pcd, port->port_id, prof_id);\n"
        "\tmutex_unlock(&fman->cc_port_lock);\n"
        "\tdev_info(port->dev, \"fman_port: ingress policer detached (slot %u)\\n\",\n"
        "\t\t prof_id);\n"
        "}\n"
        "EXPORT_SYMBOL_GPL(fman_port_plcr_detach);\n"
        "\n"
        "int fman_port_plcr_read_counters(struct fman_port *port,\n"
        "\t\t\t\t struct fman_pcd *pcd,\n"
        "\t\t\t\t u32 *green, u32 *yellow, u32 *red)\n"
        "{\n"
        "\tstruct fman *fman;\n"
        "\tu8 prof_id;\n"
        "\tint err;\n"
        "\n"
        "\tif (!port || !pcd || port->port_type != FMAN_PORT_TYPE_RX)\n"
        "\t\treturn -EINVAL;\n"
        "\tfman = port->fm;\n"
        "\tmutex_lock(&fman->cc_port_lock);\n"
        "\tif (!port->plcr_attached) {\n"
        "\t\tmutex_unlock(&fman->cc_port_lock);\n"
        "\t\treturn -ENOENT;\n"
        "\t}\n"
        "\tprof_id = port->plcr_profile_id;\n"
        "\terr = fman_pcd_plcr_read_counters(pcd, port->port_id, prof_id,\n"
        "\t\t\t\t\t  green, yellow, red);\n"
        "\tmutex_unlock(&fman->cc_port_lock);\n"
        "\treturn err;\n"
        "}\n"
        "EXPORT_SYMBOL_GPL(fman_port_plcr_read_counters);\n",
    ),
]

DPAA_ETH_H_EDITS = [
    (
        "track ingress policer cookie and counter baselines in struct dpaa_priv",
        "\tbool ingress_police_installed;\n",
        "\tbool ingress_police_installed;\n"
        "\t/* F-329 (G10 Phase 4): cookie + seeded HW counter baselines. */\n"
        "\tunsigned long ingress_police_cookie;\n"
        "\tu32 ingress_police_last_green;\n"
        "\tu32 ingress_police_last_yellow;\n"
        "\tu32 ingress_police_last_red;\n"
        "\tbool ingress_police_stats_valid;\n",
    ),
]

DPAA_ETH_C_EDITS = [
    (
        "remove unused DPAA_POLICE_PROFILE_ID and switch matchall local to hw profile",
        "#define DPAA_POLICE_PROFILE_ID 0\n"
        "\n"
        "static int dpaa_setup_tc_matchall_replace(struct net_device *net_dev,\n"
        "\t\t\t\t\t  struct tc_cls_matchall_offload *f)\n"
        "{\n"
        "\tstruct dpaa_priv *priv = netdev_priv(net_dev);\n"
        "\tstruct netlink_ext_ack *extack = f->common.extack;\n"
        "\tstruct fman_policer_profile prof = {};\n"
        "\tconst struct flow_action_entry *act;\n"
        "\tstruct fman_pcd *pcd;\n"
        "\tstruct fman *fm;\n"
        "\tu8 port_id;\n"
        "\tint err;\n",
        "static int dpaa_setup_tc_matchall_replace(struct net_device *net_dev,\n"
        "\t\t\t\t\t  struct tc_cls_matchall_offload *f)\n"
        "{\n"
        "\tstruct dpaa_priv *priv = netdev_priv(net_dev);\n"
        "\tstruct netlink_ext_ack *extack = f->common.extack;\n"
        "\tstruct fman_pcd_plcr_hw_profile hw = {};\n"
        "\tconst struct flow_action_entry *act;\n"
        "\tstruct fman_pcd *pcd;\n"
        "\tstruct fman *fm;\n"
        "\tu8 port_id;\n"
        "\tint err;\n",
    ),
    (
        "validate police inputs and attach via fman_port_plcr_attach on port_id slot",
        "\tfm = fman_bind(priv->mac_dev->fman_dev);\n"
        "\tif (!fm)\n"
        "\t\treturn -ENODEV;\n"
        "\tport_id = fman_port_get_id(priv->mac_dev->port[RX]);\n"
        "\n"
        "\t/* tc police rates are bytes/s, FMan profiles are bits/s; bursts are\n"
        "\t * bytes on both sides. peakrate present => trTCM (RFC 2698), else\n"
        "\t * srTCM (RFC 2697). Colour-blind: we do not honour an upstream colour.\n"
        "\t */\n"
        "\tprof.color_mode = FMAN_POLICER_COLOR_BLIND;\n"
        "\tprof.cir_bps    = act->police.rate_bytes_ps * 8;\n"
        "\tprof.cbs_bytes  = act->police.burst;\n"
        "\n"
        "\tif (act->police.peakrate_bytes_ps) {\n"
        "\t\tprof.mode      = FMAN_POLICER_MODE_TRTCM;\n"
        "\t\tprof.pir_bps   = act->police.peakrate_bytes_ps * 8;\n"
        "\t\tprof.pbs_bytes = act->police.burst;\n"
        "\t} else {\n"
        "\t\t/* Single-rate (no peakrate): silicon lacks srTCM, so 0100 maps\n"
        "\t\t * this to RFC2698 with PIR==CIR/PBS==CBS; leaving them 0 would\n"
        "\t\t * red-mark and drop ALL frames. Mirror CIR/CBS to police, not drop. */\n"
        "\t\tprof.mode      = FMAN_POLICER_MODE_SRTCM;\n"
        "\t\tprof.pir_bps   = prof.cir_bps;\n"
        "\t\tprof.pbs_bytes = prof.cbs_bytes;\n"
        "\t}\n"
        "\n"
        "\terr = fman_policer_install(fm, port_id, DPAA_POLICE_PROFILE_ID, &prof);\n"
        "\tif (err) {\n"
        "\t\tNL_SET_ERR_MSG_MOD(extack, \"policer install failed\");\n"
        "\t\tnetif_err(priv, drv, net_dev,\n"
        "\t\t\t  \"ingress policer install failed: %d\\n\", err);\n"
        "\t\treturn err;\n"
        "\t}\n"
        "\n"
        "\t/* The profile is now programmed but unbound: nothing routes frames\n"
        "\t * into it yet. The RX port already has an RSS-hashing KeyGen scheme\n"
        "\t * (created at port init by keygen_port_hashing_init()) that every\n"
        "\t * ingress frame is dispatched to - that is why un-policed traffic\n"
        "\t * flows through KeyGen at all. Reprogram THAT existing scheme's\n"
        "\t * next-engine in place to the policer profile, preserving its hash\n"
        "\t * spreading and FQID base, so conforming (green/yellow) frames\n"
        "\t * continue to the same hashed RX FQs while the policer meters and\n"
        "\t * drops the excess. Creating a *separate* catch-all scheme does not\n"
        "\t * work: the silicon's scheme-select picks the lowest-id scheme bound\n"
        "\t * to the port, so the pre-existing hash scheme always wins selection\n"
        "\t * and a higher-id policer scheme is never consulted (confirmed on\n"
        "\t * hardware 2026-06-09: traffic stayed uncapped at line rate).\n"
        "\t */\n"
        "\tpcd = fman_get_pcd(fm);\n"
        "\tif (!pcd) {\n"
        "\t\tNL_SET_ERR_MSG_MOD(extack, \"FMan PCD subsystem unavailable\");\n"
        "\t\terr = -ENODEV;\n"
        "\t\tgoto err_policer;\n"
        "\t}\n"
        "\n"
        "\terr = fman_pcd_kg_port_attach_policer(pcd, port_id,\n"
        "\t\t\t\t\t      DPAA_POLICE_PROFILE_ID);\n"
        "\tif (err) {\n"
        "\t\tNL_SET_ERR_MSG_MOD(extack, \"policer KeyGen attach failed\");\n"
        "\t\tnetif_err(priv, drv, net_dev,\n"
        "\t\t\t  \"policer KeyGen attach failed: %d\\n\", err);\n"
        "\t\tgoto err_policer;\n"
        "\t}\n"
        "\n"
        "\tpriv->ingress_police_installed = true;\n"
        "\treturn 0;\n"
        "\n"
        "err_policer:\n"
        "\tfman_policer_destroy(fm, port_id, DPAA_POLICE_PROFILE_ID);\n"
        "\treturn err;\n"
        "}\n"
        "\n"
        "static int dpaa_setup_tc_matchall_destroy(struct net_device *net_dev)\n"
        "{\n"
        "\tstruct dpaa_priv *priv = netdev_priv(net_dev);\n"
        "\tstruct fman *fm;\n"
        "\tu8 port_id;\n"
        "\n"
        "\tif (!priv->ingress_police_installed)\n"
        "\t\treturn 0;\n"
        "\n"
        "\tif (priv->mac_dev) {\n"
        "\t\t/* Reverse of _replace: restore the RX port's existing RSS hash\n"
        "\t\t * scheme to plain BMI-enqueue next-engine, then remove the\n"
        "\t\t * policer profile. The scheme itself is NOT destroyed - it is\n"
        "\t\t * the port's permanent RSS-hashing scheme (created at port\n"
        "\t\t * init); _replace only flipped its next-engine to the policer.\n"
        "\t\t */\n"
        "\t\tfm = fman_bind(priv->mac_dev->fman_dev);\n"
        "\t\tif (fm) {\n"
        "\t\t\tstruct fman_pcd *pcd = fman_get_pcd(fm);\n"
        "\n"
        "\t\t\tport_id = fman_port_get_id(priv->mac_dev->port[RX]);\n"
        "\t\t\tif (pcd)\n"
        "\t\t\t\tfman_pcd_kg_port_detach_policer(pcd, port_id);\n"
        "\t\t\tfman_policer_destroy(fm, port_id,\n"
        "\t\t\t\t\t     DPAA_POLICE_PROFILE_ID);\n"
        "\t\t}\n"
        "\t}\n"
        "\n"
        "\tpriv->ingress_police_installed = false;\n"
        "\treturn 0;\n"
        "}\n",
        "\tif (!act->police.rate_bytes_ps) {\n"
        "\t\tNL_SET_ERR_MSG_MOD(extack,\n"
        "\t\t\t\t   \"policer requires a positive byte rate\");\n"
        "\t\treturn -EOPNOTSUPP;\n"
        "\t}\n"
        "\tif (!act->police.burst) {\n"
        "\t\tNL_SET_ERR_MSG_MOD(extack,\n"
        "\t\t\t\t   \"policer requires a positive burst size\");\n"
        "\t\treturn -EOPNOTSUPP;\n"
        "\t}\n"
        "\tif (act->police.avrate) {\n"
        "\t\tNL_SET_ERR_MSG_MOD(extack,\n"
        "\t\t\t\t   \"avrate policing is not supported\");\n"
        "\t\treturn -EOPNOTSUPP;\n"
        "\t}\n"
        "\tif (act->police.overhead) {\n"
        "\t\tNL_SET_ERR_MSG_MOD(extack,\n"
        "\t\t\t\t   \"policer overhead is not supported\");\n"
        "\t\treturn -EOPNOTSUPP;\n"
        "\t}\n"
        "\tif (check_mul_overflow(act->police.rate_bytes_ps, 8ULL, &hw.cir_bps) ||\n"
        "\t    hw.cir_bps > 100000000000ULL) {\n"
        "\t\tNL_SET_ERR_MSG_MOD(extack,\n"
        "\t\t\t\t   \"policer rate exceeds hardware range\");\n"
        "\t\treturn -ERANGE;\n"
        "\t}\n"
        "\tif (!priv->mac_dev || !priv->mac_dev->fman_dev || !priv->mac_dev->port[RX])\n"
        "\t\treturn -ENODEV;\n"
        "\tfm = dev_get_drvdata(priv->mac_dev->fman_dev);\n"
        "\tif (!fm)\n"
        "\t\treturn -ENODEV;\n"
        "\tpcd = fman_get_pcd(fm);\n"
        "\tif (!pcd) {\n"
        "\t\tNL_SET_ERR_MSG_MOD(extack, \"FMan PCD subsystem unavailable\");\n"
        "\t\treturn -ENODEV;\n"
        "\t}\n"
        "\tport_id = fman_port_get_id(priv->mac_dev->port[RX]);\n"
        "\n"
        "\t/* F-329 (G10 Phase 4): relocate port-wide ingress policing to\n"
        "\t * HWP -> PLCR -> downstream (fmbm_rfpne = 0x004C0000, per-port physical\n"
        "\t * slot = port_id) so ASK2-engaged and disengaged ports are metered\n"
        "\t * identically without colliding across ports. Linux tc police passes\n"
        "\t * only green (conforming to both rate/burst and peakrate/mtu) and drops\n"
        "\t * yellow + red (drop_yellow = true).\n"
        "\t * Note: F-266 control-plane punt intentionally exits ahead of HWP->PLCR;\n"
        "\t * ports with F-323 soft-parser class dispatch override $nia directly. */\n"
        "\thw.color_aware = false;\n"
        "\thw.drop_yellow = true;\n"
        "\thw.cbs_bytes   = act->police.burst;\n"
        "\tif (act->police.peakrate_bytes_ps) {\n"
        "\t\tif (check_mul_overflow(act->police.peakrate_bytes_ps, 8ULL,\n"
        "\t\t\t\t       &hw.pir_bps) ||\n"
        "\t\t    hw.pir_bps > 100000000000ULL ||\n"
        "\t\t    hw.pir_bps < hw.cir_bps) {\n"
        "\t\t\tNL_SET_ERR_MSG_MOD(extack,\n"
        "\t\t\t\t\t   \"policer peakrate out of range or below rate\");\n"
        "\t\t\treturn -ERANGE;\n"
        "\t\t}\n"
        "\t\tif (!act->police.mtu) {\n"
        "\t\t\tNL_SET_ERR_MSG_MOD(extack,\n"
        "\t\t\t\t\t   \"peakrate policing requires a positive mtu\");\n"
        "\t\t\treturn -EOPNOTSUPP;\n"
        "\t\t}\n"
        "\t\t/* A460: FLS_L2 meters Ethernet + 802.1Q tag; add VLAN_HLEN so\n"
        "\t\t * mtu 1514 on a tagged WAN admits full-size 1518-byte frames. */\n"
        "\t\tif (check_add_overflow(act->police.mtu, (u32)VLAN_HLEN,\n"
        "\t\t\t\t       &hw.pbs_bytes)) {\n"
        "\t\t\tNL_SET_ERR_MSG_MOD(extack,\n"
        "\t\t\t\t\t   \"policer mtu exceeds hardware range\");\n"
        "\t\t\treturn -ERANGE;\n"
        "\t\t}\n"
        "\t\thw.trtcm     = true;\n"
        "\t} else {\n"
        "\t\thw.trtcm     = false;\n"
        "\t\thw.pir_bps   = hw.cir_bps;\n"
        "\t\thw.pbs_bytes = hw.cbs_bytes;\n"
        "\t}\n"
        "\n"
        "\terr = fman_port_plcr_attach(priv->mac_dev->port[RX], pcd,\n"
        "\t\t\t\t    port_id, &hw);\n"
        "\tif (err) {\n"
        "\t\tNL_SET_ERR_MSG_MOD(extack, \"policer attach failed\");\n"
        "\t\tnetif_err(priv, drv, net_dev,\n"
        "\t\t\t  \"ingress policer attach failed: %d\\n\", err);\n"
        "\t\treturn err;\n"
        "\t}\n"
        "\n"
        "\tpriv->ingress_police_installed = true;\n"
        "\tpriv->ingress_police_cookie = f->cookie;\n"
        "\tpriv->ingress_police_stats_valid =\n"
        "\t\t!fman_port_plcr_read_counters(priv->mac_dev->port[RX], pcd,\n"
        "\t\t\t\t\t      &priv->ingress_police_last_green,\n"
        "\t\t\t\t\t      &priv->ingress_police_last_yellow,\n"
        "\t\t\t\t\t      &priv->ingress_police_last_red);\n"
        "\treturn 0;\n"
        "}\n"
        "\n"
        "static int dpaa_setup_tc_matchall_destroy(struct net_device *net_dev)\n"
        "{\n"
        "\tstruct dpaa_priv *priv = netdev_priv(net_dev);\n"
        "\tstruct fman *fm;\n"
        "\n"
        "\tif (!priv->ingress_police_installed)\n"
        "\t\treturn 0;\n"
        "\n"
        "\tif (priv->mac_dev && priv->mac_dev->fman_dev && priv->mac_dev->port[RX]) {\n"
        "\t\tfm = dev_get_drvdata(priv->mac_dev->fman_dev);\n"
        "\t\tif (fm) {\n"
        "\t\t\tstruct fman_pcd *pcd = fman_get_pcd(fm);\n"
        "\n"
        "\t\t\tif (pcd)\n"
        "\t\t\t\tfman_port_plcr_detach(priv->mac_dev->port[RX], pcd);\n"
        "\t\t}\n"
        "\t}\n"
        "\n"
        "\tpriv->ingress_police_installed = false;\n"
        "\tpriv->ingress_police_cookie = 0;\n"
        "\tpriv->ingress_police_stats_valid = false;\n"
        "\treturn 0;\n"
        "}\n"
        "\n"
        "static u32 dpaa_police_counter_delta(u32 last, u32 now)\n"
        "{\n"
        "\treturn now >= last ? now - last : now;\n"
        "}\n"
        "\n"
        "static int dpaa_setup_tc_matchall_stats(struct net_device *net_dev,\n"
        "\t\t\t\t\tstruct tc_cls_matchall_offload *f)\n"
        "{\n"
        "\tstruct dpaa_priv *priv = netdev_priv(net_dev);\n"
        "\tstruct fman *fm;\n"
        "\tstruct fman_pcd *pcd;\n"
        "\tu32 g, y, r, dg, dy, dr;\n"
        "\tu64 total_pkts, total_drops;\n"
        "\tint err;\n"
        "\n"
        "\tif (!priv->ingress_police_installed ||\n"
        "\t    priv->ingress_police_cookie != f->cookie)\n"
        "\t\treturn -ENOENT;\n"
        "\tif (!priv->mac_dev || !priv->mac_dev->fman_dev || !priv->mac_dev->port[RX])\n"
        "\t\treturn -ENODEV;\n"
        "\tfm = dev_get_drvdata(priv->mac_dev->fman_dev);\n"
        "\tpcd = fm ? fman_get_pcd(fm) : NULL;\n"
        "\tif (!pcd)\n"
        "\t\treturn -ENODEV;\n"
        "\n"
        "\terr = fman_port_plcr_read_counters(priv->mac_dev->port[RX], pcd,\n"
        "\t\t\t\t\t   &g, &y, &r);\n"
        "\tif (err)\n"
        "\t\treturn err;\n"
        "\tif (!priv->ingress_police_stats_valid) {\n"
        "\t\tdg = dy = dr = 0;\n"
        "\t\tpriv->ingress_police_stats_valid = true;\n"
        "\t} else {\n"
        "\t\tdg = dpaa_police_counter_delta(priv->ingress_police_last_green, g);\n"
        "\t\tdy = dpaa_police_counter_delta(priv->ingress_police_last_yellow, y);\n"
        "\t\tdr = dpaa_police_counter_delta(priv->ingress_police_last_red, r);\n"
        "\t}\n"
        "\tpriv->ingress_police_last_green = g;\n"
        "\tpriv->ingress_police_last_yellow = y;\n"
        "\tpriv->ingress_police_last_red = r;\n"
        "\n"
        "\t/* FMPL counts frames per colour only (no byte counter). With\n"
        "\t * drop_yellow = true, both yellow and red frames are discarded. */\n"
        "\ttotal_pkts = (u64)dg + dy + dr;\n"
        "\ttotal_drops = (u64)dy + dr;\n"
        "\tflow_stats_update(&f->stats, 0, total_pkts, total_drops,\n"
        "\t\t\t  total_pkts ? jiffies : 0,\n"
        "\t\t\t  FLOW_ACTION_HW_STATS_IMMEDIATE);\n"
        "\treturn 0;\n"
        "}\n",
    ),
    (
        "wire TC_CLSMATCHALL_STATS and cookie check on DESTROY in dpaa_setup_tc_block_cb",
        "\tif (type == TC_SETUP_CLSMATCHALL) {\n"
        "\t\tswitch (f->command) {\n"
        "\t\tcase TC_CLSMATCHALL_REPLACE:\n"
        "\t\t\treturn dpaa_setup_tc_matchall_replace(net_dev, f);\n"
        "\t\tcase TC_CLSMATCHALL_DESTROY:\n"
        "\t\t\treturn dpaa_setup_tc_matchall_destroy(net_dev);\n"
        "\t\tdefault:\n"
        "\t\t\treturn -EOPNOTSUPP;\n"
        "\t\t}\n"
        "\t}\n",
        "\tif (type == TC_SETUP_CLSMATCHALL) {\n"
        "\t\tstruct dpaa_priv *priv = netdev_priv(net_dev);\n"
        "\n"
        "\t\tswitch (f->command) {\n"
        "\t\tcase TC_CLSMATCHALL_REPLACE:\n"
        "\t\t\treturn dpaa_setup_tc_matchall_replace(net_dev, f);\n"
        "\t\tcase TC_CLSMATCHALL_DESTROY:\n"
        "\t\t\tif (!priv->ingress_police_installed ||\n"
        "\t\t\t    priv->ingress_police_cookie != f->cookie)\n"
        "\t\t\t\treturn -ENOENT;\n"
        "\t\t\treturn dpaa_setup_tc_matchall_destroy(net_dev);\n"
        "\t\tcase TC_CLSMATCHALL_STATS:\n"
        "\t\t\treturn dpaa_setup_tc_matchall_stats(net_dev, f);\n"
        "\t\tdefault:\n"
        "\t\t\treturn -EOPNOTSUPP;\n"
        "\t\t}\n"
        "\t}\n",
    ),
]


A460_OLD = (
    "\t\thw.trtcm     = true;\n"
    "\t\thw.pbs_bytes = act->police.mtu;\n"
)
A460_NEW = (
    "\t\t/* A460: FLS_L2 meters Ethernet + 802.1Q tag; add VLAN_HLEN so\n"
    "\t\t * mtu 1514 on a tagged WAN admits full-size 1518-byte frames. */\n"
    "\t\tif (check_add_overflow(act->police.mtu, (u32)VLAN_HLEN,\n"
    "\t\t\t\t       &hw.pbs_bytes)) {\n"
    "\t\t\tNL_SET_ERR_MSG_MOD(extack,\n"
    "\t\t\t\t\t   \"policer mtu exceeds hardware range\");\n"
    "\t\t\treturn -ERANGE;\n"
    "\t\t}\n"
    "\t\thw.trtcm     = true;\n"
)


def apply_one(path: str, edits: list[tuple[str, str, str]]) -> None:
    with open(path, "r") as fh:
        text = fh.read()
    if MARK in text:
        upgraded = False
        if path == DPAA_ETH_C and A460_NEW not in text:
            if text.count(A460_OLD) != 1:
                sys.exit(f"### {MARK}: {path}: marker present but A460 hunk missing and upgrade anchor not unique")
            text = text.replace(A460_OLD, A460_NEW, 1)
            upgraded = True
        for label, _target, replacement in edits:
            if replacement not in text:
                sys.exit(f"### {MARK}: {path}: marker present but '{label}' missing")
        if upgraded:
            with open(path, "w") as fh:
                fh.write(text)
            print(f"### {MARK}: upgraded {path} with A460 VLAN_HLEN padding")
        else:
            print(f"### {MARK}: {path} already applied")
        return
    for label, target, replacement in edits:
        n = text.count(target)
        if n != 1:
            sys.exit(f"### {MARK}: {path}: anchor '{label}' matched {n} times (expected 1)")
        text = text.replace(target, replacement, 1)
    with open(path, "w") as fh:
        fh.write(text)
    print(f"### {MARK}: patched {path} ({len(edits)} anchor(s))")


def main() -> int:
    apply_one(FMAN_PCD_H, FMAN_PCD_H_EDITS)
    apply_one(FMAN_PCD_C, FMAN_PCD_C_EDITS)
    apply_one(FMAN_PCD_KG_C, FMAN_PCD_KG_C_EDITS)
    apply_one(FMAN_PORT_H, FMAN_PORT_H_EDITS)
    apply_one(FMAN_PORT_C, FMAN_PORT_C_EDITS)
    apply_one(DPAA_ETH_H, DPAA_ETH_H_EDITS)
    apply_one(DPAA_ETH_C, DPAA_ETH_C_EDITS)
    return 0


if __name__ == "__main__":
    sys.exit(main())
