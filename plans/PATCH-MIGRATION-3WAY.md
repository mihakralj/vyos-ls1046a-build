# PATCH MIGRATION: `git apply --3way` + Mergiraf + rerere

**Status:** Draft for review
**Date:** 2026-05-09
**Companion plan:** [`INTEGRATION-PLAN.md`](./INTEGRATION-PLAN.md) (single-repo merge + FLAVOR switch)

---

## Sequencing recommendation: **AFTER the integration merge**

This patch-migration plan should land **after** the `INTEGRATION-PLAN.md` merge has reached at least PR 4 (the FLAVOR switch wired into CI with the `ask` flavor green). Reasoning:

1. **The patch surface roughly triples in scope post-merge.** Today the consumer holds ~22 patches under `data/`. After the integration merge absorbs `kernel-ls1046a-build/release/patches/{vyos,ask,fixes}/` (17 patches) plus `release/patches/board/*` (board bringup that the consumer still has split between `data/kernel-patches/` and ad-hoc DTS sources), the total grows to ~40+ patches across `kernel/common/patches/{vyos,board,fixes}/` and `kernel/flavors/{ask,vpp,default}/patches/`. Migrating that surface in one pass is cheaper than migrating the consumer's 22 patches now and re-doing the same work for the producer's 17 after they land in the same tree.
2. **The producer's existing patch-authoring workflow already uses git diff** (`scripts/normalize-patch.awk` consumes `git diff --no-prefix`), so producer patches are *closer to git format than consumer patches today*. Doing the migration after the merge lets us regularize on a single git-format convention across all buckets in one pass.
3. **The producer's hunk-validator hook (`.clinehooks/hunk-validator`) is the prior art for the "stop on malformed hunk" failure mode** that this migration attacks more comprehensively. Folding that hook into the merged repo is a natural sub-task of the migration; keeping it intact during the integration merge avoids losing the existing safety net.
4. **The integration merge already changes every `bin/ci-*.sh` script** (gating on `${FLAVOR}`, switching to `kernel/common/scripts/apply-to-tree.sh`). Stacking the `patch → git apply --3way` substitution on the same scripts in a separate PR (after the FLAVOR switch is green) avoids interleaving two large diff sets across the same files and makes review tractable.
5. **Patch-rot detector (Phase 8) wants to know about flavors.** The weekly check needs to iterate over every per-flavor bucket (common-vyos, common-board, common-fixes, flavor-ask-{ask,fixes}, flavor-vpp-*), not the flat `data/*.patch` set. Designing it post-merge avoids two rewrites of the workflow.

**Concretely:** insert this plan as **PR 9** in the integration sequence (after PR 8's optional matrix builds), or as **PR 4.5** if we're willing to delay the `default` and `vpp` flavor validation by one PR. Recommendation: **PR 9** (i.e., after the integration is fully bedded in).

If the team prefers to do the migration first (e.g., to stop the silent-fuzz failure mode immediately on the consumer's existing 22 patches), the plan is independently executable today against the current `data/*.patch` layout — none of the steps below depend on the post-merge directory structure. They will simply need to be repeated in spirit (not in mechanics — the patches themselves don't need re-regeneration) when the producer's patches arrive in `kernel/flavors/ask/patches/`.

---

## Why this work is needed

The `vyos-ls1046a-build` repo currently applies upstream modifications using GNU `patch` with `--no-backup-if-mismatch -p1`. This silently fuzzes hunks into the wrong place when upstream context drifts, and silently no-ops when patches reference files that have moved. Both failure modes are undetectable from build logs.

The producer-side ask13→ask14 incident (documented in `kernel-ls1046a-build/AGENTS.md`) shows the worst-case symptom: a malformed `@@ -25,3 +25,6 @@` hunk header silently truncated 12 added lines to 6, dropping `obj-$(CONFIG_FSL_SDK_FMAN)` and `obj-$(CONFIG_FSL_SDK_DPAA_ETH)` from a Makefile and producing a kernel with neither `sdk_fman` nor `sdk_dpaa` built. `git apply` reported success. Only post-hoc visual inspection caught it.

The replacement stack is:

1. **`git apply --3way`** for patch application. Uses blob SHAs as anchors, does a real 3-way merge when context drifts, fails loudly with conflict markers instead of silently fuzzing.
2. **Mergiraf** as a git merge driver. AST-aware conflict resolution for C, Python, Rust, and other tree-sitter-supported languages. Kicks in automatically when 3-way merge has line-level conflicts that don't actually overlap structurally.
3. **`git rerere`** to memoize manual conflict resolutions across builds.

`git apply --3way` requires patches to carry `index abc1234..def5678 100644` lines referencing real blob SHAs. Patches produced by `diff -u` do not have these. Patches produced by `git diff` or `git format-patch` do. All existing `.patch` files in this repo must be regenerated through git tooling.

---

## Inputs

- Repo root: `/home/vyos/vyos-ls1046a-build` (or post-merge: same path with `kernel/{common,flavors/*}/patches/`)
- **Pre-merge patch inventory:**
  - `data/vyos-1x-*.patch` — applied by `pre_build_hook` in `bin/ci-setup-vyos1x.sh` against a fresh `vyos/vyos-1x` clone at branch `current`
  - `data/kernel-patches/*.patch` — applied during the kernel build, ordered by filename, against the kernel source tree
  - `data/vyos-build-005-add_vim_link.patch`, `data/vyos-build-007-no_sbsign.patch` — applied against `vyos/vyos-build` checkout at branch `current`
- **Post-merge patch inventory** (additional buckets):
  - `kernel/common/patches/vyos/*.patch` — VyOS kernel deltas (3 patches)
  - `kernel/common/patches/board/*.patch` — board bringup (boot, eMMC, console, fan, LED, mono DTS)
  - `kernel/common/patches/fixes/*.patch` — flavor-agnostic 6.6.y repairs
  - `kernel/flavors/ask/patches/{ask,fixes}/*.patch` — 8 ASK fast-path + ASK-specific 6.6.y fixes
  - `kernel/flavors/vpp/patches/*.patch` — VPP / AF_XDP-specific (placeholder)
- **CI scripts that apply patches** (verify with `grep -rn 'patch ' bin/`):
  - `bin/ci-setup-vyos1x.sh`
  - `bin/ci-setup-kernel.sh`
  - `bin/ci-setup-kernel-ask.sh`
  - `bin/ci-setup-vyos-build.sh`
  - Post-merge: `kernel/common/scripts/apply-to-tree.sh`
- **Self-hosted runner:** Debian 13 trixie, aarch64 (Cobalt 100, 32 vCPU). Persistent state in `/opt/`, `$HOME/.ccache`, `/opt/opam`. Runner user `vyos` with passwordless sudo.
- **Build workflow:** `.github/workflows/auto-build.yml` (called from `self-hosted-build.yml`)

---

## Outputs expected

By end of migration:

1. Every `.patch` file under `data/` (and post-merge: under `kernel/common/patches/` and `kernel/flavors/*/patches/`) is in git format (has `diff --git a/X b/X` headers and `index ABC..DEF` lines).
2. Every `.patch` file applies cleanly with `git apply --3way --check` against the upstream version it targets, without fuzz or context massaging.
3. Patches that were "additions only" (single hunk, `@@ -0,0 +1,N @@`) have been removed; the added file content lives as a plain file under `data/files/` (or post-merge: `kernel/common/files/`, `kernel/flavors/*/files/`), copied into place by the relevant `bin/ci-*.sh` script.
4. All `bin/ci-*.sh` scripts use `git apply --3way --whitespace=nowarn` instead of `patch ... -p1`. The `--no-backup-if-mismatch` flag appears nowhere in the repo.
5. Mergiraf is installed on the self-hosted runner and registered as a git merge driver in `/etc/gitconfig` so that all builds benefit.
6. `.gitattributes` files are created in each upstream clone path (`vyos-build/`, `vyos-1x/`, kernel source) at clone-time by the relevant `bin/ci-*.sh` script, mapping `.c`, `.h`, `.py`, `.dts`, `.dtsi`, `.S` to `merge=mergiraf`.
7. A patch-rot detector workflow (`.github/workflows/patch-rot-check.yml`) runs weekly against latest upstream HEAD and warns when any patch will not apply cleanly.
8. Documentation in `AGENTS.md` (or new `docs/PATCHES.md`) describes the new conventions for adding patches.

---

## Pre-flight

```sh
cd /home/vyos/vyos-ls1046a-build

# 1. Confirm clean main and create migration branch
git status --short
git checkout -b chore/patch-migration-3way

# 2. Inventory all patches (post-merge: includes kernel/ tree)
find data kernel -name '*.patch' -type f 2>/dev/null | sort > /tmp/patch-inventory.txt
wc -l /tmp/patch-inventory.txt

# 3. Find every patch-application call site
grep -rn -E '\b(patch|git[ -]apply)\b' bin/ .github/ data/ kernel/ 2>/dev/null \
    | grep -v Binary > /tmp/patch-callsites.txt
cat /tmp/patch-callsites.txt

# 4. Identify patches that are file-additions only (no modifications)
mkdir -p /tmp/migration
> /tmp/migration/file-additions.txt
> /tmp/migration/modifications.txt
while IFS= read -r p; do
    if grep -q '^@@' "$p" && ! grep -q '^@@ -[1-9]' "$p"; then
        echo "$p" >> /tmp/migration/file-additions.txt
    else
        echo "$p" >> /tmp/migration/modifications.txt
    fi
done < /tmp/patch-inventory.txt

echo "File-addition patches:"; cat /tmp/migration/file-additions.txt
echo "Modification patches:"; cat /tmp/migration/modifications.txt
```

If inventories look wrong (patches outside expected paths, callsites in unexpected scripts), **stop and ask before continuing.**

---

## Phase 1 — Convert file-addition patches to plain files

File-addition patches do not benefit from `git apply --3way` (no upstream context to merge against) and are brittle: if upstream later adds a file at the same path, the patch silently fails with "file already exists." Convert them to plain file copies.

For each patch in `/tmp/migration/file-additions.txt`:

```sh
PATCH=data/vyos-1x-add-foo.patch
TARGET=$(grep -m1 '^+++ b/' "$PATCH" | sed 's|^+++ b/||')
mkdir -p data/files/vyos-1x
OUT=data/files/vyos-1x/$(basename "$TARGET")
awk '/^@@ / { in_hunk=1; next } in_hunk && /^\+/ { sub(/^\+/, ""); print }' \
    "$PATCH" > "$OUT"

# Verify the extracted file matches `patch -p1` output
mkdir -p /tmp/verify && rm -rf /tmp/verify/*
cp -r vyos-build /tmp/verify/
( cd /tmp/verify/vyos-build && patch -p1 < "$REPO/$PATCH" )
diff -u "/tmp/verify/vyos-build/$TARGET" "$OUT" \
    || { echo "MISMATCH on $PATCH"; exit 1; }

# Remove the old patch
git rm "$PATCH"
```

Update the relevant `bin/ci-*.sh` script to copy the file instead of applying the patch:

```sh
# Old:
# patch --no-backup-if-mismatch -p1 -d vyos-build < data/vyos-1x-add-foo.patch
# New:
mkdir -p vyos-build/$(dirname "$TARGET")
cp data/files/vyos-1x/foo.something vyos-build/$TARGET
```

**Acceptance gate:** every patch in `/tmp/migration/file-additions.txt` removed; corresponding plain file under `data/files/` (or post-merge: `kernel/.../files/`); relevant `bin/ci-*.sh` updated. Run a build end-to-end and confirm no patch-apply errors.

---

## Phase 2 — Regenerate modification patches in git format

Regenerate each modification patch through git so it carries the index lines `--3way` needs.

### 2a. vyos-1x patches

Applied in `pre_build_hook` against a fresh `vyos/vyos-1x` checkout at branch `current`. Regenerate against today's `current` HEAD:

```sh
REPO=$(pwd)
mkdir -p /tmp/regen && cd /tmp/regen
rm -rf vyos-1x
git clone --branch current --depth 50 https://github.com/vyos/vyos-1x.git
cd vyos-1x

for p in "$REPO"/data/vyos-1x-*.patch; do
    base=$(basename "$p")
    echo "=== Regenerating $base ==="
    if ! patch -p1 < "$p"; then
        echo "FAIL: $base did not apply against current HEAD"
        exit 1
    fi
    git add -A
    git diff --cached > "$REPO/$base.new"
    git commit -m "tmp: $base" --quiet
done

for p in "$REPO"/data/vyos-1x-*.patch; do
    base=$(basename "$p")
    [ -f "$REPO/$base.new" ] && mv "$REPO/$base.new" "$p"
done
cd "$REPO"
```

### 2b. vyos-build patches

Same pattern, target `vyos/vyos-build@current`.

### 2c. Kernel patches

Kernel patches target a specific kernel version, not a moving branch. Read `data/ask-kernel.pin` (or post-merge: `kernel/common/scripts/fetch-kernel.sh`'s pinned version) for the exact tag.

```sh
REPO=$(pwd)
KERNEL_TAG=$(cat data/ask-kernel.pin 2>/dev/null | tr -d '[:space:]')
[ -z "$KERNEL_TAG" ] && KERNEL_TAG=$(grep -oE 'v6\.6\.[0-9]+' bin/ci-setup-kernel.sh | head -1)
[ -z "$KERNEL_TAG" ] && { echo "Cannot determine kernel tag"; exit 1; }

cd /tmp/regen
rm -rf linux
git clone --branch "$KERNEL_TAG" --depth 1 \
    https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git
cd linux

# Apply patches in filename order (post-merge: respect bucket order vyos → board → fixes → ask → flavor-fixes)
for p in "$REPO"/data/kernel-patches/*.patch; do
    base=$(basename "$p")
    echo "=== Regenerating $base ==="
    if ! patch -p1 < "$p"; then
        echo "FAIL: $base did not apply against $KERNEL_TAG"
        exit 1
    fi
    git add -A
    git diff --cached > "$REPO/data/kernel-patches/$base.new"
    git commit -m "tmp: $base" --quiet --allow-empty
done

for p in "$REPO"/data/kernel-patches/*.patch; do
    [ -f "$p.new" ] && mv "$p.new" "$p"
done
```

### 2d. Post-merge: producer-bucket patches

Same kernel target, but iterate buckets in apply order:

```sh
for bucket_dir in \
    "$REPO/kernel/common/patches/vyos" \
    "$REPO/kernel/common/patches/board" \
    "$REPO/kernel/common/patches/fixes" \
    "$REPO/kernel/flavors/ask/patches/ask" \
    "$REPO/kernel/flavors/ask/patches/fixes"; do
    for p in "$bucket_dir"/*.patch; do
        # ... same regenerate loop as 2c ...
    done
done
```

**Acceptance gate after Phase 2:**

```sh
for p in $(cat /tmp/migration/modifications.txt); do
    head -3 "$p" | grep -q '^diff --git' && \
    grep -q '^index [0-9a-f]\+\.\.[0-9a-f]\+' "$p" \
        || { echo "BAD: $p"; exit 1; }
done
echo "All modification patches are git-format."
```

---

## Phase 3 — Verify `--3way` applies cleanly

```sh
REPO=$(pwd)
verify_patches() {
    local target_repo=$1 ref=$2; shift 2
    local patches=("$@")
    cd /tmp/regen
    rm -rf verify
    git clone --branch "$ref" --depth 50 "$target_repo" verify
    cd verify
    for p in "${patches[@]}"; do
        if ! git apply --3way --check --whitespace=nowarn "$p"; then
            echo "FAIL: $p does not apply cleanly to $target_repo@$ref"
            return 1
        fi
        git apply --3way --whitespace=nowarn "$p"
        git add -A
        git commit -m "tmp" --quiet --allow-empty
    done
    cd "$REPO"
}

verify_patches https://github.com/vyos/vyos-1x.git current "$REPO"/data/vyos-1x-*.patch
verify_patches https://github.com/vyos/vyos-build.git current "$REPO"/data/vyos-build-*.patch
verify_patches https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git \
    "$KERNEL_TAG" "$REPO"/data/kernel-patches/*.patch
# Post-merge: also verify each kernel/{common,flavors/*}/patches/* bucket
```

If any patch fails, **stop and report which patch and against which upstream tag.** Do not silently regenerate again.

---

## Phase 4 — Update `bin/ci-*.sh` scripts

Replace every `patch` invocation with `git apply --3way`:

```sh
# Old (in pre_build_hook of ci-setup-vyos1x.sh):
for p in ../ls1046a-patches/vyos-1x-*.patch; do
    patch --no-backup-if-mismatch -p1 < "$p"
done

# New:
for p in ../ls1046a-patches/vyos-1x-*.patch; do
    git apply --3way --whitespace=nowarn "$p" || {
        echo "::error::Failed to apply $p — context drift, run mergiraf review or refresh patch" >&2
        exit 1
    }
done
```

Audit every file in `/tmp/patch-callsites.txt`. Remove every `--no-backup-if-mismatch` from the repo. Do not silence new failures with `|| true`. Loud failure is the point.

For non-pre_build_hook scripts:

```sh
# Old:
patch --no-backup-if-mismatch -p1 -d vyos-build < data/vyos-build-005-add_vim_link.patch

# New:
git -C vyos-build apply --3way --whitespace=nowarn "$REPO_ROOT"/data/vyos-build-005-add_vim_link.patch \
    || { echo "::error::vyos-build-005 failed"; exit 1; }
```

**Post-merge note:** the producer's `kernel/common/scripts/apply-to-tree.sh` (formerly `kernel-ls1046a-build/scripts/apply-to-tree.sh`) gets the same treatment — replace its `git apply --check && git apply` pair with `git apply --3way --check && git apply --3way`.

**Acceptance gate after Phase 4:**

```sh
# Should produce zero matches
grep -rn 'no-backup-if-mismatch' bin/ .github/ kernel/
grep -rn 'patch[ ]*--*p1[ ]*<' bin/ .github/ kernel/
# Should produce many matches
grep -rn 'git apply --3way' bin/ .github/ kernel/
```

---

## Phase 5 — Install Mergiraf on the self-hosted runner

One-time setup on the persistent Cobalt 100 runner. Do not put this in the workflow itself — `cargo install` or binary download per build is wasteful and breaks when network/cache fails. Treat Mergiraf the same way `/opt/opam` is treated: bootstrap once, reuse.

SSH to the runner as `vyos` and run:

```sh
# Pre-built aarch64 binary (preferred — no rust toolchain needed)
MERGIRAF_VERSION=$(curl -fsSL https://codeberg.org/api/v1/repos/mergiraf/mergiraf/releases | \
    jq -r '[.[] | select(.draft==false and .prerelease==false)][0].tag_name')
echo "Installing mergiraf $MERGIRAF_VERSION"

cd /tmp
curl -fsSL "https://codeberg.org/mergiraf/mergiraf/releases/download/${MERGIRAF_VERSION}/mergiraf_aarch64-unknown-linux-gnu.tar.gz" \
    -o mergiraf.tar.gz
tar -xzf mergiraf.tar.gz
sudo install -m 0755 mergiraf /usr/local/bin/mergiraf
rm -f mergiraf mergiraf.tar.gz
mergiraf --version
```

If asset name differs, check actual releases at https://codeberg.org/mergiraf/mergiraf/releases and adjust.

Fallback if no aarch64 prebuilt for the version you want:

```sh
sudo apt-get install -y rustc cargo
cargo install --locked mergiraf
sudo install -m 0755 ~/.cargo/bin/mergiraf /usr/local/bin/mergiraf
sudo apt-get autoremove -y rustc cargo
```

**Do not check Mergiraf into the workflow as a `cargo install` step.** Host-level provisioning, parallel to `/opt/opam`.

**Acceptance gate after Phase 5:** `which mergiraf && mergiraf --version` works as `vyos` on the runner.

---

## Phase 6 — Configure git on the runner

System-wide config so every build benefits without per-job setup:

```sh
sudo tee -a /etc/gitconfig > /dev/null <<'EOF'
[merge]
    conflictstyle = zdiff3
[merge "mergiraf"]
    name = "Mergiraf"
    driver = "mergiraf merge --git %O %A %B -s %S -x %X -y %Y -p %P"
[rerere]
    enabled = true
    autoupdate = true
EOF
```

`zdiff3` (zealous diff3) is required for Mergiraf's best work; the linear `merge` default elides context Mergiraf needs.

**Acceptance gate:** deliberate-conflict smoke test triggers Mergiraf:

```sh
cd /tmp && rm -rf merge-test && mkdir merge-test && cd merge-test
git init -q
echo '*.c merge=mergiraf' > .gitattributes
cat > foo.c <<'EOF'
int add(int a, int b) { return a + b; }
int sub(int a, int b) { return a - b; }
EOF
git add . && git commit -qm base
git checkout -qb branch1
sed -i 's/int add/long add/' foo.c && git commit -aqm "rename add"
git checkout -q main 2>/dev/null || git checkout -q master
sed -i 's/return a + b/return (a + b)/' foo.c && git commit -aqm "parenthesize add"
git merge branch1 2>&1 | grep -i mergiraf && echo "Mergiraf is wired up."
```

---

## Phase 7 — Add `.gitattributes` to upstream clones at clone-time

Runner-level git config sets up the merge driver, but each upstream tree needs `.gitattributes` to tell git which files use it. Add this in every `bin/ci-*.sh` that clones an upstream repo, immediately after the clone:

```sh
# In bin/ci-setup-vyos1x.sh (and ci-setup-vyos-build.sh, ci-setup-kernel.sh, post-merge: kernel/common/scripts/apply-to-tree.sh):
cat > vyos-1x/.gitattributes <<'EOF'
*.c    merge=mergiraf
*.h    merge=mergiraf
*.py   merge=mergiraf
*.sh   merge=mergiraf
*.dts  merge=mergiraf
*.dtsi merge=mergiraf
*.S    merge=mergiraf
EOF
```

For the kernel tree, broader file set; consider also `*.lds`. Mergiraf falls back gracefully (line-based) for unsupported extensions, so over-listing is harmless.

**Note:** `.gitattributes` must be in the upstream tree at the time of `git apply --3way`. Do not put it in the parent repo — git apply only consults attributes in the target tree.

---

## Phase 8 — Patch-rot CI detector

Create `.github/workflows/patch-rot-check.yml`:

```yaml
name: Patch rot check

# Runs weekly against latest upstream HEAD to catch drift before it bites a real build.
# Does not block, only reports.

on:
  schedule:
    - cron: "0 6 * * 1"   # Mondays 06:00 UTC
  workflow_dispatch:

jobs:
  check:
    runs-on: ubuntu-24.04-arm
    permissions:
      contents: read
    steps:
      - uses: actions/checkout@v6

      - name: Check vyos-1x patches against latest current
        run: |
          set -e
          git clone --branch current --depth 1 https://github.com/vyos/vyos-1x.git
          cd vyos-1x
          rc=0
          for p in ../data/vyos-1x-*.patch; do
              if ! git apply --3way --check --whitespace=nowarn "$p" 2>&1; then
                  echo "::warning file=$p::Patch will not apply cleanly against latest vyos/vyos-1x current"
                  rc=1
              fi
          done
          exit $rc
        continue-on-error: true

      - name: Check vyos-build patches against latest current
        run: |
          set -e
          git clone --branch current --depth 1 https://github.com/vyos/vyos-build.git
          cd vyos-build
          rc=0
          for p in ../data/vyos-build-*.patch; do
              if ! git apply --3way --check --whitespace=nowarn "$p" 2>&1; then
                  echo "::warning file=$p::Patch will not apply cleanly against latest vyos/vyos-build current"
                  rc=1
              fi
          done
          exit $rc
        continue-on-error: true

      - name: Check kernel patches against pinned tag
        run: |
          set -e
          KERNEL_TAG=$(cat data/ask-kernel.pin | tr -d '[:space:]')
          git clone --branch "$KERNEL_TAG" --depth 1 \
              https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git
          cd linux
          rc=0
          # Post-merge: iterate every bucket in apply order
          for bucket in \
              ../kernel/common/patches/vyos \
              ../kernel/common/patches/board \
              ../kernel/common/patches/fixes \
              ../kernel/flavors/ask/patches/ask \
              ../kernel/flavors/ask/patches/fixes \
              ../kernel/flavors/vpp/patches; do
              [ -d "$bucket" ] || continue
              for p in "$bucket"/*.patch; do
                  [ -f "$p" ] || continue
                  if ! git apply --3way --check --whitespace=nowarn "$p" 2>&1; then
                      echo "::warning file=$p::Patch will not apply cleanly against $KERNEL_TAG"
                      rc=1
                  fi
              done
          done
          exit $rc
        continue-on-error: true
```

Use GitHub-hosted ARM here (not the self-hosted runner): cheap correctness check, not a full build, should not block real builds. `continue-on-error: true` ensures one failing patch does not mask the others.

---

## Phase 9 — Documentation

Add a section to `AGENTS.md` (or create `docs/PATCHES.md`):

```markdown
## Patch conventions

- All `.patch` files in `data/` (and post-merge `kernel/`) are git-format diffs (have `diff --git` and `index abc..def` lines).
- Patches are applied with `git apply --3way --whitespace=nowarn`. Never with raw GNU `patch`. Never with `--no-backup-if-mismatch`.
- File-addition-only patches do not exist. Files to be added land in `data/files/<target>/...` (or `kernel/common/files/`, `kernel/flavors/*/files/`) and are `cp`'d into place by the relevant `bin/ci-*.sh` script.
- To add a new patch:
  1. Clone the target upstream at the version you're patching.
  2. Make your changes.
  3. `git add -A && git diff --cached > /path/to/repo/<bucket>/<NNN>-<slug>.patch`
  4. Add a header comment in the patch file noting upstream version, why the patch exists, and status (carry-forward / pending-upstream / never-upstreamable).
- Patch rot is monitored weekly by `.github/workflows/patch-rot-check.yml`. Watch the Actions tab for warnings.
- The self-hosted runner has Mergiraf installed and registered as a git merge driver; conflicting hunks in C, Python, DTS, and shell files are resolved syntax-aware where possible. Manual conflict resolutions are memoized via `git rerere`.
```

Cross-reference the producer's existing `kernel-ls1046a-build/.clinerules/10-patch-authoring.md` (now folded into the merged `AGENTS.md` per `INTEGRATION-PLAN.md` §6.1): the visual-inspection step described there remains mandatory even after `git apply --3way` adoption — `--3way` catches context drift but does not catch malformed hunk arithmetic that produces a patch that *applies* but generates *wrong content*.

---

## Acceptance suite (full verification)

Run after every phase. Full suite passes after Phase 9.

```sh
#!/bin/sh
set -e
cd "$(git rev-parse --show-toplevel)"

echo "[1/8] No --no-backup-if-mismatch left anywhere"
! grep -rn 'no-backup-if-mismatch' bin/ .github/ data/ kernel/ 2>/dev/null \
    || { echo FAIL; exit 1; }

echo "[2/8] No raw 'patch -p1' calls in CI scripts"
! grep -rnE 'patch[ ]+(-[A-Za-z]+ )*-p1' bin/ .github/ kernel/ 2>/dev/null \
    || { echo FAIL; exit 1; }

echo "[3/8] All *.patch files are git format"
fail=0
for p in $(find data kernel -name '*.patch' -type f 2>/dev/null); do
    head -1 "$p" | grep -q '^diff --git' || { echo "  not git-format: $p"; fail=1; }
    grep -q '^index [0-9a-f]\+\.\.[0-9a-f]\+' "$p" || { echo "  no index line: $p"; fail=1; }
done
[ $fail -eq 0 ] || exit 1

echo "[4/8] No file-addition-only patches"
fail=0
for p in $(find data kernel -name '*.patch' -type f 2>/dev/null); do
    if grep -q '^@@' "$p" && ! grep -q '^@@ -[1-9]' "$p"; then
        echo "  pure-addition: $p"; fail=1
    fi
done
[ $fail -eq 0 ] || exit 1

echo "[5/8] All patches apply --3way against their target upstream"
# Run the verify_patches loop from Phase 3 here.

echo "[6/8] CI scripts clone upstream + drop .gitattributes"
grep -lq 'gitattributes' bin/ci-setup-vyos1x.sh \
    && grep -lq 'gitattributes' bin/ci-setup-vyos-build.sh \
    && grep -lq 'gitattributes' bin/ci-setup-kernel.sh \
    || { echo FAIL; exit 1; }

echo "[7/8] Patch-rot workflow exists"
test -f .github/workflows/patch-rot-check.yml || { echo FAIL; exit 1; }

echo "[8/8] Documentation updated"
grep -lq 'git apply --3way' AGENTS.md docs/PATCHES.md 2>/dev/null \
    || { echo FAIL; exit 1; }

echo "All acceptance tests passed."
```

---

## Stop conditions

Halt and ask before proceeding if:

1. A patch in Phase 2 fails to apply with the existing `patch` tool against the documented upstream tag. The patch is already broken or the documented tag is wrong; do not guess.
2. Phase 3 verification fails for a patch that Phase 2 said was successfully regenerated. Internally inconsistent — bug in the regeneration script.
3. Mergiraf release archives do not include an `aarch64-unknown-linux-gnu` build for the latest version. Decide between (a) older version that does, (b) cargo build, or (c) skip Mergiraf install. Do not silently fall back.
4. A `bin/ci-*.sh` script applies patches in a way not matching Phase 4 patterns (e.g., `patch -p2`, applies in chroot, applies after non-trivial transform). Replacement may need different `git apply` flags.
5. Patch count after migration does not match count before, after accounting for file-addition patches removed in Phase 1. Patches should not vanish silently.

---

## Rollback

Migration contained in branch `chore/patch-migration-3way`. Revert: `git checkout main` and originals return. Mergiraf install on the runner is harmless if config is removed:

```sh
sudo sed -i '/\[merge "mergiraf"\]/,/^$/d' /etc/gitconfig
sudo rm -f /usr/local/bin/mergiraf
```

Patches in git format apply fine with old-style `patch -p1` (`index` lines ignored), so even if workflow scripts revert, regenerated patches remain compatible.

---

## What NOT to do

- Do not invoke `cargo install mergiraf` from inside the workflow. Host-level provisioning.
- Do not silence `git apply --3way` failures with `|| true` or `|| patch -p1`. Loud failure is the point.
- Do not regenerate patches by hand-editing them to add `index` lines. Use actual `git diff --cached` output.
- Do not switch to `git am` instead of `git apply` for the build path. `git am` creates commits in the target tree, which the build does not need and which will confuse downstream `dpkg-buildpackage` / `git describe` invocations.
- Do not add Mergiraf as a runtime dependency in `auto-build.yml`'s "Install Dependencies (host base)" step. The merge driver runs only when conflicts occur during `git apply --3way`; making it a build-time install adds latency without benefit on the happy path.

---

## Interaction with `INTEGRATION-PLAN.md`

If executed **after** the integration merge (PR 9 in the integration sequence):

- Patch surface to migrate: ~40+ patches across `kernel/common/patches/{vyos,board,fixes}/`, `kernel/flavors/ask/patches/{ask,fixes}/`, and `kernel/flavors/vpp/patches/` (if any), plus the unchanged `data/{vyos-1x,vyos-build}-*.patch` set.
- The producer's existing `kernel-ls1046a-build/scripts/normalize-patch.awk` (folded into `kernel/common/scripts/normalize-patch.awk` per `INTEGRATION-PLAN.md` §2) is **made obsolete** by Phase 2's git-diff-based regeneration. Mark it as such or delete; document in the commit body why.
- The producer's `.clinehooks/block-dual-ref-push.sh` and `.clinehooks/hunk-validator` (folded into the merged `.clinehooks/` per integration plan) **remain in force** — `git apply --3way` does not detect malformed hunk arithmetic that produces well-formed but semantically wrong patches. The producer's ask13→ask14 incident is the cautionary tale.
- The patch-rot workflow (Phase 8) iterates every flavor bucket, not just `data/`.

If executed **before** the integration merge:

- Patch surface to migrate: ~22 patches under `data/` only.
- After the integration merge lands, the producer's 17 patches arrive in `kernel/flavors/ask/patches/` already in git-diff format (the producer authors via `git diff --no-prefix` piped through `normalize-patch.awk`). These need a one-shot regeneration through Phase 2's git pipeline (skipping `normalize-patch.awk`) to gain proper `index` lines, but the existing context is already correct.
- Mergiraf, rerere, runner config, and patch-rot workflow are unchanged by the merge — those phases do not need re-execution.

**Recommendation:** execute **after** the integration merge. The cost difference is small (~2x patch count to regenerate), the consolidation of the migration into a single PR against a single tree is clearer to review, and the post-merge patch surface is the long-term steady state — migrating it once and only once minimizes churn.