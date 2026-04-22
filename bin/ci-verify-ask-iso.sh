#!/bin/bash
# ci-verify-ask-iso.sh — assert that the freshly-built ISO actually contains
# every ASK-customised package that ci-consume-ask-kernel.sh staged into
# live-build's packages.chroot/. Fails the job (exit 1) if any expected
# package is missing, or is present but not at its `+ask*` / `-vyos` version.
#
# Why this exists: run 24794085304 shipped a green build whose ISO silently
# contained stock Debian iptables_1.8.9-2 and ppp_2.4.9-1+1.1+b1 instead of
# the iptables_1.8.10+ask1 / ppp_…+ask1 debs we had downloaded — because
# those .debs were never copied into packages.chroot/. After fixing that in
# ci-consume-ask-kernel.sh, we also add this assertion so any future
# regression (missing stage, apt version-compare tie, build-time rename,
# etc.) fails loudly instead of hiding behind a green check.
#
# Invoked from bin/ci-build-iso.sh while the freshly-built ISO is mounted
# on /tmp/iso-mount. Expects packages/.ask-expected-packages.txt to have
# been produced by ci-consume-ask-kernel.sh.

set -euo pipefail
cd "${GITHUB_WORKSPACE:-.}"

ISO_MOUNT="${1:-/tmp/iso-mount}"
PKGS_FILE="$ISO_MOUNT/live/filesystem.packages"
EXPECTED="packages/.ask-expected-packages.txt"

if [ ! -f "$PKGS_FILE" ]; then
    echo "ERROR: $PKGS_FILE missing — is the ISO mounted at $ISO_MOUNT ?"
    exit 1
fi
if [ ! -f "$EXPECTED" ]; then
    echo "WARN: $EXPECTED missing — ci-consume-ask-kernel.sh didn't run or produced no expectations; skipping verification."
    exit 0
fi

fail=0
total=0
echo "### Verifying ASK-package presence in $PKGS_FILE"
while IFS= read -r pkg; do
    [ -z "$pkg" ] && continue
    total=$((total+1))
    # filesystem.packages lines look like:
    #   iptables 1.8.10+ask1
    #   linux-image-6.6.135-vyos 6.6.135-1
    # (dpkg-style "name version", one per line, no arch on the main name
    #  except for :arch-qualified libs).
    # Accept optional ":arm64" arch qualifier on the name.
    line=$(awk -v p="$pkg" '
        $1 == p || $1 == p":arm64" || $1 == p":all" { print; exit }
    ' "$PKGS_FILE")
    if [ -z "$line" ]; then
        echo "  MISSING: $pkg — not present in the ISO"
        fail=$((fail+1))
        continue
    fi
    ver=$(awk '{print $2}' <<<"$line")
    # Acceptance rule: for ASK-flavoured names we require the version string
    # to contain "+ask" (iptables/ppp/xtables libs) OR the name itself to end
    # in "-vyos" (kernel, headers — these are ASK-built at upstream version
    # 6.6.X-1 with no +ask version bump, so we match on the -vyos suffix in
    # the package NAME rather than the version).
    case "$pkg" in
        linux-image-*-vyos|linux-headers-*-vyos|ask-modules-*)
            echo "  OK:      $pkg $ver"
            ;;
        *)
            if [[ "$ver" == *+ask* ]]; then
                echo "  OK:      $pkg $ver"
            else
                echo "  WRONG:   $pkg $ver — expected a +ask* version"
                fail=$((fail+1))
            fi
            ;;
    esac
done < "$EXPECTED"

echo
if (( fail > 0 )); then
    echo "### ASK ISO verification FAILED: $fail of $total expected packages missing/wrong"
    echo "### Inspect vyos-build/data/live-build-config/packages.chroot/ and the"
    echo "### 'lb bootstrap' / 'lb chroot' logs for why packages.chroot was not honoured."
    exit 1
fi
echo "### ASK ISO verification PASSED: all $total expected packages present at correct versions"