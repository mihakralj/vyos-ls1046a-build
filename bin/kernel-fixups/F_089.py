"""F-089: §17 FE descriptor static asserts + KUnit test injection.

Injects two files into the kernel tree:
  1. fman-pcd-fe-static-asserts.h → drivers/net/ethernet/freescale/fman/
     Compile-time BUILD_BUG_ON guards for all 6 FE types, NIA encodings,
     and descriptor sizes.  Fails the build if any constant drifts.

  2. fman_pcd_fe_test.c → drivers/net/ethernet/freescale/fman/tests/
     KUnit suite (8 test cases) validating the above at KUnit time.

Both are copied verbatim from kernel/common/files/ in the repo.
The static-assert header is #included in fman_pcd.c via a sed injection
after the last FE type #define (FMAN_FE_TYPE_EXT_HASH).

Disposition: permanent-with-justification
  (until tree-canonical migration folds these into their owning patches)

Upstream-Status: Inappropriate [LS1046A Mono Gateway DK FE-VM]
Risk-Tier: A (new files only, no hot-path changes)
"""

import sys, shutil, os

repo_root = os.environ.get("GITHUB_WORKSPACE", os.getcwd())
kroot = "drivers/net/ethernet/freescale/fman"
changes = 0

# ── 1. Copy static-assert header ───────────────────────────────────────
hdr_src = os.path.join(repo_root, "kernel/common/files/fman-pcd-fe-static-asserts.h")
hdr_dst = os.path.join(kroot, "fman-pcd-fe-static-asserts.h")

if not os.path.exists(hdr_src):
    print(f"### F-089: WARNING — header source not found: {hdr_src}")
else:
    need_copy = True
    if os.path.exists(hdr_dst):
        with open(hdr_src) as f: src = f.read()
        with open(hdr_dst) as f: dst = f.read()
        if src == dst:
            need_copy = False
    if need_copy:
        shutil.copy2(hdr_src, hdr_dst)
        changes += 1
        print(f"### F-089: copied {os.path.basename(hdr_src)} → {hdr_dst}")
    else:
        print(f"### F-089: header already up to date")

# ── 2. Add #include to fman_pcd.c ───────────────────────────────────────
pcd_c = os.path.join(kroot, "fman_pcd.c")
if os.path.exists(pcd_c):
    with open(pcd_c) as f: src = f.read()

    include_line = '#include "fman-pcd-fe-static-asserts.h"'
    if include_line not in src:
        # Insert after the LAST FE #define — FMAN_FE_HASH_SIZE from patch 0131
        # (FMAN_FE_TYPE_* is from 0124, SIZE/FE_HASH_SIZE from 0131 — need the later one)
        anchor = "#define FMAN_FE_HASH_SIZE"
        if anchor not in src:
            # Fallback: use FMAN_FE_HASH_CONTEXT_SIZE
            anchor = "#define FMAN_FE_HASH_CONTEXT_SIZE"
        if anchor in src:
            idx = src.find(anchor)
            newline = src.index('\n', idx)
            src = src[:newline + 1] + '\n' + include_line + '\n' + src[newline + 1:]
            with open(pcd_c, 'w') as f: f.write(src)
            changes += 1
            print(f"### F-089: #include injected into fman_pcd.c after {anchor.split()[1]}")
        else:
            # Last resort: append at end before any KUnit guard
            print("### F-089: WARNING — no FE define anchor found; appending at EOF")
            src += '\n' + include_line + '\n'
            with open(pcd_c, 'w') as f: f.write(src)
            changes += 1
    else:
        print("### F-089: #include already present in fman_pcd.c")
else:
    print(f"### F-089: fman_pcd.c not found — skipping include injection")

# ── 3. Copy KUnit test file ─────────────────────────────────────────────
test_src = os.path.join(repo_root, "kernel/common/files/fman_pcd_fe_test.c")
test_dir = os.path.join(kroot, "tests")
test_dst = os.path.join(test_dir, "fman_pcd_fe_test.c")

if not os.path.exists(test_src):
    print(f"### F-089: WARNING — test source not found: {test_src}")
else:
    os.makedirs(test_dir, exist_ok=True)
    need_copy = True
    if os.path.exists(test_dst):
        with open(test_src) as f: src = f.read()
        with open(test_dst) as f: dst = f.read()
        if src == dst:
            need_copy = False
    if need_copy:
        shutil.copy2(test_src, test_dst)
        changes += 1
        print(f"### F-089: copied {os.path.basename(test_src)} → {test_dst}")
    else:
        print(f"### F-089: KUnit test already up to date")

# ── 4. Add KUnit #include trailer to fman_pcd.c ─────────────────────────
if os.path.exists(pcd_c):
    with open(pcd_c) as f: src = f.read()

    # Check if end-of-file KUnit guard already exists
    kunit_guard = '#if IS_ENABLED(CONFIG_FSL_FMAN_PCD_KUNIT_TEST)'
    test_include = '#include "tests/fman_pcd_fe_test.c"'

    if test_include not in src:
        if kunit_guard in src:
            # Add our test include inside the existing guard block
            # Find the guard block and add before #endif
            guard_end = '#endif /* CONFIG_FSL_FMAN_PCD_KUNIT_TEST */'
            if guard_end in src:
                insert = '\n' + test_include + '\n'
                idx = src.rfind(guard_end)
                src = src[:idx] + insert + src[idx:]
                with open(pcd_c, 'w') as f: f.write(src)
                changes += 1
                print(f"### F-089: KUnit #include added to existing guard block")
            else:
                # No existing #endif — add full guard block at end
                block = f'\n\n{kunit_guard}\n{test_include}\n#endif /* CONFIG_FSL_FMAN_PCD_KUNIT_TEST */\n'
                src += block
                with open(pcd_c, 'w') as f: f.write(src)
                changes += 1
                print(f"### F-089: KUnit guard block + #include added at EOF")
        else:
            # No existing guard — add at end of file
            block = f'\n\n{kunit_guard}\n{test_include}\n#endif /* CONFIG_FSL_FMAN_PCD_KUNIT_TEST */\n'
            src += block
            with open(pcd_c, 'w') as f: f.write(src)
            changes += 1
            print(f"### F-089: KUnit guard block added at EOF (no existing guard)")
    else:
        print("### F-089: KUnit test #include already present")
else:
    print(f"### F-089: fman_pcd.c not found — skipping KUnit wiring")

print(f"### F-089: total changes: {changes}")
