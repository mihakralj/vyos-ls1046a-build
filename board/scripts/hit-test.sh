#!/bin/bash
# hit-test.sh — Definitive ASK2 FE-VM flow-HIT test
# Run on the board (192.168.1.185) after cold-booting.
#
# Extraction order: MSB-first (CONFIRMED 2026-07-13, extended 2026-08-06/07/08)
#   PORT_ID(1, =0x00) → SIP(4) → DIP(4) → PROTO(1) → SPORT(2) → DPORT(2) = 14 bytes
# Hash algorithm: RAW CRC-64 (no final complement)
# contextSize: key_size=14
#
# SUPERSEDED NOTE (2026-08-08): the 13-byte no-PORT_ID key this script used
# before is stale. HW-confirmed correct format is 14 bytes with PORT_ID=0x00
# prepended (EKFC=0x801C0006), independently CRC-64-matched 3 times
# (2026-08-06, 2026-08-07, 2026-08-08). See AGENTS.md §S6 "Target EKFC" and
# plans/ASK2-MASTER-PLAN.md §1.3. This still does not produce a HIT (open,
# unrelated question, tracked in decomp/hitmiss-path.md) — this script
# verifies wiring/key-format, not the open failure itself.
#
# CRITICAL: KeyGen scheme registers reset to boot-default on every
# reboot/kexec. This script MUST reconfigure the live scheme's EKFC via
# `fe_kg_ekfc` before arming (step 2a below) — omitting this step silently
# runs the test against whatever EKFC KeyGen booted with (commonly the
# mainline RSS 4-tuple default, 0x00180006, NOT this test's key format),
# invalidating the result without any visible error. Discovered 2026-08-08
# after this exact script's shape was reused unmodified across several
# board experiments with the step missing.
set -euo pipefail

PCD="/sys/kernel/debug/fman_pcd/0"
PORT="0x11"
SCHEME="4"
EKFC="801c0006"

# Test flow: 10.99.2.106:44444 → 10.99.2.185:55555 TCP
# Key (MSB-first, PORT_ID prefix + 5-tuple): 000A63026A0A6302B906AD9CD903
# CRC-64 raw: 0xb508e222f73f6794 (HW-confirmed 2026-08-06/07/08)
# Bucket index: (0xb508e222f73f6794 >> 48) & 0x0FFF = 0x508
# (mask reduced 0x7fff->0x0fff: 512 KiB order-7 dma_alloc_coherent -> 64 KiB order-4; see vyos-offload-ask)
KEY="000A63026A0A6302B906AD9CD903"
EXPECTED_BUCKET="508"

echo "=== ASK2 HIT Test — $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
echo "Key: $KEY (MSB-first: PORT_ID→SIP→DIP→PROTO→SPORT→DPORT, 14 bytes)"
echo "Expected bucket: 0x$EXPECTED_BUCKET"
echo ""

# Step 1: Disengage first (clean state)
echo "--- Step 1: Disengage ---"
echo "disengage $PORT" > "$PCD/fe_arm" 2>/dev/null || true
sleep 1

# Step 2: Engage with keysize=14
echo "--- Step 2: Engage keysize=14 ---"
echo "set $PORT" > "$PCD/fe_port"
echo "set 0x0fff 14 0" > "$PCD/fe_ehash"
echo get > "$PCD/fe_pool"
echo build > "$PCD/fe_singletons"
echo build > "$PCD/fe_hashfe"
echo "build 200" > "$PCD/fe_enq"
echo build > "$PCD/fe_enter"

enter_off=$(grep -oP "FE_ENTER root AD: \K0x[0-9a-fA-F]+" "$PCD/fe_arm" 2>/dev/null)
[ -z "$enter_off" ] || [ "$enter_off" = "0x0" ] && enter_off="0x56100"

# Step 2a: Synchronize KeyGen's live EKFC to match this test's key format.
# MANDATORY — do not remove. See the header note above.
echo "--- Step 2a: fe_kg_ekfc set $SCHEME $EKFC ---"
echo "set $SCHEME $EKFC" > "$PCD/fe_kg_ekfc"

echo "engage $PORT $enter_off" > "$PCD/fe_arm"

echo "FE pipeline state:"
cat "$PCD/fe_arm"
cat "$PCD/fe_enter"
cat "$PCD/fe_hashfe" | head -2
echo ""

# Step 3: Verify EXT_HASH contextSize
echo "--- Step 3: Verify EXT_HASH w1 (contextSize) ---"
w1_line=$(cat "$PCD/fe_hashfe" | grep "hash_fe")
w1=$(echo "$w1_line" | awk '{print $4}')  # $1=label $2=off=... $3=word0(type) $4=word1(hashMask|ctxSize|shift)
cs=$(( (0x$w1 >> 8) & 0xFF ))
cs_actual=$((cs + 1))
echo "EXT_HASH w1=0x$w1 → contextSize-1=$cs → contextSize=$cs_actual"
if [ "$cs_actual" -eq 14 ]; then
    echo "✓ contextSize=14 CORRECT"
else
    echo "✗ contextSize=$cs_actual WRONG (expected 14)"
    echo "ABORT: contextSize must be 14 for PORT_ID-prefixed 5-tuple key"
    exit 1
fi
echo ""

# Step 4: Insert flow
echo "--- Step 4: Insert flow ---"
echo "add 0 $KEY 0x55500" > "$PCD/fe_flow"
echo "Flow table:"
cat "$PCD/fe_flow" | head -5
echo ""

# Step 5: Send test frame from .106
echo "--- Step 5: Sending test TCP SYN ---"
echo "(send from test host: nc -w 2 -p 44444 10.99.2.185 55555)"
echo ""

# Step 6: Start listener and wait for HIT
echo "--- Step 6: Start listener on .185:55555 ---"
timeout 15 nc -l -p 55555 > /tmp/hit-result.txt 2>&1 &
NC_PID=$!
echo "Listener PID=$NC_PID, waiting 3s for connection..."
sleep 3

# Step 7: Check result
echo "--- Step 7: Result ---"
if [ -s /tmp/hit-result.txt ]; then
    echo "✓ HIT! Received: $(cat /tmp/hit-result.txt)"
    RESULT="HIT"
else
    echo "✗ MISS (listener received nothing after 3s)"
    RESULT="MISS"
fi
echo ""

# Step 8: Check BMI stall
echo "--- Step 8: BMI stall check ---"
python3 -c "
import mmap, struct, os
fd = os.open('/dev/mem', os.O_RDONLY)
mm = mmap.mmap(fd, mmap.PAGESIZE, mmap.MAP_SHARED, mmap.PROT_READ, offset=0x1a91000)
fmfp_ps = struct.unpack('>I', mm[0x28:0x2C])[0]
mm.close(); os.close(fd)
stall = '[STALLED]' if fmfp_ps & 0x80000000 else '[OK]'
print('eth4 FMBM_RFPNE=0x%08X %s' % (fmfp_ps, stall))
" 2>/dev/null || echo "(register read failed — not critical)"
echo ""

# Step 9: Verify MISS→EXIT works (send ICMP, should be dropped not stalled)
echo "--- Step 9: MISS→EXIT verification (ping, expect 0 replies) ---"
ping -c 2 -W 2 10.99.2.185 2>&1 | tail -2 || true
echo ""

# Summary
echo "=== RESULT: $RESULT ==="
echo "Key: $KEY"
echo "Bucket: 0x$EXPECTED_BUCKET"
echo "contextSize: $cs_actual"
echo "Timestamp: $(date -u +%Y-%m-%dT%H:%M:%SZ)"

kill $NC_PID 2>/dev/null || true
