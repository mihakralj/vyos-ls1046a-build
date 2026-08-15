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
"""

import sys

path = "drivers/net/ethernet/freescale/fman/fman_pcd.c"
with open(path) as f:
    src = f.read()

marker = "F-197(resolve-fqid-scheme-fallback)"
if marker in src:
    print("### F-197: resolver correction already applied")
    sys.exit(1)

old = """\tu32 params_fqid;\n\tunsigned int i;\n"""
new = """\tu32 params_fqid;\n\tu32 scheme_fqid = 0;\n\tbool scheme_fqid_ambiguous = false;\n\tunsigned int i;\n"""
if src.count(old) != 1:
    print(f"### F-197: FATAL: declaration anchor count is {src.count(old)}, expected 1")
    sys.exit(1)
src = src.replace(old, new, 1)

old = """\t\t\tif (READ_ONCE(scheme->used) &&\n\t\t\t    READ_ONCE(scheme->hw_port_id) == hw_port_id)\n\t\t\t\tdev_info_ratelimited(fman_get_dev(fman),\n\t\t\t\t\t     \"fe_flow: F-196 scheme=%u hw_port=0x%02x base_fqid=0x%x hash_fqs=0x%x hashing=%u next_engine=%u\\n\",\n\t\t\t\t\t     i, hw_port_id, READ_ONCE(scheme->base_fqid),\n\t\t\t\t\t     READ_ONCE(scheme->hash_fqid_count),\n\t\t\t\t\t     READ_ONCE(scheme->use_hashing),\n\t\t\t\t\t     READ_ONCE(scheme->next_engine));\n\t\t}\n\t}\n\n\treturn params_fqid;\n}\n"""
new = """\t\t\tif (READ_ONCE(scheme->used) &&\n\t\t\t    READ_ONCE(scheme->hw_port_id) == hw_port_id) {\n\t\t\t\tu32 base_fqid = READ_ONCE(scheme->base_fqid);\n\n\t\t\t\tdev_info_ratelimited(fman_get_dev(fman),\n\t\t\t\t\t     \"fe_flow: F-196 scheme=%u hw_port=0x%02x base_fqid=0x%x hash_fqs=0x%x hashing=%u next_engine=%u\\n\",\n\t\t\t\t\t     i, hw_port_id, base_fqid,\n\t\t\t\t\t     READ_ONCE(scheme->hash_fqid_count),\n\t\t\t\t\t     READ_ONCE(scheme->use_hashing),\n\t\t\t\t\t     READ_ONCE(scheme->next_engine));\n\t\t\t\tif (base_fqid && scheme_fqid &&\n\t\t\t\t    base_fqid != scheme_fqid)\n\t\t\t\t\tscheme_fqid_ambiguous = true;\n\t\t\t\telse if (base_fqid)\n\t\t\t\t\tscheme_fqid = base_fqid;\n\t\t\t}\n\t\t}\n\t}\n\n\t/* F-197(resolve-fqid-scheme-fallback): preserve a non-zero FM_CTL\n\t * params-page value.  A zero page field falls back only to a unique\n\t * non-zero base FQID from a used scheme bound to this ingress port.\n\t * Distinct candidates are unsafe: cross-port/wrong-FQ enqueue is a\n\t * proven drop path, so retain zero and make the ambiguity visible.\n\t */\n\tif (params_fqid)\n\t\treturn params_fqid;\n\tif (scheme_fqid_ambiguous) {\n\t\tdev_warn_ratelimited(fman_get_dev(fman),\n\t\t\t\t     \"fe_flow: F-197 ambiguous base FQID hw_port=0x%02x; refusing fallback\\n\",\n\t\t\t\t     hw_port_id);\n\t\treturn 0;\n\t}\n\tdev_info_ratelimited(fman_get_dev(fman),\n\t\t\t     \"fe_flow: F-197 resolved hw_port=0x%02x params_fqid=0x0 scheme_fqid=0x%x\\n\",\n\t\t\t     hw_port_id, scheme_fqid);\n\treturn scheme_fqid;\n}\n"""
if src.count(old) != 1:
    print(f"### F-197: FATAL: resolver-body anchor count is {src.count(old)}, expected 1")
    sys.exit(1)
src = src.replace(old, new, 1)

with open(path, "w") as f:
    f.write(src)

print("### fman_pcd.c: F-197 same-port KeyGen FQID fallback applied (2 blocks)")
