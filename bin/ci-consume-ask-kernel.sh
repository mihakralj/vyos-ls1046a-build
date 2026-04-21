#!/bin/bash
# ci-consume-ask-kernel.sh — download prebuilt ASK kernel + userspace .debs
# from mihakralj/lts_6.6_ls1046a and stage them for live-build.
#
# Replaces the from-scratch kernel compile previously driven by
# ci-setup-kernel.sh + ci-build-packages.sh (linux-kernel target).
#
# Reads the pinned tag from data/ask-kernel.pin. Override with env
# ASK_KERNEL_TAG or workflow_dispatch input.
#
# Called by: .github/workflows/auto-build.yml "Consume prebuilt ASK kernel" step
# Expects: GITHUB_WORKSPACE set, gh CLI, GH_TOKEN env for rate limit.
set -euo pipefail
cd "${GITHUB_WORKSPACE:-.}"

PIN_FILE=data/ask-kernel.pin
TAG="${ASK_KERNEL_TAG:-}"
if [ -z "$TAG" ] && [ -f "$PIN_FILE" ]; then
    TAG=$(tr -d '[:space:]' < "$PIN_FILE")
fi
[ -n "$TAG" ] || { echo "ERROR: no ASK kernel tag (set ASK_KERNEL_TAG or populate $PIN_FILE)"; exit 1; }

REPO="mihakralj/lts_6.6_ls1046a"
STAGE=work/ask-kernel
mkdir -p "$STAGE" packages

echo "### Fetching ASK kernel release $TAG from $REPO"
# gh is already authenticated via GITHUB_TOKEN in CI; public repo downloads
# work without auth too.
gh release download "$TAG" -R "$REPO" --dir "$STAGE" --clobber \
    --pattern 'linux-*.deb' \
    --pattern 'iptables_*_arm64.deb' \
    --pattern 'libxt*_arm64.deb' \
    --pattern 'libip[46]tc*_arm64.deb' \
    --pattern 'libxtables*_arm64.deb' \
    --pattern 'libiptc*_arm64.deb' \
    --pattern 'ppp_*_arm64.deb' \
    --pattern 'ppp-dev_*_all.deb' \
    --pattern 'pppoe_*_arm64.deb' \
    --pattern 'SHA256SUMS' \
    --pattern 'manifest.json'

echo "### Verifying SHA256SUMS"
(cd "$STAGE" && sha256sum -c SHA256SUMS)

# Ship everything except the dbg kernel (+80 MB, not wanted in the ISO).
# Skip -dev packages that conflict with Debian base unless actually needed.
echo "### Staging .deb files to packages/"
find "$STAGE" -maxdepth 1 -name '*.deb' \
    ! -name '*-dbg_*' \
    ! -name 'libip4tc-dev*' \
    ! -name 'libip6tc-dev*' \
    ! -name 'libiptc-dev*' \
    ! -name 'libxtables-dev*' \
    -exec cp -v {} packages/ \;

# Expose version for downstream steps (ISO manifest, DTB fallback logic, logs).
KVER=$(jq -r .kernel_version "$STAGE/manifest.json")
REF_SHA=$(jq -r '.reference_sha[0:12]' "$STAGE/manifest.json")
if [ -n "${GITHUB_ENV:-}" ]; then
    echo "KERNEL_VER=$KVER"         >> "$GITHUB_ENV"
    echo "ASK_KERNEL_TAG=$TAG"      >> "$GITHUB_ENV"
    echo "ASK_REF_SHA=$REF_SHA"     >> "$GITHUB_ENV"
fi

# Stash provenance so it lands in build artefacts.
cp "$STAGE/manifest.json" "packages/.ask-kernel-manifest.json"

echo "### ASK kernel consumed: $TAG (linux $KVER, ref $REF_SHA)"
ls -la packages/ | grep -E '\.(deb|json)$' || true