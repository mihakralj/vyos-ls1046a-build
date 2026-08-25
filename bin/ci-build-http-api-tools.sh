#!/bin/bash
# ci-build-http-api-tools.sh — Rebuild vyos-http-api-tools with the MCP SDK
#
# Called from: ci-build-packages.sh (after the package loop; standalone-safe).
# Produces:    vyos-http-api-tools_<ver>+mcp1_arm64.deb staged into
#              vyos-build/data/live-build-config/packages.chroot/ so live-build
#              dpkg-installs it BEFORE apt's resolve pass — the higher version
#              (2.6+mcp1 > 2.6) then wins over packages.vyos.net's stock 2.6.
#
# WHY THIS EXISTS
# ---------------
# The VyOS HTTP API (`service https api` — the REST + GraphQL backend the MCP
# server mounts /mcp/ into) does NOT run under the system python3. It runs in a
# self-contained dh-virtualenv shipped by the `vyos-http-api-tools` package at
# /usr/share/vyos-http-api-tools/ (server shebang
# `#!/usr/share/vyos-http-api-tools/bin/python3`). That venv ALREADY carries
# fastapi/starlette/uvicorn/pydantic v2 — verified on board .185 image
# 2026.08.25-0150: pydantic 2.10.2, pydantic_core 2.27.1 (compiled aarch64),
# fastapi 0.115.5, starlette 0.41.3, anyio 4.6.2.
#
# The `mcp` SDK (1.8.1 — the version the T9030-mcp fork targets: it uses the
# v1 SDK API mcp.server.Server / mcp.types.* / StreamableHTTPSessionManager /
# inputSchema) needs pydantic>=2 (satisfied) plus four pure-Python deps NOT in
# the venv: httpx, httpx-sse, pydantic-settings, sse-starlette. None are Debian
# packages and there is no system python3-mcp, so the clean, reproducible,
# dpkg-owned way to add them is to rebuild the venv with `mcp==1.8.1` appended
# to requirements.in and let dh-virtualenv pip-install the resolved set. mcp +
# those four are Architecture:all pure-Python wheels (no compilation), so the
# arm64 target is a non-issue; pydantic_core is already present.
#
# The api/mcp/*.py server code ships via vyos-1x (data/vyos-1x-041-mcp-server
# .patch) into /usr/libexec/vyos/services/api/mcp/, NOT here. This package only
# provides the interpreter + libraries that code imports. The MCP endpoint
# stays DORMANT until `set service https api mcp ...` is configured.
set -xo pipefail
# NOTE: intentionally NOT set -e — errors are handled explicitly so failures
# are loud (a silent fallback to the mcp-less repo 2.6 would leave /mcp/
# importing a missing module at runtime).

WORKSPACE="${GITHUB_WORKSPACE:-$(pwd)}"
DEST_CHROOT="$WORKSPACE/vyos-build/data/live-build-config/packages.chroot"

# Pin the exact SDK version the fork was developed against. Pinning it in
# requirements.in BEFORE pip-compile forces the resolver down the 1.8.1
# dependency graph from the start (an unpinned `mcp` could resolve the newer
# v2 SDK line, whose API the api/mcp code does not match).
MCP_PIN="mcp==1.8.1"

# vyos-http-api-tools is versioned independently of vyos-1x; track rolling HEAD
# and suffix +mcp1 so the produced .deb apt-version sorts ABOVE the stock 2.6.
HAT_BRANCH="rolling"
MCP_DEB_SUFFIX="+mcp1"
SRC="$WORKSPACE/vyos-http-api-tools"

### Locate / refresh source (reuse a persistent clone on the self-hosted runner)
if [ -d "$SRC/.git" ]; then
  echo "### Reusing vyos-http-api-tools clone at $SRC"
  git -C "$SRC" fetch -q origin "$HAT_BRANCH" 2>/dev/null || true
  git -C "$SRC" reset -q --hard "origin/$HAT_BRANCH" 2>/dev/null || git -C "$SRC" reset -q --hard HEAD
  git -C "$SRC" clean -fdq
else
  echo "### Cloning vyos-http-api-tools ($HAT_BRANCH)"
  git clone --depth 1 -b "$HAT_BRANCH" \
    https://github.com/vyos/vyos-http-api-tools.git "$SRC" \
    || { echo "ERROR: failed to clone vyos-http-api-tools" >&2; exit 1; }
fi

cd "$SRC" || { echo "ERROR: cannot cd $SRC" >&2; exit 1; }

### Inject the pinned MCP SDK into the top-level requirement set
# requirements.in is the human-authored top-level set; requirements.txt is the
# pip-compiled fully-pinned lock that dh_virtualenv installs from. Pin mcp in
# .in so pip-compile resolves the four transitive deps (httpx, httpx-sse,
# pydantic-settings, sse-starlette) at versions consistent with the venv's
# existing pins.
if grep -q '^mcp\b' requirements.in; then
  sed -i -E "s/^mcp([<>=!~].*)?$/${MCP_PIN}/" requirements.in
else
  printf '%s\n' "$MCP_PIN" >> requirements.in
fi
echo "### requirements.in after MCP injection:"; cat requirements.in

### Regenerate the lock the same way upstream does (pip-compile requirements.in)
if ! command -v pip-compile >/dev/null 2>&1; then
  pip install --break-system-packages -q pip-tools \
    || { echo "ERROR: pip-tools (pip-compile) unavailable" >&2; exit 1; }
fi
if ! pip-compile --quiet --output-file requirements.txt requirements.in; then
  echo "ERROR: pip-compile failed to resolve requirements with ${MCP_PIN}" >&2
  exit 1
fi
grep -q "^${MCP_PIN}$" requirements.txt \
  || { echo "ERROR: ${MCP_PIN} missing from resolved requirements.txt" >&2; exit 1; }
echo "### Resolved MCP-related pins:"
grep -iE '^(mcp|httpx|httpx-sse|pydantic-settings|sse-starlette|anyio|pydantic)==' requirements.txt || true

### Bump debian/changelog so the produced .deb sorts above stock 2.6
BASE_VER=$(dpkg-parsechangelog -SVersion 2>/dev/null || echo "2.6")
NEW_VER="${BASE_VER}${MCP_DEB_SUFFIX}"
DEBEMAIL="maintainers@vyos.net" DEBFULLNAME="LS1046A MCP integration" \
  dch --newversion "$NEW_VER" --distribution unstable --force-bad-version \
      "Add mcp==1.8.1 + deps to the HTTP API virtualenv (LS1046A)" \
  || { echo "ERROR: dch changelog bump failed" >&2; exit 1; }

### Build the dh-virtualenv .deb
# dh_virtualenv pip-installs requirements.txt into the venv at build time
# (needs PyPI network — available on the runner). Native aarch64 host → pip
# fetches the aarch64 pydantic_core wheel automatically. debian/rules already
# handles the bookworm dh_virtualenv getargspec shim + --use-system-packages.
if ! command -v dh_virtualenv >/dev/null 2>&1; then
  echo "### Installing dh-virtualenv build dep (should already be in ci-install-deps.sh)"
  apt-get install -y --no-install-recommends dh-virtualenv python3-venv python3-pip \
    || { echo "ERROR: dh-virtualenv unavailable" >&2; exit 1; }
fi
if ! dpkg-buildpackage -us -uc -b; then
  echo "ERROR: vyos-http-api-tools dpkg-buildpackage failed" >&2
  exit 1
fi

### Collect the newest produced .deb (dpkg-buildpackage writes to $SRC/..)
mkdir -p "$DEST_CHROOT"
built=$(find "$WORKSPACE" -maxdepth 1 -name 'vyos-http-api-tools_*_arm64.deb' \
        -printf '%T@ %p\n' 2>/dev/null | sort -nr | awk 'NR==1 {print $2}')
if [ -z "$built" ] || [ ! -f "$built" ]; then
  echo "ERROR: no vyos-http-api-tools_*_arm64.deb produced" >&2
  ls -la "$WORKSPACE"/*.deb 2>/dev/null || true
  exit 1
fi
cp -v "$built" "$DEST_CHROOT/"

### Fail fast if the venv did not actually get the mcp package
TMPX=$(mktemp -d)
dpkg-deb -x "$built" "$TMPX"
if find "$TMPX/usr/share/vyos-http-api-tools" -type d -name mcp 2>/dev/null | grep -q mcp; then
  echo "### OK: mcp package present in the venv site-packages"
else
  echo "ERROR: built vyos-http-api-tools venv does NOT contain mcp — refusing to stage" >&2
  find "$TMPX/usr/share/vyos-http-api-tools/lib" -maxdepth 4 -name 'mcp*' 2>/dev/null | head >&2 || true
  rm -rf "$TMPX"
  exit 1
fi
rm -rf "$TMPX"

echo "### vyos-http-api-tools (+mcp, ${NEW_VER}) staged: $DEST_CHROOT/$(basename "$built")"
