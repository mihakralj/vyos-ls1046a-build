#!/bin/bash
# check-ask.sh — ASK (Application Solutions Kit) fast-path health check
#
# Tests all ASK components and reports status with ✅/❌ indicators.
# Run as root or with sudo for full dmesg access.

PASS="✅"
FAIL="❌"
WARN="⚠️"

pass=0
fail=0

ok()   { echo "  $PASS $*"; ((pass++)); }
fail() { echo "  $FAIL $*"; ((fail++)); }
warn() { echo "  $WARN $*"; }

# Need dmesg access for several checks
DMESG=$(dmesg 2>/dev/null)
if [ -z "$DMESG" ]; then
  DMESG=$(sudo dmesg 2>/dev/null)
fi

echo "ASK Fast-Path Health Check"
echo "=========================="
echo ""

### --- Kernel hooks ---
echo "Kernel Hooks:"
if echo "$DMESG" | grep -q "fp_netfilter.*hooks registered"; then
  ts=$(echo "$DMESG" | grep "fp_netfilter.*hooks registered" | head -1 | sed 's/.*\[\s*\([0-9.]*\)\].*/\1/')
  ok "fp_netfilter hooks registered at T+${ts}s (built-in)"
else
  fail "fp_netfilter hooks not registered"
fi
echo ""

### --- Kernel modules ---
echo "Kernel Modules:"
if lsmod | grep -q "^cdx "; then
  sz=$(lsmod | awk '/^cdx /{print $2}')
  ok "cdx.ko loaded (${sz} bytes)"
else
  fail "cdx.ko not loaded"
fi

if lsmod | grep -q "^fci "; then
  ok "fci.ko loaded (depends on cdx)"
else
  fail "fci.ko not loaded"
fi

if lsmod | grep -q "^auto_bridge "; then
  ok "auto_bridge.ko loaded (bridge offload)"
else
  if echo "$DMESG" | grep -q "auto_bridge.*Unknown symbol"; then
    syms=$(echo "$DMESG" | grep "auto_bridge.*Unknown symbol" | sed 's/.*Unknown symbol \([^ ]*\).*/\1/' | tr '\n' ', ' | sed 's/,$//')
    fail "auto_bridge.ko — missing symbols: $syms"
  else
    fail "auto_bridge.ko not loaded"
  fi
fi
echo ""

### --- Devices ---
echo "Device Nodes:"
if [ -c /dev/cdx_ctrl ]; then
  ok "CDX control device /dev/cdx_ctrl present"
else
  fail "CDX control device /dev/cdx_ctrl missing"
fi

if [ -c /dev/fm0-pcd ]; then
  oh_count=$(ls /dev/fm0-port-oh* 2>/dev/null | wc -l)
  rx_count=$(ls /dev/fm0-port-rx* 2>/dev/null | wc -l)
  ok "FMD shim /dev/fm0-pcd present (${oh_count} OH, ${rx_count} RX ports)"
else
  fail "FMD shim /dev/fm0-pcd missing"
fi
echo ""

### --- CAAM / IPsec ---
echo "CAAM IPsec:"
if echo "$DMESG" | grep -q "cdx_ipsec_init.*job ring"; then
  ok "CAAM IPsec init (job ring device found)"
else
  fail "CAAM IPsec init not detected"
fi

if echo "$DMESG" | grep -q "dpa_ipsec start failed"; then
  reason=$(echo "$DMESG" | grep "dpa_ipsec start failed" | head -1)
  fail "dpa_ipsec start failed"
else
  ok "dpa_ipsec initialized"
fi
echo ""

### --- SDK DPAA drivers ---
echo "SDK DPAA Drivers:"
if [ -d /sys/bus/platform/drivers/fsl_dpa ]; then
  dpa_count=$(ls -d /sys/bus/platform/drivers/fsl_dpa/soc:* 2>/dev/null | wc -l)
  ok "fsl_dpa driver loaded ($dpa_count ethernet devices)"
else
  fail "fsl_dpa driver not loaded"
fi

if [ -d /sys/bus/platform/drivers/fsl_mac ]; then
  mac_count=$(ls -d /sys/bus/platform/drivers/fsl_mac/*.ethernet 2>/dev/null | wc -l)
  ok "fsl_mac driver loaded ($mac_count MACs)"
else
  fail "fsl_mac driver not loaded"
fi
echo ""

### --- Network interfaces ---
echo "Network Interfaces:"
nic_up=0
nic_total=0
for iface in eth0 eth1 eth2 eth3 eth4; do
  if [ -d "/sys/class/net/$iface" ]; then
    ((nic_total++))
    state=$(cat "/sys/class/net/$iface/operstate" 2>/dev/null)
    if [ "$state" = "up" ]; then
      ((nic_up++))
    fi
  fi
done
if [ "$nic_total" -eq 5 ]; then
  ok "All 5 NICs present ($nic_up UP)"
elif [ "$nic_total" -gt 0 ]; then
  warn "$nic_total/5 NICs present ($nic_up UP)"
else
  fail "No ethernet interfaces found"
fi
echo ""

### --- dpa_app ---
echo "DPA App (FMan PCD):"
if echo "$DMESG" | grep -q "start_dpa_app.*failed"; then
  rc=$(echo "$DMESG" | grep "start_dpa_app.*failed" | head -1 | sed 's/.*rc \([0-9]*\).*/\1/')
  fail "dpa_app failed (rc=$rc)"
elif echo "$DMESG" | grep -q "start_dpa_app"; then
  ok "dpa_app executed"
else
  warn "dpa_app status unknown (no dmesg entry)"
fi

if [ -f /etc/cdx_cfg.xml ]; then
  ok "CDX config /etc/cdx_cfg.xml present"
else
  fail "CDX config /etc/cdx_cfg.xml missing"
fi

if [ -f /etc/cdx_pcd.xml ]; then
  ok "PCD rules /etc/cdx_pcd.xml present"
else
  fail "PCD rules /etc/cdx_pcd.xml missing"
fi

if [ -f /etc/cdx_sp.xml ]; then
  ok "Soft parser /etc/cdx_sp.xml present"
else
  fail "Soft parser /etc/cdx_sp.xml missing"
fi

if echo "$DMESG" | grep -q "failed to locate eth bman pool"; then
  fail "BMan fragment buffer pool not found"
fi
echo ""

### --- CMM daemon ---
echo "CMM Daemon:"
if pgrep -x cmm >/dev/null 2>&1; then
  pid=$(pgrep -x cmm)
  ok "CMM running (PID $pid)"
elif [ -x /usr/bin/cmm ]; then
  # Check why it failed
  status=$(systemctl is-active cmm.service 2>/dev/null)
  if [ "$status" = "failed" ]; then
    reason=$(journalctl -u cmm.service -n 3 --no-pager 2>/dev/null | grep -i "error\|symbol\|undefined" | head -1)
    fail "CMM failed: $reason"
  else
    fail "CMM not running (service status: $status)"
  fi
else
  fail "CMM binary /usr/bin/cmm not found"
fi
echo ""

### --- Services ---
echo "Services:"
for svc in ask-modules-load ask-conntrack-fix sfp-tx-enable-sdk; do
  svc_name="${svc}.service"
  if systemctl is-enabled "$svc_name" >/dev/null 2>&1; then
    result=$(systemctl show -p ActiveState -p Result "$svc_name" 2>/dev/null)
    active=$(echo "$result" | grep ActiveState | cut -d= -f2)
    svc_result=$(echo "$result" | grep "^Result" | cut -d= -f2)
    if [ "$active" = "active" ] && [ "$svc_result" = "success" ]; then
      ok "$svc_name completed"
    elif [ "$active" = "failed" ]; then
      fail "$svc_name failed"
    else
      warn "$svc_name state=$active result=$svc_result"
    fi
  else
    warn "$svc_name not enabled"
  fi
done

svc_name="cmm.service"
if systemctl is-enabled "$svc_name" >/dev/null 2>&1; then
  active=$(systemctl is-active "$svc_name" 2>/dev/null)
  if [ "$active" = "active" ]; then
    ok "$svc_name active"
  else
    fail "$svc_name not active ($active)"
  fi
else
  warn "$svc_name not enabled"
fi
echo ""

### --- Libraries ---
echo "Libraries:"
LDD_CMM=$(ldd /usr/bin/cmm 2>/dev/null)
lib_path=$(echo "$LDD_CMM" | grep libnfnetlink | awk '{print $3}')
if [ -n "$lib_path" ] && [ -e "$lib_path" ]; then
  lib_size=$(stat -Lc%s "$lib_path" 2>/dev/null || echo 0)
  if [ "$lib_size" -gt 100000 ]; then
    ok "libnfnetlink NXP-patched ($lib_path, ${lib_size} bytes)"
  else
    fail "libnfnetlink system version ($lib_path, ${lib_size} bytes) — missing NXP extensions"
  fi
elif [ -f /usr/local/lib/libnfnetlink.so.0.2.0 ]; then
  lib_size=$(stat -c%s /usr/local/lib/libnfnetlink.so.0.2.0 2>/dev/null || echo 0)
  if [ "$lib_size" -gt 100000 ]; then
    ok "libnfnetlink NXP-patched (/usr/local/lib, ${lib_size} bytes)"
  else
    fail "libnfnetlink at /usr/local/lib but wrong size (${lib_size} bytes)"
  fi
else
  fail "libnfnetlink not found"
fi
echo ""

### --- Summary ---
echo "=========================="
total=$((pass + fail))
echo "Result: $pass/$total checks passed"
if [ "$fail" -eq 0 ]; then
  echo "Status: ALL CHECKS PASSED"
  exit 0
else
  echo "Status: $fail FAILED"
  exit 1
fi