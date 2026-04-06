#!/usr/bin/env python3
"""
inject-sdk-drivers.py — Inject NXP SDK DPAA1 drivers into a vanilla 6.6 kernel tree.

Extracts sdk-drivers.tar.zst into the kernel tree and patches parent
Kconfig/Makefile files to wire in the SDK build system.

The SDK replaces mainline DPAA1 drivers (fman, dpaa, qbman) with NXP's
full-featured SDK versions that include PCD (Parse, Classify, Distribute)
hardware classifier support needed for ASK fast-path.

Usage:
    python3 inject-sdk-drivers.py <kernel-tree-path> <tarball-path>

    e.g.: python3 inject-sdk-drivers.py /opt/vyos-dev/linux data/sdk-drivers.tar.zst
"""

import os
import sys
import re
import subprocess
import shutil

results = {"ok": 0, "skip": 0, "fail": 0}

def inject_line_after(filepath, anchor_re, new_line, label=""):
    """Insert new_line after the first line matching anchor_re."""
    if not os.path.exists(filepath):
        print(f"  FAIL {label}: {filepath} not found")
        results["fail"] += 1
        return False
    with open(filepath, "r") as f:
        lines = f.readlines()

    if any(new_line.strip() in line for line in lines):
        print(f"  SKIP {label}: already present")
        results["skip"] += 1
        return True

    for i, line in enumerate(lines):
        if re.search(anchor_re, line):
            lines.insert(i + 1, new_line + "\n")
            with open(filepath, "w") as f:
                f.writelines(lines)
            print(f"  OK   {label}")
            results["ok"] += 1
            return True

    print(f"  FAIL {label}: anchor not found: {anchor_re}")
    results["fail"] += 1
    return False

def inject_line_before(filepath, anchor_re, new_line, label=""):
    """Insert new_line before the first line matching anchor_re."""
    if not os.path.exists(filepath):
        print(f"  FAIL {label}: {filepath} not found")
        results["fail"] += 1
        return False
    with open(filepath, "r") as f:
        lines = f.readlines()

    if any(new_line.strip() in line for line in lines):
        print(f"  SKIP {label}: already present")
        results["skip"] += 1
        return True

    for i, line in enumerate(lines):
        if re.search(anchor_re, line):
            lines.insert(i, new_line + "\n")
            with open(filepath, "w") as f:
                f.writelines(lines)
            print(f"  OK   {label}")
            results["ok"] += 1
            return True

    print(f"  FAIL {label}: anchor not found: {anchor_re}")
    results["fail"] += 1
    return False

def append_if_missing(filepath, text, label=""):
    """Append text to file if not already present."""
    if not os.path.exists(filepath):
        print(f"  FAIL {label}: {filepath} not found")
        results["fail"] += 1
        return False
    with open(filepath, "r") as f:
        content = f.read()

    marker = text.strip().split("\n")[0].strip()
    if marker in content:
        print(f"  SKIP {label}: already present")
        results["skip"] += 1
        return True

    with open(filepath, "a") as f:
        f.write(text)
    print(f"  OK   {label}")
    results["ok"] += 1
    return True

def extract_tarball(kernel_dir, tarball_path):
    """Extract SDK driver tarball into kernel tree."""
    print("\n=== Phase 1: Extract SDK drivers ===")

    # Check if sdk_dpaa already exists
    sdk_dpaa = os.path.join(kernel_dir, "drivers/net/ethernet/freescale/sdk_dpaa")
    if os.path.isdir(sdk_dpaa):
        print(f"  SKIP extract: sdk_dpaa/ already exists")
        results["skip"] += 1
        return True

    if not os.path.exists(tarball_path):
        print(f"  FAIL extract: {tarball_path} not found")
        results["fail"] += 1
        return False

    # Extract preserving directory structure
    ret = subprocess.run(
        ["tar", "--zstd", "-xf", tarball_path, "-C", kernel_dir],
        capture_output=True, text=True
    )
    if ret.returncode != 0:
        print(f"  FAIL extract: {ret.stderr}")
        results["fail"] += 1
        return False

    # Verify extraction
    dirs = ["drivers/net/ethernet/freescale/sdk_dpaa",
            "drivers/net/ethernet/freescale/sdk_fman",
            "drivers/staging/fsl_qbman"]
    for d in dirs:
        full = os.path.join(kernel_dir, d)
        if os.path.isdir(full):
            count = sum(1 for _ in os.scandir(full) if _.is_file())
            print(f"  OK   {d}/ ({count} files)")
            results["ok"] += 1
        else:
            print(f"  FAIL {d}/ missing after extract")
            results["fail"] += 1

    return True

def patch_freescale_kconfig(kernel_dir):
    """Add SDK driver entries to drivers/net/ethernet/freescale/Kconfig."""
    print("\n=== Phase 2: Patch Kconfig files ===")

    fsl_kconfig = os.path.join(kernel_dir, "drivers/net/ethernet/freescale/Kconfig")

    # Add sdk_fman Kconfig source after the mainline fman entry
    inject_line_after(
        fsl_kconfig,
        r'source.*drivers/net/ethernet/freescale/fman/Kconfig',
        'source "drivers/net/ethernet/freescale/sdk_fman/Kconfig"',
        "freescale/Kconfig: sdk_fman source",
    )

    # Add sdk_dpaa Kconfig source after the mainline dpaa entry
    inject_line_after(
        fsl_kconfig,
        r'source.*drivers/net/ethernet/freescale/dpaa/Kconfig',
        'source "drivers/net/ethernet/freescale/sdk_dpaa/Kconfig"',
        "freescale/Kconfig: sdk_dpaa source",
    )

    # Add fsl_qbman to staging Kconfig
    staging_kconfig = os.path.join(kernel_dir, "drivers/staging/Kconfig")
    inject_line_before(
        staging_kconfig,
        r'^source.*drivers/staging/axis-fifo/Kconfig',
        'source "drivers/staging/fsl_qbman/Kconfig"',
        "staging/Kconfig: fsl_qbman source",
    )

def patch_freescale_makefile(kernel_dir):
    """Add SDK driver entries to Makefiles."""
    print("\n=== Phase 3: Patch Makefiles ===")

    fsl_makefile = os.path.join(kernel_dir, "drivers/net/ethernet/freescale/Makefile")

    # Add sdk_fman dir
    append_if_missing(
        fsl_makefile,
        "\n# NXP SDK FMan + DPAA drivers\n"
        "obj-$(if $(CONFIG_FSL_SDK_FMAN),y) += sdk_fman/\n"
        "obj-$(if $(CONFIG_FSL_SDK_DPAA_ETH),y) += sdk_dpaa/\n",
        "freescale/Makefile: sdk_fman + sdk_dpaa",
    )

    # Add fsl_qbman to staging Makefile
    staging_makefile = os.path.join(kernel_dir, "drivers/staging/Makefile")
    append_if_missing(
        staging_makefile,
        "\n# NXP SDK QBMan (BMan + QMan)\n"
        "obj-$(CONFIG_FSL_SDK_DPA)+= fsl_qbman/\n",
        "staging/Makefile: fsl_qbman",
    )

def apply_compat_fixes(kernel_dir):
    """Apply compatibility fixes for mainline 6.6 differences."""
    print("\n=== Phase 4: Compatibility fixes ===")

    # Fix 1: class_create(THIS_MODULE, name) → class_create(name)
    # Only in fman_test.c which may not be built, but fix it anyway
    fman_test = os.path.join(
        kernel_dir,
        "drivers/net/ethernet/freescale/sdk_fman/src/wrapper/fman_test.c"
    )
    if os.path.exists(fman_test):
        with open(fman_test, "r") as f:
            content = f.read()
        new_content = re.sub(
            r'class_create\s*\(\s*THIS_MODULE\s*,\s*',
            'class_create(',
            content
        )
        if new_content != content:
            with open(fman_test, "w") as f:
                f.write(new_content)
            print("  OK   fman_test.c: class_create compat fix")
            results["ok"] += 1
        else:
            print("  SKIP fman_test.c: class_create already fixed")
            results["skip"] += 1

    # Fix 2: Ensure fsl_hypervisor.h exists in include/linux/
    # The tarball includes it, but in case mainline already has one we don't clobber
    hv_header = os.path.join(kernel_dir, "include/linux/fsl_hypervisor.h")
    if os.path.exists(hv_header):
        print("  SKIP fsl_hypervisor.h: already exists")
        results["skip"] += 1
    else:
        # Create a minimal stub — the actual content from NXP isn't needed
        # since nothing in our build path actually uses hypervisor features
        with open(hv_header, "w") as f:
            f.write("""/* SPDX-License-Identifier: GPL-2.0 */
/* Stub for NXP SDK QBMan build — no hypervisor on LS1046A */
#ifndef _FSL_HYPERVISOR_H
#define _FSL_HYPERVISOR_H
struct fsl_hv_ioctl_restart { unsigned int ret; unsigned int param1; };
struct fsl_hv_ioctl_status { unsigned int ret; unsigned int param1; };
struct fsl_hv_ioctl_start { unsigned int ret; unsigned int param1; };
struct fsl_hv_ioctl_stop { unsigned int ret; unsigned int param1; };
struct fsl_hv_ioctl_memcpy { unsigned int ret; unsigned int param1; };
struct fsl_hv_ioctl_doorbell { unsigned int ret; unsigned int param1; };
struct fsl_hv_ioctl_prop { unsigned int ret; unsigned int param1; };
#endif
""")
        print("  OK   fsl_hypervisor.h: stub created")
        results["ok"] += 1

    # Fix 3: Add 'select PHYLINK' to FSL_SDK_FMAN menuconfig
    # Mainline FSL_FMAN does 'select PHYLINK' but SDK disables it.
    # Without this, PHYLINK stays =m (from other module selectors like MVPP2)
    # and SFP driver can't be built-in — breaking TFTP dev boot.
    sdk_fman_kconfig = os.path.join(
        kernel_dir,
        "drivers/net/ethernet/freescale/sdk_fman/Kconfig"
    )
    if os.path.exists(sdk_fman_kconfig):
        with open(sdk_fman_kconfig, "r") as f:
            content = f.read()
        if "select PHYLINK" not in content:
            new_content = content.replace(
                "depends on (FSL_SOC || ARM64 || ARM) && FSL_SDK_BMAN && FSL_SDK_QMAN && !FSL_FMAN\n",
                "depends on (FSL_SOC || ARM64 || ARM) && FSL_SDK_BMAN && FSL_SDK_QMAN && !FSL_FMAN\nselect PHYLINK\n",
            )
            if new_content != content:
                with open(sdk_fman_kconfig, "w") as f:
                    f.write(new_content)
                print("  OK   sdk_fman/Kconfig: added select PHYLINK")
                results["ok"] += 1
            else:
                print("  FAIL sdk_fman/Kconfig: could not insert select PHYLINK")
                results["fail"] += 1
        else:
            print("  SKIP sdk_fman/Kconfig: select PHYLINK already present")
            results["skip"] += 1

    # Fix 3: Ensure fsl_bman.h and fsl_qman.h are in include/linux/
    # These ARE needed by the SDK drivers and come from the tarball
    for hdr in ["fsl_bman.h", "fsl_qman.h", "fsl_usdpaa.h", "fsl_devices.h"]:
        path = os.path.join(kernel_dir, "include/linux", hdr)
        if os.path.exists(path):
            print(f"  OK   {hdr}: present")
            results["ok"] += 1
        else:
            print(f"  FAIL {hdr}: missing after extraction!")
            results["fail"] += 1

    # Fix 4: CONFIG_STAGING=y enables the staging directory build
    # This is handled in kernel config, not here — just verify Kconfig exists
    staging_kconfig = os.path.join(kernel_dir, "drivers/staging/fsl_qbman/Kconfig")
    if os.path.exists(staging_kconfig):
        print("  OK   staging/fsl_qbman/Kconfig: present")
        results["ok"] += 1
    else:
        print("  FAIL staging/fsl_qbman/Kconfig: missing!")
        results["fail"] += 1

def disable_mainline_dpaa_conflict(kernel_dir):
    """Ensure mainline DPAA1 drivers won't conflict with SDK."""
    print("\n=== Phase 5: Disable conflicting mainline drivers ===")

    # The config fragment (ls1046a-sdk.config) handles this via:
    #   # CONFIG_FSL_DPAA is not set
    #   # CONFIG_FSL_FMAN is not set
    # But we also need to make sure the mainline fman/dpaa Kconfig don't
    # interfere. Since they're 'source'd by the parent Kconfig, both
    # mainline and SDK entries exist — config selection determines which builds.
    print("  INFO Conflict resolution handled by kernel config (ls1046a-sdk.config)")
    print("  INFO Mainline DPAA disabled: CONFIG_FSL_DPAA=n, CONFIG_FSL_FMAN=n")
    print("  INFO SDK DPAA enabled: CONFIG_FSL_SDK_FMAN=y, CONFIG_FSL_SDK_DPAA_ETH=y")

def main():
    if len(sys.argv) < 3:
        print(f"Usage: {sys.argv[0]} <kernel-tree-path> <sdk-drivers-tarball>")
        print(f"  e.g.: {sys.argv[0]} /opt/vyos-dev/linux data/sdk-drivers.tar.zst")
        sys.exit(1)

    kernel_dir = sys.argv[1]
    tarball_path = sys.argv[2]

    if not os.path.isdir(os.path.join(kernel_dir, "drivers/net/ethernet/freescale")):
        print(f"ERROR: {kernel_dir} doesn't look like a kernel tree")
        sys.exit(1)

    print(f"Injecting NXP SDK DPAA1 drivers into: {kernel_dir}")
    print(f"From tarball: {tarball_path}")

    extract_tarball(kernel_dir, tarball_path)
    patch_freescale_kconfig(kernel_dir)
    patch_freescale_makefile(kernel_dir)
    apply_compat_fixes(kernel_dir)
    disable_mainline_dpaa_conflict(kernel_dir)

    print(f"\n=== Summary ===")
    print(f"  OK:   {results['ok']}")
    print(f"  SKIP: {results['skip']}")
    print(f"  FAIL: {results['fail']}")

    if results["fail"] > 0:
        print("\nSome steps failed. Check FAIL messages above.")
        sys.exit(1)
    else:
        print("\nSDK driver injection complete!")
        sys.exit(0)

if __name__ == "__main__":
    main()