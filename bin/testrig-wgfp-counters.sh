# shellcheck shell=bash
# Optional per-cell WGFP counter snapshots for the WireGuard test rigs.
# stats_sw avoids QMan queries; these samples do not establish queue drain.

wgfp_qman_values() {
  printf '%s\n' "$1" | awk '
    BEGIN { want["xmit_ok"]=1; want["xmit_fail"]=1; want["xmit_ern"]=1 }
    {
      for (i=1; i<=NF; i++) {
        n=split($i, part, "=")
        if (part[1] in want) {
          seen[part[1]]++
          if (n != 2 || part[2] !~ /^[0-9]+$/) invalid=1
          value[part[1]]=part[2]
        }
      }
    }
    END {
      for (key in want) if (seen[key] != 1) invalid=1
      if (invalid) exit 1
      print value["xmit_ok"], value["xmit_fail"], value["xmit_ern"]
    }'
}

# WGFP_XMIT_FAIL_BUDGET=N (default 0) lets xmit_fail rise by at most N inside one
# cell (before -> settle). xmit_ern and changes between cells still fail.
wgfp_xmit_fail_budget() {
  local budget=${WGFP_XMIT_FAIL_BUDGET:-0}
  case $budget in
    ''|*[!0-9]*) echo "ERROR: WGFP_XMIT_FAIL_BUDGET must be a non-negative integer" >&2; return 1 ;;
  esac
  printf '%s\n' "$((10#$budget))"
}

wgfp_fail_within_budget() {
  local base=$1 current=$2 sample_id=$3 budget
  budget=$(wgfp_xmit_fail_budget) || return 1
  [ "$current" -ge "$base" ] && [ $((current - base)) -le "$budget" ] || return 1
  if [ "$current" != "$base" ]; then
    echo "WARNING: WGFP xmit_fail +$((current - base)) during $sample_id (budget $budget)" >&2
  fi
}

wgfp_counter_previous() {
  local previous
  wgfp_xmit_fail_budget >/dev/null || return 1
  if ! previous=$(WGFP_XMIT_FAIL_BUDGET=${WGFP_XMIT_FAIL_BUDGET:-0} python3 - "$WGFP_COUNTER_LOG" <<'PY'
import os
import re
import sys

try:
    budget = int(os.environ["WGFP_XMIT_FAIL_BUDGET"], 10)
    expected = "before"
    active = previous = base = None
    with open(sys.argv[1], encoding="utf-8") as log:
        for line in log:
            if not line.endswith("\n"):
                raise ValueError("truncated sample")
            cell, direction, rep, phase, attempt, state, header, _ = line.rstrip("\n").split("\t")
            key = (cell, direction, rep, attempt)
            if (not cell or not direction or not rep.isascii() or not rep.isdecimal()
                    or attempt != "1" or phase != expected or not state.startswith("on ")):
                raise ValueError("invalid sample sequence")
            if phase == "before":
                active, expected = key, "after"
            elif key != active:
                raise ValueError("mismatched sample")
            elif phase == "after":
                expected = "settle"
            else:
                active, expected = None, "before"

            boots = [field[8:] for field in state.split() if field.startswith("boot_id=")]
            if len(boots) != 1 or not re.fullmatch(
                    r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", boots[0]):
                raise ValueError("invalid boot ID")
            values = {}
            for field in header.split():
                name, separator, value = field.partition("=")
                if name in {"xmit_ok", "xmit_fail", "xmit_ern"}:
                    if name in values or not separator or not value.isascii() or not value.isdecimal():
                        raise ValueError("invalid QMan counter")
                    values[name] = value
            if values.keys() != {"xmit_ok", "xmit_fail", "xmit_ern"}:
                raise ValueError("missing QMan counter")
            current = (boots[0], int(values["xmit_fail"]), values["xmit_ern"])
            if phase == "before":
                if previous is not None and current != previous:
                    raise ValueError("boot or QMan counter changed")
                base = current
            elif (current[0] != base[0] or current[2] != base[2]
                    or not 0 <= current[1] - base[1] <= budget):
                raise ValueError("boot or QMan counter changed")
            previous = current
    if previous is None or expected != "before":
        raise ValueError("unsettled history")
except (OSError, UnicodeError, ValueError) as exc:
    raise SystemExit(f"ERROR: previous WGFP counter history is incomplete or failed: {exc}")

print(*previous)
PY
  ); then
    return 1
  fi
  read -r WGFP_QMAN_LAST_BOOT WGFP_QMAN_LAST_FAIL WGFP_QMAN_LAST_ERN <<< "$previous"
  WGFP_QMAN_SETTLED=1
}

wgfp_counter_preflight() {
  [ -n "${WGFP_COUNTER_LOG:-}" ] && [ -s "$WGFP_COUNTER_LOG" ] || return 0
  (wgfp_counter_previous)
}

wgfp_require_unbound() {
  local state
  [ "${WGFP:-0}" = 1 ] || return 0
  state=$(dut 'sudo cat /sys/module/wireguard/parameters/wgfp_peer') ||
    { echo "ERROR: cannot read WGFP counters before setup" >&2; return 1; }
  [ "$state" = off ] ||
    { echo "ERROR: WGFP is already bound; refusing to replace it" >&2; return 1; }
  state=$(dut 'sudo cat /sys/module/wireguard/parameters/wgfp_peer_lease') ||
    { echo "ERROR: atomic WGFP reservation is unavailable; refusing setup" >&2; return 1; }
  [ "$state" = unowned ] ||
    { echo "ERROR: WGFP is already reserved; refusing to replace it" >&2; return 1; }
  wgfp_require_threaded
}

# Softirq WGFP NAPIs (threaded 0) starve user space for ~10 s at saturation, so systemd's 10 s
# hardware watchdog resets the board mid-cell. The driver accepts "threaded" only before WGFP's
# first NAPI on a boot, so the rig opts in here, ahead of any bind. WGFP_THREADED=0 is an explicit
# A/B and is refused unless the hardware watchdog has been raised (bin/kdump-arm.sh sets 120 s).
wgfp_threaded_value() {
  printf '%s\n' "$1" | awk 'NR == 1 {
      for (i=1; i<=NF; i++) if ($i ~ /^threaded=/) { n++; v=substr($i, 10) }
    }
    END { if (n != 1 || (v != "0" && v != "1")) exit 1; print v }'
}

wgfp_require_threaded() {
  local want=${WGFP_THREADED:-1} stats cur wd
  [ "${WGFP:-0}" = 1 ] || return 0
  case $want in 0|1) ;; *) echo "ERROR: WGFP_THREADED must be 0 or 1" >&2; return 1;; esac
  if ! stats=$(dut 'sudo head -n1 /sys/kernel/debug/dpaa_wgfp/stats_sw'); then
    echo "ERROR: cannot read WGFP software-only stats (requires an image with stats_sw)" >&2
    return 1
  fi
  if ! cur=$(wgfp_threaded_value "$stats"); then
    echo "ERROR: invalid WGFP threaded mode in software-only stats" >&2
    return 1
  fi
  if [ "$cur" != "$want" ]; then
    dut "echo 'threaded $want' | sudo tee /sys/kernel/debug/dpaa_wgfp/ctl >/dev/null" ||
      { echo "ERROR: cannot set WGFP threaded=$want (currently $cur): WGFP NAPIs already exist on this boot; reboot the DUT and run before any other WGFP bind" >&2; return 1; }
    if ! stats=$(dut 'sudo head -n1 /sys/kernel/debug/dpaa_wgfp/stats_sw') ||
       ! cur=$(wgfp_threaded_value "$stats") || [ "$cur" != "$want" ]; then
      echo "ERROR: WGFP threaded mode did not read back as $want" >&2
      return 1
    fi
  fi
  if [ "$cur" = 0 ]; then
    wd=$(dut 'systemctl show -P RuntimeWatchdogUSec') ||
      { echo "ERROR: cannot read the DUT hardware watchdog" >&2; return 1; }
    case $wd in
      0|infinity|[6-9][0-9]s|[1-9][0-9][0-9]s|*min*) ;;
      *) echo "ERROR: WGFP threaded=0 starves user space past the ${wd} hardware watchdog and resets the DUT; use WGFP_THREADED=1 or raise the watchdog (bin/kdump-arm.sh)" >&2
         return 1;;
    esac
  fi
}

wgfp_counter_sample() {
  [ -n "${WGFP_COUNTER_LOG:-}" ] || return 0
  local cell=$1 direction=$2 rep=$3 phase=$4 attempt=${5:-1}
  local state boot boot_after stats header values sinks fail ern sample_id
  boot=$(dut 'cat /proc/sys/kernel/random/boot_id') ||
    { echo "ERROR: cannot read the DUT boot ID" >&2; return 1; }
  if ! [[ $boot =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$ ]]; then
    echo "ERROR: invalid DUT boot ID" >&2
    return 1
  fi
  local known=${WGFP_QMAN_BOOT_BEFORE:-${WGFP_QMAN_LAST_BOOT:-}}
  if [ -n "$known" ] && [ "$boot" != "$known" ]; then
    echo "ERROR: DUT rebooted before the $phase sample of $cell $direction (boot_id $known -> $boot); the WGFP binding and DUT-side lease are gone" >&2
    return 1
  fi
  state=$(dut 'sudo cat /sys/module/wireguard/parameters/wgfp_peer') ||
    { echo "ERROR: cannot read WGFP counters" >&2; return 1; }
  case $state in
    'on '*) ;;
    *) echo "ERROR: WGFP is not bound while sampling $cell $direction" >&2; return 1 ;;
  esac
  stats=$(dut 'sudo cat /sys/kernel/debug/dpaa_wgfp/stats_sw') ||
    { echo "ERROR: cannot read WGFP QMan counters from software-only stats (requires an image with stats_sw)" >&2; return 1; }
  boot_after=$(dut 'cat /proc/sys/kernel/random/boot_id') ||
    { echo "ERROR: cannot recheck the DUT boot ID" >&2; return 1; }
  if [ "$boot" != "$boot_after" ]; then
    echo "ERROR: DUT boot changed while sampling $cell $direction" >&2
    return 1
  fi
  header=${stats%%$'\n'*}
  if ! values=$(wgfp_qman_values "$header"); then
    echo "ERROR: missing, duplicate or invalid WGFP QMan counters" >&2
    return 1
  fi
  read -r _ fail ern <<< "$values"
  if ! sinks=$(printf '%s\n' "$stats" | awk '
    BEGIN { want["fqid"]=1; want["frames"]=1; want["errs"]=1; want["sg"]=1 }
    NR == 1 {
      for (i=1; i<=NF; i++) {
        n=split($i, part, "=")
        if (part[1] == "sinks") {
          sink_fields++
          if (n != 2 || part[2] !~ /^[0-9]+$/) invalid=1
          expected=part[2]
        }
      }
      next
    }
    $1 ~ /^sink[0-9]+$/ {
      name=$1
      if (seen_sinks[name]++ || index($0, "|")) invalid=1
      for (i=2; i<=NF; i++) {
        n=split($i, part, "=")
        if (part[1] in want) {
          seen[name, part[1]]++
          if (n != 2 ||
              (part[1] == "fqid" && part[2] !~ /^0x[0-9a-fA-F]+$/) ||
              (part[1] != "fqid" && part[2] !~ /^[0-9]+$/)) invalid=1
        }
      }
      for (key in want) if (seen[name, key] != 1) invalid=1
      gsub(/\t/, " ")
      lines = lines (count ? "|" : "") $0
      count++
    }
    END {
      # per-port sinks (stage 3) appear with the first record on a port, so a
      # freshly bound peer legitimately has none
      if (invalid || sink_fields != 1 || expected < 0 || count != expected) exit 1
      print lines
    }'); then
    echo "ERROR: missing, duplicate or invalid WGFP sink counters" >&2
    return 1
  fi
  if [ "$phase" = before ] && [ -z "${WGFP_QMAN_LAST_FAIL+x}" ] &&
     [ -s "$WGFP_COUNTER_LOG" ]; then
    wgfp_counter_previous || return 1
  fi
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "$cell" "$direction" "$rep" "$phase" "$attempt" "$state boot_id=$boot" "$header" "$sinks" \
    >> "$WGFP_COUNTER_LOG" || return 1
  sample_id="$cell/$direction/$rep/$attempt"
  case $phase in
    before)
      if [ -n "${WGFP_QMAN_SAMPLE_ID:-}" ] ||
         { [ -n "${WGFP_QMAN_LAST_FAIL+x}" ] && [ "${WGFP_QMAN_SETTLED:-0}" != 1 ]; }; then
        echo "ERROR: previous WGFP QMan sample is incomplete" >&2
        return 1
      fi
      if [ -n "${WGFP_QMAN_LAST_FAIL+x}" ]; then
        if [ "$boot" != "$WGFP_QMAN_LAST_BOOT" ]; then
          echo "ERROR: DUT boot changed between WGFP samples" >&2
          return 1
        fi
        if [ "$fail" != "$WGFP_QMAN_LAST_FAIL" ] || [ "$ern" != "$WGFP_QMAN_LAST_ERN" ]; then
          echo "ERROR: WGFP QMan failures or rejections changed before $sample_id" >&2
          return 1
        fi
      fi
      WGFP_QMAN_SAMPLE_ID=$sample_id
      WGFP_QMAN_FAIL_BEFORE=$fail
      WGFP_QMAN_ERN_BEFORE=$ern
      WGFP_QMAN_BOOT_BEFORE=$boot
      ;;
    after)
      if [ "${WGFP_QMAN_SAMPLE_ID:-}" != "$sample_id" ] ||
         [ -z "${WGFP_QMAN_FAIL_BEFORE:-}" ] || [ -z "${WGFP_QMAN_ERN_BEFORE:-}" ]; then
        echo "ERROR: missing matching WGFP QMan baseline for $sample_id" >&2
        return 1
      fi
      if [ "$boot" != "$WGFP_QMAN_BOOT_BEFORE" ]; then
        echo "ERROR: DUT boot changed during $sample_id" >&2
        return 1
      fi
      if ! wgfp_fail_within_budget "$WGFP_QMAN_FAIL_BEFORE" "$fail" "$sample_id" ||
         [ "$ern" != "$WGFP_QMAN_ERN_BEFORE" ]; then
        echo "ERROR: WGFP QMan failures or rejections changed during $sample_id" >&2
        return 1
      fi
      WGFP_QMAN_SAMPLE_ID=
      WGFP_QMAN_LAST_ID=$sample_id
      WGFP_QMAN_LAST_FAIL=$fail
      WGFP_QMAN_LAST_ERN=$ern
      WGFP_QMAN_LAST_BOOT=$boot
      WGFP_QMAN_SETTLED=0
      ;;
    settle)
      if [ -n "${WGFP_QMAN_SAMPLE_ID:-}" ] ||
         [ "${WGFP_QMAN_LAST_ID:-}" != "$sample_id" ] ||
         [ "${WGFP_QMAN_SETTLED:-1}" != 0 ]; then
        echo "ERROR: missing matching WGFP QMan post-run sample for $sample_id" >&2
        return 1
      fi
      if [ "$boot" != "$WGFP_QMAN_LAST_BOOT" ]; then
        echo "ERROR: DUT boot changed while settling $sample_id" >&2
        return 1
      fi
      if ! wgfp_fail_within_budget "$WGFP_QMAN_FAIL_BEFORE" "$fail" "$sample_id" ||
         [ "$ern" != "$WGFP_QMAN_LAST_ERN" ]; then
        echo "ERROR: WGFP QMan failures or rejections changed while settling $sample_id" >&2
        return 1
      fi
      WGFP_QMAN_LAST_FAIL=$fail
      WGFP_QMAN_SETTLED=1
      ;;
    *) echo "ERROR: invalid WGFP counter phase: $phase" >&2; return 1 ;;
  esac
}

# Capture ethtool -S TX error/drop baseline for a space-separated list of DPAA
# egress interfaces before a WGFP run; compare in wgfp_counter_teardown_check.
wgfp_ethtool_tx_snapshot() {
  local ifaces=${1:-} dev out
  for dev in $ifaces; do
    out=$(dut "sudo ethtool -S '$dev' 2>/dev/null" || true)
    printf '%s\n' "$out" | awk -v d="$dev" '
      /tx_(errors|dropped|ern|confirm_err|fman_err)/ || /ERN/ {
        gsub(/:/, "", $1)
        if ($2 ~ /^[0-9]+$/) print d "." $1 "=" $2
      }'
  done
}

# Post-teardown verification: after WGFP unbinds, confirm wgfp_peer is off,
# no flows remain stuck/active, every QMan sink drained to frm_cnt=0 (via the
# quiesced debugfs stats query), and optional ethtool -S TX error counters on
# egress ports did not grow.
wgfp_counter_teardown_check() {
  local ifaces=${1:-} baseline=${2:-} state stats bad_sinks after diff_err=0
  [ "${WGFP:-0}" = 1 ] || return 0
  state=$(dut 'sudo cat /sys/module/wireguard/parameters/wgfp_peer') ||
    { echo "ERROR: teardown check cannot read wgfp_peer" >&2; return 1; }
  [ "$state" = off ] ||
    { echo "ERROR: teardown check expected wgfp_peer=off, got: $state" >&2; return 1; }
  stats=$(dut 'sudo cat /sys/kernel/debug/dpaa_wgfp/stats') ||
    { echo "ERROR: teardown check cannot read /sys/kernel/debug/dpaa_wgfp/stats" >&2; return 1; }
  bad_sinks=$(printf '%s\n' "$stats" | awk '
    $1 ~ /^sink[0-9]+$/ {
      fc = ""
      for (i = 2; i <= NF; i++) {
        if ($i ~ /^frm_cnt=/) fc = substr($i, 9)
      }
      if (fc != "" && fc != "0") print $1 " frm_cnt=" fc
    }')
  if [ -n "$bad_sinks" ]; then
    echo "ERROR: WGFP QMan sink(s) non-empty after teardown: $bad_sinks" >&2
    return 1
  fi
  if [ -n "$ifaces" ] && [ -n "$baseline" ]; then
    after=$(wgfp_ethtool_tx_snapshot "$ifaces")
    if [ "$after" != "$baseline" ]; then
      echo "ERROR: DPAA ethtool -S TX error/drop counters changed during WGFP run" >&2
      diff_err=1
    fi
  fi
  return "$diff_err"
}
