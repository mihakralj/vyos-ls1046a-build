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
# Expects: GITHUB_WORKSPACE set, curl, jq. (gh CLI not required — uses REST API
# directly so this works inside minimal builder containers.)
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

# curl auth header if a token is available (works with public repos without it
# too, but authenticated requests get higher rate limits).
AUTH_ARGS=()
if [ -n "${GH_TOKEN:-}" ]; then
    AUTH_ARGS=(-H "Authorization: Bearer $GH_TOKEN")
elif [ -n "${GITHUB_TOKEN:-}" ]; then
    AUTH_ARGS=(-H "Authorization: Bearer $GITHUB_TOKEN")
fi

echo "### Fetching ASK kernel release $TAG from $REPO"
API="https://api.github.com/repos/$REPO/releases/tags/$TAG"
ASSETS_JSON=$(curl -fsSL "${AUTH_ARGS[@]}" \
    -H "Accept: application/vnd.github+json" \
    -H "X-GitHub-Api-Version: 2022-11-28" \
    "$API")

# Patterns we want to pull down. BRE globs expanded via `case` below.
want() {
    case "$1" in
        linux-*.deb)                return 0 ;;
        iptables_*_arm64.deb)       return 0 ;;
        libxt*_arm64.deb)           return 0 ;;
        libip4tc*_arm64.deb)        return 0 ;;
        libip6tc*_arm64.deb)        return 0 ;;
        libxtables*_arm64.deb)      return 0 ;;
        libiptc*_arm64.deb)         return 0 ;;
        ppp_*_arm64.deb)            return 0 ;;
        ppp-dev_*_all.deb)          return 0 ;;
        pppoe_*_arm64.deb)          return 0 ;;
        SHA256SUMS|manifest.json)   return 0 ;;
    esac
    return 1
}

# Asset download URLs use /repos/:owner/:repo/releases/assets/:id with
# Accept: application/octet-stream so they work inside the API auth flow.
mapfile -t ASSETS < <(jq -r '.assets[] | "\(.id) \(.name)"' <<<"$ASSETS_JSON")
[ "${#ASSETS[@]}" -gt 0 ] || { echo "ERROR: no assets in release $TAG"; exit 1; }

for entry in "${ASSETS[@]}"; do
    id="${entry%% *}"
    name="${entry#* }"
    if want "$name"; then
        echo "  fetching $name"
        curl -fsSL "${AUTH_ARGS[@]}" \
            -H "Accept: application/octet-stream" \
            -o "$STAGE/$name" \
            "https://api.github.com/repos/$REPO/releases/assets/$id"
    fi
done

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