#!/bin/bash
# ci-setup-vyos1x.sh — Stage vyos-1x patches and generate package.toml
# Called by: .github/workflows/auto-build.yml "Setup vyos-1x patches" step
# Expects: GITHUB_WORKSPACE set, MOK_KEY and MINISIGN_PRIVATE_KEY in env
set -ex -o pipefail
cd "${GITHUB_WORKSPACE:-.}"

### Write secrets to disk
[ -n "$MOK_KEY" ] && echo "$MOK_KEY" > board/mok/MOK.key
[ -n "$MINISIGN_PRIVATE_KEY" ] && echo "$MINISIGN_PRIVATE_KEY" > data/vyos-ls1046a.minisign.key

### vyos-1x patches
# IMPORTANT: build.py does `git checkout current` which reverts any direct
# patches applied to the cloned repo. We use pre_build_hook to apply patches
# AFTER checkout, before dpkg-buildpackage.
VYOS1X_BUILD=vyos-build/scripts/package-build/vyos-1x
PATCH_STAGING="$VYOS1X_BUILD/ls1046a-patches"
mkdir -p "$PATCH_STAGING"

# Copy all unified-diff patches. Patch 010 (vpp-platform-bus) and the former
# patch-mmcblk-default were Python patchers; both have been folded back into
# proper git-format unified diffs (vyos-1x-010-*.patch handles vpp; the mmcblk
# default is now part of vyos-1x-007-prefer-emmc-default.patch).
for p in data/vyos-1x-*.patch; do
  cp "$p" "$PATCH_STAGING/"
done
cp data/reftree.cache "$PATCH_STAGING/"

# Substitute @@FLAVOR@@ placeholder in the MOTD patch so the post-login banner
# correctly identifies which build flavor is installed (default | ask | vpp).
# The MOTD patch (vyos-1x-012-ls1046a-motd.patch) ships with literal
# `@@FLAVOR@@` in the new-file content; sed-replace it on the STAGED copy
# only, so the in-repo patch stays flavor-agnostic.
#
# Why sed the staged copy (not the source patch): keeps `git status` clean
# across CI runs and lets a single patch file serve all three flavors.
#
# Why this does NOT break `git apply --3way`: the patch's `index` blob SHAs
# refer to the UPSTREAM `default_motd.j2` (source side) which is unchanged;
# the new-file side is computed from the patch body and never SHA-checked
# against anything by git apply.
MOTD_PATCH="$PATCH_STAGING/vyos-1x-012-ls1046a-motd.patch"
if [ -f "$MOTD_PATCH" ]; then
  sed -i "s/@@FLAVOR@@/${FLAVOR:-default}/g" "$MOTD_PATCH"
  echo "### MOTD patch flavor substituted: @@FLAVOR@@ → ${FLAVOR:-default}"
fi

# NOTE: pre_build_hook MUST be a TOML *literal* multi-line string ('''...''')
# not a TOML basic multi-line string ("""...""").  The basic string interprets
# backslash escapes, so a `\"` inside the bash sed pattern gets unescaped to a
# literal `"` BEFORE bash sees it — turning  sed -i "/^# For \"X\"$/.../d"
# into   sed -i "/^# For "X"$/.../d"   which bash then word-splits on the
# embedded quotes, leaving the unquoted argument  /^# For X$/.../d  that no
# longer matches the upstream  # For "nat64"  / # End "nat64"  block headers.
# Result: jool + nat-rtsp dependencies are NOT stripped, vyos-1x.deb fails to
# install in lb chroot. Verified failure mode in run 25706953044 (2026-05-12).
# Literal '''...''' passes the body through verbatim, so bash receives \"
# unmolested and converts it to " correctly.
cat > "$VYOS1X_BUILD/package.toml" <<'EOF'
[[packages]]
name = "vyos-1x"
commit_id = "current"
scm_url = "https://github.com/vyos/vyos-1x.git"
pre_build_hook = '''
  set -ex
  cp ../ls1046a-patches/reftree.cache data/reftree.cache
  sed -i 's/all: clean copyright/all: clean/' Makefile
  # Remove packages not available for ARM64 from dependencies, plus sub-packages
  # that ci-build-packages.sh intentionally does NOT build (jool, nat-rtsp, qat,
  # mlnx, realtek-r8126, realtek-r8152, ipt-netflow, igb, ixgbe, ixgbevf —
  # see bin/ci-build-packages.sh for the rationale).  Stripping at sed-time
  # (pre-patch) keeps debian/control's blob SHA stable for any later git apply
  # --3way calls that depend on the upstream blob hash.
  sed -i '/accel-ppp-ng/d' debian/control
  # Strip whole "# For X" / "# End X" guard blocks so the leading comment and
  # the trailing comment go away together with the body.
  for blk in nat64 'system conntrack modules rtsp' 'qat' 'mellanox' 'realtek-r8126' 'realtek-r8152' 'ipt-netflow' 'intel-igb' 'intel-ixgbe' 'intel-ixgbevf'; do
    sed -i "/^# For \"${blk}\"$/,/^# End \"${blk}\"$/d" debian/control
  done
  # Relax pylint --errors-only to ignore checks added in pylint 3.x that
  # the upstream vyos-builder Docker image (Debian bookworm, pylint 2.16)
  # never enforced.  We're on Debian trixie (pylint 3.3.4) which trips:
  #   E0606: possibly-used-before-assignment (~22 sites in vyos-1x source)
  #   E1111: assigning-from-no-return     (interfaces_wireless.py:179)
  #   E0001: syntax-error — pylint 3.x now picks up *.graphql and *.tmpl
  #          files via the Makefile's `git ls-files src/services` glob and
  #          treats them as Python, which obviously fails to parse.  Old
  #          pylint silently skipped non-.py extensions.
  # All three are real upstream bugs that vyos itself never fails CI on
  # because their builder uses old pylint.  Disabling matches upstream
  # behaviour and avoids us having to carry per-file fix patches.
  # E0001 (syntax-error) is *fatal* in pylint 3.x and cannot be silenced
  # via --disable.  --ignore-paths does NOT skip files passed explicitly
  # on the command line, but --ignore-patterns DOES (verified locally
  # against pylint 3.3.4 on Debian trixie).
  # Write a project-local .pylintrc so we don't have to inject regexes
  # into the Makefile recipe with all the make/sed escaping headaches.
  cat > .pylintrc <<'PYLINTRC'
[MAIN]
ignore-patterns=.*\\.graphql$,.*\\.tmpl$
[MESSAGES CONTROL]
disable=E0606,E1111
PYLINTRC
  patch_fail=0
  # Drop a .gitattributes that wires Mergiraf as the merge driver for
  # source-language files. git apply --3way only consults attributes in
  # the target tree, so this MUST live inside the upstream clone.
  cat > .gitattributes <<'GITATTR'
*.c     merge=mergiraf
*.h     merge=mergiraf
*.cc    merge=mergiraf
*.cpp   merge=mergiraf
*.hpp   merge=mergiraf
*.py    merge=mergiraf
*.json  merge=mergiraf
*.yml   merge=mergiraf
*.yaml  merge=mergiraf
*.toml  merge=mergiraf
*.xml   merge=mergiraf
GITATTR
  for p in ../ls1046a-patches/vyos-1x-*.patch; do
    # Skip if already applied (idempotent across pre_build_hook re-invocations
    # and forward-compatible if upstream lands an equivalent change).
    if git apply --reverse --check --whitespace=nowarn "$p" >/dev/null 2>&1; then
      echo "SKIP: $(basename $p) — already applied (reverse-applies cleanly)"
      continue
    fi
    if ! git apply --3way --whitespace=nowarn "$p"; then
      echo "::error::$(basename $p) failed to apply with --3way — context drift, refresh patch" >&2
      patch_fail=1
    fi
  done
  echo "### VERIFY: VPP patches in source tree"
  grep -c 'fsl_dpa' src/conf_mode/vpp.py || echo "MISSING: fsl_dpa in vpp.py"
  grep -c 'namespace' data/templates/vpp/startup.conf.j2 || echo "MISSING: namespace in startup.conf.j2"
  grep -c '1 << 28' python/vyos/vpp/config_verify.py || echo "MISSING: 256M in config_verify.py"
  grep -c 'min_cpus.*2' python/vyos/vpp/config_resource_checks/resource_defaults.py || echo "MISSING: min_cpus 2 in resource_defaults.py"
  if grep -qE '_dpaa_unbind_ifaces|vpp-dpaa-unbound|DPDK DPAA PMD' src/conf_mode/vpp.py; then
    echo "::error::legacy DPAA PMD unbind path is still present in vpp.py" >&2
    patch_fail=1
  fi
  # NOTE: a trailing `[ X ] && cmd && exit 1` chain returns 1 when the test
  # is FALSE (patch_fail=0), and as the LAST statement in the hook that 1
  # becomes the script's exit status — vyos-build then logs "pre_build_hook
  # failed" and aborts the rebuild, leaving any stale vyos-1x.deb from a
  # prior successful run on the self-hosted runner to be picked up by lb
  # chroot_install (with its un-stripped jool / nat-rtsp deps). Use a
  # proper if-block and an explicit `exit 0` on the success path.
  if [ $patch_fail -eq 1 ]; then
    echo "ERROR: some patches failed — check build output" >&2
    exit 1
  fi
  exit 0
'''
EOF

echo "### vyos-1x patch staging complete: $(ls "$PATCH_STAGING" | wc -l) files staged"
