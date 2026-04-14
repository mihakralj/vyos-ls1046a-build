# ASK/DPAA Testing Checklist

Run these commands on the Mono Gateway after booting the ASK-enabled ISO.

## 1. SDK DPAA Kernel Drivers

```bash
# Verify SDK drivers loaded (NOT mainline)
dmesg | grep -i "fsl_dpa\|sdk_dpaa\|fsl_mac"

# Check all 5 FMan interfaces are UP
ip link show | grep -E "eth[0-4]:"

# Verify BMan/QMan portals initialized
dmesg | grep -i "bman\|qman" | head -20
```

**Expected:** 5 interfaces (eth0-eth4), BMan/QMan portal messages, no "No BMan portals available" errors.

## 2. FMan Chardevs (USDPAA Interface)

```bash
# FMan character devices — required for dpa_app/FMC
ls -la /dev/fm0*
```

**Expected:**
```
/dev/fm0
/dev/fm0-pcd
/dev/fm0-oh0 ... /dev/fm0-oh3    (4 OH ports)
/dev/fm0-rx0 ... /dev/fm0-rx4    (5 RX ports)
/dev/fm0-tx0 ... /dev/fm0-tx7    (8 TX ports)
```

## 3. FMan Microcode Version

```bash
dmesg | grep -i "fman.*microcode\|fman.*ucode\|fman.*firmware"
```

**Expected:** FMan µcode v210.10.1 or later (ASK-enabled).

## 4. ASK Kernel Hooks

```bash
# Fast-path netfilter hooks
dmesg | grep -i "fp_netfilter"

# IPsec flow offload
dmesg | grep -i "ipsec_flow"

# CAAM crypto engine
dmesg | grep -i "caam\|sec.*era" | head -10
```

**Expected:**
- `fp_netfilter: hooks registered` + `conntrack force-enabled`
- `ipsec_flow: initialized`
- CAAM Era 8, 82+ algorithms registered

## 5. CDX Module

```bash
# Check cdx.ko loaded
lsmod | grep cdx

# CDX control device
ls -la /dev/cdx_ctrl

# CDX kernel messages
dmesg | grep -i cdx | head -20
```

**Expected:** `cdx` module loaded, `/dev/cdx_ctrl` present, 5 ports registered (3×1G + 2×10G).

## 6. dpa_app (FMan PCD Programmer)

```bash
# dpa_app runs once at boot via cdx.ko call_usermodehelper
# Check if it completed successfully
dmesg | grep -i "dpa_app\|pcd.*config\|coarse.*class"

# CDX config files used
ls -la /etc/cdx/
```

**Expected:** dpa_app exit 0, PCD rules programmed, FMan Coarse Classifier configured.

## 7. FCI Module

```bash
lsmod | grep fci
dmesg | grep -i "fci" | head -10
```

**Expected:** `fci` module loaded, FCI netlink interface created.

## 8. CMM Daemon

```bash
# CMM service status
systemctl status cmm

# CMM process
ps aux | grep cmm

# CMM procfs interfaces
cat /proc/fast_path 2>/dev/null
cat /proc/memory_manager 2>/dev/null

# CMM config
cat /etc/config/fastforward
```

**Expected:** CMM active, `/proc/fast_path` shows offload stats.

## 9. Conntrack (Required for Fast-Path)

```bash
# VyOS notrack rules must be flushed for ASK
nft list table ip vyos_conntrack 2>/dev/null | head -20

# Conntrack should be active
cat /proc/sys/net/netfilter/nf_conntrack_count
sysctl net.netfilter.nf_conntrack_max
```

**Expected:** No `notrack` rules in `vyos_conntrack` (the `ask-conntrack-fix` service flushes them).

## 10. Fast-Path Offload Verification

```bash
# Generate traffic through the gateway (from another host)
# Then check offload counters:

# CDX FQID stats (if available)
cat /proc/cdx_fqid_stats 2>/dev/null | head -20

# Conntrack entries — offloaded flows bypass Linux
conntrack -L 2>/dev/null | head -10

# Compare interface counters: if fast-path is working,
# the kernel RX/TX counters should NOT increase for offloaded flows
ip -s link show eth0
```

**Expected:** After initial connection setup via Linux stack, subsequent packets for that flow are handled entirely by FMan hardware — kernel counters stop incrementing for offloaded flows.

## 11. SFP+ 10G Ports (SDK Mode)

```bash
# SDK uses fixed-link — no SFP hot-plug
# SFP TX must be enabled by sfp-tx-enable-sdk service
systemctl status sfp-tx-enable-sdk

# Check 10G link status
ethtool eth3 2>/dev/null | grep -i "speed\|link"
ethtool eth4 2>/dev/null | grep -i "speed\|link"

# GPIO state for TX_DISABLE (should be deasserted)
cat /sys/kernel/debug/gpio 2>/dev/null | grep -i sfp
```

**Expected:** `sfp-tx-enable-sdk` active, eth3/eth4 at 10000Mb/s (if modules inserted before boot).

## Quick One-Liner Health Check

```bash
echo "=== SDK DPAA ===" && dmesg | grep -c "fsl_dpa" && \
echo "=== FMan chardevs ===" && ls /dev/fm0* 2>/dev/null | wc -l && \
echo "=== ASK hooks ===" && dmesg | grep -c "fp_netfilter\|ipsec_flow" && \
echo "=== CDX ===" && ls /dev/cdx_ctrl 2>/dev/null && lsmod | grep -c cdx && \
echo "=== FCI ===" && lsmod | grep -c fci && \
echo "=== CMM ===" && systemctl is-active cmm 2>/dev/null && \
echo "=== Interfaces ===" && ip -br link show | grep -c eth
```

## Known Issues

| Issue | Symptom | Status |
|-------|---------|--------|
| auto_bridge.ko | Missing kernel symbols (`br_fdb_register_can_expire_cb`) | ❌ Blocked — bridge hooks patch needed |
| CDX reload | Cannot rmmod+insmod without reboot (stale procfs/ipsec hooks) | Known limitation |
| VSP not configured | `h_DfltVsp` NULL on SDK FMan ports | Non-fatal, PCD still operates |
| QMan ErrInt | "Invalid Enqueue State" during PCD init | Transient — stops after init |
| 10G SFP modules | Must be inserted BEFORE boot (no hot-plug in SDK mode) | By design |