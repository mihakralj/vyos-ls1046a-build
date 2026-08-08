#!/bin/bash
# T26-verify-wiring-and-record.sh — re-run the CRC64-confirmed portid=0x00
# armed test, but this time: (1) read FMBM_RCCB immediately after arming to
# verify it actually points at our FE_ENTER root_ad (not a scaffold/stale
# value — cross-checking the F-165 historical precedent where an earlier
# test's "clean MISS" turned out to mean the comparator was never reached
# at all), (2) dump the full 320B DDR flow record raw, before and after
# sending the matching frame.
set -uo pipefail
PCD="/sys/kernel/debug/fman_pcd/0"
KEY="000a63026a0a6302b906ad9cd903"   # portid(00)|SIP|DIP|PROTO|SPORT|DPORT

echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) T26 wiring+record verification ==="

echo "--- fault baseline ---"
for f in bmi_err fpm_err kg_err; do
  echo -n "$f: "; sudo -n cat "$PCD/dcsr/$f" 2>&1 | head -1
done

echo "--- fe_port set 11 ---"
sudo -n bash -c "echo 'set 11' > $PCD/fe_port"; echo "rc=$?"

echo "--- fe_ehash set 0xfff 14 0 ---"
sudo -n bash -c "echo 'set 0xfff 14 0' > $PCD/fe_ehash"; echo "rc=$?"

echo "--- fe_pool get ---"
sudo -n bash -c "echo get > $PCD/fe_pool"; echo "rc=$?"

echo "--- fe_singletons build ---"
sudo -n bash -c "echo build > $PCD/fe_singletons"; echo "rc=$?"

echo "--- fe_hashfe build ---"
sudo -n bash -c "echo build > $PCD/fe_hashfe"; echo "rc=$?"
sudo -n cat "$PCD/fe_hashfe"
hash_fe_off=$(sudo -n cat "$PCD/fe_hashfe" | grep -oP '0x[0-9a-fA-F]{4,6}' | head -1)
echo "parsed hash_fe_off=$hash_fe_off"

echo "--- fe_enq build 0x300 ---"
sudo -n bash -c "echo 'build 0x300' > $PCD/fe_enq"; echo "rc=$?"

echo "--- fe_enter build ---"
sudo -n bash -c "echo build > $PCD/fe_enter"; echo "rc=$?"
sudo -n cat "$PCD/fe_enter"
enter_off=$(sudo -n cat "$PCD/fe_enter" | grep -oP '0x[0-9a-fA-F]{4,6}' | head -1)
echo "parsed enter_off=$enter_off"

echo "--- fe_flow add 0 $KEY $hash_fe_off ---"
sudo -n bash -c "echo 'add 0 $KEY $hash_fe_off' > $PCD/fe_flow"; echo "rc=$?"
sudo -n cat "$PCD/fe_flow"
rec_addr=$(sudo -n cat "$PCD/fe_flow" | grep -oP 'rec=\K0x[0-9a-fA-F]+')
echo "parsed rec_addr=$rec_addr"
echo

echo "--- RAW DDR record dump BEFORE arm/traffic (320 bytes @ $rec_addr) ---"
sudo -n python3 -c "
import mmap, os
fd = os.open('/dev/mem', os.O_RDONLY)
addr = $rec_addr
pgsz = mmap.PAGESIZE
aligned = addr - (addr % pgsz)
off = addr - aligned
mm = mmap.mmap(fd, pgsz*2, mmap.MAP_SHARED, mmap.PROT_READ, offset=aligned)
data = mm[off:off+320]
mm.close(); os.close(fd)
for i in range(0, len(data), 16):
    chunk = data[i:i+16]
    print('+%04x: %s' % (i, chunk.hex()))
"
echo

echo "--- fe_arm engage 0x11 $enter_off 0x300 ---"
sudo -n bash -c "echo 'engage 0x11 $enter_off 0x300' > $PCD/fe_arm"; rc=$?
echo "rc=$rc"
sudo -n cat "$PCD/fe_arm"
echo

echo "--- FMBM_RCCB readback (port 0x11 base 0x1a91000 + 0x34) ---"
sudo -n python3 -c "
import mmap, struct, os
fd = os.open('/dev/mem', os.O_RDONLY)
mm = mmap.mmap(fd, mmap.PAGESIZE, mmap.MAP_SHARED, mmap.PROT_READ, offset=0x1a91000)
rccb = struct.unpack('>I', mm[0x34:0x38])[0]
rfpne = struct.unpack('>I', mm[0x28:0x2C])[0]
mm.close(); os.close(fd)
print('FMBM_RCCB raw = 0x%08x' % rccb)
print('FMBM_RFPNE raw = 0x%08x (stall bit: %s)' % (rfpne, bool(rfpne & 0x80000000)))
"
echo "expected enter_off = $enter_off  (compare against FMBM_RCCB above, several possible encodings)"
python3 -c "
enter_off = int('$enter_off', 16)
print(f'enter_off decimal = {enter_off}')
print(f'enter_off >> 12 (if bits[27:12] = offset>>12) = {enter_off >> 12:#x}')
print(f'enter_off << 12 (if register holds offset<<12 pre-shift) = {enter_off << 12:#x}')
"
echo

echo "--- fault check post-arm ---"
for f in bmi_err fpm_err kg_err; do
  echo -n "$f: "; sudo -n cat "$PCD/dcsr/$f" 2>&1 | head -1
done
ip -br link show eth4
echo

echo "--- fe_ehash_stats BEFORE traffic ---"
sudo -n cat "$PCD/fe_ehash_stats" 2>&1
