#!/bin/bash
# pps-bench.sh — Small-frame PPS benchmark for LS1046A Mono Gateway
#
# Measures packets-per-second forwarding on kernel or VPP dataplane.
# Run on the DUT (Mono Gateway) while an external traffic generator
# sends 64-byte UDP packets through two ports.
#
# Usage:
#   pps-bench.sh [OPTIONS]
#
# Options:
#   -i IFACE    Ingress interface (default: eth3)
#   -o IFACE    Egress interface (default: eth4)
#   -d SECONDS  Duration of measurement (default: 30)
#   -s SECONDS  Sample interval (default: 1)
#   -v          VPP mode — read counters from VPP CLI instead of kernel
#   -c          Show per-CPU softirq breakdown
#   -q          Quiet — only print final summary
#   -h          Help
#
# Theory: 64-byte Ethernet frames at line rate
#   1G  = 1,488,095 pps  (84 bytes wire = 64 + 20 preamble/IFG)
#   10G = 14,880,952 pps
#
# Test topology:
#   [Generator port A] ---eth3--→ [DUT] ---eth4--→ [Generator port B]
#   Generator sends on port A, receives on port B.
#   DUT forwards between eth3 ↔ eth4 (L3 routing or VPP bridge).

set -euo pipefail

INGRESS="eth3"
EGRESS="eth4"
DURATION=30
INTERVAL=1
VPP_MODE=0
SHOW_CPU=0
QUIET=0

usage() {
    sed -n '2,/^$/s/^# //p' "$0"
    exit 0
}

while getopts "i:o:d:s:vcqh" opt; do
    case $opt in
        i) INGRESS="$OPTARG" ;;
        o) EGRESS="$OPTARG" ;;
        d) DURATION="$OPTARG" ;;
        s) INTERVAL="$OPTARG" ;;
        v) VPP_MODE=1 ;;
        c) SHOW_CPU=1 ;;
        q) QUIET=1 ;;
        h) usage ;;
        *) usage ;;
    esac
done

# Colors
RED='\033[0;31m'
GRN='\033[0;32m'
YEL='\033[0;33m'
CYN='\033[0;36m'
RST='\033[0m'

log() { [[ $QUIET -eq 0 ]] && echo -e "$@"; }

# ── Kernel counter helpers ─────────────────────────────────────────
read_kernel_counters() {
    local iface=$1
    local base="/sys/class/net/$iface/statistics"
    if [[ ! -d "$base" ]]; then
        echo "0 0 0 0"
        return
    fi
    local rx_pkt=$(cat "$base/rx_packets")
    local tx_pkt=$(cat "$base/tx_packets")
    local rx_bytes=$(cat "$base/rx_bytes")
    local tx_bytes=$(cat "$base/tx_bytes")
    echo "$rx_pkt $tx_pkt $rx_bytes $tx_bytes"
}

read_kernel_drops() {
    local iface=$1
    local base="/sys/class/net/$iface/statistics"
    if [[ ! -d "$base" ]]; then
        echo "0 0"
        return
    fi
    local rx_drop=$(cat "$base/rx_dropped")
    local tx_drop=$(cat "$base/tx_dropped")
    echo "$rx_drop $tx_drop"
}

# ── VPP counter helpers ───────────────────────────────────────────
read_vpp_counters() {
    # Returns: rx_pkt tx_pkt rx_bytes tx_bytes for a VPP interface
    local iface=$1
    local vpp_iface
    # Map kernel name to VPP interface name
    case "$iface" in
        eth3) vpp_iface="TenGigabitEthernet3" ;;
        eth4) vpp_iface="TenGigabitEthernet4" ;;
        *)    vpp_iface="$iface" ;;
    esac

    if ! command -v vppctl &>/dev/null; then
        echo "0 0 0 0"
        return
    fi

    # Parse VPP's show interface output
    local output
    output=$(vppctl show interface "$vpp_iface" 2>/dev/null || true)

    local rx_pkt=0 tx_pkt=0 rx_bytes=0 tx_bytes=0
    rx_pkt=$(echo "$output" | grep -oP 'rx packets\s+\K\d+' || echo 0)
    tx_pkt=$(echo "$output" | grep -oP 'tx packets\s+\K\d+' || echo 0)
    rx_bytes=$(echo "$output" | grep -oP 'rx bytes\s+\K\d+' || echo 0)
    tx_bytes=$(echo "$output" | grep -oP 'tx bytes\s+\K\d+' || echo 0)
    echo "$rx_pkt $tx_pkt $rx_bytes $tx_bytes"
}

# ── CPU measurement ───────────────────────────────────────────────
read_cpu_busy() {
    # Returns aggregate CPU busy% (user+system+softirq+steal)
    awk '/^cpu / {
        total = 0; for(i=2;i<=NF;i++) total+=$i
        idle = $5 + $6  # idle + iowait
        printf "%.1f", 100 * (1 - idle/total)
    }' /proc/stat
}

read_softirq_net() {
    # Returns per-CPU NET_RX softirq counts (space-separated)
    awk '/NET_RX/ { for(i=2;i<=NF;i++) printf "%s ", $i; print "" }' /proc/softirqs
}

# ── Formatting helpers ────────────────────────────────────────────
fmt_pps() {
    local pps=$1
    if (( pps >= 1000000 )); then
        awk "BEGIN {printf \"%.2f Mpps\", $pps / 1000000}"
    elif (( pps >= 1000 )); then
        awk "BEGIN {printf \"%.1f Kpps\", $pps / 1000}"
    else
        printf "%d pps" "$pps"
    fi
}

fmt_bps() {
    local bps=$1
    if (( bps >= 1000000000 )); then
        awk "BEGIN {printf \"%.2f Gbps\", $bps / 1000000000}"
    elif (( bps >= 1000000 )); then
        awk "BEGIN {printf \"%.1f Mbps\", $bps / 1000000}"
    else
        awk "BEGIN {printf \"%.1f Kbps\", $bps / 1000}"
    fi
}

# ── Pre-flight checks ────────────────────────────────────────────
log "${CYN}═══════════════════════════════════════════════════════════════${RST}"
log "${CYN}  PPS Benchmark — LS1046A Mono Gateway${RST}"
log "${CYN}═══════════════════════════════════════════════════════════════${RST}"

MODE_STR="kernel"
[[ $VPP_MODE -eq 1 ]] && MODE_STR="VPP"

log ""
log "  Mode:      ${YEL}${MODE_STR}${RST}"
log "  Ingress:   ${YEL}${INGRESS}${RST}"
log "  Egress:    ${YEL}${EGRESS}${RST}"
log "  Duration:  ${YEL}${DURATION}s${RST}"
log "  Interval:  ${YEL}${INTERVAL}s${RST}"
log ""

# Verify interfaces exist
if [[ $VPP_MODE -eq 0 ]]; then
    for iface in "$INGRESS" "$EGRESS"; do
        if [[ ! -d "/sys/class/net/$iface" ]]; then
            echo "ERROR: Interface $iface not found in /sys/class/net/" >&2
            echo "Available: $(ls /sys/class/net/ | tr '\n' ' ')" >&2
            exit 1
        fi
    done
    # Show link state
    for iface in "$INGRESS" "$EGRESS"; do
        state=$(cat "/sys/class/net/$iface/operstate" 2>/dev/null || echo "unknown")
        speed=$(cat "/sys/class/net/$iface/speed" 2>/dev/null || echo "?")
        log "  $iface: state=${YEL}${state}${RST}  speed=${YEL}${speed}Mbps${RST}"
    done
else
    if ! command -v vppctl &>/dev/null; then
        echo "ERROR: vppctl not found — VPP not installed?" >&2
        exit 1
    fi
    if ! vppctl show version &>/dev/null; then
        echo "ERROR: VPP not running (vppctl failed)" >&2
        exit 1
    fi
    log "  VPP: $(vppctl show version 2>/dev/null | head -1)"
fi

# Check IP forwarding (kernel mode)
if [[ $VPP_MODE -eq 0 ]]; then
    fwd=$(cat /proc/sys/net/ipv4/ip_forward)
    if [[ "$fwd" != "1" ]]; then
        log "  ${RED}WARNING: ip_forward=0 — packets won't be forwarded${RST}"
        log "  ${RED}  Fix: sysctl -w net.ipv4.ip_forward=1${RST}"
    fi
fi

log ""
log "${CYN}───────────────────────────────────────────────────────────────${RST}"

# ── Read counters function (mode-aware) ───────────────────────────
read_counters() {
    local iface=$1
    if [[ $VPP_MODE -eq 1 ]]; then
        read_vpp_counters "$iface"
    else
        read_kernel_counters "$iface"
    fi
}

# ── Measurement loop ─────────────────────────────────────────────
SAMPLES=0
TOTAL_IN_PPS=0
TOTAL_OUT_PPS=0
PEAK_IN_PPS=0
PEAK_OUT_PPS=0

# Take initial snapshot
read -r IN_RX0 IN_TX0 IN_RXB0 IN_TXB0 <<< "$(read_counters "$INGRESS")"
read -r OUT_RX0 OUT_TX0 OUT_RXB0 OUT_TXB0 <<< "$(read_counters "$EGRESS")"
IN_DROP0=$(read_kernel_drops "$INGRESS" | awk '{print $1}')
OUT_DROP0=$(read_kernel_drops "$EGRESS" | awk '{print $1}')
[[ $SHOW_CPU -eq 1 ]] && SIRQ0=($(read_softirq_net))

ELAPSED=0
if [[ $QUIET -eq 0 ]]; then
    printf "%-6s  %-14s  %-14s  %-12s  %-12s  %s\n" \
        "Time" "IN rx pps" "OUT tx pps" "IN rx bps" "OUT tx bps" "CPU%"
    printf "%-6s  %-14s  %-14s  %-12s  %-12s  %s\n" \
        "──────" "──────────────" "──────────────" "────────────" "────────────" "─────"
fi

while (( ELAPSED < DURATION )); do
    sleep "$INTERVAL"
    ELAPSED=$((ELAPSED + INTERVAL))

    # Read new counters
    read -r IN_RX1 IN_TX1 IN_RXB1 IN_TXB1 <<< "$(read_counters "$INGRESS")"
    read -r OUT_RX1 OUT_TX1 OUT_RXB1 OUT_TXB1 <<< "$(read_counters "$EGRESS")"
    CPU=$(read_cpu_busy)

    # Compute deltas (PPS and BPS)
    IN_RX_PPS=$(( (IN_RX1 - IN_RX0) / INTERVAL ))
    OUT_TX_PPS=$(( (OUT_TX1 - OUT_TX0) / INTERVAL ))
    IN_RX_BPS=$(( (IN_RXB1 - IN_RXB0) * 8 / INTERVAL ))
    OUT_TX_BPS=$(( (OUT_TXB1 - OUT_TXB0) * 8 / INTERVAL ))

    # Track stats
    SAMPLES=$((SAMPLES + 1))
    TOTAL_IN_PPS=$((TOTAL_IN_PPS + IN_RX_PPS))
    TOTAL_OUT_PPS=$((TOTAL_OUT_PPS + OUT_TX_PPS))
    (( IN_RX_PPS > PEAK_IN_PPS )) && PEAK_IN_PPS=$IN_RX_PPS
    (( OUT_TX_PPS > PEAK_OUT_PPS )) && PEAK_OUT_PPS=$OUT_TX_PPS

    if [[ $QUIET -eq 0 ]]; then
        printf "%-6s  %-14s  %-14s  %-12s  %-12s  %s%%\n" \
            "${ELAPSED}s" \
            "$(fmt_pps $IN_RX_PPS)" \
            "$(fmt_pps $OUT_TX_PPS)" \
            "$(fmt_bps $IN_RX_BPS)" \
            "$(fmt_bps $OUT_TX_BPS)" \
            "$CPU"
    fi

    # Update baseline
    IN_RX0=$IN_RX1; IN_TX0=$IN_TX1; IN_RXB0=$IN_RXB1; IN_TXB0=$IN_TXB1
    OUT_RX0=$OUT_RX1; OUT_TX0=$OUT_TX1; OUT_RXB0=$OUT_RXB1; OUT_TXB0=$OUT_TXB1
done

# ── Final summary ────────────────────────────────────────────────
# Read final drop counters
IN_DROP1=$(read_kernel_drops "$INGRESS" | awk '{print $1}')
OUT_DROP1=$(read_kernel_drops "$EGRESS" | awk '{print $1}')
IN_DROPS=$((IN_DROP1 - IN_DROP0))
OUT_DROPS=$((OUT_DROP1 - OUT_DROP0))

# Compute averages
AVG_IN_PPS=0
AVG_OUT_PPS=0
if (( SAMPLES > 0 )); then
    AVG_IN_PPS=$((TOTAL_IN_PPS / SAMPLES))
    AVG_OUT_PPS=$((TOTAL_OUT_PPS / SAMPLES))
fi

# Compute forwarding ratio
FWD_RATIO="N/A"
if (( AVG_IN_PPS > 0 )); then
    FWD_RATIO=$(awk "BEGIN {printf \"%.1f\", $AVG_OUT_PPS * 100 / $AVG_IN_PPS}" 2>/dev/null || echo "N/A")
    FWD_RATIO="${FWD_RATIO}%"
fi

# Theoretical line rate for reference
IN_SPEED=$(cat "/sys/class/net/$INGRESS/speed" 2>/dev/null || echo 0)
OUT_SPEED=$(cat "/sys/class/net/$EGRESS/speed" 2>/dev/null || echo 0)
# Line rate at 64-byte frames: speed_bps / (84 bytes * 8 bits)
IN_LINE_RATE=0
OUT_LINE_RATE=0
if (( IN_SPEED > 0 )); then
    IN_LINE_RATE=$(awk "BEGIN {printf \"%d\", $IN_SPEED * 1000000 / 672}")
fi
if (( OUT_SPEED > 0 )); then
    OUT_LINE_RATE=$(awk "BEGIN {printf \"%d\", $OUT_SPEED * 1000000 / 672}")
fi

echo ""
echo -e "${CYN}═══════════════════════════════════════════════════════════════${RST}"
echo -e "${CYN}  PPS Benchmark Results — ${MODE_STR} mode${RST}"
echo -e "${CYN}═══════════════════════════════════════════════════════════════${RST}"
echo ""
echo -e "  Ingress ($INGRESS):"
echo -e "    Average RX:    ${GRN}$(fmt_pps $AVG_IN_PPS)${RST}"
echo -e "    Peak RX:       ${GRN}$(fmt_pps $PEAK_IN_PPS)${RST}"
if (( IN_LINE_RATE > 0 )); then
    IN_PCT=$(awk "BEGIN {printf \"%.1f\", $PEAK_IN_PPS * 100 / $IN_LINE_RATE}" 2>/dev/null || echo "?")
    echo -e "    Line rate:     $(fmt_pps $IN_LINE_RATE)  (peak = ${YEL}${IN_PCT}%${RST} of line rate)"
fi
echo -e "    Drops:         ${RED}${IN_DROPS}${RST}"
echo ""
echo -e "  Egress ($EGRESS):"
echo -e "    Average TX:    ${GRN}$(fmt_pps $AVG_OUT_PPS)${RST}"
echo -e "    Peak TX:       ${GRN}$(fmt_pps $PEAK_OUT_PPS)${RST}"
if (( OUT_LINE_RATE > 0 )); then
    OUT_PCT=$(awk "BEGIN {printf \"%.1f\", $PEAK_OUT_PPS * 100 / $OUT_LINE_RATE}" 2>/dev/null || echo "?")
    echo -e "    Line rate:     $(fmt_pps $OUT_LINE_RATE)  (peak = ${YEL}${OUT_PCT}%${RST} of line rate)"
fi
echo -e "    Drops:         ${RED}${OUT_DROPS}${RST}"
echo ""
echo -e "  Forwarding ratio:  ${YEL}${FWD_RATIO}${RST}  (egress TX / ingress RX)"
echo -e "  Duration:          ${DURATION}s  (${SAMPLES} samples)"
echo ""

# Per-CPU softirq delta (kernel mode only)
if [[ $SHOW_CPU -eq 1 ]] && [[ $VPP_MODE -eq 0 ]]; then
    SIRQ1=($(read_softirq_net))
    echo -e "  ${CYN}Per-CPU NET_RX softirqs:${RST}"
    for ((i=0; i<${#SIRQ1[@]}; i++)); do
        delta=$((${SIRQ1[$i]} - ${SIRQ0[$i]:-0}))
        printf "    CPU%-2d: %d\n" "$i" "$delta"
    done
    echo ""
fi

echo -e "${CYN}═══════════════════════════════════════════════════════════════${RST}"

# ── Machine-readable output (for scripted comparisons) ────────────
cat << JSONEOF
{
  "mode": "$MODE_STR",
  "ingress": "$INGRESS",
  "egress": "$EGRESS",
  "duration_sec": $DURATION,
  "avg_in_pps": $AVG_IN_PPS,
  "avg_out_pps": $AVG_OUT_PPS,
  "peak_in_pps": $PEAK_IN_PPS,
  "peak_out_pps": $PEAK_OUT_PPS,
  "in_drops": $IN_DROPS,
  "out_drops": $OUT_DROPS,
  "fwd_ratio": "$FWD_RATIO",
  "in_line_rate_pps": $IN_LINE_RATE,
  "out_line_rate_pps": $OUT_LINE_RATE
}
JSONEOF