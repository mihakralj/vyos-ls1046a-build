#!/bin/bash
# Patch DPDK process.c to add portal mmap after PORTAL_MAP ioctl
set -e

FILE="/opt/vyos-dev/dpaa-pmd/src/dpdk/drivers/bus/dpaa/base/qbman/process.c"
BACKUP="${FILE}.orig"

# Backup original
cp "$FILE" "$BACKUP"

# Create the patched version using python for reliable text manipulation
python3 << 'PYEOF'
import sys

with open("/opt/vyos-dev/dpaa-pmd/src/dpdk/drivers/bus/dpaa/base/qbman/process.c.orig", "r") as f:
    content = f.read()

# 1. Add #include <sys/mman.h> after #include <sys/ioctl.h>
content = content.replace(
    '#include <sys/ioctl.h>',
    '#include <sys/ioctl.h>\n#include <sys/mman.h>'
)

# 2. Add portal size defines and forward declaration before process_portal_map
portal_defs = '''
/*
 * Portal window sizes for QBMan portals on LS1046A (from DT reg properties).
 * Both BMan and QMan portals have 16KB CE and 16KB CI windows:
 *   qoriq-bman-portals.dtsi: reg = <0x0 0x4000>, <0x4000000 0x4000>
 *   qoriq-qman-portals.dtsi: reg = <0x0 0x4000>, <0x4000000 0x4000>
 *
 * On mainline kernels, PORTAL_MAP ioctl returns physical addresses.
 * DPDK must mmap() via the usdpaa fd to get virtual addresses with
 * correct page protection (CE=WB-NonShareable, CI=Device-nGnRnE).
 */
#define DPAA_PORTAL_CE_SIZE	0x4000   /* 16KB - from DTS reg property */
#define DPAA_PORTAL_CI_SIZE	0x4000   /* 16KB - from DTS reg property */

static int portal_mmap_phys(void **virt, uint64_t phys, size_t size);

'''

content = content.replace(
    'int process_portal_map(struct dpaa_ioctl_portal_map *params)\n{',
    portal_defs + 'int process_portal_map(struct dpaa_ioctl_portal_map *params)\n{'
)

# 3. Replace the simple "return 0;" in process_portal_map with mmap logic
# The old code is:
#   	return 0;
#   }
#   
#   int process_portal_unmap(

old_return = '''\treturn 0;
}

int process_portal_unmap('''

new_return = '''\t/*
\t * Mainline kernel returns physical addresses in addr.cena/cinh.
\t * Map them via the usdpaa fd mmap handler which sets correct pgprot.
\t */
\tret = portal_mmap_phys(&params->addr.cena,
\t\t\t       (uint64_t)(uintptr_t)params->addr.cena,
\t\t\t       DPAA_PORTAL_CE_SIZE);
\tif (ret) {
\t\tfprintf(stderr, "portal CE mmap failed: phys=%p\\n",
\t\t\tparams->addr.cena);
\t\treturn ret;
\t}

\tret = portal_mmap_phys(&params->addr.cinh,
\t\t\t       (uint64_t)(uintptr_t)params->addr.cinh,
\t\t\t       DPAA_PORTAL_CI_SIZE);
\tif (ret) {
\t\tfprintf(stderr, "portal CI mmap failed: phys=%p\\n",
\t\t\tparams->addr.cinh);
\t\tmunmap(params->addr.cena, DPAA_PORTAL_CE_SIZE);
\t\tparams->addr.cena = NULL;
\t\treturn ret;
\t}

\treturn 0;
}

/* Map a physical portal window via the usdpaa fd */
static int portal_mmap_phys(void **virt, uint64_t phys, size_t size)
{
\tvoid *addr;

\taddr = mmap(NULL, size, PROT_READ | PROT_WRITE, MAP_SHARED,
\t\t    fd, (off_t)phys);
\tif (addr == MAP_FAILED) {
\t\tperror("mmap(portal)");
\t\treturn -errno;
\t}

\t*virt = addr;
\treturn 0;
}

int process_portal_unmap('''

# Find the first occurrence after process_portal_map
# We need to be precise - find the return 0 that belongs to process_portal_map
idx = content.find('int process_portal_map(')
if idx < 0:
    print("ERROR: could not find process_portal_map", file=sys.stderr)
    sys.exit(1)

# Find "return 0;" after that position, then the closing } and next function
search_start = content.find(old_return, idx)
if search_start < 0:
    print("ERROR: could not find return pattern after process_portal_map", file=sys.stderr)
    sys.exit(1)

content = content[:search_start] + new_return + content[search_start + len(old_return):]

with open("/opt/vyos-dev/dpaa-pmd/src/dpdk/drivers/bus/dpaa/base/qbman/process.c", "w") as f:
    f.write(content)

print("Patch applied successfully")
PYEOF

echo "Verifying patch..."
grep -n "portal_mmap_phys\|DPAA_PORTAL_CE_SIZE\|sys/mman" "$FILE" | head -10
