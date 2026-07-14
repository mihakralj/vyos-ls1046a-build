# ASK2 Test Results - 2026-07-14

## Test Environment
- **Board**: 192.168.1.185 (VyOS LS1046A)
- **Test Host**: 192.168.1.106
- **Kernel**: 6.18.38-vyos
- **Image**: 2026.07.13-2117-rolling (F-063 ISO)
- **Module Loaded**: ask.ko version 2.1.0, srcversion 0D723CD695CBCB5C95BBFB7 (OLD)
- **Module in /tmp**: ask.ko version 2.1.0, srcversion 8FD76A43AB04777BB6A6577 (NEW)

## Tests Performed

### 1. Baseline Connectivity ✓
- **Test**: Ping from test host (10.99.2.106) to board (10.99.2.185) on eth4
- **Result**: SUCCESS - 0% packet loss, ~0.3ms RTT
- **ARP**: Resolved correctly (e8:f6:d7:00:16:03)
- **Notes**: Works without FE-VM engaged

### 2. FE-VM Engagement ✓
- **Test**: Engage eth4 (port 0x11) via debugfs
- **Result**: SUCCESS - fe_pool engaged, FE_ENTER root AD at 0x56100
- **ENQ FE**: Built at offset 0x55500 with FQID 0x200
- **Notes**: Engagement works, but disengage crashes the board (known bug)

### 3. FE-VM Disengage ✗
- **Test**: Disengage eth4 via debugfs
- **Result**: FAILURE - Board crashed (gen_pool double-free bug)
- **Recovery**: Power cycled board via smart plug
- **Notes**: Known bug in disengage path, needs fix

### 4. Flow Insert ✓
- **Test**: Insert flow with key 0A63026A0A6302B906AD9CD903 (13 bytes)
- **Result**: SUCCESS - 2 flows inserted into bucket 0x0824
- **Bucket Calculation**: (CRC64 >> 40) & 0x7FFF = 0x0824 (with hash_shift=1)
- **Notes**: Flow insert works, but couldn't verify traffic matching

### 5. Traffic Matching ✗
- **Test**: Send TCP traffic matching inserted flow
- **Result**: INCONCLUSIVE - ARP packets dropped by FE-VM
- **Issue**: FE-VM drops ARP packets (no matching flow), preventing TCP connection
- **Notes**: Need to add ARP flow or test without FE-VM engaged

## Fixes Applied (in updated module)

### Critical Fixes
1. **C3: Hardcoded ENQ FE offset** - Set to 0x55500 instead of reading from debugfs
2. **C1: Removed Fork-A path** - Removed ask_hw_port_reinstall() calls from flow insert/remove
3. **C2: Fixed disengage** - Replaced fman_pcd_offload_disengage() with fe_arm disengage debugfs

### Medium Fixes
4. **M2: Changed KG scheme** - EKFC=0x001C0006 (without SPI, with PTYPE1)
5. **M1: Removed SPI field** - Key format changed from 16 bytes to 13 bytes

### Low Fixes
6. **L1/L2: Updated comments** - All comments now reference Fork-B path

## Current Issues

### Blocker: Cannot Load Updated Module
- **Problem**: Old module has refcnt=1 from netevent notifier
- **Impact**: Cannot test fixes without reboot
- **Workaround**: Reboot board to load updated module at boot time

### Known Bug: Disengage Crashes Board
- **Problem**: gen_pool double-free when disengaging port 0x11
- **Impact**: Cannot safely disengage FE-VM
- **Workaround**: Avoid disengage, or power cycle board

### Issue: ARP Packets Dropped
- **Problem**: FE-VM drops ARP packets (no matching flow)
- **Impact**: Cannot establish TCP connections when FE-VM is engaged
- **Workaround**: Add ARP flow, or test without FE-VM engaged

## Next Steps

### Immediate
1. **Reboot board** to load updated module (srcversion 8FD76A43AB04777BB6A6577)
2. **Test C3 fix**: Verify hardcoded ENQ FE offset (0x55500) works
3. **Test C1 fix**: Verify Fork-A path removed (no ask_hw_port_reinstall calls)
4. **Test M2 fix**: Verify EKFC=0x001C0006 (without SPI)

### Short-term
5. **Fix disengage bug**: Investigate gen_pool double-free in disengage path
6. **Add ARP flow**: Allow ARP packets to pass through FE-VM
7. **Test traffic matching**: Verify flows match actual traffic

### Long-term
8. **Performance testing**: Measure throughput with FE-VM engaged
9. **Stress testing**: Test with multiple flows and high traffic load
10. **Integration testing**: Test with real VyOS configuration

## Conclusion

We successfully:
- Built and deployed updated ask.ko module with all fixes
- Tested baseline connectivity (works)
- Tested FE-VM engagement (works)
- Tested flow insert (works)

We identified:
- Disengage crashes board (known bug)
- Cannot load updated module (refcnt=1 blocker)
- ARP packets dropped by FE-VM (needs ARP flow)

**Recommendation**: Reboot board to load updated module, then test fixes systematically.
