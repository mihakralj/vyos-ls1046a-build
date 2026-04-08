#!/bin/bash
# ask-activate.sh — Deploy and activate ASK hardware flow offload stack
#
# Handles the full activation sequence:
#   1. Install ASK binaries and libraries
#   2. Fix conntrack (remove VyOS notrack rules)
#   3. Load CDX and FCI kernel modules
#   4. Enable SFP+ TX (unbind sfp driver, deassert TX_DISABLE GPIO)
#   5. Bring up all interfaces + DHCP
#   6. CDX port registration via cdx_init
#   7. Set up WAN/LAN forwarding topology
#   8. Start CMM daemon
#
# Usage: Copy to device and run as root:
#   scp ask-activate.sh vyos@<device>:/tmp/
#   ssh vyos@<device> 'sudo bash /tmp/ask-activate.sh'
#
# Prerequisites:
#   - SDK+ASK kernel TFTP-booted with SDK DTS
#   - cdx.ko and fci.ko in /usr/lib/modules/$(uname -r)/extra/
#   - cdx_init in /tmp/ or /usr/local/bin/
#
# This script is idempotent — safe to re-run.

set -euo pipefail

TFTP_SERVER="192.168.1.137"
ASK_DIR="/opt/ask"
LOG="/tmp/ask-activate.log"

log() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG"; }
die() { log "FATAL: $*"; exit 1; }

log "=== ASK Hardware Flow Offload Activation ==="
log "Kernel: $(uname -r)"

# Verify we're on an ASK-capable SDK kernel
grep -q 'fsl,ls1046a' /proc/device-tree/compatible 2>/dev/null || die "Not an LS1046A board"
test -e /dev/fm0-pcd || die "No /dev/fm0-pcd — SDK FMan PCD not available"

# ============================================================
# Phase 1: Install ASK binaries and libraries
# ============================================================
log "--- Phase 1: Installing ASK stack ---"

if [ ! -f /tmp/ask-deploy.tar.gz ]; then
    log "Fetching ask-deploy.tar.gz from TFTP server..."
    cd /tmp && wget -q "http://${TFTP_SERVER}/ask-deploy.tar.gz" 2>/dev/null \
        || tftp -g -r ask-deploy.tar.gz "${TFTP_SERVER}" 2>/dev/null \
        || die "Cannot fetch ask-deploy.tar.gz"
fi

mkdir -p "${ASK_DIR}"
tar xzf /tmp/ask-deploy.tar.gz -C /tmp/ 2>/dev/null || true

# Install kernel modules
log "Installing kernel modules..."
MODDIR="/lib/modules/$(uname -r)/extra"
mkdir -p "${MODDIR}"
cp /tmp/ask-deploy/modules/cdx.ko "${MODDIR}/" 2>/dev/null || true
cp /tmp/ask-deploy/modules/fci.ko "${MODDIR}/" 2>/dev/null || true
depmod -a 2>/dev/null || true

# Install binaries
log "Installing binaries..."
cp /tmp/ask-deploy/bin/dpa_app /usr/local/bin/ && chmod +x /usr/local/bin/dpa_app
cp /tmp/ask-deploy/bin/cmm /usr/local/bin/ && chmod +x /usr/local/bin/cmm
cp /tmp/ask-deploy/bin/fmc /usr/local/bin/ && chmod +x /usr/local/bin/fmc

# Install shared libraries
log "Installing shared libraries..."
for lib in /tmp/ask-deploy/lib/*.so*; do
    cp "$lib" /usr/local/lib/
done
cd /usr/local/lib
ln -sf libmnl.so.0.2.0 libmnl.so.0 2>/dev/null || true
ln -sf libmnl.so.0 libmnl.so 2>/dev/null || true
ln -sf libcli.so.1.10.8 libcli.so.1 2>/dev/null || true
ln -sf libcli.so.1 libcli.so 2>/dev/null || true
ln -sf libfci.so.0.1 libfci.so.0 2>/dev/null || true
ln -sf libfci.so.0 libfci.so 2>/dev/null || true
ln -sf libcmm.so.0.0.0 libcmm.so.0 2>/dev/null || true
ln -sf libcmm.so.0 libcmm.so 2>/dev/null || true

# NXP patched libnfnetlink must REPLACE the system version
# (has nfnl_set_nonblocking_mode needed by CMM)
# DO NOT put it only in /usr/local/lib — ldconfig prefers system version
cp /usr/local/lib/libnfnetlink.so.0.2.0 /usr/lib/aarch64-linux-gnu/libnfnetlink.so.0.2.0

# Remove NXP libnetfilter_conntrack from /usr/local/lib — use system Debian 1.0.9
# NXP version has lower CTA_MAX causing "ctnetlink kernel ABI is broken" crash
rm -f /usr/local/lib/libnetfilter_conntrack.so* 2>/dev/null || true

echo "/usr/local/lib" > /etc/ld.so.conf.d/ask.conf
ldconfig

# Install config files
log "Installing config files..."
mkdir -p /etc/config /etc/fmc/config "${ASK_DIR}/etc"
cp /tmp/ask-deploy/etc/fastforward /etc/config/
cp /tmp/ask-deploy/etc/cdx_cfg.xml "${ASK_DIR}/etc/"
cp /tmp/ask-deploy/etc/cdx_pcd.xml "${ASK_DIR}/etc/"
cp /tmp/ask-deploy/etc/cdx_sp.xml "${ASK_DIR}/etc/"
cp /tmp/ask-deploy/etc/fmc/config/hxs_pdl_v3.xml /etc/fmc/config/ 2>/dev/null || true

log "Phase 1 complete: binaries installed"

# ============================================================
# Phase 2: Fix conntrack (remove VyOS notrack rules)
# ============================================================
log "--- Phase 2: Fixing conntrack ---"

# Remove notrack rules from VyOS vyos_conntrack table
for family in ip ip6; do
    for chain in PREROUTING OUTPUT; do
        handle=$(nft -a list chain ${family} vyos_conntrack "${chain}" 2>/dev/null \
            | grep 'notrack' | grep -o 'handle [0-9]*' | awk '{print $2}' | head -n 1)
        if [ -n "$handle" ]; then
            nft delete rule ${family} vyos_conntrack "${chain}" handle "$handle" 2>/dev/null || true
            log "Removed notrack from ${family} vyos_conntrack ${chain} (handle $handle)"
        fi
    done
done

# Force-activate conntrack hooks via nft ct expression
# (kernel 6.6 lazy-loads hooks — need a ct expression to trigger nf_ct_netns_get)
if ! nft list table inet ct_force 2>/dev/null | grep -q 'ct state'; then
    log "Creating ct_force table to activate conntrack hooks..."
    nft add table inet ct_force 2>/dev/null || true
    nft add chain inet ct_force prerouting '{ type filter hook prerouting priority -200; }' 2>/dev/null || true
    nft add chain inet ct_force output '{ type filter hook output priority -200; }' 2>/dev/null || true
    nft add rule inet ct_force prerouting ct state new counter accept 2>/dev/null || true
    nft add rule inet ct_force output ct state new counter accept 2>/dev/null || true
fi

# Enable IP forwarding
sysctl -w net.ipv4.ip_forward=1 2>/dev/null || true

# Verify conntrack
sleep 1
CT_COUNT=$(cat /proc/net/nf_conntrack 2>/dev/null | wc -l)
log "Phase 2 complete: conntrack entries = ${CT_COUNT}"

# ============================================================
# Phase 3: Load CDX and FCI kernel modules
# ============================================================
log "--- Phase 3: Loading ASK kernel modules ---"

# Sign modules if needed (kernel may require signatures)
KDIR="/opt/vyos-dev/linux"
if [ -f "${KDIR}/certs/signing_key.pem" ] && command -v sign-file >/dev/null 2>&1; then
    sign-file sha512 "${KDIR}/certs/signing_key.pem" "${KDIR}/certs/signing_key.x509" "${MODDIR}/cdx.ko" 2>/dev/null || true
    sign-file sha512 "${KDIR}/certs/signing_key.pem" "${KDIR}/certs/signing_key.x509" "${MODDIR}/fci.ko" 2>/dev/null || true
fi

# Load CDX
if ! lsmod | grep -q '^cdx '; then
    log "Loading cdx.ko..."
    insmod "${MODDIR}/cdx.ko" 2>&1 | tee -a "$LOG" || true
    sleep 1
fi

if [ -e /dev/cdx_ctrl ]; then
    log "CDX loaded: /dev/cdx_ctrl present"
else
    log "WARNING: CDX /dev/cdx_ctrl not found — check dmesg"
    dmesg | tail -n 20 >> "$LOG"
fi

# Load FCI (depends on CDX)
if ! lsmod | grep -q '^fci '; then
    log "Loading fci.ko..."
    insmod "${MODDIR}/fci.ko" 2>&1 | tee -a "$LOG" || true
    sleep 1
fi

if lsmod | grep -q '^fci '; then
    log "FCI loaded"
else
    log "WARNING: FCI not loaded — check dmesg"
fi

log "Phase 3 complete: modules loaded"
lsmod | grep -E '^(cdx|fci)' | tee -a "$LOG"

# ============================================================
# Phase 4: Enable SFP+ TX (deassert TX_DISABLE via GPIO)
# ============================================================
log "--- Phase 4: Enabling SFP+ TX ---"

# SDK fsl_mac driver has no phylink/SFP awareness. The kernel SFP driver
# (sfp.c) binds to sfp-xfi0/sfp-xfi1 but its state machine never starts
# because no MAC calls sfp_bus_add_upstream(). TX_DISABLE stays asserted.
#
# Fix: Unbind the SFP driver (releasing the GPIO), then manually deassert
# TX_DISABLE via sysfs GPIO.
#
# GPIO mapping (Mono Gateway DK with hardware inverter):
#   Physical HIGH → inverter → SFP TX_DISABLE LOW → TX ENABLED
#
# eth3 (sfp-xfi0): GPIO2 pin 14 = Linux GPIO 590 (gpiochip576 base + 14)
# eth4 (sfp-xfi1): GPIO2 pin 15 = Linux GPIO 591 (gpiochip576 base + 15)

GPIOCHIP2_BASE=576
SFP_CAGES=(
    "sfp-xfi0:$((GPIOCHIP2_BASE + 14)):eth3"
    "sfp-xfi1:$((GPIOCHIP2_BASE + 15)):eth4"
)

for cage_info in "${SFP_CAGES[@]}"; do
    IFS=: read -r sfp_dev gpio_num iface <<< "$cage_info"

    # Unbind SFP driver if currently bound (releases GPIO)
    if [ -L "/sys/bus/platform/devices/$sfp_dev/driver" ]; then
        log "  $sfp_dev: unbinding SFP driver"
        echo "$sfp_dev" > /sys/bus/platform/drivers/sfp/unbind 2>/dev/null || true
        sleep 0.2
    fi

    # Export GPIO and deassert TX_DISABLE
    if [ ! -d "/sys/class/gpio/gpio${gpio_num}" ]; then
        echo "$gpio_num" > /sys/class/gpio/export 2>/dev/null || {
            log "  $sfp_dev: GPIO $gpio_num export failed"
            continue
        }
    fi
    echo out > "/sys/class/gpio/gpio${gpio_num}/direction" 2>/dev/null || true
    echo 1 > "/sys/class/gpio/gpio${gpio_num}/value" 2>/dev/null || true
    log "  $sfp_dev ($iface): TX_DISABLE deasserted (GPIO $gpio_num = HIGH)"
done

log "Phase 4 complete: SFP TX enabled"

# ============================================================
# Phase 5: Bring up all interfaces + DHCP
# ============================================================
log "--- Phase 5: Bringing up interfaces ---"

for iface in eth0 eth1 eth2 eth3 eth4; do
    if [ -d "/sys/class/net/$iface" ]; then
        ip link set "$iface" up 2>/dev/null || true
    fi
done

# Wait for copper 10GBASE-T negotiation on SFP-10G-T modules
sleep 3

# Request DHCP on SFP+ interfaces if they don't have an IP yet
for iface in eth3 eth4; do
    if [ -d "/sys/class/net/$iface" ]; then
        carrier=$(cat /sys/class/net/$iface/carrier 2>/dev/null || echo 0)
        existing_ip=$(ip -4 addr show "$iface" 2>/dev/null | grep -oP 'inet \K[\d.]+' | head -1)
        if [ "$carrier" = "1" ] && [ -z "$existing_ip" ]; then
            log "  Requesting DHCP on $iface..."
            dhclient -1 -q "$iface" 2>/dev/null &
        elif [ -n "$existing_ip" ]; then
            log "  $iface: already has IP $existing_ip"
        else
            log "  $iface: no carrier, skipping DHCP"
        fi
    fi
done
wait 2>/dev/null || true
sleep 2

for iface in eth0 eth1 eth2 eth3 eth4; do
    carrier=$(cat /sys/class/net/$iface/carrier 2>/dev/null || echo 0)
    speed=$(cat /sys/class/net/$iface/speed 2>/dev/null || echo "?")
    ip=$(ip -4 addr show "$iface" 2>/dev/null | grep -oP 'inet \K[\d./]+' | head -1)
    log "  $iface: carrier=$carrier speed=${speed}M ip=${ip:-none}"
done

log "Phase 5 complete: interfaces up"

# ============================================================
# Phase 6: CDX port registration via cdx_init
# ============================================================
log "--- Phase 6: CDX port registration ---"

# cdx_init registers 7 ports with CDX:
#   2x OH  (dpa-fman0-oh@2, dpa-fman0-oh@3) — PCD classifier ports
#   3x 1G  (MAC2/eth1, MAC5/eth2, MAC6/eth0) — RJ45 ports
#   2x 10G (MAC9/eth3, MAC10/eth4) — SFP+ ports
#
# NOTE: cdx_init MUST run on a clean system (first boot or after clean reboot).
# Re-running after rmmod/insmod cdx leaves stale QMan FQ state that causes
# "qman_init_fq failed for fqid 99" on OH ports. Use proc_cleanup.ko to
# remove stale /proc/oh* and /proc/fqid_stats entries if needed.

CDX_INIT=""
if [ -x /tmp/cdx_init ]; then
    CDX_INIT="/tmp/cdx_init"
elif [ -x /usr/local/bin/cdx_init ]; then
    CDX_INIT="/usr/local/bin/cdx_init"
fi

if [ -n "$CDX_INIT" ] && [ -e /dev/cdx_ctrl ]; then
    log "Running cdx_init..."
    $CDX_INIT -v 2>&1 | tee -a "$LOG"
    CDX_RC=${PIPESTATUS[0]}
    if [ $CDX_RC -eq 0 ]; then
        log "CDX port registration: SUCCESS (7 ports)"
    else
        log "WARNING: cdx_init failed (exit $CDX_RC) — check dmesg"
        dmesg | tail -n 10 >> "$LOG"
    fi
else
    log "WARNING: cdx_init not found or CDX not loaded — skipping port registration"
fi

log "Phase 6 complete"

# ============================================================
# Phase 7: Set up WAN/LAN forwarding topology
# ============================================================
log "--- Phase 7: Setting up forwarding topology ---"

# eth0 = LAN (10.99.0.1/24), eth1 = mgmt, eth2 = WAN with NAT
WAN_IF="eth2"
LAN_IF="eth0"
MGMT_IF="eth1"

LAN_SUBNET="10.99.0"
LAN_CURRENT=$(ip -4 addr show "${LAN_IF}" | grep -oP 'inet \K[\d.]+' | head -n 1)

if [ "$LAN_CURRENT" != "${LAN_SUBNET}.1" ]; then
    log "Setting ${LAN_IF} to ${LAN_SUBNET}.1/24..."
    ip addr flush dev "${LAN_IF}" 2>/dev/null || true
    ip addr add "${LAN_SUBNET}.1/24" dev "${LAN_IF}"
fi

# Default route via WAN
ip route del default 2>/dev/null || true
WAN_GW=$(ip -4 route show dev "${WAN_IF}" 2>/dev/null | grep -oP 'via \K[\d.]+' | head -1)
if [ -z "$WAN_GW" ]; then
    WAN_GW="192.168.1.1"
fi
ip route add default via "${WAN_GW}" dev "${WAN_IF}" 2>/dev/null || true

# NAT: masquerade LAN→WAN
nft add table ip nat 2>/dev/null || true
nft add chain ip nat postrouting '{ type nat hook postrouting priority 100; }' 2>/dev/null || true
if ! nft list chain ip nat postrouting 2>/dev/null | grep -q "masquerade"; then
    nft add rule ip nat postrouting oifname "${WAN_IF}" ip saddr "${LAN_SUBNET}.0/24" masquerade
fi

WAN_IP=$(ip -4 addr show "${WAN_IF}" | grep -oP 'inet \K[\d.]+' | head -n 1)
log "Forwarding topology configured"
log "  WAN: ${WAN_IF} (${WAN_IP:-dhcp})"
log "  LAN: ${LAN_IF} (${LAN_SUBNET}.1/24)"
log "  MGMT: ${MGMT_IF}"

log "Phase 7 complete"

# ============================================================
# Phase 8: Start CMM daemon
# ============================================================
log "--- Phase 8: Starting CMM daemon ---"

if [ -e /dev/cdx_ctrl ] && [ -x /usr/local/bin/cmm ]; then
    # Kill any existing CMM
    killall cmm 2>/dev/null || true
    sleep 1

    log "Starting CMM..."
    /usr/local/bin/cmm -f /etc/config/fastforward -n 65536 &
    CMM_PID=$!
    sleep 2

    if kill -0 $CMM_PID 2>/dev/null; then
        log "CMM running (PID $CMM_PID)"
    else
        log "WARNING: CMM exited — check dmesg"
    fi
else
    log "WARNING: CDX not loaded or cmm not installed — skipping CMM"
fi

# ============================================================
# Summary
# ============================================================
log ""
log "=== ASK Activation Summary ==="
log "Kernel:     $(uname -r)"
log "CDX:        $(test -e /dev/cdx_ctrl && echo 'LOADED' || echo 'NOT LOADED')"
log "FCI:        $(lsmod | grep -q '^fci ' && echo 'LOADED' || echo 'NOT LOADED')"
log "CMM:        $(pgrep -x cmm >/dev/null && echo "RUNNING (PID $(pgrep -x cmm))" || echo 'NOT RUNNING')"
log "Conntrack:  $(cat /proc/net/nf_conntrack 2>/dev/null | wc -l) entries"
log "IP Forward: $(cat /proc/sys/net/ipv4/ip_forward)"
log "Interfaces:"
for iface in eth0 eth1 eth2 eth3 eth4; do
    carrier=$(cat /sys/class/net/${iface}/carrier 2>/dev/null || echo "?")
    speed=$(cat /sys/class/net/${iface}/speed 2>/dev/null || echo "?")
    ip=$(ip -4 addr show "${iface}" 2>/dev/null | grep -oP 'inet \K[\d./]+' | head -n 1)
    log "  ${iface}: carrier=${carrier} speed=${speed}M ip=${ip:-none}"
done
log ""
log "To test forwarding: connect a host to ${LAN_IF} with IP ${LAN_SUBNET}.2/24 gw ${LAN_SUBNET}.1"
log "Then: iperf3 -c <WAN_TARGET> (traffic will be forwarded through the device)"
log "Check offloaded flows: cat /proc/net/nf_conntrack | grep fp"
log ""
log "Full log: ${LOG}"