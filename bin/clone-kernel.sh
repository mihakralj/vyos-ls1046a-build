#!/bin/bash
# clone-kernel.sh — Maintain a shallow git clone of linux-stable.
#
# The clone lives in ~/kernel-git-cache/linux/ (persistent across
# runner checkouts).  A symlink is created at the expected location
# so that build-kernel.sh sees it and build.py skips the tarball download.
#
# Benefits:
#   - Real git tags and commit SHAs (enables proper 3-way merges)
#   - Survives `actions/checkout` which does `git clean -fdx`
#   - Incremental: subsequent builds use `git fetch --depth=1` instead of re-cloning
#
# Usage:
#   bin/clone-kernel.sh [version]       # clone or update
#   bin/clone-kernel.sh --force [ver]   # fresh clone

set -e
VERSION="${1:-6.18.38}"
FORCE=0
if [ "$1" = "--force" ]; then
    VERSION="${2:-6.18.38}"
    FORCE=1
fi

TAG="v${VERSION}"
URL="https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git"
CACHE_ROOT="${HOME}/kernel-git-cache"
CACHE_DIR="${CACHE_ROOT}/linux"

# The expected kernel source location (where build.py looks)
WORKSPACE="${GITHUB_WORKSPACE:-$(cd "$(dirname "$0")/.." && pwd)}"
BKDIR="${WORKSPACE}/vyos-build/scripts/package-build/linux-kernel"
KERNEL_SRC="${BKDIR}/linux"

# Step 1: clone/fetch the kernel into the persistent cache
mkdir -p "$CACHE_ROOT"

if [ -d "${CACHE_DIR}/.git" ] && [ "$FORCE" != "1" ]; then
    echo "### Updating kernel git cache at $CACHE_DIR"
    cd "$CACHE_DIR"
    # Check if we're on the right tag
    CURRENT=$(git tag --points-at HEAD 2>/dev/null | grep "^${TAG}" || true)
    if [ -z "$CURRENT" ]; then
        echo "### Fetching ${TAG}..."
        git fetch --depth=1 origin tag "$TAG" 2>/dev/null || true
        git checkout -f "$TAG" 2>/dev/null || \
            git checkout -f "refs/tags/${TAG}" 2>/dev/null || true
    else
        echo "### Already at ${TAG}"
    fi
else
    echo "### Cloning linux-stable ${TAG} into cache (shallow)..."
    [ "$FORCE" = "1" ] && rm -rf "$CACHE_DIR"
    git clone --depth=1 --branch "$TAG" "$URL" "$CACHE_DIR"
fi

echo "### Kernel git cache ready: $(cd "$CACHE_DIR" && git describe --tags)"
echo "### Size: $(du -sh "$CACHE_DIR" | cut -f1)"

# Step 2: symlink from the workspace to the cache
mkdir -p "$BKDIR"
if [ -L "$KERNEL_SRC" ]; then
    # Already a symlink — update if needed
    TARGET=$(readlink "$KERNEL_SRC")
    if [ "$TARGET" != "$CACHE_DIR" ]; then
        rm -f "$KERNEL_SRC"
        ln -s "$CACHE_DIR" "$KERNEL_SRC"
    fi
elif [ -d "$KERNEL_SRC" ]; then
    # Legacy: real directory — replace with symlink
    echo "### Replacing legacy kernel directory with symlink to cache"
    rm -rf "$KERNEL_SRC"
    ln -s "$CACHE_DIR" "$KERNEL_SRC"
else
    ln -s "$CACHE_DIR" "$KERNEL_SRC"
fi

echo "### Symlink: $KERNEL_SRC -> $(readlink "$KERNEL_SRC")"
echo "### Done. build.py will use this git clone instead of downloading a tarball."
