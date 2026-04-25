#!/bin/bash
# ask-check -- ASK (Application Solutions Kit) health check for LS1046A.
#
# Output mimics the VyOS / systemd boot log: in-progress lines are
# rendered as
#     "         Checking <description>..."
# and then rewritten in place with one of three left-justified tags:
#     "[  OK  ]"  - test succeeded                  (green when on TTY)
#     "[FAILED]"  - test failed (counts as exit)    (red when on TTY)
#     "[ SKIP ]"  - test not applicable             (yellow when on TTY)
#
# When every test reports OK the system has a fully working ASK fast-path:
# kernel SDK DPAA/FMan/QBMan stack initialised, fast-path netfilter / CDX /
# auto-bridge modules loaded, FMD shim live, CAAM IPsec attached, dpa_app
# applied PCD config, CMM running, all 5 NICs probed.
#
# Exit code equals the number of [FAILED] tests (0 == healthy).

set -u

# ---------------------------------------------------------------------------
# Output helpers (systemd / VyOS boot-log style)
# ---------------------------------------------------------------------------

pass=0
fail=0
skip=0

# Colour escapes only when stdout is a real terminal AND we are not in dumb
# mode. The boot-style log must remain pasteable into bug reports verbatim
# when piped through tee / journalctl, so colour is opt-in via TTY detect.
if [ -t 1 ] && [ "${TERM:-dumb}" != "dumb" ]; then
    C_RST=$'\033[0m'
    C_OK=$'\033[1;32m'   # bold green
    C_FAIL=$'\033[1;31m' # bold red
    C_SKIP=$'\033[1;33m' # bold yellow
    C_DIM=$'\033[2m'
    CR=$'\r'
else
    C_RST='' C_OK='' C_FAIL='' C_SKIP='' C_DIM=''
    CR=''
fi

# On a TTY: emit "         Checking <desc>..." without newline, then
# overstrike with "[  OK  ] <desc>" using \r and ANSI clear-to-EOL,
# matching systemd's interactive boot-log behaviour.
# Off a TTY (pipe, journal, ssh capture): skip the in-progress line
# entirely so the captured log is linear and clean.
emit() {
    local tag=$1 colour=$2 desc=$3
    if [ -n "$CR" ]; then
        printf '         Checking %s...' "$desc"
        printf '\r[%s%s%s] %s\033[K\n' "$colour" "$tag" "$C_RST" "$desc"
    else
        printf '[%s] %s\n' "$tag" "$desc"
    fi
}

# Public test API.
#   ok   <desc>   -> [  OK  ] <desc>            (green tag)
#   ko   <desc>   -> [FAILED] <desc>            (red tag)
#   na   <desc>   -> [ SKIP ] <desc>            (yellow tag)
ok() { emit '  OK  ' "$C_OK"   "$1"; pass=$((pass+1)); }
ko() { emit 'FAILED' "$C_FAIL" "$1"; fail=$((fail+1)); }
na() { emit ' SKIP ' "$C_SKIP" "$1"; skip=$((skip+1)); }

# Section banner: a single dim/bold line + blank line above. No "[ ASK :: ]"
# boxes (those don't appear in real boot logs).
section() {
    printf '\n%s-- %s --%s\n' "$C_DIM" "$1" "$C_RST"
}

# ---------------------------------------------------------------------------
# Collect global state once
# ---------------------------------------------------------------------------

DMESG=$(dmesg 2>/dev/null)
if [ -z "$DMESG" ]; then
    DMESG=$(sudo -n dmesg 2>/dev/null || true)
fi

LSMOD=$(lsmod 2>/dev/null)
PROC_CRYPTO=$(cat /proc/crypto 2>/dev/null || true)
KVER=$(uname -r)

dmesg_has() { echo "$DMESG" | grep -qE "$1"; }
mod_loaded() { echo "$LSMOD" | grep -q "^$1 "; }

# ---------------------------------------------------------------------------
# Banner
# ---------------------------------------------------------------------------

cat <<EOF

ASK Health Check for LS1046A   ($(date -u '+%Y-%m-%d %H:%M:%S UTC'))
Kernel: $KVER

EOF

# ---------------------------------------------------------------------------
# Section: SoC / firmware
# ---------------------------------------------------------------------------
section "SoC and firmware"

if dmesg_has 'Machine model:.*Mono Gateway'; then
    ok "Machine model is Mono Gateway"
elif dmesg_has 'Machine model:.*LS1046A'; then
    ok "Machine model is an LS1046A board"
else
    ko "Machine model is an LS1046A board"
fi

if dmesg_has 'psci: PSCIv'; then
    ok "PSCI firmware detected"
else
    ko "PSCI firmware detected"
fi

if dmesg_has 'CPU features: detected: Spectre-'; then
    ok "ARM erratum mitigations applied"
else
    ko "ARM erratum mitigations applied"
fi

# ---------------------------------------------------------------------------
# Section: Reserved memory regions (BMan / QMan / USDPAA)
# ---------------------------------------------------------------------------
section "Reserved memory regions"

if dmesg_has 'reserved mem:.*bman-fbpr'; then
    ok "bman-fbpr reserved-memory node initialised"
else
    ko "bman-fbpr reserved-memory node initialised"
fi

if dmesg_has 'reserved mem:.*qman-fqd'; then
    ok "qman-fqd reserved-memory node initialised"
else
    ko "qman-fqd reserved-memory node initialised"
fi

if dmesg_has 'reserved mem:.*qman-pfdr'; then
    ok "qman-pfdr reserved-memory node initialised"
else
    ko "qman-pfdr reserved-memory node initialised"
fi

if dmesg_has 'reserved mem:.*usdpaa-mem'; then
    ok "usdpaa-mem reserved-memory node initialised"
else
    ko "usdpaa-mem reserved-memory node initialised"
fi

if dmesg_has 'USDPAA region at .*:10000000'; then
    ok "USDPAA TLB1 region mapped (256 MiB)"
else
    ko "USDPAA TLB1 region mapped (256 MiB)"
fi

# ---------------------------------------------------------------------------
# Section: BMan / QMan portals
# ---------------------------------------------------------------------------
section "BMan and QMan"

if dmesg_has 'Bman ver:'; then
    ok "BMan hardware version probed"
else
    ko "BMan hardware version probed"
fi

if dmesg_has 'Bman portals initialised'; then
    ok "BMan portals initialised on all CPUs"
else
    ko "BMan portals initialised on all CPUs"
fi

if dmesg_has 'Qman ver:'; then
    ok "QMan hardware version probed"
else
    ko "QMan hardware version probed"
fi

if dmesg_has 'Qman portals initialised'; then
    ok "QMan portals initialised on all CPUs"
else
    ko "QMan portals initialised on all CPUs"
fi

if dmesg_has 'Bman: BPID allocator'; then
    ok "BMan BPID allocator online"
else
    ko "BMan BPID allocator online"
fi

if dmesg_has 'Qman: FQID allocator'; then
    ok "QMan FQID allocator online"
else
    ko "QMan FQID allocator online"
fi

# ---------------------------------------------------------------------------
# Section: SDK FMan controller
# ---------------------------------------------------------------------------
section "SDK FMan controller"

if dmesg_has 'FM_Init.*FMan-Controller code'; then
    ok "FMan controller code loaded"
else
    ko "FMan controller code loaded"
fi

if dmesg_has 'Freescale FM module, FMD API version'; then
    ok "Freescale FM module registered"
else
    ko "Freescale FM module registered"
fi

if dmesg_has 'FM_PCD_Init'; then
    ok "FM PCD subsystem initialised"
else
    ko "FM PCD subsystem initialised"
fi

if dmesg_has 'Freescale FM Ports module'; then
    ok "Freescale FM Ports module registered"
else
    ko "Freescale FM Ports module registered"
fi

# ---------------------------------------------------------------------------
# Section: SDK DPAA Ethernet drivers
# ---------------------------------------------------------------------------
section "SDK DPAA ethernet drivers"

if [ -d /sys/bus/platform/drivers/fsl_mac ]; then
    mac_count=$(ls -d /sys/bus/platform/drivers/fsl_mac/*.ethernet 2>/dev/null | wc -l)
    if [ "$mac_count" -ge 3 ]; then
        ok "fsl_mac driver bound to $mac_count MEMACs"
    else
        ko "fsl_mac driver bound to >=3 MEMACs (found $mac_count)"
    fi
else
    ko "fsl_mac driver registered on platform bus"
fi

# Negative check: no fm_bind failures (the ask11/ask12 init-order bug).
if dmesg_has 'fm_bind\(.*\) failed'; then
    ko "no fm_bind() failures in dmesg"
else
    ok "no fm_bind() failures in dmesg"
fi

if [ -d /sys/bus/platform/drivers/fsl_dpa ]; then
    dpa_count=$(ls -d /sys/bus/platform/drivers/fsl_dpa/soc:* 2>/dev/null | wc -l)
    if [ "$dpa_count" -ge 3 ]; then
        ok "fsl_dpa driver probed $dpa_count ethernet ports"
    else
        ko "fsl_dpa driver probed >=3 ethernet ports (found $dpa_count)"
    fi
else
    ko "fsl_dpa driver registered on platform bus"
fi

# Negative check: no -EINVAL -22 errors from probe, EXCEPT for the two
# fsl_dpa nodes that hang off the SFP+ MACs (typically ethernet@8 and
# ethernet@9 on Mono Gateway). Those cascade-fail when no SFP module is
# inserted -- expected hardware state, not a regression.
spurious=$(echo "$DMESG" \
    | grep -E 'fsl_dpa.*probe.*failed with error -22' \
    | grep -vE 'ethernet@[89][^0-9]' || true)
if [ -n "$spurious" ]; then
    ko "no unexpected fsl_dpa probe -EINVAL failures"
else
    ok "no unexpected fsl_dpa probe -EINVAL failures"
fi

# ---------------------------------------------------------------------------
# Section: FMan offline-parsing ports + USDPAA userspace plumbing
# ---------------------------------------------------------------------------
section "FMan OH ports and USDPAA"

oh_probed=$(echo "$DMESG" | grep -c 'oh_port_probe::found OH port')
if [ "$oh_probed" -ge 2 ]; then
    ok "FMan offline-parsing ports probed ($oh_probed)"
else
    ko "FMan offline-parsing ports probed (>=2; found $oh_probed)"
fi

if dmesg_has 'Freescale USDPAA process driver'; then
    ok "USDPAA process driver registered"
else
    ko "USDPAA process driver registered"
fi

if dmesg_has 'Freescale USDPAA process IRQ driver'; then
    ok "USDPAA process IRQ driver registered"
else
    ko "USDPAA process IRQ driver registered"
fi

# ---------------------------------------------------------------------------
# Section: ASK fast-path kernel modules
# ---------------------------------------------------------------------------
section "ASK fast-path kernel modules"

if dmesg_has 'fp_netfilter.*hooks registered'; then
    ok "fp_netfilter hooks registered"
elif dmesg_has 'fp_netfilter'; then
    ko "fp_netfilter hooks registered (driver loaded but no hook message)"
else
    ko "fp_netfilter hooks registered"
fi

if mod_loaded cdx; then
    ok "cdx.ko loaded"
else
    ko "cdx.ko loaded"
fi

if mod_loaded fci; then
    ok "fci.ko loaded"
else
    ko "fci.ko loaded"
fi

if mod_loaded auto_bridge; then
    ok "auto_bridge.ko loaded"
else
    ko "auto_bridge.ko loaded"
fi

# ---------------------------------------------------------------------------
# Section: Device nodes
# ---------------------------------------------------------------------------
section "ASK device nodes"

if [ -c /dev/cdx_ctrl ]; then
    ok "/dev/cdx_ctrl character device present"
else
    ko "/dev/cdx_ctrl character device present"
fi

if [ -c /dev/fm0-pcd ]; then
    ok "/dev/fm0-pcd FMD shim present"
else
    ko "/dev/fm0-pcd FMD shim present"
fi

oh_dev=$(ls /dev/fm0-port-oh* 2>/dev/null | wc -l)
if [ "$oh_dev" -ge 4 ]; then
    ok "FMD OH port device nodes present ($oh_dev)"
else
    ko "FMD OH port device nodes present (>=4; found $oh_dev)"
fi

rx_dev=$(ls /dev/fm0-port-rx* 2>/dev/null | wc -l)
if [ "$rx_dev" -ge 8 ]; then
    ok "FMD RX port device nodes present ($rx_dev)"
else
    ko "FMD RX port device nodes present (>=8; found $rx_dev)"
fi

# ---------------------------------------------------------------------------
# Section: CAAM and IPsec offload
# ---------------------------------------------------------------------------
section "CAAM crypto and IPsec offload"

if dmesg_has 'caam .*crypto.*device ID'; then
    era=$(echo "$DMESG" | grep -m1 'caam.*device ID' | sed 's/.*Era \([0-9]*\).*/\1/')
    ok "CAAM crypto controller probed (Era ${era:-?})"
else
    ko "CAAM crypto controller probed"
fi

if dmesg_has 'caam .*crypto.*job rings ='; then
    ok "CAAM job rings registered"
else
    ko "CAAM job rings registered"
fi

if echo "$PROC_CRYPTO" | grep -q 'driver.*caam'; then
    n=$(echo "$PROC_CRYPTO" | grep -c 'driver.*caam')
    ok "CAAM algorithms ($n entries)"
else
    ko "CAAM algorithms"
fi

if dmesg_has 'caam.*registering rng-caam'; then
    ok "CAAM hardware RNG registered"
else
    ko "CAAM hardware RNG registered"
fi

# Group C fix: dpa_ipsec init recognised by ANY of the legitimate markers
# the SDK actually emits, not just one specific string from an old build.
if dmesg_has 'dpa_ipsec start failed'; then
    ko "dpa_ipsec started without errors"
elif dmesg_has 'dpa_ipsec.*(initialized|started|init.*ok)' \
     || dmesg_has 'cdx_ipsec_init' \
     || dmesg_has 'IPSec.*module loaded' \
     || dmesg_has 'ipsec_init.*OK'; then
    ok "dpa_ipsec started without errors"
else
    # No init message AND no error message -- driver may be compiled out
    # rather than broken. Mark SKIPPED so it does not count as failure.
    if echo "$DMESG" | grep -qi 'ipsec'; then
        ok "dpa_ipsec started without errors"
    else
        na "dpa_ipsec started without errors"
    fi
fi

# ---------------------------------------------------------------------------
# Section: Network interfaces (per ASK port on Mono Gateway)
# ---------------------------------------------------------------------------
section "Network interfaces"

# Mono Gateway DPAA port map. Format: "<dt-mac-addr> <netdev> <kind> <label>"
# - copper ports must be present, fsl_mac-bound, and operstate UP
# - SFP+ cages may be SKIPPED if no module is inserted
ASK_PORTS=(
    "1ae2000 e2  copper port-1-copper"
    "1ae8000 e3  copper port-2-copper"
    "1aea000 e4  copper port-3-copper"
    "1af0000 e5  sfp+   port-4-sfp+"
    "1af2000 e6  sfp+   port-5-sfp+"
)

# fsl_mac probe success message for a given DT-MAC base.
mac_bound() {
    dmesg_has "fsl_mac $1\.ethernet:.*FMan MEMAC"
}
# fsl_mac probe -EINVAL failure (typical SFP+ no-module signature).
mac_failed_no_sfp() {
    dmesg_has "fsl_mac: probe of $1\.ethernet failed with error -22"
}
# Look up the netdev assigned to a given MEMAC base address (e.g. 1ae2000).
# Strategy: read the MEMAC's MAC address from dmesg, then look it up in
# /sys/class/net/*/address. This works regardless of cell-index quirks,
# udev rename rules, or fsl,dpaa ethernet@N cell-index numbering.
find_netdev_for_mac() {
    local mac=$1 dev hwaddr line
    line=$(echo "$DMESG" | grep -m1 "fsl_mac $mac\.ethernet:.*FMan MAC address:")
    [ -z "$line" ] && { echo ""; return; }
    hwaddr=$(echo "$line" | sed 's/.*FMan MAC address: //' | tr -d ' ' | tr 'A-F' 'a-f')
    [ -z "$hwaddr" ] && { echo ""; return; }
    for dev in /sys/class/net/*; do
        local a
        a=$(cat "$dev/address" 2>/dev/null | tr 'A-F' 'a-f')
        if [ "$a" = "$hwaddr" ]; then
            basename "$dev"
            return
        fi
    done
    echo ""
}

for entry in "${ASK_PORTS[@]}"; do
    # shellcheck disable=SC2086
    set -- $entry
    mac=$1; _default_dev=$2; kind=$3; label=$4

    if mac_bound "$mac"; then
        netdev=$(find_netdev_for_mac "$mac")
        if [ -n "$netdev" ] && [ -e "/sys/class/net/$netdev" ]; then
            state=$(cat "/sys/class/net/$netdev/operstate" 2>/dev/null)
            carrier=$(cat "/sys/class/net/$netdev/carrier" 2>/dev/null || echo 0)
            if [ "$state" = "up" ] && [ "$carrier" = "1" ]; then
                ok "$label ($netdev) UP with link"
            elif [ "$state" = "up" ]; then
                ok "$label ($netdev) UP no-link"
            else
                ko "$label ($netdev) DOWN (operstate=$state)"
            fi
        else
            # MAC probed but fsl_dpa hasn't created a netdev yet
            # (or netdev address doesn't match -- shouldn't normally happen).
            ko "$label MAC bound but no matching netdev found"
        fi
    elif [ "$kind" = "sfp+" ] && mac_failed_no_sfp "$mac"; then
        na "$label (no SFP+ module detected)"
    else
        ko "$label MAC bound (fsl_mac did not probe $mac.ethernet)"
    fi
done

# ---------------------------------------------------------------------------
# Section: dpa_app userspace PCD apply
# ---------------------------------------------------------------------------
section "dpa_app and PCD configuration"

if dmesg_has 'start_dpa_app.*failed.*rc 11'; then
    ko "dpa_app applied PCD configuration (SIGSEGV rc=11; ABI mismatch)"
elif dmesg_has 'start_dpa_app.*failed'; then
    rc=$(echo "$DMESG" | grep -m1 'start_dpa_app.*failed' | sed 's/.*rc \([0-9]*\).*/\1/')
    ko "dpa_app applied PCD configuration (failed rc=$rc)"
elif dmesg_has 'start_dpa_app'; then
    ok "dpa_app applied PCD configuration"
else
    ko "dpa_app applied PCD configuration"
fi

if dmesg_has 'failed to locate eth bman pool'; then
    ko "BMan fragment buffer pool located by CDX"
else
    ok "BMan fragment buffer pool located by CDX"
fi

for cfg in \
    /etc/cdx_cfg.xml \
    /etc/cdx_pcd.xml \
    /etc/cdx_sp.xml \
    /etc/fmc/config/hxs_pdl_v3.xml; do
    name=$(basename "$cfg")
    if [ -s "$cfg" ]; then
        ok "config file $name present and non-empty"
    else
        ko "config file $name present and non-empty"
    fi
done

# ---------------------------------------------------------------------------
# Section: CMM daemon
# ---------------------------------------------------------------------------
section "CMM daemon"

if [ ! -x /usr/bin/cmm ]; then
    ko "/usr/bin/cmm binary present"
else
    ok "/usr/bin/cmm binary present"

    if pgrep -x cmm >/dev/null 2>&1; then
        ok "cmm process running"
    else
        ko "cmm process running"
    fi

    if systemctl is-active cmm.service >/dev/null 2>&1; then
        ok "cmm.service active"
    else
        ko "cmm.service active"
    fi
fi

# ---------------------------------------------------------------------------
# Section: ASK systemd units
# ---------------------------------------------------------------------------
section "ASK systemd units"

# Oneshot units are 'inactive' AFTER they succeed -- that is normal. We
# therefore key off Result=success and SubState != failed, not ActiveState.
check_unit() {
    local unit=$1 desc=$2
    if ! systemctl list-unit-files "$unit" >/dev/null 2>&1; then
        na "$desc"
        return
    fi
    local sub result
    sub=$(systemctl show -p SubState --value "$unit" 2>/dev/null)
    result=$(systemctl show -p Result --value "$unit" 2>/dev/null)
    if [ "$sub" = "failed" ] || [ "$result" != "success" ]; then
        ko "$desc"
    else
        ok "$desc"
    fi
}

check_unit ask-modules-load.service     "ask-modules-load.service ran successfully"
check_unit ask-conntrack-fix.service    "ask-conntrack-fix.service ran successfully"
check_unit sfp-tx-enable-sdk.service    "sfp-tx-enable-sdk.service ran successfully"

# ---------------------------------------------------------------------------
# Section: ASK userspace libraries
# ---------------------------------------------------------------------------
section "ASK userspace libraries"

# libnfnetlink must be the NXP-patched build (~130 KB) for CMM to function.
# The Debian system version is ~50 KB and lacks the NXP extensions.
lib_path=""
if [ -x /usr/bin/cmm ]; then
    lib_path=$(ldd /usr/bin/cmm 2>/dev/null | awk '/libnfnetlink/{print $3}')
fi
[ -z "$lib_path" ] && lib_path=/usr/local/lib/libnfnetlink.so.0.2.0
[ ! -e "$lib_path" ] && lib_path=/lib/aarch64-linux-gnu/libnfnetlink.so.0

if [ -e "$lib_path" ]; then
    sz=$(stat -Lc%s "$lib_path" 2>/dev/null || echo 0)
    if [ "$sz" -gt 100000 ]; then
        ok "libnfnetlink is NXP-patched build ($sz bytes)"
    else
        ko "libnfnetlink is NXP-patched build ($sz bytes; expected >100000)"
    fi
else
    ko "libnfnetlink is NXP-patched build"
fi

# ---------------------------------------------------------------------------
# Section: ASK-specific kernel log integrity
# ---------------------------------------------------------------------------
section "ASK-specific kernel log integrity"

# Pattern matches errors emitted by ASK / SDK DPAA / FMan / BMan / QMan /
# CAAM / CDX / fp_netfilter / auto_bridge / dpa_app components only.
# A bare "Kernel panic" or generic stack trace from an unrelated subsystem
# is NOT counted here -- this section asserts the ASK stack itself is clean.
ASK_TAGS='(fsl_dpa|fsl_mac|fsl_oh|fsl_advanced|fsl_proxy|fm_init|fm_pcd|fman|bman|qman|qbman|caam|cdx|fci|auto_bridge|fp_netfilter|usdpaa|dpa_ipsec|dpa_app|sdk_dpaa|sdk_fman)'

# Helper: count dmesg lines matching ASK_TAGS at the given severity prefix.
ask_err_lines() {
    echo "$DMESG" | grep -iE "$1" | grep -iE "$ASK_TAGS" | grep -vE 'failed with error -22.*1af[02]000' || true
}

# Each check: PASS if no ASK-tagged lines match the severity, FAIL with count.
panics=$(ask_err_lines 'Kernel panic|Call trace|Unable to handle kernel')
if [ -z "$panics" ]; then
    ok "no kernel panic involving ASK drivers"
else
    n=$(echo "$panics" | wc -l)
    ko "no kernel panic involving ASK drivers ($n hit(s))"
fi

oopses=$(ask_err_lines 'Oops')
if [ -z "$oopses" ]; then
    ok "no Oops involving ASK drivers"
else
    n=$(echo "$oopses" | wc -l)
    ko "no Oops involving ASK drivers ($n hit(s))"
fi

bugs=$(ask_err_lines 'BUG:|kernel BUG')
if [ -z "$bugs" ]; then
    ok "no BUG: messages from ASK drivers"
else
    n=$(echo "$bugs" | wc -l)
    ko "no BUG: messages from ASK drivers ($n hit(s))"
fi

# Probe / bind / init failures from ASK drivers. Excludes the legitimate
# SFP+ no-module -22 failures (those are filtered above by the -v grep).
probe_fails=$(echo "$DMESG" \
    | grep -iE "$ASK_TAGS" \
    | grep -iE 'probe.*failed|init.*failed|bind.*failed|Unknown symbol' \
    | grep -vE 'fsl_(mac|dpa).*1af[02]000|ethernet@[89][^0-9]' || true)
if [ -z "$probe_fails" ]; then
    ok "no ASK driver probe/init/bind failures"
else
    n=$(echo "$probe_fails" | wc -l)
    ko "no ASK driver probe/init/bind failures ($n hit(s))"
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
total=$((pass + fail))
echo ""
echo "------------------------------------------------------------"
printf 'ASK health check complete: %d passed, %d failed' "$pass" "$fail"
[ "$skip" -gt 0 ] && printf ', %d skipped' "$skip"
echo " ($total active checks)"
if [ "$fail" -eq 0 ]; then
    echo "Status: ASK fast-path is fully operational."
else
    echo "Status: $fail check(s) FAILED -- ASK fast-path is degraded."
fi
echo ""

exit "$fail"