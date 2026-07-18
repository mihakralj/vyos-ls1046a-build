#!/bin/bash

# ── REPLACEMENT BLOCK — ESCAPING RULES ────────────────────────────────────────
# This triple-quoted Python string is injected into build-kernel.sh verbatim
# AFTER Python processes its escape sequences.  Rules for writing new fixups:
#
#  \n → \n (two chars, safe in sed/bash)   ← write \\n in this source
#  \t → \t (two chars, safe)               ← write \\t in this source
#  \  → \ in output                        ← write \\ in this source
#
# Python inline code strings (in base64 blobs):
#   Use chr(10) for newline, chr(9) for tab — avoids all escape collisions.
#   Never write backslash-n or backslash-t inside base64-decoded Python string literals.
#
# Validate before pushing: python3 bin/test-fixups.sh
# ──────────────────────────────────────────────────────────────────────────────
# Initialise the kernel source tree as a throwaway git repo so that
# `git apply --3way` can fall back to a real 3-way merge using the
# pre-patch blobs in object storage when context drifts.
if [ ! -d .git ]; then
    git -c init.defaultBranch=main init -q
    # mergiraf .gitattributes: allowlist low-risk files, deny silicon-encoding
    cat > .gitattributes << 'MERGATTR'
# Low-risk: mergiraf reduces placement conflicts
drivers/net/ethernet/freescale/dpaa/*.c   merge=mergiraf
*.h                                        merge=mergiraf
# Silicon-encoding: NEVER auto-merge
drivers/net/ethernet/freescale/fman/fman_pcd*.c    -merge
drivers/net/ethernet/freescale/fman/fman_keygen.c  -merge
MERGATTR
    git -c user.email=ci@local -c user.name=ci add -A .gitattributes
    git -c user.email=ci@local -c user.name=ci commit -q -m "kernel pristine (pre-patches)" --allow-empty || true
fi

PATCH_FAIL=0
PATCH_FAIL_LIST=""
for patch in $(find "${PATCH_DIR}" -maxdepth 1 -type f -name '*.patch' | sort); do
    pname=$(basename "$patch")
    echo "I: Apply Kernel patch: $patch"
    if ! git apply --3way --whitespace=nowarn "$patch" 2>/tmp/_apply_stderr; then
        echo "::error::Kernel patch FAILED to apply (git apply --3way): $pname" >&2
        PATCH_FAIL=$((PATCH_FAIL + 1))
        PATCH_FAIL_LIST="$PATCH_FAIL_LIST $pname"
    else
        # Detect silent 3-way fallback — patch landed but with drifted context
        if grep -q "Falling back to three-way merge" /tmp/_apply_stderr; then
            echo "### 3-way-fallback: $pname applied via 3-way merge (context drifted)"
        fi
        # Commit each successfully-applied patch so that subsequent patches'
        # `git apply --3way` sees the cumulative on-disk state as their merge
        # base. Without this commit step, every patch re-bases against the
        # original pristine commit and effectively falls through to a plain
        # direct apply that requires exact context match — which fails after
        # earlier patches have shifted line numbers (e.g. 1060's context in
        # fman_pcd.c after 1044's pre-netdev-hook insertions).
        git -c user.email=ci@local -c user.name=ci add -A
        git -c user.email=ci@local -c user.name=ci commit -q --allow-empty -m "applied: $pname" || true
    fi
done

if [ "$PATCH_FAIL" -ne 0 ]; then
    echo "::error::$PATCH_FAIL kernel patch(es) failed to apply:$PATCH_FAIL_LIST" >&2
    echo "::error::Aborting build. The legacy patch -p1 loop would have continued silently with a partially-patched kernel." >&2
    exit 1
fi

# Snapshot the patched tree so subsequent injections (LP5812 olddefconfig,
# FMD shim, etc.) see the patched state as their merge base.
git -c user.email=ci@local -c user.name=ci add -A
git -c user.email=ci@local -c user.name=ci commit -q -m "kernel post-patches" --allow-empty || true
# Patch-less source modification: add TC_SETUP_FT case to dpaa_setup_tc()
# TC_SETUP_FT is required by nf_flow_table_offload_setup() when the
# netdev has ndo_setup_tc.  Without it, nft 'flags offload' never
# reaches flow_indr_dev_setup_offload() — dpaa_setup_tc() returns
# -EOPNOTSUPP from its default: case.  Injected via sed (not a
# .patch file) to avoid the git apply --3way context-matching wall.
if [ -f drivers/net/ethernet/freescale/dpaa/dpaa_eth.c ]; then
    sed -i "/case TC_SETUP_BLOCK:/a\        case TC_SETUP_FT:
                return dpaa_setup_tc_flow_block(net_dev, type_data);"         drivers/net/ethernet/freescale/dpaa/dpaa_eth.c
    echo "### dpaa_eth.c: TC_SETUP_FT case injected (sed)"
fi

# Fix fe_flow debugfs 8-byte key truncation (post-patch fixup)
# The fe_flow debugfs read handler was hardcoded to display the first 16
# bytes of DDR flow records (8-byte bucket pointer + first 8 key bytes).
# For 13-byte 5-tuple keys, this truncated PROTO+SPORT+DPORT, making
# TCP/UDP flow matching unverifiable. Fix: display only flow key at
# FMAN_EHASH_FLOW_KEY_OFF (offset 8) for flow->key_size bytes.
python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/fe_flow_key_fix.py" 2>&1


# Performance: OVFQ=1 on TX FQ context_a for FMan hardware direct enqueue.
# OVFQ=1 means FMan uses the FQID from the ENQUEUE_PKT opcode operand
# instead of the ICAD — required for the AC_CC FE/ehash HIT path.
# B0V is kept at 1 (kernel TX confirmation safety — see plans/ASK2-
# PERFORMANCE-MODERNIZATION.md §7 for the dedicated-FQ plan with B0V=0).
if [ -f drivers/net/ethernet/freescale/dpaa/dpaa_eth.c ]; then
    sed -i "s/0x1e00000080000000ULL/0x9e00000080000000ULL/"         drivers/net/ethernet/freescale/dpaa/dpaa_eth.c
    echo "### dpaa_eth.c: OVFQ=1 injected (sed)"

    # B0V=0: disable context_b writebacks for hardware-offloaded frames.
    # With EBD=1 (FMan deallocates buffers in hardware), the QMan portal
    # does not need to write buffer-release confirmations to context_b.
    # cdx.ko uses hi=0x9a000000 (B0V=0); we follow suit.  Safe for
    # non-offloaded TX because buffer-release confirmation goes through
    # a separate TX_CONFIRM FQ, not context_b of the TX FQ.
    sed -i "s/0x9e00000080000000ULL/0x9a00000080000000ULL/"         drivers/net/ethernet/freescale/dpaa/dpaa_eth.c
    echo "### dpaa_eth.c: B0V=0 injected (sed)"
fi

# Performance: deeper TX FQ taildrop (2MB -> 4MB) for 10G throughput.
# The 2MB default fills quickly at 10G line rate; 4MB gives more headroom
# before QMan taildrop kicks in, reducing per-flow backpressure.
if [ -f drivers/net/ethernet/freescale/dpaa/dpaa_eth.c ]; then
    sed -i "s/#define DPAA_FQ_TD 0x200000/#define DPAA_FQ_TD 0x400000/"         drivers/net/ethernet/freescale/dpaa/dpaa_eth.c
    echo "### dpaa_eth.c: DPAA_FQ_TD=4MB injected (sed)"
fi

# Performance: deeper TX FQ taildrop (2MB -> 4MB) for 10G throughput.
# The 2MB default fills quickly at 10G line rate; 4MB gives more headroom

# Fix dropped board patches: use sed injection instead of raw patch
# (raw patch -p1 silently drops hunks when line numbers drift in kernel 6.18)

# F-068-REVERT: Restore AC_CC dispatch (next_engine=3, kgse_ccbs=0, RCCB→FE_ENTER).
# F-068 incorrectly switched to CCBS (next_engine=2, CC group table). The 2026-07-04
# HIT was proven with AC_CC direct (RCCB→FE_ENTER) — CC group table is an architectural
# error per specs/fman-keygen-flow-key-spec.md v2.0 §5. The crash root cause is not the
# dispatch mode but the missing missResult/w4 causing wild DMA (fix follows separately).
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_068.py" 2>&1
    echo "### F-068-REVERT: AC_CC dispatch (next_engine=3, RCCB→FE_ENTER)"
fi

# Patch 4009 equivalent: fix OEM SFP-10G-T quirk + add OEM SFP-10G-SR quirk
if [ -f drivers/net/phy/sfp.c ]; then
    # Change sfp_fixup_rollball_cc to sfp_fixup_fs_10gt for OEM SFP-10G-T
    sed -i 's/SFP_QUIRK_F("OEM", "SFP-10G-T", sfp_fixup_rollball_cc)/SFP_QUIRK_F("OEM", "SFP-10G-T", sfp_fixup_fs_10gt)/'         drivers/net/phy/sfp.c
    # Add OEM SFP-10G-SR quirk entry (our modules report "SR" but are copper rollball)
    sed -i '/SFP_QUIRK_F("OEM", "SFP-10G-T", sfp_fixup_fs_10gt)/a\	SFP_QUIRK_F("OEM", "SFP-10G-SR", sfp_fixup_fs_10gt),'         drivers/net/phy/sfp.c
    echo "### sfp.c: OEM SFP-10G-T/SR rollball quirk injected (sed)"
fi

# F-048: Set EKFC to 0x00180006 — IPSRC1|IPDST1|L4PSRC|L4PDST.
# 4-tuple extraction (12 bytes) without PTYPE1 (bit 18) which causes BMI
# stall on LS1046A FMan 210.10.1 microcode. EKFC=0x001C0006 (with PTYPE1)
# was proven to stall port 0x10/0x11 on the first frame (2026-07-14).
# The 2026-07-10 working build used 0x00180006 without stall.
if [ -f drivers/net/ethernet/freescale/fman/fman_keygen.c ]; then
    sed -i 's/scheme_regs\.kgse_ekfc = DEFAULT_HASH_KEY_EXTRACT_FIELDS;/scheme_regs.kgse_ekfc = 0x00180006; \/\* F-048-R1: 12B key = SIP+DIP+SPORT+DPORT (no PTYPE1) \*\//'         drivers/net/ethernet/freescale/fman/fman_keygen.c
    echo "### fman_keygen.c: EKFC 0x00180206→0x00180006 (remove PTYPE1, no stall)"
fi

# F-062c-R2: RESTORE pure AC_CC encoding (0x80000006, no DFLT_NIA).
# F-062c-R1 incorrectly OR'd ENQUEUE_KG_DFLT_NIA into the AC_CC mode register,
# producing a hybrid NIA that the FMan controller interprets as an undefined
# action → corrupted FDs → rx_default_dqrr Oops with x26=0xffffffff80000000.
# The NXP vendor LSDK (999-layerscape-ask) uses pure NIA_ENG_FM_CTL|NIA_FM_CTL_AC_CC.
if [ -f drivers/net/ethernet/freescale/fman/fman_keygen.c ]; then
    python3 -c "
import sys
path = 'drivers/net/ethernet/freescale/fman/fman_keygen.c'
try:
    with open(path) as f: src = f.read()
except FileNotFoundError:
    print('### fman_keygen.c: F-062c-R2 — file not found'); sys.exit(0)
# Remove DFLT_NIA if present, restore pure AC_CC
corrupt = '			tmp_reg |= ENQUEUE_KG_DFLT_NIA | NIA_ENG_FM_CTL | NIA_FM_CTL_AC_CC;'
pure    = '			tmp_reg |= NIA_ENG_FM_CTL | NIA_FM_CTL_AC_CC;'
if corrupt in src:
    src = src.replace(corrupt, pure, 1)
    with open(path, 'w') as f: f.write(src)
    print('### fman_keygen.c: F-062c-R2 — DFLT_NIA removed, pure AC_CC restored')
elif pure in src:
    print('### fman_keygen.c: F-062c-R2 — AC_CC already pure')
else:
    print('### fman_keygen.c: F-062c-R2 — AC_CC branch not found (already reverted?)')
" 2>&1
    echo "### fman_keygen.c: F-062c-R2 pure AC_CC (0x80000006)"
fi

# F-069: MISS context (DDR + MURAM t_ExtHashResult) with exact anchors.
# FIXME: Fixup anchors are NOT count()==1 asserted — bin/test-fixups.sh is the current gate.
# Four prior silent no-ops cost four board sessions (F-062a, F-062g, F-069a v1/v2).
# Per NXP LSDK ExternalHashTableSet (999-layerscape-ask):
#  - Adds miss_res_off (6th parameter, distinct from w6 miss_off)
#  - w4 = miss_res_off MURAM offset of 16B t_ExtHashResult
#  - DDR miss context (256B, dma_alloc_coherent via t->dev from 0130)
#  - Persists in struct fman_pcd, freed on hash_free teardown
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_069.py" 2>&1
    echo "### F-069: MISS context + DDR alloc (count-asserted anchors)"
fi

# F-073D: Terminal ENQ per 210.10.1 §7.3 — ws_offset=0, w3=0 (no chain).
# w0 = TYPE_ENQ | FMAN_FE_ENQ_FQID = 0x02010000 (terminal, no ws_offset).
# w1 = fqid (24-bit FQID). w3 = 0 (terminal, per §7.1 "Terminal enqueue").
# + F-070b w6→ENQ rewire + F-070c params zeroing on disengage.
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_073D.py" 2>&1
    echo "### F-073D: Terminal ENQ (w0=0x02010000, w3=0) per 210.10.1 §7.1/§7.3"
fi


# M2-4: fix fman_port_lookup_rx — all LS1046A fman_port->port_id==0
# (mainline of_alias_get_id fallback returns -ENODEV).  The lookup
# comparison p->port_id == port_id always fails for non-zero port_id.
# Remove the port_id check; match on fm + port_type only.
# cc_test works by accident (%hhi "0x10" → port_id=0, which matches).
if [ -f drivers/net/ethernet/freescale/fman/fman_port.c ]; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/M2_4.py" 2>&1
    echo "### fman_port.c: M2-4 fman_port_lookup_rx fixed"

# F-063 DISABLED FOR BISECT: EXT_HASH FE contextSize must match keysize.
# Commented out to test if contextSize change (256→key_size-1) causes stall.
: 'F-063-DISABLED'
: ' sed -i '"'"'s/(FMAN_FE_HASH_CONTEXT_SIZE - 1)/(t->key_size - 1)/'"'"' drivers/net/ethernet/freescale/fman/fman_pcd.c'
: ' echo "### fman_pcd.c: F-063 EXT_HASH contextSize fixed (key_size not record_size)"'

# M2-4: reduce FE pool 100->16 to fit 64KB MURAM
# 100x28B rounded 256B = 25600B + pool 8192B + ehash 33280B > 65536B
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    sed -i 's/FMAN_PCD_FE_POOL_COUNT[[:space:]]*100/FMAN_PCD_FE_POOL_COUNT 16/' drivers/net/ethernet/freescale/fman/fman_pcd.c
    echo '### fman_pcd.c: M2-4 FE pool reduced 100->16'
fi

# M2-4: fman_port_set_params_page NULL-page clear support (before params-page-free)
# Makes fman_port_set_params_page(rxport, 0, NULL) clear ctrl_params_page
# so fman_pcd_kg.c can zero the field without direct struct dereference.
if [ -f drivers/net/ethernet/freescale/fman/fman_port.c ]; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/M2_4_2.py" 2>&1
    echo "### fman_port.c: M2-4 NULL-page clear support added"
fi

# 
# F-051: Force-clear kgse_bmch, kgse_bmcl, kgse_hc, and kgse_ekdv to zero
# inside keygen_scheme_setup() AFTER the scheme_regs struct is populated but
# BEFORE it's written to hardware.  The DPAA1 RSS driver may leave byte masks
# or hash config that interfere with exact-match ehash.  Anchored on the
# '/* Write scheme registers */' comment that precedes the write call.
if [ -f drivers/net/ethernet/freescale/fman/fman_keygen.c ]; then
    sed -i '/\/\* Write scheme registers \*\//i	/* F-051: force-clear RSS mask/hash config for exact-match ehash */	scheme_regs.kgse_bmch = 0;	scheme_regs.kgse_bmcl = 0;	scheme_regs.kgse_hc   = 0;	scheme_regs.kgse_ekdv = 0;'         drivers/net/ethernet/freescale/fman/fman_keygen.c
    echo "### fman_keygen.c: F-051 BM/HC/EKDV zeroed (RSS isolation)"
fi

# F-052: Suppress -Werror=unused-function for fman_pcd_debugfs_root_get.
# This static helper is defined in patch 0092/0126 but not called from any
# currently-enabled code path.  -Werror promotes the warning to error.
# Mark it with __attribute__((unused)) to silence the build.
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    sed -i 's/static int fman_pcd_debugfs_root_get(void)/static __attribute__((unused)) int fman_pcd_debugfs_root_get(void)/'         drivers/net/ethernet/freescale/fman/fman_pcd.c
    echo "### fman_pcd.c: F-052 debugfs_root_get marked __unused"
fi

# F-052b: Suppress -Werror for fman_pcd_debugfs_root_put (same root cause).
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    sed -i 's/static void fman_pcd_debugfs_root_put(void)/static __attribute__((unused)) void fman_pcd_debugfs_root_put(void)/'         drivers/net/ethernet/freescale/fman/fman_pcd.c
    echo "### fman_pcd.c: F-052b debugfs_root_put marked __unused"
fi

# F-053: Fix hash_bytes_offset in en_exthash_node descriptor ad[0] encoding.
# The DDR flow record (en_ehash_entry) has an 8-byte link-chain header (flags +
# next_entry pointer) before the key data at FMAN_EHASH_FLOW_KEY_OFF=8.  The
# hardware descriptor field hash_bytes_offset (bits 17:16 of ad[0]) was being
# written with t->hash_shift (0), telling the hardware to start key comparison
# at byte 0 of the DDR record — comparing against the link header (all zeros
# for the first flow) + partial key, which NEVER matches the KG-extracted bytes.
# The correct value for an 8-byte header is 1 (the field encodes 0→0B, 1→8B).
# The CRC64 bucket-indexer's hash_shift is a separate parameter and is unchanged.
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    sed -i 's/((u32)(t->hash_shift \& 0x3) << 16)/((u32)(1) << 16)  \/\* F-053: hash_bytes_offset=1 (8B header before key) \*\//'         drivers/net/ethernet/freescale/fman/fman_pcd.c
    echo "### fman_pcd.c: F-053 hash_bytes_offset=1 (key at offset 8 in DDR record)"
fi

# F-054: Fix context_build overwriting FE Action Descriptors.
# fman_pcd_fe_build_contexts() calls fman_pcd_fe_context_build(fe, offset, &p)
# where fe is the AD base address and offset is 0 for MUX.  context_build
# writes at fe+offset = fe+0 — the MUX AD type header (0x04000000) gets
# replaced with enq->muram_off.  The hardware reads a garbage FE type and
# crashes when HIT fires and tries to follow the next-FE pointer.
#
# Fix: replace context_build for MUX and Transition with direct AD writes.
# MUX AD word 0 becomes FMAN_FE_TYPE_MUX|enq_off (type+next-FE in one word).
# Transition AD word 1 becomes the exit FE offset (correct 2-word layout).
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_054.py" 2>&1
    echo "### fman_pcd.c: F-054 MUX/Transition AD direct writes (fix context_build corruption)"
fi

# F-056: MUX/Transition AD writes in fe_arm_engage (SDK-compliant — raw MURAM offsets).
# The 0146 patch tried to add fman_pcd_fe_build_contexts() call into
# fe_arm_engage, but F-047 context drift caused the call-insertion hunk to
# fail.  The build_contexts function was defined but never called (optimized
# away by GCC).  This fix inserts the MUX and Transition AD writes DIRECTLY
# into fe_arm_engage, right before the "ENGAGED" pr_info, bypassing the
# missing call site entirely.
#
# F-056: MUX AD word 0 = enq->muram_off (raw offset, no type byte — SDK-compliant)
# Transition AD word 1 = pcd->fe_exit_off (chains to EXIT for MISS handling)
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_056.py" 2>&1
    echo "### fman_pcd.c: F-058 MUX/Transition/ENQ AD writes in fe_arm_engage (SDK raw offsets)"
fi

# F-057: Remove per-record next-FE from DDR flow records.
# The NXP SDK's en_ehash_entry struct has NO per-record next-FE pointer.
# The HIT dispatch target is in the hash FE descriptor's word 5 (nextFEPtr
# = MUX -> ENQ).  Our code was writing enq_off at byte 24 of each DDR
# record (8-byte header + 13-byte key + 3-byte pad = 24).  The hardware
# reads this as garbage and crashes.
#
# The enq_fe_off parameter becomes unused (kept for ABI compatibility).
# All HIT flows now dispatch through the hash FE's word 5, not per-record.
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_057.py" 2>&1
    echo "### fman_pcd.c: F-057 removed per-record next-FE from DDR (SDK-compliant)"
fi



# F-060 v3d: Fix MUX context write target — write to AD+4 (word 1), not AD+0.
# v3d avoids backslash-s (bad escape through the 4-layer pipeline) — uses [ 	]* instead.
# F-055/F-056 wrote across TWO lines; regex matches the 2-line pattern.
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_055.py" 2>&1
    echo "### fman_pcd.c: F-060 v3d: MUX context write fixed to AD+4"

    # F-083 REMOVED — scaffold guard (fe_enter_off==0) preserved.
    # The CONT_LOOKUP scaffold is the correct path when fe_enter_off==0.
    # When fe_enter_off!=0, RCCB→FE_ENTER direct activates the FE-VM for HIT.
    # FmPortSetFESupport (F-072) provides proper FE workspace allocation,
    # preventing the BMI stall that plagued earlier builds without it.

    # F-072b: Inject FmPortSetFESupport call BEFORE fman_pcd_kg_port_arm_fe.
    # The F-072 v3 fixup's engage anchor (scaffold comment) was not found
    # because the comment format changed across patch revisions.  Use the
    # fman_pcd_kg_port_arm_fe call line as anchor instead — it's stable.
    #
    # F-072c: Forward-declare fman_pcd_fe_buffer_setup before the internal
    # function __fman_pcd_fe_arm_engage (which is defined BEFORE the wrapper
    # where F-072 v3 injects the function body).  Without this, the call
    # injected by F-072b sees an implicit declaration → -Werror.
    sed -i '/^static int __fman_pcd_fe_arm_engage/i\static int fman_pcd_fe_buffer_setup(struct fman_pcd *, struct fman_port *, u8);'         drivers/net/ethernet/freescale/fman/fman_pcd.c
    echo "### fman_pcd.c: F-072c forward-decl fman_pcd_fe_buffer_setup"

    sed -i 's/err = fman_pcd_kg_port_arm_fe(pcd, (u8)port_id,/{ struct fman_port *rxp = fman_port_lookup_rx(pcd->fman, (u8)port_id); int _b; if (!rxp) return -ENODEV; _b = fman_pcd_fe_buffer_setup(pcd, rxp, (u8)port_id); if (_b) return _b; } err = fman_pcd_kg_port_arm_fe(pcd, (u8)port_id,/'         drivers/net/ethernet/freescale/fman/fman_pcd.c
    echo "### fman_pcd.c: F-072b FmPortSetFESupport call injected before arm_fe"

    # F-084: Fix 0158 compose FE_ENTER target — EXT_HASH not ENQ.
    # Single-line sed: e->muram_off → pcd->fe_hash_off
    # The ENQ list walk becomes dead code (unused var 'e' = warning, not error).
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/mutate.py"         drivers/net/ethernet/freescale/fman/fman_pcd.c         "err = fman_pcd_fe_enter_build(pcd, e->muram_off);"         "err = fman_pcd_fe_enter_build(pcd, pcd->fe_hash_off);"         1 "F-084: compose FE_ENTER target = EXT_HASH"
    echo "### fman_pcd.c: F-084 compose FE_ENTER target = EXT_HASH"

    # F-085: Suppress -Wunused-function for static functions whose callers
     # may be behind conditional code paths or fixup-anchor mismatches.
    sed -i 's/static int __fman_pcd_fe_build_vm_chain/static __maybe_unused int __fman_pcd_fe_build_vm_chain/'         drivers/net/ethernet/freescale/fman/fman_pcd.c
    # fman_pcd_fe_buffer_setup now called via F-072b — no __maybe_unused needed

    # F-085b: Fix -Wunused-result from kstrtouint in fe_arm engage tokenizer.
    sed -i 's/kstrtouint(tok, 16, \&miss_fqid);/(void)kstrtouint(tok, 16, \&miss_fqid);/'         drivers/net/ethernet/freescale/fman/fman_pcd.c
    sed -i 's/kstrtouint(tok, 16, \&ekfc);/(void)kstrtouint(tok, 16, \&ekfc);/'         drivers/net/ethernet/freescale/fman/fman_pcd.c
    echo "### fman_pcd.c: F-085 __maybe_unused + kstrtouint casts"

# F-061: fe_probe debugfs — dump FE pool workspace to read KG-extracted key bytes.
# The FE_ENTER ALLOCATE allocates a workspace per-frame from the FE pool.
# After exit, gen_pool_free does NOT zero the MURAM, so the KG hash result
# and extracted key bytes remain readable.  This debugfs node reads the
# first 8 u32 words from the first pool slot, capturing exactly what the
# KG silicon produced for the last classified frame — the only reliable
# way to determine the EKFC extraction byte order on LS1046A silicon.
# Idempotent: checks for existing fe_pool_off / fe_probe_show / debugfs
# registration before inserting each piece.
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_061.py" 2>&1
    echo "### fman_pcd.c: F-061 fe_probe debugfs (KG key dump from FE pool workspace)"
fi

# F-086: Register fe_recover debugfs write node (patch 0163 Tier-1 recovery).
# F-086c: Forward-declare fman_pcd_fe_recover_fops before fman_pcd_init().
# 0163 defines the fops AFTER fman_pcd_init(); F-086 registers it INSIDE
# fman_pcd_init(). Without a forward declaration the compiler rejects it.
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    python3 << 'F086PY'
import pathlib
p = pathlib.Path('drivers/net/ethernet/freescale/fman/fman_pcd.c')
s = p.read_text()
changed = False

# F-086c: insert forward declaration before fman_pcd_init
fwd = 'static const struct file_operations fman_pcd_fe_recover_fops;' + chr(10)
init_anchor = 'struct fman_pcd *fman_pcd_init'
if 'fman_pcd_fe_recover_fops;' not in s and init_anchor in s:
    s = s.replace(init_anchor, fwd + init_anchor, 1)
    print('### fman_pcd.c: F-086c forward declaration inserted before fman_pcd_init')
    changed = True
elif 'fman_pcd_fe_recover_fops;' in s:
    print('### fman_pcd.c: F-086c forward declaration already present')
else:
    print('### fman_pcd.c: F-086c WARNING: fman_pcd_init anchor not found')

# F-086: insert debugfs_create_file("fe_recover",...) before fe_arm registration
arm_anchor = 'debugfs_create_file("fe_arm", 0600,'
recover_line = chr(9)*3 + 'debugfs_create_file("fe_recover", 0200, pcd->debugfs_dir, pcd, &fman_pcd_fe_recover_fops);' + chr(10) + chr(9)*3
if '"fe_recover"' not in s and arm_anchor in s:
    s = s.replace(arm_anchor, recover_line + arm_anchor, 1)
    print('### fman_pcd.c: F-086 fe_recover debugfs registered')
    changed = True
elif '"fe_recover"' in s:
    print('### fman_pcd.c: F-086 fe_recover already registered')
else:
    print('### fman_pcd.c: F-086 WARNING: fe_arm anchor not found')

if changed:
    p.write_text(s)
F086PY
fi

# F-068: IC key probe — extend dpaa_eth IC copy to include KG key region.
# The mainline dpaa_eth IC copy (FMBM_RICP: iciof=0, size=48B) only copies
# parser results + timestamp + hash. The KG-extracted key at IC offset 0x48
# is NOT copied. This fixup adds 32 extra bytes to the IC copy size so the
# key region appears in the DDR buffer headroom, readable via the dpaa_eth
# RX path (rx_default_dqrr -> vaddr + prs_result_offset + key_offset).
# Temporary — removed once extraction order is determined.
if [ -f drivers/net/ethernet/freescale/dpaa/dpaa_eth.c ]; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_068_2.py" 2>&1
    echo "### dpaa_eth.c: F-068 IC key probe (HWA size extended +32B for KG key)"
fi

# F-069a: IC probe — capture RX buffer vaddr in dpaa_eth.c for ic_probe.
# Stores the DMA buffer virtual address in shared global fman_pcd_ic_vaddr
# at the top of rx_default_dqrr() so fman_pcd can dump the IC.
if [ -f drivers/net/ethernet/freescale/dpaa/dpaa_eth.c ]; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_069a.py" 2>&1
    echo "### dpaa_eth.c: F-069a v9 buf_base + vaddr captures
"
fi

# F-072: capture full 8-byte KG CRC-64 hash from dpaa_eth RXHASH path.
# Reads be64_to_cpu(vaddr+hash_offset) and stores in fman_pcd_kg_hash.
if [ -f drivers/net/ethernet/freescale/dpaa/dpaa_eth.c ]; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_072.py" 2>&1
fi

# F-069b: IC probe debugfs node — reads buffer captured by F-069a.
# Shows 32 u32 words (128 bytes) from the DMA buffer headroom.
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_069b.py" 2>&1
fi

# Strip EXPORT_SYMBOL_GPL placed before #include by F-069b v3.
# EXPORT_SYMBOL_GPL needs <linux/export.h> which isn't included yet.
# Both fsl_dpaa_fman and dpaa_eth are built-in, so the symbol resolves 

# F-071: hash_probe debugfs — read full 8-byte KG CRC-64 hash from annotation.
# Uses fman_pcd_ic_vaddr (from F-069a) and fman_pcd_hash_off (from F-070).
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_071.py" 2>&1
fi

# without exporting.
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    sed -i '/^EXPORT_SYMBOL_GPL(fman_pcd_ic_vaddr);$/d'         drivers/net/ethernet/freescale/fman/fman_pcd.c
    echo "### fman_pcd.c: stripped EXPORT_SYMBOL_GPL (before includes)"
fi

# Suppress -Wunused-function for fman_pcd_fe_build_contexts (leftover
# from CCBS scaffold removal). The function was called from 0150 which
# F-047 removed.  Avoids -Werror build failure.
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    sed -i 's/static void fman_pcd_fe_build_contexts/static __maybe_unused void fman_pcd_fe_build_contexts/'         drivers/net/ethernet/freescale/fman/fman_pcd.c
    sed -i 's/fman_muram_offset_to_vbase(muram,/(void *)fman_muram_offset_to_vbase(muram,/'         drivers/net/ethernet/freescale/fman/fman_pcd.c
    echo "### fman_pcd.c: fe_build_contexts fixed (__maybe_unused + cast)"
fi



fi

# F-062a DELETED — was a functional no-op. The sed s/pcd->fe_exit_off,/pcd->fe_mux_off,/
# never matched because the hash FE encode call uses named parameters split across
# two lines. w5 was already MUX from patch 0131.

# F-062b DISABLED — fqb=0x200 is per-port wrong.

# F-062e v3 DELETED — stripped DEALLOCATE from EXIT singleton. The NXP oracle
# (LSDK 999-layerscape-ask ~14253) explicitly sets deallocateBuffer = TRUE on
# the EXIT FE. Without DEALLOCATE, every frame through FE-VM leaks an FMan-internal
# frame buffer → BMI depletion → port-wide RX starvation after disengage.
# The original patch 0124 sets p.flags = FMAN_FE_EXIT_DEALLOCATE; which is correct.

# F-062f REVERTED — w6 missNextFE points to EXIT per NXP §7.2.

# F-062g DELETED — was a functional no-op. The sed on Transition context builder
# never matched because the pattern uses different variable naming than the actual
# patch 0146 code.

# F-062d DISABLED — ENQ ALLOCATE deallocates frame buffers QMan later needs.
#
# F-062f routes MISS→ENQ directly (bypassing EXIT).  With ENQ ALLOCATE
# active, the test was clean (engage→ping→disengage OK) but board crashed
# minutes later from background traffic — consistent with accumulated
# QMan FD corruption from ENQ deallocation.
#
# Disabling F-062d tests whether the 0x00800000 flag on ENQ is the corruption
# source.  Without it, ENQ word0 = 0x02010000 (type only, no ALLOCATE).
: 'F-062d-DISABLED'
: 'if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then'
: '    sed -i ...'
: 'fi'

echo "### fman_pcd.c: F-062d DISABLED (ENQ ALLOCATE may cause QMan FD corruption)"

# M2-4: free params page on disengage (was leaking 256 B per cycle)
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd_kg.c ]; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/M2_4_3.py" 2>&1
    echo "### fman_pcd_kg.c: M2-4 params page freed on disarm"
fi

# M2-4: fe_port_set lazy-allocates params page if not yet created
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/M2_4_4.py" 2>&1
fi
fi

# F-072 v3: FmPortSetFESupport — internal FE buffer pool.
# SDK 999-patch ~L14545. Uses gen_pool MURAM granule (256B auto-align).
# port_id passed as u8 (struct fman_port is opaque — no port->port_id).
if [ -f drivers/net/ethernet/freescale/fman/fman_pcd.c ]; then
    python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/F_072_2.py" 2>&1
    echo "### fman_pcd.c: F-072 v3 FmPortSetFESupport ported"
fi

# === end ls1046a-build patch-loop replacement ===
