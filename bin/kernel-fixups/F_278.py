"""F-278: forbid sysfs unbind of the FMan parent so PCD release cannot run live.

fman_pcd_release() runs from a void devm action, so it cannot veto driver-core
teardown after an ehash drain fails. PCD init is the last step of fman_probe(),
and FMan is built in (=y). The board also disables OF_OVERLAY, while FMan and
DPAA suppress sysfs bind attrs, so supported runtime unbind, module unload and
overlay removal paths are unavailable. The devres action remains reachable on
probe failure, before dependent netdev consumers can engage PCD.
"""

import sys

PATH = "drivers/net/ethernet/freescale/fman/fman.c"
MARKER = "F-278: no sysfs unbind"

OLD = (
    "static struct platform_driver fman_driver = {\n"
    "\t.driver = {\n"
    "\t\t.name = \"fsl-fman\",\n"
    "\t\t.of_match_table = fman_match,\n"
    "\t},\n"
)

NEW = (
    "static struct platform_driver fman_driver = {\n"
    "\t.driver = {\n"
    "\t\t.name = \"fsl-fman\",\n"
    "\t\t.of_match_table = fman_match,\n"
    "\t\t/* F-278: no sysfs unbind; board config disables OF_OVERLAY\n"
    "\t\t * because fman_pcd_release() cannot veto live devres teardown. */\n"
    "\t\t.suppress_bind_attrs = true,\n"
    "\t},\n"
)

try:
    with open(PATH) as stream:
        source = stream.read()
except FileNotFoundError:
    print(f"### F-278: FATAL: {PATH} not found")
    sys.exit(1)

if MARKER in source:
    if source.count(".suppress_bind_attrs = true,") < 1:
        print("### F-278: FATAL: marker present but suppress_bind_attrs missing")
        sys.exit(1)
    print("### F-278: already applied")
    sys.exit(0)

count = source.count(OLD)
if count != 1:
    print(f"### F-278: FATAL: anchor found {count} times (expected 1)")
    sys.exit(1)

if source.count("fman_pcd_release_action, pcd)") != 1:
    print("### F-278: FATAL: devm PCD release registration not found once")
    sys.exit(1)

source = source.replace(OLD, NEW, 1)
with open(PATH, "w") as stream:
    stream.write(source)
print("### F-278: applied")
