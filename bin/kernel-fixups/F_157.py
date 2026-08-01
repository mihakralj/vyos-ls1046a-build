"""F-157: Wire the dedicated TX FQ into the FE-VM ENQ (HIT destination).

Item R1 — separate HIT from MISS destination so HIT is observable.

Problem: the FE-VM ENQ FE is built by __fman_pcd_fe_build_vm_chain() with
`tx_fqid = 0x200` (hardcoded).  The CC miss-AD also targets 0x200.  A
CC-matched + FE-VM HIT frame is therefore enqueued to the same kernel RX
FQ as a miss — HIT and MISS converge on the kernel path and no instrument
(tcpdump, fe_buffer depletion counter, fe_probe object-pool dump) can
discriminate them.  Every prior "HIT still fails" conclusion was blind to
this.

Fix: fman_pcd_fe_engage() takes a caller-supplied enq_fqid (the dedicated
TX FQ allocated by ask.ko, P4.1, on QMan DC-portal ch 0x801 = eth4 TX).
Store it on pcd->fe_enq_fqid; __fman_pcd_fe_build_vm_chain() uses it for
the ENQ build (falling back to 0x200 when unset).  A HIT frame then goes
to the dedicated TX FQ (observable on eth4 TX / dedicated-FQ stats) while
a miss still goes miss-AD -> kernel FQ 0x200 on eth3.  This is the first
unambiguous HIT/MISS discriminator.

Changes in fman_pcd.c:
  1. struct fman_pcd: add `u32 fe_enq_fqid;` (after fe_hash_off).
  2. fman_pcd_fe_engage(struct fman *fm, u8 hw_port_id, u32 enq_fqid):
     store pcd->fe_enq_fqid = enq_fqid before the VM-chain-build block
     (inserted by F_092).  The arm/port_set tail is unchanged.
  3. __fman_pcd_fe_build_vm_chain(): `const u32 tx_fqid = pcd->fe_enq_fqid
     ? pcd->fe_enq_fqid : 0x200;` replaces the hardcoded 0x200.
  4. include/linux/fsl/fman_pcd.h: declaration gains the third param.

Runs AFTER F_092 (which inserts the chain-build call) and F_148 (scaffold
write).  Execution order in ci-setup-kernel.sh must place F_157 after
F_148.

Disposition: fold-into 0158 + 0153
"""

import sys, os, re

kroot = "drivers/net/ethernet/freescale/fman"
pcd_c = os.path.join(kroot, "fman_pcd.c")
hdr = "include/linux/fsl/fman_pcd.h"

if not os.path.exists(pcd_c):
    print("### F-157: fman_pcd.c not found")
    sys.exit(0)

with open(pcd_c) as f:
    src = f.read()

changes = 0

# ── 1. struct fman_pcd: add fe_enq_fqid field ──
field_anchor = "\tunsigned long fe_hash_off;"
field_insert = ("\tunsigned long fe_hash_off;\n"
                "\tu32 fe_enq_fqid;\t/* F-157: FE-VM ENQ target on HIT (dedicated TX FQ) */")
if "u32 fe_enq_fqid;" in src:
    print("### F-157: fe_enq_fqid field already present")
elif field_anchor in src:
    src = src.replace(field_anchor, field_insert, 1)
    changes += 1
    print("### F-157: added u32 fe_enq_fqid to struct fman_pcd")
else:
    print("### F-157: FATAL: fe_hash_off anchor not found in struct fman_pcd")
    sys.exit(1)

# ── 2. fman_pcd_fe_engage(): extend signature + store enq_fqid ──
eng_sig = "int fman_pcd_fe_engage(struct fman *fm, u8 hw_port_id)"
eng_sig_new = "int fman_pcd_fe_engage(struct fman *fm, u8 hw_port_id,\n\t\t\t u32 enq_fqid)"
if eng_sig_new in src:
    print("### F-157: fe_engage signature already extended")
elif eng_sig in src:
    src = src.replace(eng_sig, eng_sig_new, 1)
    changes += 1
    print("### F-157: extended fman_pcd_fe_engage signature with enq_fqid")

    # Store pcd->fe_enq_fqid right after the pcd lookup (before VM chain block).
    # Anchor on the params-page ensure call that follows the miss_fqid
    # computation in the post-0153/F-092 body.
    store_anchor = "\tmiss_fqid = (hw_port_id == 0x10) ? 0x200 : 0x2B9;"
    if store_anchor in src:
        # Fallback: original 0153 body computes miss_fqid.  Insert the
        # enq_fqid store adjacent (before ensure_params_page).
        ensure_anchor = "\terr = fman_pcd_port_ensure_params_page(pcd, rxport);"
        if ensure_anchor in src:
            store_insert = ("\tpcd->fe_enq_fqid = enq_fqid;\t/* F-157: HIT ENQ target */\n\n"
                            "\terr = fman_pcd_port_ensure_params_page(pcd, rxport);")
            src = src.replace(ensure_anchor, store_insert, 1)
            changes += 1
            print("### F-157: store pcd->fe_enq_fqid = enq_fqid before params page")
        else:
            print("### F-157: WARNING: ensure_params_page anchor not found for store")
    else:
        # post-F-092 body may have dropped the miss_fqid line; anchor on the
        # F_092 chain-build insertion instead.
        chain_marker = "if (!pcd->fe_vm_chain_built) {"
        if chain_marker in src:
            # find the preceding 'if (muram)' / block start and insert before
            # the chain-build if STATEMENT is reached (just before it).
            pre_anchor = "\tif (!pcd->fe_vm_chain_built) {"
            store_line = ("\tpcd->fe_enq_fqid = enq_fqid;\t/* F-157: HIT ENQ target */\n\n"
                          "\tif (!pcd->fe_vm_chain_built) {")
            if pre_anchor in src:
                src = src.replace(pre_anchor, store_line, 1)
                changes += 1
                print("### F-157: store pcd->fe_enq_fqid before VM chain build (alt anchor)")
            else:
                print("### F-157: FATAL: neither miss_fqid nor chain-build anchor found")
                sys.exit(1)
        else:
            print("### F-157: FATAL: cannot locate store site in fman_pcd_fe_engage")
            sys.exit(1)
else:
    print("### F-157: FATAL: fman_pcd_fe_engage signature not found")
    sys.exit(1)

# ── 3. __fman_pcd_fe_build_vm_chain(): use pcd->fe_enq_fqid ──
tx_anchor = "\tconst u32 tx_fqid       = 0x200;  /* TODO: dedicated offload TX FQ */"
tx_new = ("\tconst u32 tx_fqid       = pcd->fe_enq_fqid ?\n"
          "\t                        pcd->fe_enq_fqid : 0x200;\t/* F-157: dedicated TX FQ */")
if "pcd->fe_enq_fqid ?" in src:
    print("### F-157: chain builder already uses fe_enq_fqid")
elif tx_anchor in src:
    src = src.replace(tx_anchor, tx_new, 1)
    changes += 1
    print("### F-157: __fman_pcd_fe_build_vm_chain uses fe_enq_fqid (fallback 0x200)")
else:
    print("### F-157: FATAL: tx_fqid hardcode anchor not found")
    sys.exit(1)

# ── 4. fman_pcd.h: declaration gains third param ──
if os.path.exists(hdr):
    with open(hdr) as f:
        hsrc = f.read()
    hsig = "int fman_pcd_fe_engage(struct fman *fm, u8 hw_port_id);"
    hsig_new = ("int fman_pcd_fe_engage(struct fman *fm, u8 hw_port_id,\n"
                "\t\t\t u32 enq_fqid);")
    if hsig_new in hsrc:
        print("### F-157: header declaration already extended")
    elif hsig in hsrc:
        hsrc = hsrc.replace(hsig, hsig_new, 1)
        with open(hdr, "w") as f:
            f.write(hsrc)
        changes += 1
        print("### F-157: extended fman_pcd.h declaration")
    else:
        print("### F-157: FATAL: fman_pcd.h fe_engage declaration not found")
        sys.exit(1)
else:
    print("### F-157: FATAL: fman_pcd.h not found")
    sys.exit(1)

if changes:
    with open(pcd_c, "w") as f:
        f.write(src)
    print(f"### F-157: {changes} change(s) applied")

else:
    print("### F-157: no changes applied")
