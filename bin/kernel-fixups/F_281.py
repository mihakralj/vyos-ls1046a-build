"""F-281: forbid sysfs unbind of FMan ports so their register mappings live as long as their users.

fman_port_driver has no .remove. fman_port_probe() maps the BMI/QMI window with
devm_ioremap()/__devm_request_region(), but the struct fman_port itself is
kzalloc'd and stays referenced by the MAC (fman_port_bind() in mac.c), the
fman_port_active_list and FMan PCD/FE arm code. A sysfs unbind would release
the devm mapping while those users keep port->bmi_regs/qmi_regs, so the next
fman_port_enable()/disable() or PCD port access faults on unmapped MMIO.
The driver is built in (=y), so after F-278/F-279 this was the last
DPAA1 sysfs unbind path left. Same contract as F-278: no unbind, no proof of
walker quiescence.
"""

import sys

PATH = "drivers/net/ethernet/freescale/fman/fman_port.c"
MARKER = "F-281: no sysfs unbind"

OLD = (
    "static struct platform_driver fman_port_driver = {\n"
    "\t.driver = {\n"
    "\t\t.name = \"fsl-fman-port\",\n"
    "\t\t.of_match_table = fman_port_match,\n"
    "\t},\n"
)

NEW = (
    "static struct platform_driver fman_port_driver = {\n"
    "\t.driver = {\n"
    "\t\t.name = \"fsl-fman-port\",\n"
    "\t\t.of_match_table = fman_port_match,\n"
    "\t\t/* F-281: no sysfs unbind; there is no .remove and the MAC/PCD\n"
    "\t\t * keep using the devm-mapped port registers after probe. */\n"
    "\t\t.suppress_bind_attrs = true,\n"
    "\t},\n"
)

try:
    with open(PATH) as stream:
        source = stream.read()
except FileNotFoundError:
    print(f"### F-281: FATAL: {PATH} not found")
    sys.exit(1)

if MARKER in source:
    if source.count(".suppress_bind_attrs = true,") < 1:
        print("### F-281: FATAL: marker present but suppress_bind_attrs missing")
        sys.exit(1)
    print("### F-281: already applied")
    sys.exit(0)

count = source.count(OLD)
if count != 1:
    print(f"### F-281: FATAL: anchor found {count} times (expected 1)")
    sys.exit(1)

if "devm_ioremap(port->dev" not in source:
    print("### F-281: FATAL: devm port register mapping not found")
    sys.exit(1)

source = source.replace(OLD, NEW, 1)
with open(PATH, "w") as stream:
    stream.write(source)
print("### F-281: applied")
