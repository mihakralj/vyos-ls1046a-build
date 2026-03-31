#!/bin/bash
# pps-gen.sh — Traffic generator for PPS benchmark (runs on EXTERNAL machine)
#
# Sends 64-byte UDP packets at maximum rate from a Linux machine to the
# LS1046A Mono Gateway DUT. Uses Linux kernel pktgen module for wire-rate
# small-frame generation (no DPDK required on generator).
#
# Requirements:
#   - Linux machine with kernel pktgen module (most distros)
#   - Direct connection to DUT ingress port (no switch preferred)
#   - Root access
#
# Usage:
#   pps-gen.sh -i IFACE -d DST_IP [-s SRC_IP] [-m DST_MAC] [-t SECONDS] [-n THREADS]
#
# Options:
#   -i IFACE    Generator egress interface (e.g., enp1s0)
#   -d DST_IP   Destination IP (DUT ingress IP)
#   -s SRC_IP   Source IP (default: 10.99.0.1)
#   -m DST_MAC  Destination MAC (DUT ingress MAC, required for L2)
#   -t SECONDS  Duration (default: 30)
#   -n THREADS  Number of CPU threads (default: 1)
#   -p SIZE     Packet size in bytes (default: 64, min 60)
#   -r RATE     Rate limit in pps (default: 0 = max rate)
#   -h          Help
#
# Example — blast 64-byte UDP at DUT's eth3:
#   ./pps-gen.sh -i enp1s0 -d 10.99.0.2 -m aa:bb:cc:dd:ee:ff -t 30
#
# Example — 4 threads, 128-byte packets, 60 seconds:
#   ./pps-gen.sh -i enp1s0 -d 10.99.0.2 -m aa:bb:cc:dd:ee:ff -t 60 -n 4 -p 128
#
# Theory: 64-byte frames at 10G = 14.88 Mpps
# A single x86 core typically generates 3-6 Mpps with kernel pktgen.
# Use -n 4 for higher rates on multi-core generators.
#
# NOTE: pktgen bypasses the kernel networking stack entirely —
# packets go directly from pktgen → NIC driver → wire.
# It does NOT use sockets, iptables, or routing.

set -euo pipefail

IFACE=""
DST_IP=""
SRC_IP="10.99.0.1"
DST_MAC=""
DURATION=30
THREADS=1
PKT_SIZE=64
RATE=0  # 0 = max

usage() {
    sed -n '2,/^set -/s/^# //p' "$0"
    exit 0
}

while getopts "i:d:s:m:t:n:p:r:h" opt; do
    case $opt in
        i) IFACE="$OPTARG" ;;
        d) DST_IP="$OPTARG" ;;
        s) SRC_IP="$OPTARG" ;;
        m) DST_MAC="$OPTARG" ;;
        t) DURATION="$OPTARG" ;;
        n) THREADS="$OPTARG" ;;
        p) PKT_SIZE="$OPTARG" ;;
        r) RATE="$OPTARG" ;;
        h) usage ;;
        *) usage ;;
    esac
done

# Validate required args
if [[ -z "$IFACE" ]] || [[ -z "$DST_IP" ]]; then
    echo "ERROR: -i IFACE and -d DST_IP are required" >&2
    usage
fi

if [[ $EUID -ne 0 ]]; then
    echo "ERROR: Must run as root (pktgen requires kernel module)" >&2
    exit 1
fi

# Minimum Ethernet frame = 60 bytes (14 header + 46 payload)
(( PKT_SIZE < 60 )) && PKT_SIZE=60

echo "═══════════════════════════════════════════════════════════"
echo "  PPS Traffic Generator — Linux kernel pktgen"
echo "═══════════════════════════════════════════════════════════"
echo ""
echo "  Interface:  $IFACE"
echo "  Dst IP:     $DST_IP"
echo "  Src IP:     $SRC_IP"
echo "  Dst MAC:    ${DST_MAC:-auto (ARP)}"
echo "  Pkt size:   ${PKT_SIZE} bytes"
echo "  Duration:   ${DURATION}s"
echo "  Threads:    $THREADS"
echo "  Rate limit: $([ "$RATE" -eq 0 ] && echo 'max' || echo "${RATE} pps")"
echo ""

# ── Load pktgen module ────────────────────────────────────────────
if ! lsmod | grep -q pktgen; then
    echo "Loading pktgen module..."
    modprobe pktgen
fi

PGDIR=/proc/net/pktgen

if [[ ! -d "$PGDIR" ]]; then
    echo "ERROR: /proc/net/pktgen not found — kernel pktgen module failed" >&2
    exit 1
fi

# ── Auto-detect DST_MAC if not provided ───────────────────────────
if [[ -z "$DST_MAC" ]]; then
    echo "Resolving MAC for $DST_IP via ARP..."
    # Ensure interface is up with an IP
    ip link set "$IFACE" up 2>/dev/null || true
    # Check if we have an IP on this interface
    if ! ip addr show "$IFACE" | grep -q "inet "; then
        echo "  Assigning $SRC_IP/24 to $IFACE"
        ip addr add "$SRC_IP/24" dev "$IFACE" 2>/dev/null || true
    fi
    # ARP ping
    arping -I "$IFACE" -c 3 "$DST_IP" &>/dev/null || true
    DST_MAC=$(arp -n "$DST_IP" 2>/dev/null | awk '/ether/ {print $3}')
    if [[ -z "$DST_MAC" ]]; then
        echo "ERROR: Cannot resolve MAC for $DST_IP — use -m to specify" >&2
        exit 1
    fi
    echo "  Resolved: $DST_IP → $DST_MAC"
fi

# ── Configure pktgen threads ─────────────────────────────────────

# Reset any previous config
echo "reset" > "$PGDIR/pgctrl" 2>/dev/null || true

# UDP port range for flow diversity (helps RSS distribution on DUT)
UDP_SRC_MIN=1024
UDP_SRC_MAX=65535
UDP_DST=9  # discard port

for ((t=0; t<THREADS; t++)); do
    THREAD="kpktgend_${t}"
    DEV="${IFACE}@${t}"

    if [[ ! -f "$PGDIR/$THREAD" ]]; then
        echo "WARNING: Thread $THREAD not found (only $(ls $PGDIR/kpktgend_* 2>/dev/null | wc -l) available)"
        continue
    fi

    # Remove previous device from thread
    echo "rem_device_all" > "$PGDIR/$THREAD"

    # Add device to thread
    echo "add_device $DEV" > "$PGDIR/$THREAD"

    # Configure the device
    DEV_FILE="$PGDIR/$DEV"

    # Packet count: 0 = infinite (run until stopped)
    echo "count 0" > "$DEV_FILE"

    # Packet size (Ethernet frame minus CRC, pktgen adds CRC)
    echo "pkt_size $PKT_SIZE" > "$DEV_FILE"
    echo "min_pkt_size $PKT_SIZE" > "$DEV_FILE"
    echo "max_pkt_size $PKT_SIZE" > "$DEV_FILE"

    # Rate limiting (0 = wire rate)
    if (( RATE > 0 )); then
        # Per-thread rate
        PER_THREAD_RATE=$((RATE / THREADS))
        echo "ratep $PER_THREAD_RATE" > "$DEV_FILE"
    fi

    # Delay between packets (nanoseconds, 0 = max)
    echo "delay 0" > "$DEV_FILE"

    # Source/destination
    echo "dst $DST_IP" > "$DEV_FILE"
    echo "src_min $SRC_IP" > "$DEV_FILE"
    echo "src_max $SRC_IP" > "$DEV_FILE"
    echo "dst_mac $DST_MAC" > "$DEV_FILE"

    # UDP ports — vary source for flow distribution
    echo "udp_src_min $UDP_SRC_MIN" > "$DEV_FILE"
    echo "udp_src_max $UDP_SRC_MAX" > "$DEV_FILE"
    echo "udp_dst_min $UDP_DST" > "$DEV_FILE"
    echo "udp_dst_max $UDP_DST" > "$DEV_FILE"
    echo "flag UDPSRC_RND" > "$DEV_FILE"

    echo "  Configured thread $t: $DEV → $DST_IP ($DST_MAC)"
done

# ── Run pktgen ───────────────────────────────────────────────────
echo ""
echo "Starting traffic generation for ${DURATION}s..."
echo "  Press Ctrl+C to stop early"
echo ""

# Start in background, stop after duration
echo "start" > "$PGDIR/pgctrl" &
PKTGEN_PID=$!

# Wait for duration, then stop
sleep "$DURATION"
echo "stop" > "$PGDIR/pgctrl" 2>/dev/null || true
wait $PKTGEN_PID 2>/dev/null || true

# ── Collect results ──────────────────────────────────────────────
echo ""
echo "═══════════════════════════════════════════════════════════"
echo "  Generator Results"
echo "═══════════════════════════════════════════════════════════"
echo ""

TOTAL_PKTS=0
TOTAL_BYTES=0
TOTAL_ERRORS=0

for ((t=0; t<THREADS; t++)); do
    DEV="${IFACE}@${t}"
    DEV_FILE="$PGDIR/$DEV"
    [[ ! -f "$DEV_FILE" ]] && continue

    RESULT=$(cat "$DEV_FILE")

    # Parse results
    PKTS=$(echo "$RESULT" | grep -oP 'sofar: \K\d+' || echo 0)
    ERRS=$(echo "$RESULT" | grep -oP 'errors: \K\d+' || echo 0)
    PPS_VAL=$(echo "$RESULT" | grep -oP '\d+pps' | head -1 | tr -d 'pps' || echo 0)
    MBPS_VAL=$(echo "$RESULT" | grep -oP '\d+Mb/sec' | head -1 | tr -d 'Mb/sec' || echo 0)

    TOTAL_PKTS=$((TOTAL_PKTS + PKTS))
    TOTAL_ERRORS=$((TOTAL_ERRORS + ERRS))

    printf "  Thread %d: %s packets, %s pps, %s Mb/s, %s errors\n" \
        "$t" "$PKTS" "$PPS_VAL" "$MBPS_VAL" "$ERRS"
done

echo ""
echo "  Total packets sent: $TOTAL_PKTS"
echo "  Total errors:       $TOTAL_ERRORS"
if (( DURATION > 0 && TOTAL_PKTS > 0 )); then
    AVG_PPS=$((TOTAL_PKTS / DURATION))
    echo "  Average rate:       $AVG_PPS pps"
fi
echo ""
echo "═══════════════════════════════════════════════════════════"

# ── Cleanup ──────────────────────────────────────────────────────
for ((t=0; t<THREADS; t++)); do
    THREAD="kpktgend_${t}"
    [[ -f "$PGDIR/$THREAD" ]] && echo "rem_device_all" > "$PGDIR/$THREAD" 2>/dev/null || true
done