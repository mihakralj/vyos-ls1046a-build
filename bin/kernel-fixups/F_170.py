"""F-170: extend the F-072 hash_probe capture hook from eth4-only to eth3+eth4
(Task #26 follow-up -- eth3/port 0x10 PORT_ID characterization).

CONTEXT (2026-08-06): the annotation-hash-match technique (brute-force the
real hardware KG-computed CRC-64 against every plausible field ordering/value)
just found F-163's PORT_ID byte is wrong for eth4/port 0x11 -- silicon
extracts PORT_ID=0x00 for KG_SCH_KN_PORT_ID there, not the raw hw_port_id
(0x11) that ask_dpaa_get_fman_port_id() writes into the ehash flow record.

Open question: is 0x00 an eth4-specific value (implying a real but different
port-numbering convention, e.g. a partition-relative index) or a universal
always-zero/non-functional field regardless of port? Answering this requires
running the identical test on eth3/port 0x10 -- but F-072's capture hook
(drivers/net/ethernet/freescale/dpaa/dpaa_eth.c) is hardcoded to fire only
for net_dev->name == "eth4":

    if (!strcmp(net_dev->name, "eth4")) {
        fman_pcd_hash_off = hash_offset;
        fman_pcd_kg_hash = be64_to_cpu(*(__be64 *)(vaddr + hash_offset));
    }

This fixup extends that condition to also fire for "eth3", and additionally
records which interface the capture came from (a new global
fman_pcd_hash_ifname) so hash_probe's output is unambiguous when either
interface could have produced the last capture. Purely additive: no existing
capture behavior for eth4 changes, and no write path is touched -- this only
widens a read-only diagnostic hook.
"""

import sys

path = "drivers/net/ethernet/freescale/dpaa/dpaa_eth.c"
try:
    with open(path) as f:
        src = f.read()
except FileNotFoundError:
    print("### F-170: dpaa_eth.c not found")
    sys.exit(0)

changes = 0

old_capture = (
    '\n\t/* F-072 v7: capture hash for eth4 only */\n'
    '\tif (!strcmp(net_dev->name, "eth4")) {\n'
    '\t\tfman_pcd_hash_off = hash_offset;\n'
    '\t\tfman_pcd_kg_hash = be64_to_cpu(*(__be64 *)(vaddr + hash_offset));\n'
    '\t}'
)
new_capture = (
    '\n\t/* F-170: capture hash for eth3+eth4 (F-072 widened for port-0x10\n'
    '\t * PORT_ID characterization, Task #26 follow-up) */\n'
    '\tif (!strcmp(net_dev->name, "eth4") || !strcmp(net_dev->name, "eth3")) {\n'
    '\t\tfman_pcd_hash_off = hash_offset;\n'
    '\t\tfman_pcd_kg_hash = be64_to_cpu(*(__be64 *)(vaddr + hash_offset));\n'
    '\t\tstrscpy(fman_pcd_hash_ifname, net_dev->name,\n'
    '\t\t\tsizeof(fman_pcd_hash_ifname));\n'
    '\t}'
)

if 'strscpy(fman_pcd_hash_ifname' in src:
    print("### F-170: dpaa_eth.c already widened")
elif old_capture in src:
    src = src.replace(old_capture, new_capture, 1)
    changes += 1
    print("### dpaa_eth.c: F-170 capture widened to eth3+eth4")
else:
    print(
        "### F-170: FATAL: expected F-072 capture block not found verbatim "
        "in dpaa_eth.c -- F-072 may not have applied, or source has "
        "drifted. Refusing to guess."
    )
    sys.exit(1)

# extern + global for the new ifname buffer
if "fman_pcd_hash_ifname" not in src.split(new_capture)[0]:
    anchor = "extern unsigned int fman_pcd_hash_off;\n"
    if anchor in src:
        src = src.replace(
            anchor,
            anchor + "extern char fman_pcd_hash_ifname[16];\n",
            1,
        )
        changes += 1
        print("### dpaa_eth.c: F-170 extern added")

if changes:
    with open(path, "w") as f:
        f.write(src)
    print(f"### dpaa_eth.c: F-170 {changes} change(s) applied")
else:
    print("### dpaa_eth.c: F-170 no changes")
    sys.exit(1)

# --- fman_pcd.c: define the global + show it in hash_probe ---
pcd_path = "drivers/net/ethernet/freescale/fman/fman_pcd.c"
with open(pcd_path) as f:
    pcd_src = f.read()

pcd_changes = 0

if "fman_pcd_hash_ifname" not in pcd_src:
    anchor = "unsigned int fman_pcd_hash_off;\nvoid *fman_pcd_ic_vaddr;\n"
    if anchor in pcd_src:
        pcd_src = pcd_src.replace(
            anchor,
            anchor + 'char fman_pcd_hash_ifname[16] = "";\n',
            1,
        )
        pcd_changes += 1
        print("### fman_pcd.c: F-170 global defined")
    else:
        print("### F-170: FATAL: expected F-071 globals anchor not found in fman_pcd.c")
        sys.exit(1)

old_show = (
    '\tseq_printf(m, "hash_off=%u captured=%016llx\\n",\n'
    '\t\tfman_pcd_hash_off, fman_pcd_kg_hash);\n'
)
new_show = (
    '\tseq_printf(m, "hash_off=%u captured=%016llx if=%s\\n",\n'
    '\t\tfman_pcd_hash_off, fman_pcd_kg_hash,\n'
    '\t\tfman_pcd_hash_ifname[0] ? fman_pcd_hash_ifname : "?");\n'
)
if 'if=%s' in pcd_src:
    print("### fman_pcd.c: F-170 hash_probe_show already widened")
elif old_show in pcd_src:
    pcd_src = pcd_src.replace(old_show, new_show, 1)
    pcd_changes += 1
    print("### fman_pcd.c: F-170 hash_probe_show now prints ifname")
else:
    print("### F-170: FATAL: expected F-071 hash_probe_show body not found verbatim")
    sys.exit(1)

if pcd_changes:
    with open(pcd_path, "w") as f:
        f.write(pcd_src)
    print(f"### fman_pcd.c: F-170 {pcd_changes} change(s) applied")
else:
    print("### fman_pcd.c: F-170 no changes")
    sys.exit(1)
