#!/bin/bash
# ci-setup-vyos1x-stream.sh — Stage vyos-1x patches for stream build
# Called by: .github/workflows/stream-build.yml
# Expects: GITHUB_WORKSPACE set, MOK_KEY and MINISIGN_PRIVATE_KEY in env
# Difference from ci-setup-vyos1x.sh: uses pre-populated local vyos-1x
# (extracted from stream tarball by ci-setup-stream.sh) instead of git clone
set -ex
cd "${GITHUB_WORKSPACE:-.}"

### Write secrets to disk
[ -n "$MOK_KEY" ] && echo "$MOK_KEY" > data/mok/MOK.key
[ -n "$MINISIGN_PRIVATE_KEY" ] && echo "$MINISIGN_PRIVATE_KEY" > data/vyos-ls1046a.minisign.key

### vyos-1x patches
# build.py does `git checkout <commit_id>` on the repo directory.
# For stream builds, vyos-1x/ is pre-populated from the tarball and
# git-init'd by ci-setup-stream.sh, so we use commit_id = "HEAD".
# scm_url is empty — build.py skips git clone when repo_dir exists.
VYOS1X_BUILD=vyos-build/scripts/package-build/vyos-1x
PATCH_STAGING="$VYOS1X_BUILD/ls1046a-patches"
mkdir -p "$PATCH_STAGING"

# Copy unified diff patches EXCEPT 010 (replaced by Python patcher)
for p in data/vyos-1x-*.patch; do
  case "$(basename "$p")" in
    vyos-1x-010-*) echo "Skipping $p (replaced by patch-vpp-platform-bus.py)" ;;
    *) cp "$p" "$PATCH_STAGING/" ;;
  esac
done
cp data/scripts/patch-mmcblk-default.py "$PATCH_STAGING/"
cp data/scripts/patch-vpp-platform-bus.py "$PATCH_STAGING/"
cp data/reftree.cache "$PATCH_STAGING/"

# Write package.toml for stream: no scm_url, commit_id=HEAD (local source)
cat > "$VYOS1X_BUILD/package.toml" <<'EOF'
[[packages]]
name = "vyos-1x"
commit_id = "HEAD"
scm_url = ""
pre_build_hook = """
  set -ex
  cp ../ls1046a-patches/reftree.cache data/reftree.cache
  sed -i 's/all: clean copyright/all: clean/' Makefile
  patch_fail=0
  for p in ../ls1046a-patches/vyos-1x-*.patch; do
    if ! patch --no-backup-if-mismatch -p1 < "$p"; then
      echo "WARNING: $(basename $p) failed to apply (continuing)"
      patch_fail=1
    fi
  done
  python3 ../ls1046a-patches/patch-mmcblk-default.py
  python3 ../ls1046a-patches/patch-vpp-platform-bus.py
  echo "### VERIFY: VPP patches in source tree"
  grep -c 'fsl_dpa' src/conf_mode/vpp.py || echo "MISSING: fsl_dpa in vpp.py"
  grep -c 'namespace' data/templates/vpp/startup.conf.j2 || echo "MISSING: namespace in startup.conf.j2"
  grep -c '1 << 28' python/vyos/vpp/config_verify.py || echo "MISSING: 256M in config_verify.py"
  grep -c 'min_cpus.*2' python/vyos/vpp/config_resource_checks/resource_defaults.py || echo "MISSING: min_cpus 2 in resource_defaults.py"
  [ $patch_fail -eq 1 ] && echo "WARNING: some patches failed — check build output"
  true
"""
EOF

echo "### vyos-1x stream patch staging complete: $(ls "$PATCH_STAGING" | wc -l) files staged"
