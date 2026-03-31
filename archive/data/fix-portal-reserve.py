#!/usr/bin/env python3
"""
Fix portal_reserve() to memunmap kernel CE mapping before userspace remap.

ROOT CAUSE #8: ARM64 memory attribute conflict
- Kernel maps portal CE as MEMREMAP_WC (Normal Non-Cacheable)
- Our usdpaa_mmap uses pgprot_cached_nonshared (Normal WB Non-Shareable)
- Two mappings of same physical page with different Normal cacheability
  on ARM64 = CONSTRAINED UNPREDICTABLE = kernel crash

FIX: memunmap the kernel CE mapping in portal_reserve().
After reservation, the portal is exclusively owned by userspace.
CI mapping is kept because the kernel IRQ handler needs it for IIR writes.
"""
import sys

# === Fix bman_portal.c ===
with open("/opt/vyos-dev/linux/drivers/soc/fsl/qbman/bman_portal.c", "r") as f:
    content = f.read()

old_reserve = """\tlist_del(&pcfg->list);
\tpcfg->reserved = true;
\tspin_unlock(&bman_free_lock);
\t*pcfg_out = pcfg;
\treturn 0;"""

new_reserve = """\tlist_del(&pcfg->list);
\tpcfg->reserved = true;
\tspin_unlock(&bman_free_lock);

\t/*
\t * Unmap kernel CE mapping to avoid ARM64 memory attribute conflict.
\t * Kernel maps CE as MEMREMAP_WC (Normal-NC), but userspace needs
\t * Normal-WB-NS for QBMan stashing. Two mappings with different
\t * Normal cacheability on ARM64 = CONSTRAINED UNPREDICTABLE.
\t * CI mapping kept: IRQ handler needs it for IIR write at CI+0xE0C.
\t */
\tif (pcfg->addr_virt_ce) {
\t\tmemunmap(pcfg->addr_virt_ce);
\t\tpcfg->addr_virt_ce = NULL;
\t}

\t*pcfg_out = pcfg;
\treturn 0;"""

if old_reserve not in content:
    print("ERROR: Could not find bman reserve pattern", file=sys.stderr)
    sys.exit(1)
content = content.replace(old_reserve, new_reserve)

with open("/opt/vyos-dev/linux/drivers/soc/fsl/qbman/bman_portal.c", "w") as f:
    f.write(content)
print("bman_portal.c patched - memunmap CE in reserve()")

# === Fix qman_portal.c ===
with open("/opt/vyos-dev/linux/drivers/soc/fsl/qbman/qman_portal.c", "r") as f:
    content = f.read()

old_qreserve = """\tlist_del(&pcfg->list);
\tpcfg->reserved = true;
\tspin_unlock(&qman_free_lock);
\t*pcfg_out = pcfg;
\treturn 0;"""

new_qreserve = """\tlist_del(&pcfg->list);
\tpcfg->reserved = true;
\tspin_unlock(&qman_free_lock);

\t/*
\t * Unmap kernel CE mapping to avoid ARM64 memory attribute conflict.
\t * Same rationale as bman_portal_reserve().
\t */
\tif (pcfg->addr_virt_ce) {
\t\tmemunmap(pcfg->addr_virt_ce);
\t\tpcfg->addr_virt_ce = NULL;
\t}

\t*pcfg_out = pcfg;
\treturn 0;"""

if old_qreserve not in content:
    print("ERROR: Could not find qman reserve pattern", file=sys.stderr)
    sys.exit(1)
content = content.replace(old_qreserve, new_qreserve)

with open("/opt/vyos-dev/linux/drivers/soc/fsl/qbman/qman_portal.c", "w") as f:
    f.write(content)
print("qman_portal.c patched - memunmap CE in reserve()")
