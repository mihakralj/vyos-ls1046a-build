# shellcheck shell=bash
# Optional per-cell WGFP counter snapshots for the WireGuard test rigs.

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
}

wgfp_counter_sample() {
  [ -n "${WGFP_COUNTER_LOG:-}" ] || return 0
  local cell=$1 direction=$2 rep=$3 phase=$4 attempt=${5:-1}
  local state stats header values sinks fail ern sample_id
  state=$(dut 'sudo cat /sys/module/wireguard/parameters/wgfp_peer') ||
    { echo "ERROR: cannot read WGFP counters" >&2; return 1; }
  case $state in
    'on '*) ;;
    *) echo "ERROR: WGFP is not bound while sampling $cell $direction" >&2; return 1 ;;
  esac
  stats=$(dut 'sudo cat /sys/kernel/debug/dpaa_wgfp/stats') ||
    { echo "ERROR: cannot read WGFP QMan counters" >&2; return 1; }
  header=${stats%%$'\n'*}
  if ! values=$(printf '%s\n' "$header" | awk '
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
    }'); then
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
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "$cell" "$direction" "$rep" "$phase" "$attempt" "$state" "$header" "$sinks" \
    >> "$WGFP_COUNTER_LOG" || return 1
  sample_id="$cell/$direction/$rep/$attempt"
  case $phase in
    before)
      WGFP_QMAN_SAMPLE_ID=$sample_id
      WGFP_QMAN_FAIL_BEFORE=$fail
      WGFP_QMAN_ERN_BEFORE=$ern
      ;;
    after)
      if [ "${WGFP_QMAN_SAMPLE_ID:-}" != "$sample_id" ] ||
         [ -z "${WGFP_QMAN_FAIL_BEFORE:-}" ] || [ -z "${WGFP_QMAN_ERN_BEFORE:-}" ]; then
        echo "ERROR: missing matching WGFP QMan baseline for $sample_id" >&2
        return 1
      fi
      if [ "$fail" != "$WGFP_QMAN_FAIL_BEFORE" ] || [ "$ern" != "$WGFP_QMAN_ERN_BEFORE" ]; then
        echo "ERROR: WGFP QMan failures or rejections changed during $sample_id" >&2
        return 1
      fi
      WGFP_QMAN_SAMPLE_ID=
      ;;
    *) echo "ERROR: invalid WGFP counter phase: $phase" >&2; return 1 ;;
  esac
}
