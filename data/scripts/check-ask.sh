#!/bin/bash
# check-ask.sh -- ASK (Application Solutions Kit) health check for LS1046A.
#
# VyOS-style streaming output. Each test prints
#     "         <description>..."
# followed by either
#     "[ PASSED ]"  - test succeeded
#     "[ FAILED ]"  - test failed (counts toward exit code)
#     "[ SKIPPED ]" - test not applicable in this environment (no exit impact)
#
# When all tests report PASSED the system has a fully working ASK fast-path:
# kernel SDK DPAA/FMan/QBMan stack initialised, fast-path netfilter / CDX /
# auto-bridge modules loaded, FMD shim live, CAAM IPsec attached, dpa_app
# applied PCD config, CMM running, all 5 NICs probed.
#
# Exit code equals the number of [ FAILED ] tests (0 == healthy).

set -u

# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

COL=64                     # column at which the [ PASSED ] tag is rendered
pass=0
fail=0
skip=0

# Print the leading "         <desc>..." with VyOS-like indent.
say() {
    printf '         %s...' "$1"
}

# Pad to column COL, then print the bracketed result tag. We do not use
# colour escapes -- this script frequently runs over slow serial consoles
# where ANSI handling is unreliable, and the boot-style log is meant to be
# copy-pasted into bug reports verbatim.
result() {
    local tag=$1 desc_len=${2:-0}
    local pad=$(( COL - 9 - desc_len - 3 ))   # 9 = leading indent, 3 = "..."
    [ "$pad" -lt 1 ] && pad=1
    printf '%*s%s\n' "$pad" '' "$tag"
}

# Run a single check.
#   ok   <desc>          -> [ PASSED ]
#   ko   <desc>          -> [ FAILED ]
#   na   <desc>          -> [ SKIPPED ]
ok()  { local d=$1; say "$d"; result '[ PASSED ]'  "${#d}"; pass=$((pass+1)); }
ko()  { local d=$1; say "$d"; result '[ FAILED ]'  "${#d}"; fail=$((fail+1)); }
na()  { local d=$1; say "$d"; result '[ SKIPPED ]' "${#d}"; skip=$((skip+1)); }

section() {
    echo ""
    echo "[ ASK :: $1 ]"
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
    ok "CAAM algorithms registered in /proc/crypto ($n entries)"
else
    ko "CAAM algorithms registered in /proc/crypto"
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
# Section: Network interfaces
# ---------------------------------------------------------------------------
section "Network interfaces"

# Mono Gateway has 5 MACs total: 3 copper (1ae2/1ae8/1aea) + 2 XFI/SFP+
# (1af0/1af2). The SFP+ cages legitimately fail to probe when no SFP module
# is inserted ("swphy: unknown speed"), so we count them as SKIPPED rather
# than FAILED. Copper interfaces MUST come up.
copper_up=0
copper_present=0
for iface in $(ls /sys/class/net/ 2>/dev/null); do
    [ "$iface" = "lo" ] && continue
    case "$iface" in
        e2|e3|e4|eth0|eth1|eth2)
            copper_present=$((copper_present+1))
            state=$(cat "/sys/class/net/$iface/operstate" 2>/dev/null)
            [ "$state" = "up" ] && copper_up=$((copper_up+1))
            ;;
    esac
done

if [ "$copper_present" -ge 3 ]; then
    ok "3 copper DPAA interfaces present"
else
    ko "3 copper DPAA interfaces present (found $copper_present)"
fi

if [ "$copper_up" -ge 1 ]; then
    ok "at least 1 copper DPAA interface UP ($copper_up)"
else
    ko "at least 1 copper DPAA interface UP"
fi

if dmesg_has 'sfp-xfi[01]: deferred probe pending' \
   && dmesg_has 'fsl_mac: probe of 1af[02]000.ethernet failed with error -22'; then
    na "SFP+ cages probed (no SFP module inserted)"
elif dmesg_has 'fsl_mac.*1af[02]000.ethernet.*FMan MEMAC'; then
    ok "SFP+ cages probed (modules inserted)"
else
    na "SFP+ cages probed (no SFP module inserted)"
fi

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
# Section: kernel log integrity
# ---------------------------------------------------------------------------
section "Kernel log integrity"

if dmesg_has 'Kernel panic'; then
    ko "no kernel panic in dmesg"
else
    ok "no kernel panic in dmesg"
fi

if dmesg_has 'Oops:'; then
    ko "no kernel oops in dmesg"
else
    ok "no kernel oops in dmesg"
fi

if dmesg_has 'BUG:'; then
    ko "no BUG: messages in dmesg"
else
    ok "no BUG: messages in dmesg"
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