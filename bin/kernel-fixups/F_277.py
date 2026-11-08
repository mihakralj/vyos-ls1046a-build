"""F-277: stop F-211 IPv6 KeyGen changes when RX-port disable fails."""

import sys

PATH = "drivers/net/ethernet/freescale/fman/fman_pcd_kg.c"
MARKER = "F-277: abort on a disable error before changing KG/parser state."

OLD = (
    "\t\t * only (PCAC + BMI are per-port); the sibling stays up. Re-enabled at\n"
    "\t\t * every exit below and after the F-212 split. fman_port_disable()\n"
    "\t\t * returns 0 even on drain timeout ('forced down'), so treat any\n"
    "\t\t * return as quiesced. */\n"
    "\t\t(void)fman_port_disable(rxport);\n"
    "\n"
    "\t\tmutex_lock(lock);\n"
)

NEW = (
    "\t\t * only (PCAC + BMI are per-port); the sibling stays up. Re-enabled\n"
    "\t\t * on normal exit paths; errors abort without re-enabling it. */\n"
    "\t\t/* F-277: abort on a disable error before changing KG/parser state.\n"
    "\t\t * F-274 forces the port down on -EBUSY; keep FE state allocated and\n"
    "\t\t * do not re-enable it. A zero return is not proof that DDR ehash\n"
    "\t\t * walkers have drained. */\n"
    "\t\tverr = fman_port_disable(rxport);\n"
    "\t\tif (verr) {\n"
    "\t\t\tpr_err(\"fman_pcd fe_arm: F-277 port 0x%02x disable failed (%d); v6 arm aborted\\n\",\n"
    "\t\t\t       hw_port_id, verr);\n"
    "\t\t\treturn verr;\n"
    "\t\t}\n"
    "\n"
    "\t\tmutex_lock(lock);\n"
)

try:
    with open(PATH) as stream:
        source = stream.read()
except FileNotFoundError:
    print(f"### F-277: FATAL: {PATH} not found")
    sys.exit(1)

if MARKER in source:
    if (
        "verr = fman_port_disable(rxport);" not in source
        or "if (verr) {" not in source
        or "return verr;" not in source
    ):
        print("### F-277: FATAL: marker present but guard is missing")
        sys.exit(1)
    print("### F-277: already applied")
    sys.exit(0)

count = source.count(OLD)
if count != 1:
    print(f"### F-277: FATAL: anchor found {count} times (expected 1)")
    sys.exit(1)

source = source.replace(OLD, NEW, 1)
with open(PATH, "w") as stream:
    stream.write(source)

print("### F-277: F-211 aborts scheme/parser writes when port quiesce fails")
