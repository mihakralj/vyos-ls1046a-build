"""F-197: resolve zero params-page FQID from the same-port KeyGen scheme.

F-195 supplies the true ingress hardware port, while F-193 showed that the
FM_CTL params page exists but its default-FQID field is zero for both active
ports.  F-186 already treats the bound KeyGen scheme's base_fqid as the
authoritative own-port enqueue target.  Use that same source only when the
params-page value is zero.

The fallback is deliberately conservative: only used schemes bound to the
requested hardware port participate; zero candidates are ignored; distinct
non-zero candidates are treated as ambiguous and leave the result at zero.
No FQID is hardcoded and a non-zero params-page value remains authoritative.

Self-contained as of 2026-08-24: the retired F-196 diagnostic previously
introduced the `params_fqid` declaration and scheme loop this fixup extends;
this fixup now applies both the declaration and the fallback in one block
against the pristine resolver.
"""

import sys

path = "drivers/net/ethernet/freescale/fman/fman_pcd.c"
with open(path) as f:
    src = f.read()

marker = "F-197(resolve-fqid-scheme-fallback)"
if marker in src:
    print("### F-197: resolver correction already applied")
    sys.exit(1)

old = """\tstruct muram_info *muram;
\tvoid __iomem *page;

\tif (!fman)
\t\treturn 0x200;

\tport = fman_port_lookup_rx(fman, hw_port_id);
\tif (!port)
\t\treturn 0x200;

\tparams_off = fman_port_get_params_page(port);
\tif (!params_off)
\t\treturn 0x200;

\tmuram = fman_get_muram(fman);
\tif (!muram)
\t\treturn 0x200;

\tpage = (void *)fman_muram_offset_to_vbase(muram, params_off);
\tif (!page)
\t\treturn 0x200;

\treturn ioread32be((u32 __iomem *)((u8 __iomem *)page +
\t\t\t\t\t  FMAN_PP_RX_DEFAULT_FQID_OFF));
}
"""
new = """\tstruct muram_info *muram;
\tvoid __iomem *page;
\tu32 params_fqid;
\tu32 scheme_fqid = 0;
\tbool scheme_fqid_ambiguous = false;
\tunsigned int i;

\tif (!fman)
\t\treturn 0x200;

\tport = fman_port_lookup_rx(fman, hw_port_id);
\tif (!port)
\t\treturn 0x200;

\tparams_off = fman_port_get_params_page(port);
\tif (!params_off)
\t\treturn 0x200;

\tmuram = fman_get_muram(fman);
\tif (!muram)
\t\treturn 0x200;

\tpage = (void *)fman_muram_offset_to_vbase(muram, params_off);
\tif (!page)
\t\treturn 0x200;

\tparams_fqid = ioread32be((u32 __iomem *)((u8 __iomem *)page +
\t\t\t\t\t       FMAN_PP_RX_DEFAULT_FQID_OFF));

\tif (fman->keygen) {
\t\tfor (i = 0; i < FM_KG_MAX_NUM_OF_SCHEMES; i++) {
\t\t\tstruct keygen_scheme *scheme = &fman->keygen->schemes[i];

\t\t\tif (READ_ONCE(scheme->used) &&
\t\t\t    READ_ONCE(scheme->hw_port_id) == hw_port_id) {
\t\t\t\tu32 base_fqid = READ_ONCE(scheme->base_fqid);

\t\t\t\tif (base_fqid && scheme_fqid &&
\t\t\t\t    base_fqid != scheme_fqid)
\t\t\t\t\tscheme_fqid_ambiguous = true;
\t\t\t\telse if (base_fqid)
\t\t\t\t\tscheme_fqid = base_fqid;
\t\t\t}
\t\t}
\t}

\t/* F-197(resolve-fqid-scheme-fallback): preserve a non-zero FM_CTL
\t * params-page value.  A zero page field falls back only to a unique
\t * non-zero base FQID from a used scheme bound to this ingress port.
\t * Distinct candidates are unsafe: cross-port/wrong-FQ enqueue is a
\t * proven drop path, so retain zero and make the ambiguity visible.
\t */
\tif (params_fqid)
\t\treturn params_fqid;
\tif (scheme_fqid_ambiguous) {
\t\tdev_warn_ratelimited(fman_get_dev(fman),
\t\t\t\t     "fe_flow: F-197 ambiguous base FQID hw_port=0x%02x; refusing fallback\\n",
\t\t\t\t     hw_port_id);
\t\treturn 0;
\t}
\tdev_info_ratelimited(fman_get_dev(fman),
\t\t\t     "fe_flow: F-197 resolved hw_port=0x%02x params_fqid=0x0 scheme_fqid=0x%x\\n",
\t\t\t     hw_port_id, scheme_fqid);
\treturn scheme_fqid;
}
"""
if src.count(old) != 1:
    print(f"### F-197: FATAL: resolver anchor count is {src.count(old)}, expected 1")
    sys.exit(1)
src = src.replace(old, new, 1)

with open(path, "w") as f:
    f.write(src)

print("### fman_pcd.c: F-197 same-port KeyGen FQID fallback applied (self-contained)")
