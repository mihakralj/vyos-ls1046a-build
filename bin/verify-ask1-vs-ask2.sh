#!/bin/bash
# verify-ask1-vs-ask2.sh — comparative tests: ASK 1.0 vs ASK2
#
# ASK 1.0  = NXP vendor stack (cdx.ko + fci + cmm, no XML at runtime) on
#            OpenWrt "Mono" root@192.168.1.110 (board .110)
# ASK2     = in-tree fman_pcd + ask.ko on VyOS vyos@192.168.1.185 (board .185)
#
# Lanes (both live simultaneously; select DUT by source subnet):
#   ASK2 lane: heidi 10.99.1.15 -> .185 eth3 10.99.1.185 -> eth4 10.99.2.185 -> HELGA 10.99.2.16
#   ASK1 lane: heidi 10.99.11.15 -> .110 eth3 10.99.1.110 -> eth4 10.99.2.110 -> HELGA 10.99.12.16
#
# Generators:
#   heidi  192.168.1.15  (Proxmox, admin+sudo, iperf3 client)
#   HELGA  192.168.1.16  (Windows 11, miha@ admin, iperf3.exe server)
#
# iperf3 methodology: multi-core (-P N parallel streams) + --bidir, per operator.
#
# Usage:
#   verify-ask1-vs-ask2.sh check
#   verify-ask1-vs-ask2.sh throughput [ask1|ask2|both] [-P N] [-t SEC]
#   verify-ask1-vs-ask2.sh offload-evidence [ask1|ask2|both] [-t SEC]
#   verify-ask1-vs-ask2.sh cpu-profile [ask1|ask2|both] [-P N] [-t SEC]
#   verify-ask1-vs-ask2.sh ask1-fastpath [-P N] [-t SEC]
#   verify-ask1-vs-ask2.sh all [-P N] [-t SEC]

set -u

SSH_BASE="ssh -o ConnectTimeout=8 -o StrictHostKeyChecking=no -o BatchMode=yes"
K110="$HOME/.ssh/admin_key"
K185="$HOME/.ssh/vyos_key"
KHD="$HOME/.ssh/id_ed25519"

# .110 (ASK 1.0, OpenWrt)
H110="root@192.168.1.110"
S110() { $SSH_BASE -i "$K110" "$H110" "$@"; }

# .185 (ASK2, VyOS)
H185="vyos@192.168.1.185"
S185() { $SSH_BASE -i "$K185" "$H185" "$@"; }

# heidi (Linux generator)
HHD="admin@192.168.1.15"
SHD() { $SSH_BASE -i "$KHD" "$HHD" "$@"; }

# HELGA (Windows generator)
HHG="miha@192.168.1.16"
SHG() { $SSH_BASE -i "$KHD" "$HHG" "$@"; }

IPERF3_WIN='C:\Users\miha\AppData\Local\Microsoft\WinGet\Packages\ar51an.iPerf3_Microsoft.Winget.Source_8wekyb3d8bbwe\iperf3.exe'

# lane definitions: lane|client_src|server_dst|server_port|dut
LANE_ASK2="10.99.1.15|10.99.2.16|5201|185"
LANE_ASK1="10.99.11.15|10.99.12.16|5202|110"

OUTDIR="${OUTDIR:-/tmp/ask1-vs-ask2}"
mkdir -p "$OUTDIR"

log()  { echo "[$(date +%H:%M:%S)] $*"; }
fail() { echo "ERROR: $*" >&2; exit 1; }

lane_field() { echo "$1" | cut -d'|' -f"$2"; }

# ---------------------------------------------------------------------------
# check: verify harness plumbing
# ---------------------------------------------------------------------------
cmd_check() {
  local ok=1
  log "checking .110 (ASK1 board)"
  S110 'uname -r; cmm --version 2>/dev/null; echo vwd=$(cat /sys/class/vwd/vwd0/vwd_fast_path_enable 2>/dev/null); lsmod | grep -cE "^(cdx|fci) "' || ok=0
  log "checking .185 (ASK2 board)"
  S185 'uname -r; sudo ynl --family ask --do get-info 2>/dev/null | head -c 200' || ok=0
  log "checking heidi"
  SHD 'iperf3 --version | head -1; ip -br addr show vmbr0 | grep -o "10.99.\(1\|11\).15"' || ok=0
  log "checking HELGA"
  SHG "where iperf3 & netstat -ano | findstr /C:LISTENING | findstr 520" || ok=0
  log "lane pings (TCP-only liveness: HELGA drops ICMP on secondary IPs)"
  helga_server_up 10.99.2.16 5201
  helga_server_up 10.99.12.16 5202
  log "  ask2 lane (via .185): $(SHD 'iperf3 -c 10.99.2.16 -B 10.99.1.15 -p 5201 -t 2 2>&1 | tail -1' 2>/dev/null)"
  log "  ask1 lane (via .110): $(SHD 'iperf3 -c 10.99.12.16 -B 10.99.11.15 -p 5202 -t 2 2>&1 | tail -1' 2>/dev/null)"
  [ $ok -eq 1 ] && log "check: OK" || fail "check failed"
}

# ---------------------------------------------------------------------------
# HELGA iperf3 server management (Windows, detached via schtasks)
# ---------------------------------------------------------------------------
helga_server_up() { # <bind-ip> <port>
  local ip="$1" port="$2"
  SHG "netstat -ano | findstr /C:\"$ip:$port\" | findstr LISTENING" >/dev/null 2>&1 && return 0
  local tn="iperf3-$port"
  SHG "schtasks /create /tn $tn /tr \"$IPERF3_WIN -s -B $ip -p $port\" /sc once /st 00:00 /ru SYSTEM /f >nul 2>&1; schtasks /run /tn $tn >nul 2>&1"
  for _ in 1 2 3 4 5; do
    sleep 1
    SHG "netstat -ano | findstr /C:\"$ip:$port\" | findstr LISTENING" >/dev/null 2>&1 && return 0
  done
  return 1
}

# ---------------------------------------------------------------------------
# DUT CPU sampling over a run window
# ---------------------------------------------------------------------------
cpu_snapshot() { # <host-fn> -> prints "cpu0 idle total cpu1 ..." busy since boot
  "$1" "grep -E '^cpu[0-9]' /proc/stat | awk '{print \$1, \$5, \$2+\$3+\$4+\$5+\$6+\$7+\$8+\$9+\$10}'"
}

cpu_delta() { # <before-file> <after-file> -> prints "cpuN BUSY%"
  local b="$1" a="$2" n b_idle b_tot a_idle a_tot
  while read -r n b_idle b_tot; do
    read -r _ a_idle a_tot < <(grep "^$n " "$a")
    awk -v n="$n" -v bi="$b_idle" -v bt="$b_tot" -v ai="$a_idle" -v at="$a_tot" \
      'BEGIN{db=bt-bi; da=at-bt; if(da>0) printf "%s %5.1f%%\n", n, 100*(1-(ai-bi)/da)}'
  done < "$b"
}

# ---------------------------------------------------------------------------
# throughput run
# ---------------------------------------------------------------------------
run_throughput() { # <lane> <streams> <duration> -> prints JSON-ish summary
  local lane="$1" P="$2" T="$3"
  local src dst port dut
  src=$(lane_field "$lane" 1); dst=$(lane_field "$lane" 2)
  port=$(lane_field "$lane" 3); dut=$(lane_field "$lane" 4)
  log "throughput: lane=$dut src=$src dst=$dst P=$P T=${T}s bidir"
  [ "$dut" = "185" ] && S185 'sudo conntrack -F 2>/dev/null' >/dev/null 2>&1
  helga_server_up "$dst" "$port" || { log "HELGA server $dst:$port not up"; return 1; }
  SHD "iperf3 -c $dst -B $src -p $port -t $T -P $P --bidir --json" > "$OUTDIR/iperf-$dut.json" 2>/dev/null \
    || { log "iperf3 client failed (lane=$dut)"; return 1; }
  python3 - "$OUTDIR/iperf-$dut.json" "$dut" <<'EOF'
import json,sys
d=json.load(open(sys.argv[1])); dut=sys.argv[2]
end=d["end"]
print(f"lane={dut} streams={len(d.get('streams',[]))} "
      f"sent={end.get('sum_sent',{}).get('bits_per_second',0)/1e9:.2f}G "
      f"recv={end.get('sum_received',{}).get('bits_per_second',0)/1e9:.2f}G "
      f"bidir={end.get('sum',{}).get('bits_per_second',0)/1e9:.2f}G")
EOF
}

# ---------------------------------------------------------------------------
# offload evidence per DUT
# ---------------------------------------------------------------------------
offload_185() {
  log "  .185 ASK2 offload evidence:"
  S185 'sudo ynl --family ask --dump dump-flows --output-json 2>/dev/null | python3 -c "
import json,sys
d=json.load(sys.stdin)
hw=[f for f in d if f.get(\"offloaded\")]
pkts=sum(f.get(\"packets\") or 0 for f in hw); byt=sum(f.get(\"bytes\") or 0 for f in hw)
fwd=[f for f in hw if f.get(\"src-ip\")==\"0a63010f\"]
rev=[f for f in hw if f.get(\"src-ip\")==\"0a630210\"]
fp=sum(f.get(\"packets\") or 0 for f in fwd); rp=sum(f.get(\"packets\") or 0 for f in rev)
print(f\"    flows={len(d)} hw_offloaded={len(hw)} hw_packets={pkts} hw_bytes={byt}\")
print(f\"    direction: eth3-ingress(heidi->HELGA) pkts={fp} | eth4-ingress(HELGA->heidi) pkts={rp}\")" 2>/dev/null || echo "    dump-flows unavailable"'
  S185 'sudo conntrack -L 2>/dev/null | grep -c HW_OFFLOAD' 2>/dev/null \
    | sed 's/^/    conntrack [HW_OFFLOAD] entries: /'
}

offload_110() {
  log "  .110 ASK1 offload evidence:"
  S110 'echo "    vwd_fast_path_enable=$(cat /sys/class/vwd/vwd0/vwd_fast_path_enable 2>/dev/null)"
for p in eth3 eth4; do
  n=$(ls /proc/fqid_stats/pcd/$p 2>/dev/null | wc -l)
  f=$(grep -rh "frame count" /proc/fqid_stats/pcd/$p/* 2>/dev/null | awk "{s+=\$3} END{print s+0}")
  echo "    pcd/$p fqids=$n total_frame_count=$f"
done'
}

# ---------------------------------------------------------------------------
# cpu profile run
# ---------------------------------------------------------------------------
run_cpu() { # <lane> <streams> <duration>
  local lane="$1" P="$2" T="$3"
  local dut src dst port
  src=$(lane_field "$lane" 1); dst=$(lane_field "$lane" 2)
  port=$(lane_field "$lane" 3); dut=$(lane_field "$lane" 4)
  log "cpu-profile: lane=$dut P=$P T=${T}s"
  [ "$dut" = "185" ] && S185 'sudo conntrack -F 2>/dev/null' >/dev/null 2>&1
  helga_server_up "$dst" "$port" || return 1
  local snap
  if [ "$dut" = "185" ]; then snap=S185; else snap=S110; fi
  cpu_snapshot "$snap" > "$OUTDIR/cpu-$dut-before.txt"
  SHD "iperf3 -c $dst -B $src -p $port -t $T -P $P --bidir --json" >/dev/null 2>&1
  cpu_snapshot "$snap" > "$OUTDIR/cpu-$dut-after.txt"
  log "  DUT .$dut per-core busy% during run:"
  cpu_delta "$OUTDIR/cpu-$dut-before.txt" "$OUTDIR/cpu-$dut-after.txt" | sed 's/^/    /'
}

# ---------------------------------------------------------------------------
# sustained multi-core multi-flow bidirectional run with steady-state CPU
# ---------------------------------------------------------------------------
run_sustained() { # <lane> <P> <T>
  local lane="$1" P="$2" T="$3"
  local src dst port dut
  src=$(lane_field "$lane" 1); dst=$(lane_field "$lane" 2)
  port=$(lane_field "$lane" 3); dut=$(lane_field "$lane" 4)
  [ "$T" -gt 20 ] || { echo "T must be >20s (warmup window)"; return 1; }
  log "sustained: lane=$dut P=$P T=${T}s bidir multi-flow"
  local gov snap
  if [ "$dut" = "185" ]; then
    snap=S185; gov=$(S185 'cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor')
    S185 'sudo conntrack -F 2>/dev/null' >/dev/null 2>&1
  else
    snap=S110; gov=$(S110 'cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor')
  fi
  log "  governor=$gov (performance recommended)"
  helga_server_up "$dst" "$port" || return 1
  SHD "nohup iperf3 -c $dst -B $src -p $port -t $T -P $P --bidir --json > /tmp/iperf-sust-$dut.json 2>&1 & echo LAUNCHED" >/dev/null
  sleep 10
  cpu_snapshot "$snap" > "$OUTDIR/sust-$dut-before.txt"
  sleep $((T - 13))
  cpu_snapshot "$snap" > "$OUTDIR/sust-$dut-after.txt"
  sleep 8
  SHD "cat /tmp/iperf-sust-$dut.json" > "$OUTDIR/sust-$dut.json" 2>/dev/null
  log "  steady-state DUT .$dut per-core busy% (t=10s..$((T-3))s window):"
  cpu_delta "$OUTDIR/sust-$dut-before.txt" "$OUTDIR/sust-$dut-after.txt" | sed 's/^/    /'
  python3 - "$OUTDIR/sust-$dut.json" "$dut" <<'EOF'
import json,sys
try:
    d=json.load(open(sys.argv[1])); dut=sys.argv[2]
except Exception as e:
    print(f"    client json unreadable: {e}"); sys.exit(0)
end=d.get("end",{})
sent=end.get("sum_sent",{}).get("bits_per_second",0)/1e9
recv=end.get("sum_received",{}).get("bits_per_second",0)/1e9
print(f"    aggregate: sent={sent:.2f}G recv={recv:.2f}G total={sent+recv:.2f}G")
ss=[s for s in d.get("streams",[]) if s.get("sender")]
ss=sorted(ss,key=lambda s:-s.get("bits_per_second",0))
for s in ss[:12]:
    print(f"      stream {s.get('socket',0)}: {s.get('bits_per_second',0)/1e9:.2f}G")
EOF
}

# ---------------------------------------------------------------------------
# ASK1 fastpath A/B
# ---------------------------------------------------------------------------
cmd_ask1_fastpath() {
  local P="${2:-8}" T="${3:-10}"
  log "ASK1 fastpath A/B: vwd=0 (kernel) vs vwd=1 (cdx offload)"
  S110 'echo 0 > /sys/class/vwd/vwd0/vwd_fast_path_enable'
  log "  vwd=0:"; run_throughput "$LANE_ASK1" "$P" "$T"
  S110 'echo 1 > /sys/class/vwd/vwd0/vwd_fast_path_enable'
  log "  vwd=1:"; run_throughput "$LANE_ASK1" "$P" "$T"
}

# ---------------------------------------------------------------------------
# all
# ---------------------------------------------------------------------------
cmd_all() {
  local P="${2:-8}" T="${3:-10}"
  local lane
  for lane in "$LANE_ASK2" "$LANE_ASK1"; do
    log "============================================================"
    run_throughput "$lane" "$P" "$T"
    run_cpu "$lane" "$P" "$T"
  done
  offload_185
  offload_110
  cmd_ask1_fastpath "$@"
}

lane_of() { case "$1" in ask1|110) echo "$LANE_ASK1";; ask2|185) echo "$LANE_ASK2";; *) echo "$1";; esac; }

# ---------------------------------------------------------------------------
sub="${1:-check}"
case "$sub" in
  check) cmd_check ;;
  throughput) run_throughput "$(lane_of "${2:-ask2}")" "${3:-8}" "${4:-10}" ;;
  sustained) run_sustained "$(lane_of "${2:-ask2}")" "${3:-8}" "${4:-60}" ;;
  cpu-profile) run_cpu "$(lane_of "${2:-ask2}")" "${3:-8}" "${4:-10}" ;;
  offload-evidence) offload_185; offload_110 ;;
  ask1-fastpath) cmd_ask1_fastpath "$@" ;;
  all) cmd_all "$@" ;;
  *) echo "usage: $0 {check|throughput|cpu-profile|offload-evidence|ask1-fastpath|all} [lane P T]" >&2; exit 1 ;;
esac
