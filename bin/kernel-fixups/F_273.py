"""F-273 (WGFP gate 2, 2026-11-04): export the ehash SYNC wedge counter.

fman_pcd_ehash_del_key() returns 0 when the F-256 SYNC timeout retains the
record, so callers cannot tell a synchronized delete from an unlinked but
possibly still in-flight one. ehash_sync_wedged (F-271) counts exhausted
SYNCs; export a reader so an in-kernel caller can compare it before and
after a delete (under the same serialisation as the delete) and detect the
unsynced outcome. Read-only: no register, descriptor or record change.

Must run after F-271 (the counter). Idempotent; exits non-zero unless each
anchor matches exactly once.
"""

import sys

MARK = "F-273"
C_PATH = "drivers/net/ethernet/freescale/fman/fman_pcd.c"
H_PATH = "include/linux/fsl/fman_pcd.h"

C_OLD = 'module_param_named(ehash_sync_wedged, ehash_sync_wedged, uint, 0444);\n'
C_NEW = C_OLD + (
    '\n/* F-273: SYNCs that exhausted every attempt; a delete that bumps this\n'
    ' * retained its record (F-256) and is not proven unlinked-and-synced. */\n'
    'unsigned int fman_pcd_ehash_sync_wedged_count(void)\n'
    '{\n'
    '\treturn READ_ONCE(ehash_sync_wedged);\n'
    '}\n'
    'EXPORT_SYMBOL_GPL(fman_pcd_ehash_sync_wedged_count);\n')

H_OLD = ('int fman_pcd_fe_flow_del(struct fman *fm, u8 hw_port_id,\n'
         '\t\t\t const u8 *key, u8 key_size);\n')
H_NEW = H_OLD + 'unsigned int fman_pcd_ehash_sync_wedged_count(void);\n'


def patch(path, old, new):
    try:
        with open(path) as f:
            s = f.read()
    except FileNotFoundError:
        print(f"### F-273: FATAL: {path} not found")
        sys.exit(1)
    if "fman_pcd_ehash_sync_wedged_count" in s:
        return False
    n = s.count(old)
    if n != 1:
        print(f"### F-273: FATAL: anchor in {path} found {n} times (expected 1)")
        sys.exit(1)
    with open(path, "w") as f:
        f.write(s.replace(old, new))
    return True


a = patch(C_PATH, C_OLD, C_NEW)
b = patch(H_PATH, H_OLD, H_NEW)
print("### F-273: ehash_sync_wedged reader exported" if a or b else
      "### F-273: already applied")
