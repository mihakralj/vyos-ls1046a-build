# PATCH MIGRATION: `git apply --3way` + Mergiraf + rerere

**Status:** Ready for execution — integration merge (PRs 1–7 of `INTEGRATION-PLAN.md`) is complete on `main`. Mergiraf is already installed on the self-hosted runner (see Phase 5 note).
**Date:** 2026-05-09 (revised after pre-flight inventory)
**Companion plan:** [`INTEGRATION-PLAN.md`](./INTEGRATION-PLAN.md) (single-repo merge + FLAVOR switch)

## Pre-flight inventory snapshot (2026-05-09)

Actual patch surface as found on `main` after single-repo absorption (PR 7):

| Bucket | Count | Apply-site |
|---|---|---|
| `data/vyos-1x-*.patch` | 16 | `bin/ci-setup-vyos1x.sh` |
| `data/vyos-build-*.patch` | 2 | `bin/ci-setup-vyos-build.sh` |
| `data/kernel-patches/*.patch` | 4 | staged by `kernel/common/scripts/stage-kernel.sh` |
| `data/ask-userspace/{fmc,fmlib,libnetfilter-conntrack,libnfnetlink}/` | 5 | per-component build scripts |
| `kernel/common/patches/{vyos,board,fixes}/` | 6 | `stage-kernel.sh` + `apply-to-tree.sh` |
| `kernel/flavors/ask/patches/{ask,fixes}/` | 10 | `stage-kernel.sh` + `apply-to-tree.sh` |
| `kernel/flavors/ask/userspace-patches/{ppp,rp-pppoe}/` | 2 | `kernel/common/scripts/build-ask-ppp.sh` |
| `ASK/patches/{fmc,fmlib}/` | 4 | `bin/ci-build-fmc.sh`, `bin/ci-build-fmlib.sh` |
| `patches/libcli/` | (workspace) | `bin/ci-build-ask-userspace.sh` (already uses `git apply`) |

**Total: ~47 patches across 9 buckets, 8 distinct apply-sites.** Plan's earlier "~40+" estimate was low.

---

## Sequencing context (historical)

*Original plan called this "PR 9" of the integration sequence. PR 7 (single-repo absorption) landed 2026-05-09; PR 8 (matrix builds) is deferred. This work is now the next major migration on `main` and should land as a single dedicated branch (`chore/patch-migration-3way`) rather than being interleaved with other refactors.*

---

## Why this work is needed

The `vyos-ls1046a-build` repo currently applies upstream modifications using GNU `patch` with `--no-backup-if-mismatch -p1`. This silently fuzzes hunks into the wrong place when upstream context drifts, and silently no-ops when patches reference files that have moved. Both failure modes are undetectable from build logs.

The the archived kernel-build repo's ask13→ask14 incident (documented in `kernel-ls1046a-build/AGENTS.md`) shows the worst-case symptom: a malformed `@@ -25,3 +25,6 @@` hunk header silently truncated 12 added lines to 6, dropping `obj-$(CONFIG_FSL_SDK_FMAN)` and `obj-$(CONFIG_FSL_SDK_DPAA_ETH)` from a Makefile and producing a kernel with neither `sdk_fman` nor `sdk_dpaa` built. `git apply` reported success. Only post-hoc visual inspection caught it.

The replacement stack is:

1. **`git apply --3way`** for patch application. Uses blob SHAs as anchors, does a real 3-way merge when context drifts, fails loudly with conflict markers instead of silently fuzzing.
2. **Mergiraf** as a git merge driver. AST-aware conflict resolution for C, Python, Rust, and other tree-sitter-supported languages. Kicks in automatically when 3-way merge has line-level conflicts that don't actually overlap structurally.
3. **`git rerere`** to memoize manual conflict resolutions across builds.

`git apply --3way` requires patches to carry `index abc1234..def5678 100644` lines referencing real blob SHAs. Patches produced by `diff -u` do not have these. Patches produced by `git diff` or `git format-patch` do. All existing `.patch` files in this repo must be regenerated through git tooling.

---

## Inputs

- Repo root: `/home/vyos/vyos-ls1046a-build`
- **Patch buckets** (post-integration layout, see snapshot table above for counts):
  - `data/vyos-1x-*.patch` — against `vyos/vyos-1x@current`
  - `data/vyos-build-*.patch` — against `vyos/vyos-build@current`
  - `data/kernel-patches/*.patch` — against the pinned kernel (today: `linux-6.18.28`, see below)
  - `data/ask-userspace/{fmc,fmlib,libnetfilter-conntrack,libnfnetlink}/*.patch` — against pinned upstream userspace tarballs
  - `kernel/common/patches/{vyos,board,fixes}/*.patch` — flavor-agnostic kernel deltas
  - `kernel/flavors/ask/patches/{ask,fixes}/*.patch` — ASK fast-path + ASK-specific 6.6.y/6.18.y fixes
  - `kernel/flavors/ask/userspace-patches/{ppp,rp-pppoe}/*.patch` — against pinned ppp / rp-pppoe sources
  - `ASK/patches/{fmc,fmlib}/*.patch` — against `nxp-qoriq/fmlib` and `nxp-qoriq/fmc` at the pinned tag (`lf-6.18.2-1.0.0`)
  - `patches/libcli/*.patch` — already in git format, applied via `git apply` (line 148 of `bin/ci-build-ask-userspace.sh`); needs only the `--3way` upgrade
- **Kernel version pinning:** as of 2026-05-09, both `default` and `ask` flavors target `linux-6.18.28` (per `versions.lock` and `kernel/flavors/ask/README.md`). The historical 6.6.137 ASK base is gone — ASK was forward-ported through `nxp-qoriq/linux ask-6.6-port @ 6d0b77e` and now applies on top of `linux-6.18.28`. **Phase 2c and 2d both target the same kernel tag** (`v6.18.28`) and can share a single checkout. Re-evaluate if `versions.lock` is bumped after the migration starts.
- **CI scripts that apply patches** (verified 2026-05-09; line numbers from current `main`):
  - `bin/ci-setup-vyos1x.sh:70,74` — `patch --no-backup-if-mismatch -p1`
  - `bin/ci-setup-vyos-build.sh:35–36` — `patch --no-backup-if-mismatch -N -p1 -d vyos-build`
  - `bin/ci-build-fmc.sh:66` — `patch --no-backup-if-mismatch -p1`
  - `bin/ci-build-fmlib.sh:71,74,77` — `patch --no-backup-if-mismatch -p1` (3 patches: ASK, B1, B3)
  - `bin/ci-build-ask-userspace.sh:145,147,148` — already `git apply` for libcli; needs `--3way` flag added
  - `kernel/common/scripts/stage-kernel.sh:140` — `patch --no-backup-if-mismatch -p1 -d "$KSRC"`
  - `kernel/common/scripts/apply-to-tree.sh:135,137` — `git apply -p1 --unsafe-paths --directory="$KDIR"` (already git apply; needs `--3way`)
  - `kernel/common/scripts/build-ask-modules.sh:170` — `git apply --whitespace=nowarn` (needs `--3way`)
  - `kernel/common/scripts/build-ask-ppp.sh:174,176,183` — `patch -p1` (dry-run + apply pair)
  - `kernel/common/scripts/patch-health.sh:160` — `git apply --check -p1 --unsafe-paths` (needs `--3way` to keep dry-run parity with `apply-to-tree.sh`)
  - `bin/ci-setup-kernel.sh`, `bin/ci-setup-kernel-ask.sh` — `cp` patches into the kernel build dir only; actual application happens in `stage-kernel.sh` / `apply-to-tree.sh`
- **Self-hosted runner:** Debian 13 trixie, aarch64 (Cobalt 100, 32 vCPU). Persistent state in `/opt/`, `$HOME/.ccache`, `/opt/opam`, `~/.cargo/bin/`. Runner user `vyos` with passwordless sudo. **PATH for the `actions-runner` service includes `~/.cargo/bin`** (verified 2026-05-09 via `systemctl cat actions.runner.*`), so the `mergiraf` binary is reachable from CI steps without modification.
- **Build workflow:** `.github/workflows/auto-build.yml` (called from `self-hosted-build.yml`)

---

## Outputs expected

By end of migration:

1. Every `.patch` file under `data/` and `kernel/{common,flavors/*}/patches/` is in git format (has `diff --git a/X b/X` headers and `index ABC..DEF` lines).
2. Every `.patch` file applies cleanly with `git apply --3way --check` against the upstream version it targets, without fuzz or context massaging.
3. Patches that were "additions only" (single hunk, `@@ -0,0 +1,N @@`) have been removed; the added file content lives as a plain file under `data/files/`, `kernel/common/files/`, or `kernel/flavors/*/files/`, copied into place by the relevant `bin/ci-*.sh` script.
4. All `bin/ci-*.sh` scripts use `git apply --3way --whitespace=nowarn` instead of `patch ... -p1`. The `--no-backup-if-mismatch` flag appears nowhere in the repo.
5. Mergiraf is installed on the self-hosted runner and registered as a git merge driver so that all builds benefit. **As of 2026-05-09 mergiraf 0.17.0 is already installed at `/home/vyos/.cargo/bin/mergiraf` and wired in `~/.gitconfig` (user-scope, `vyos` user) — Phase 5/6 are de-facto complete; only the system-scope migration is optional.**
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

# 2. Inventory all patches across data/ and kernel/ trees
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

**Acceptance gate:** every patch in `/tmp/migration/file-additions.txt` removed; corresponding plain file under `data/files/` or `kernel/.../files/`; relevant `bin/ci-*.sh` updated. Run a build end-to-end and confirm no patch-apply errors.

---

## Phase 2 — Regenerate modification patches in git format

Regenerate each modification patch through git so it carries the index lines `--3way` needs.

**Hard rule: refuse fuzz during regeneration.** Apply with `patch -F0 --no-backup-if-mismatch -p1` so any context drift fails loudly instead of silently encoding the wrong location into the regenerated patch. If a patch needs fuzz to apply against current upstream, that is a real drift signal — stop, inspect, and either refresh the patch by hand or document why it is fuzz-tolerant. Do not paper over it.

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
    if ! patch -F0 --no-backup-if-mismatch -p1 < "$p"; then
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

### 2c. Kernel patches (`data/kernel-patches/` + flavor-agnostic `kernel/common/patches/`)

Kernel patches target a specific kernel version, not a moving branch. The legacy `data/ask-kernel.pin` mechanism was retired during integration (per `AGENTS.md` § "Single-repo absorption (May 2026)"). The current source of truth is `vyos-build/data/defaults.toml`, surfaced through `kernel/common/scripts/sync-kernel-version.sh`. As of 2026-05-09 the pin is `linux-6.18.28` for both `default` and `ask` flavors — a single kernel checkout works for the whole regen pass.

```sh
REPO=$(pwd)
# Source helper to populate KERNEL_VERSION (e.g. "6.18.28"). Note: the helper
# uses `set -euo pipefail`; isolate it in a sub-shell if you don't want those
# semantics inherited by the calling regen script.
KERNEL_VERSION=$(bash -c '. "'"$REPO"'/kernel/common/scripts/sync-kernel-version.sh" >/dev/null && echo "$KERNEL_VERSION"')
KERNEL_TAG="v${KERNEL_VERSION}"
[ -n "$KERNEL_VERSION" ] || { echo "Cannot determine kernel tag"; exit 1; }

cd /tmp/regen
rm -rf linux
git clone --branch "$KERNEL_TAG" --depth 1 \
    https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git
cd linux

# Apply patches in filename order, refusing fuzz
for p in "$REPO"/data/kernel-patches/*.patch; do
    base=$(basename "$p")
    echo "=== Regenerating $base ==="
    if ! patch -F0 --no-backup-if-mismatch -p1 < "$p"; then
        echo "FAIL: $base did not apply against $KERNEL_TAG (or required fuzz)"
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

### 2d. Absorbed flavor-bucket patches (kernel/common + kernel/flavors/ask)

**Use the same `/tmp/regen/linux` checkout from 2c** — do not re-clone. The buckets must be applied in the exact order `kernel/common/scripts/apply-to-tree.sh` uses, because later patches in the chain depend on earlier ones being on disk. Stage the SDK source drop first (it is a *file* drop, not a patch) so any ASK patches that touch SDK paths can resolve.

```sh
# Already in /tmp/regen/linux from Phase 2c; tree is on a tmp HEAD with all
# data/kernel-patches/* applied. That state is fine — the kernel/common +
# kernel/flavors/ask patches stack on top of those.

# Step 0: stage SDK source drop (only needed for FLAVOR=ask)
SDK_DIR="$REPO/kernel/flavors/ask/sdk-sources"
if [ -d "$SDK_DIR" ]; then
    (cd "$SDK_DIR" && find . -type f) | while read -r f; do
        f=${f#./}
        mkdir -p "$(dirname "/tmp/regen/linux/$f")"
        cp "$SDK_DIR/$f" "/tmp/regen/linux/$f"
    done
    git add -A && git commit -m "tmp: stage SDK sources" --quiet --allow-empty
fi

# Step 1: iterate buckets in apply order; cumulative state is preserved
for bucket_dir in \
    "$REPO/kernel/common/patches/vyos" \
    "$REPO/kernel/common/patches/board" \
    "$REPO/kernel/common/patches/fixes" \
    "$REPO/kernel/flavors/ask/patches/ask" \
    "$REPO/kernel/flavors/ask/patches/fixes"; do
    [ -d "$bucket_dir" ] || continue
    for p in "$bucket_dir"/*.patch; do
        [ -f "$p" ] || continue
        base=$(basename "$p")
        echo "=== Regenerating $bucket_dir/$base ==="
        if ! patch -F0 --no-backup-if-mismatch -p1 < "$p"; then
            echo "FAIL: $bucket_dir/$base did not apply (or required fuzz)"
            exit 1
        fi
        git add -A
        git diff --cached > "$p.new"
        git commit -m "tmp: $base" --quiet --allow-empty
    done
    for p in "$bucket_dir"/*.patch; do
        [ -f "$p.new" ] && mv "$p.new" "$p"
    done
done
```

For `default` flavor regen, repeat Phase 2c+2d in a separate clone *without* the SDK drop and *without* the ASK buckets. The `default` flavor today only stacks `kernel/common/patches/{vyos,board,fixes}/`.

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
# Also iterate kernel/{common,flavors/ask}/patches/* buckets in apply order
# (each bucket inherits cumulative state — see Phase 2d for the loop pattern)
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

**Note on `apply-to-tree.sh`:** the absorbed `kernel/common/scripts/apply-to-tree.sh` already uses `git apply -p1 --unsafe-paths --directory="$KDIR"` (lines 135, 137) — the change there is just adding `--3way` to both the strict apply and the `--reject` fallback. Same for `kernel/common/scripts/patch-health.sh:160` (the dry-run probe).

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

**Status (2026-05-09): already done.** `mergiraf 0.17.0` is present at `/home/vyos/.cargo/bin/mergiraf` on the Cobalt 100 runner (cargo-install path, not the `/usr/local/bin/` location originally specified). Since the runner executes as `vyos` and `~/.cargo/bin` is on that user's `$PATH`, no migration is required for the runner happy path. The `/usr/local/bin/` placement below remains the recommendation for any *new* runner provisioning.

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

**Status (2026-05-09): already done at user scope.** `~/.gitconfig` for the `vyos` user already contains `merge.conflictstyle = zdiff3`, `[merge "mergiraf"]` driver, and `[rerere]` section. Verify with `git config --get-all merge.conflictstyle` and `git config --get merge.mergiraf.driver` from any clone. The system-wide migration below is optional — useful only if a second user (e.g., `root` running the runner under sudo) ever needs the driver. **Recommendation: leave as-is.**

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

**Acceptance gate:** deliberate-conflict smoke test triggers Mergiraf. **Run as the `vyos` user** so the test exercises the same gitconfig the actions-runner sees:

```sh
sudo -iu vyos bash <<'TEST'
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
TEST
```

---

## Phase 7 — Add `.gitattributes` to upstream clones at clone-time

Runner-level git config sets up the merge driver, but each upstream tree needs `.gitattributes` to tell git which files use it. Add this in every script that clones an upstream repo (`bin/ci-setup-vyos1x.sh`, `bin/ci-setup-vyos-build.sh`, `kernel/common/scripts/fetch-kernel.sh` / `apply-to-tree.sh`), immediately after the clone:

```sh
cat > vyos-1x/.gitattributes <<'EOF'
*.c     merge=mergiraf
*.h     merge=mergiraf
*.cc    merge=mergiraf
*.cpp   merge=mergiraf
*.hpp   merge=mergiraf
*.py    merge=mergiraf
*.dts   merge=mergiraf
*.dtsi  merge=mergiraf
*.json  merge=mergiraf
*.yml   merge=mergiraf
*.yaml  merge=mergiraf
*.toml  merge=mergiraf
*.xml   merge=mergiraf
EOF
```

**Pattern set is constrained to languages mergiraf 0.17.0 actually parses** (verified via `mergiraf languages`). Notable omissions: shell scripts (`*.sh`, `*.bash`) and assembler (`*.S`, `*.lds`) — mergiraf has no AST grammar for these, so listing them yields no benefit and risks confusing future readers. Line-based merge still applies as the default fallback for any extension not listed.

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
          # Kernel pin auto-tracked via vyos-build/data/defaults.toml
          KERNEL_VERSION=$(bash -c '. kernel/common/scripts/sync-kernel-version.sh >/dev/null && echo $KERNEL_VERSION')
          KERNEL_TAG="v${KERNEL_VERSION}"
          git clone --branch "$KERNEL_TAG" --depth 1 \
              https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git
          cd linux
          rc=0
          # Iterate every flavor bucket in apply order
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

- All `.patch` files in `data/` and `kernel/` are git-format diffs (have `diff --git` and `index abc..def` lines).
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

Cross-reference the archived kernel-build repo's existing `kernel-ls1046a-build/.clinerules/10-patch-authoring.md` (now folded into the merged `AGENTS.md` per `INTEGRATION-PLAN.md` §6.1): the visual-inspection step described there remains mandatory even after `git apply --3way` adoption — `--3way` catches context drift but does not catch malformed hunk arithmetic that produces a patch that *applies* but generates *wrong content*.

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

The integration merge (PRs 1–7) is complete on `main` as of 2026-05-09. This migration runs against the post-merge tree:

- Patch surface: ~47 patches across `data/{vyos-1x,vyos-build,kernel-patches,ask-userspace}`, `kernel/common/patches/{vyos,board,fixes}/`, `kernel/flavors/ask/patches/{ask,fixes}/`, `kernel/flavors/ask/userspace-patches/`, and `ASK/patches/{fmc,fmlib}/`.
- The absorbed `kernel/common/scripts/normalize-patch.awk` is **made obsolete** by Phase 2's git-diff-based regeneration. Mark it as such (header comment) or delete in the migration PR; document in the commit body why.
- The absorbed hunk-validator hooks (`.clinehooks/block-dual-ref-push.sh`, `.clinehooks/hunk-validator`) **remain in force** — `git apply --3way` does not detect malformed hunk arithmetic that produces well-formed but semantically wrong patches. The archived kernel-build repo's ask13→ask14 incident (`AGENTS.md`) is the cautionary tale.
- The patch-rot workflow (Phase 8) iterates every flavor bucket, not just `data/`.