"""F-181: vendor-faithful per-key opcode-script in the ehash DDR record.

CONTEXT (2026-08-11): source-grounded FE-VM ehash fault-vs-vendor comparison
(we-are-mono/ASK + the running .106 OpenWrt cdx).  Container is VERIFIED
identical (struct en_exthash_bucket {u64 h; u64 pad} = our 16B bucket;
struct en_exthash_tbl_entry.hashentry = en_ehash_entry).  The vendor's
ExternalHashTableAddKey() (010-ask-fman-dpaa-ehash.patch line 6786) writes a
record whose 16-bit 'flags' u16@0 carries:

    SET_OPC_OFFSET(flags,off)   flags |= (off>>2) << 6     % bits 10:6
    SET_PARAM_OFFSET(flags,off) flags |= (off>>2)          % bits 5:0
    SET_STATS_ENABLE            flags |= 1 << 12
    SET_TIMESTAMP_ENABLE        flags |= 1 << 13

and whose body after the key carries an INLINE opcode script (terminal
ENQUEUE_PKT=0x01) followed by struct en_ehash_enqueue_param:

    mtu u16(be) | hdr_xpnd_sz u8 | bpid u8 | fqid u32(be) | word u32 | word2 u32

The FE-VM on a HIT reads flags.opc_offset -> walks the opcode list -> executes
ENQUEUE_PKT -> reads enqueue_param.fqid -> enqueues to the flow's TX FQ and
completes the compare.  OUR record (this project's fman_pcd_ehash_add_key)
writes flags=0x1000 (STATS_EN only, no opc/param offsets) then key@8 then one
u32 ENQ-FE MURAM offset.  With flags.opc_offset=0 the FE-VM's HIT walk starts
at offset 0 (the header bytes) -> no valid ENQUEUE to execute -> the ehash
comparator can never complete a HIT -> the exact "zero HIT, comparator never
executes" fault bracket (KG classify completes, comparator stats never appear).

FIX (single variable, NO container change): write the vendor opcode-script.

    flags  = SET_STATS_ENABLE | SET_OPC_OFFSET(opc_off) | SET_PARAM_OFFSET(param_off)
    opc_off = 8 + ALIGN(key_size,4)         (key_align: >4B -> (keySize+7)&0x38)
    param_off = opc_off + MAX_OPCODES(16)
    r[opc_off] = ENQUEUE_PKT (0x01)
    en_ehash_enqueue_param at param_off: mtu=be16(1500), hdr=0, bpid=0,
        fqid=be32(enq_fe_off & 0xffffff), word=0, word2=0

Anchored on the actual CI-record state (flags=0x1000 + u32 enq_fe_off at 8+
align8 keysize).  Idempotent (marker "F-181: opcode-script").  CI-only build.
"""

import sys

path = "drivers/net/ethernet/freescale/fman/fman_pcd.c"
with open(path) as f:
    src = f.read()

if "F-181: opcode-script" in src:
    print("### F-181 already applied")
    sys.exit(0)

changes = 0


def apply_block(name, old, new):
    global src, changes
    if old not in src:
        print(f"### F-181: FATAL: '{name}' text not found verbatim -- "
              "source drifted. Refusing to guess.")
        sys.exit(1)
    src = src.replace(old, new, 1)
    changes += 1
    print(f"### fman_pcd.c: F-181 {name} applied")


old_header = (
    "\t/* en_ehash_entry header: chain THIS record to the previous head. */\n"
    "\t*(__be16 *)(r + 0) = cpu_to_be16(0x1000);\t\t\t\t/* flags */\t/* F-176: STATS_EN only (bit 12) */\n"
    "\t*(__be16 *)(r + 2) = cpu_to_be16((u16)((old_native >> 32) & 0xffff));\n"
    "\t*(__be32 *)(r + 4) = cpu_to_be32((u32)(old_native & 0xffffffff));\n"
    "\tmemcpy(r + FMAN_EHASH_FLOW_KEY_OFF, key, key_size);\n"
)
new_header = (
    "\t/* F-181: vendor opcode ENQUEUE_PKT (vendor #define ENQUEUE_PKT 0x01). */\n"
    "\t#define FMAN_EHASH_OPC_ENQUEUE_PKT\t0x01\n"
    "\t/* en_ehash_entry header: chain THIS record to the previous head. */\n"
    "\t/* F-181: vendor opcode-script record -- flags carry opc/param offsets\n"
    "\t * (SET_OPC_OFFSET/SET_PARAM_OFFSET); STATS_EN bit preserves F-176.  The\n"
    "\t * FE-VM reads opc_offset on a HIT, walks the opcode list, and ENQUEUEs\n"
    "\t * to enqueue_param.fqid -- without this there is no action script to\n"
    "\t * walk and the comparator can never complete a HIT.\n"
    "\t */\n"
    "\t{\n"
    "\t\tsize_t opc_off = FMAN_EHASH_FLOW_KEY_OFF + ALIGN(key_size, sizeof(u32));\n"
    "\t\tsize_t param_off = opc_off + 16;\t/* MAX_OPCODES */\n"
    "\t\tu16 flags = (1U << 12);\t\t/* SET_STATS_ENABLE */\n"
    "\n"
    "\t\t/* vendor SET_OPC_OFFSET / SET_PARAM_OFFSET (hi: 5-bit opc, lo: 6-bit param, word>>2) */\n"
    "\t\tflags |= (u16)(((u16)(opc_off >> 2)) << 6);\n"
    "\t\tflags |= (u16)((u16)(param_off >> 2) & 0x3f);\n"
    "\t\t*(__be16 *)(r + 0) = cpu_to_be16(flags);\n"
    "\t\t*(__be16 *)(r + 2) = cpu_to_be16((u16)((old_native >> 32) & 0xffff));\n"
    "\t\t*(__be32 *)(r + 4) = cpu_to_be32((u32)(old_native & 0xffffffff));\n"
    "\t\tmemcpy(r + FMAN_EHASH_FLOW_KEY_OFF, key, key_size);\n"
    "\n"
    "\t\t/* opcode list: terminal ENQUEUE_PKT (0x01) -- the action the FE-VM runs. */\n"
    "\t\tr[opc_off] = FMAN_EHASH_OPC_ENQUEUE_PKT;\n"
    "\n"
    "\t\t/* en_ehash_enqueue_param (packed): mtu u16@0 | hdr u8@2 | bpid u8@3 |\n"
    "\t\t * fqid u32@4 | word u32@8 | word2 u32@12.  fqid = this record's ENQ\n"
    "\t\t * target (24-bit TX FQ); all BE.  mtu=1500, hdr/bpid/word/word2=0.\n"
    "\t\t */\n"
    "\t\t*(__be16 *)(r + param_off + 0) = cpu_to_be16(1500);\t/* mtu */\n"
    "\t\t*(r + param_off + 2) = 0;\t/* hdr_xpnd_sz */\n"
    "\t\t*(r + param_off + 3) = 0;\t/* bpid */\n"
    "\t\t*(__be32 *)(r + param_off + 4) = cpu_to_be32((u32)enq_fe_off & 0x00ffffff);\t/* fqid */\n"
    "\t\t*(__be32 *)(r + param_off + 8) = cpu_to_be32(0);\t/* stats word */\n"
    "\t\t*(__be32 *)(r + param_off + 12) = cpu_to_be32(0);\t/* dscp word */\n"
    "\t}\n"
)
apply_block("opcode-script record", old_header, new_header)

with open(path, "w") as f:
    f.write(src)

print(f"### fman_pcd.c: F-181 complete ({changes} blocks)")
