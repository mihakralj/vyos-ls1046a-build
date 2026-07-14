# ASK2 Test Report - 2026-07-14

## Executive Summary

Testing of ASK2 fixes revealed a critical bug in the `fe_flow` debugfs handler that prevents 5-tuple flow matching. The handler truncates flow keys to 8 bytes (SIP+DIP only), making it impossible to test the full 13-byte key matching required for TCP/UDP flows.

## Test Environment

- **Board**: 192.168.1.185 (VyOS LS1046A)
- **Test Host**: 192.168.1.106
- **Kernel**: 6.18.38-vyos
- **Image**: 2026.07.13-2117-rolling (F-063 ISO)
- **Module**: ask.ko version 2.1.0, srcversion 0D723CD695CBCB5C95BBFB7 (OLD - cannot be updated due to refcnt=1)

## Tests Performed

### 1. Module Update Attempt ✗
- **Objective**: Load updated ask.ko module with all fixes
- **Result**: FAILED - Old module has refcnt=1 from netevent notifier, cannot be unloaded
- **Impact**: Cannot test fixes without reboot
- **Workaround**: Test with old module to establish baseline

### 2. Baseline Connectivity ✓
- **Test**: Ping from test host (10.99.2.106) to board (10.99.2.185) on eth4
- **Result**: SUCCESS - 0% packet loss, ~0.3ms RTT
- **ARP**: Resolved correctly (e8:f6:d7:00:16:03)
- **Notes**: Works without FE-VM engaged

### 3. FE-VM Engagement ✓
- **Test**: Engage eth4 (port 0x11) via debugfs (userspace)
- **Result**: SUCCESS - fe_pool engaged, FE_ENTER root AD at 0x53e00
- **ENQ FE**: Built at offset 0x4b000 with FQID 0x200
- **Notes**: Engagement works from userspace, but ask module's engage function fails due to debugfs kernel write bug

### 4. FE-VM Disengage ✗
- **Test**: Disengage eth4 via debugfs
- **Result**: FAILURE - Board crashed (gen_pool double-free bug)
- **Recovery**: Power cycled board via smart plug
- **Notes**: Known bug in disengage path, needs fix

### 5. Flow Insert ✓ (with bug)
- **Test**: Insert flow with key 0A63026A0A6302B906AD9CD903 (13 bytes)
- **Result**: SUCCESS - 1 flow inserted into bucket 0x6008
- **Bug**: Flow record shows only 8 bytes of key (0a63026a0a6302b9 = SIP+DIP)
- **Impact**: Cannot test 5-tuple flow matching

### 6. Traffic Matching ✗
- **Test**: Send TCP traffic matching inserted flow
- **Result**: FAILURE - Traffic not reaching board
- **Root Cause**: 
  1. ARP resolution failing (FE-VM drops ARP packets)
  2. Static ARP entry added, but TCP traffic still not reaching board
  3. Flow key in DDR is incomplete (only 8 bytes, not 13 bytes)
- **Notes**: Cannot verify flow matching due to incomplete flow key

## Critical Bugs Found

### Bug 1: fe_flow Debugfs Handler Truncates Keys
- **Severity**: CRITICAL
- **Description**: The `fe_flow` debugfs handler only stores the first 8 bytes of flow keys (SIP+DIP), truncating the remaining 5 bytes (PROTO+SPORT+DPORT)
- **Impact**: Cannot test 5-tuple flow matching, which is essential for TCP/UDP offload
- **Evidence**: 
  - Inserted 13-byte key: `0A63026A0A6302B906AD9CD903`
  - Flow record shows: `0a63026a0a6302b9` (only 8 bytes)
  - Missing: `06AD9CD903` (PROTO+SPORT+DPORT)
- **Fix Required**: Update `fe_flow` debugfs handler to store full 13-byte keys

### Bug 2: Debugfs Kernel Write Not Supported
- **Severity**: HIGH
- **Description**: The ask module's engage function tries to write to debugfs files from kernel space using `kernel_write()`, but these files only support userspace writes
- **Impact**: FE-VM engagement fails when using ask module
- **Evidence**: All debugfs writes fail with "kernel write not supported" (error -22)
- **Workaround**: Test FE-VM engagement from userspace
- **Fix Required**: Modify fman_pcd driver to support kernel writes, or use userspace helper

### Bug 3: Disengage Crashes Board
- **Severity**: HIGH
- **Description**: Disengaging port 0x11 causes gen_pool double-free, crashing the board
- **Impact**: Cannot safely disengage FE-VM, requires power cycle to recover
- **Evidence**: Board becomes unresponsive after disengage command
- **Fix Required**: Fix gen_pool management in disengage path

## Fixes Applied (in updated module, not tested)

### Critical Fixes
1. **C3: Hardcoded ENQ FE offset** - Set to 0x55500 instead of reading from debugfs
2. **C1: Removed Fork-A path** - Removed ask_hw_port_reinstall() calls from flow insert/remove
3. **C2: Fixed disengage** - Replaced fman_pcd_offload_disengage() with fe_arm disengage debugfs

### Medium Fixes
4. **M2: Changed KG scheme** - EKFC=0x001C0006 (without SPI, with PTYPE1)
5. **M1: Removed SPI field** - Key format changed from 16 bytes to 13 bytes

### Low Fixes
6. **L1/L2: Updated comments** - All comments now reference Fork-B path

## Recommendations

### Immediate Actions
1. **Fix fe_flow debugfs handler** - Update to store full 13-byte keys (CRITICAL)
2. **Reboot board** - Load updated ask.ko module to test fixes
3. **Fix disengage bug** - Investigate gen_pool double-free in disengage path

### Short-term Actions
4. **Fix debugfs kernel write bug** - Modify fman_pcd driver to support kernel writes
5. **Add ARP flow** - Allow ARP packets to pass through FE-VM
6. **Test 5-tuple flow matching** - Verify flows match actual TCP/UDP traffic

### Long-term Actions
7. **Performance testing** - Measure throughput with FE-VM engaged
8. **Stress testing** - Test with multiple flows and high traffic load
9. **Integration testing** - Test with real VyOS configuration

## Conclusion

Testing revealed a critical bug in the `fe_flow` debugfs handler that prevents 5-tuple flow matching. This bug must be fixed before ASK2 can be properly tested. Additionally, the ask module cannot be updated due to refcnt=1, requiring a board reboot to test the fixes.

**Next Steps**:
1. Fix fe_flow debugfs handler to store full 13-byte keys
2. Reboot board to load updated ask.ko module
3. Re-test flow insertion and traffic matching
4. Verify all fixes work correctly

## Appendix: Test Commands

### Engage FE-VM (userspace)
```bash
echo "get" > /sys/kernel/debug/fman_pcd/0/fe_pool
echo "build" > /sys/kernel/debug/fman_pcd/0/fe_singletons
echo "set 0x7FFF 13 0" > /sys/kernel/debug/fman_pcd/0/fe_ehash
echo "build" > /sys/kernel/debug/fman_pcd/0/fe_hashfe
echo "build 0x200" > /sys/kernel/debug/fman_pcd/0/fe_enq
echo "build 0x11" > /sys/kernel/debug/fman_pcd/0/fe_enter
echo "engage 0x11 0x53e00" > /sys/kernel/debug/fman_pcd/0/fe_arm
```

### Insert Flow
```bash
echo "add 0 0A63026A0A6302B906AD9CD903 0x55500" > /sys/kernel/debug/fman_pcd/0/fe_flow
```

### Check Flow Table
```bash
cat /sys/kernel/debug/fman_pcd/0/fe_flow
```

### Send Test Traffic
```bash
# On test host (10.99.2.106)
echo "test payload" | nc -w 3 -p 44444 10.99.2.185 55555
```

### Add Static ARP Entry
```bash
# On test host
ip neigh replace 10.99.2.185 lladdr e8:f6:d7:00:16:03 dev eth4 nud permanent
```
