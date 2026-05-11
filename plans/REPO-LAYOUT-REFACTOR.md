# Repository Layout Refactor: Symmetric Common + Flavor Tree

**Goal.** Replace today's mixed kernel/userspace layout with a single symmetric tree where every flavor mirrors `common/` with `kernel/` and `userspace/` subdirectories, plus a small set of board-level/cross-cutting top-level dirs that don't belong to any flavor.

**Status today.** The kernel side already has a clean `kernel/{common,flavors/<X>}/` split (committed pre-multi-flavor, see `bin/ci-stage-kernel.sh` and `kernel/common/scripts/stage-kernel.sh`). The userspace side is flat under `data/` with content-based gating. Three legacy ASK working trees (`ASK/`, `ask-userspace/`, `data/ask-userspace/`) have overlapping but non-identical content and need consolidating.

---

## Recommended layout

The user-proposed layout is good but flips one organizational axis: it puts `flavor` above `domain` (kernel/userspace). The standard convention in build-system repos (Yocto `meta-*` layers, OpenWrt `target/`/`package/`, Buildroot `board/`/`package/`, Debian source-package `debian/patches/series` with `*.diff` per topic) is the opposite — group by **what is being built** first, then by **which variant**, because:

1. Build-stage scripts typically iterate one domain at a time (`stage-kernel.sh` doesn't care about userspace; `build-vyos-1x.sh` doesn't care about kernel patches). Domain-first lets the script's `find` query be a single subtree, no flavor-axis traversal.
2. Onboarding is "where do kernel patches live?" → one answer (`kernel/`), not "where do ASK kernel patches live?" → `ask/kernel/`. Reduces the search space when the question is half-formed.
3. The current `kernel/{common,flavors/<X>}/` already follows this convention. Flipping the kernel side to match a new flavor-first userspace would be churn AND a regression vs the cleaner of the two existing layouts.

So the recommendation is the **mirror** of the user's proposal: keep `kernel/` as the top-level for kernel-related artifacts (current layout, no churn), and add a parallel `userspace/` top-level for userspace artifacts. Both follow `<domain>/{common,flavors/<X>}/` internally. This is the layout chosen below.

```
vyos-ls1046a-build/
├── kernel/                              ← unchanged (already correct)
│   ├── common/
│   │   ├── files/                       (fsl_fmd_shim.c, lp5812 driver source)
│   │   ├── kernel-config/               (8 fragments — board/dpaa1/extras/i2c-gpio/leds/network-perf/sfp/extras)
│   │   ├── patches/
│   │   │   ├── board/                   (5 patches — SFP, phylink, DPAA XDP, xhci, OEM SFP)
│   │   │   ├── fixes/                   (2 patches — perf-libperf-asm-headers, leds-lp5812-register)
│   │   │   └── vyos/                    (2 patches — byte-identical to vyos-build's 0001-/0003-)
│   │   ├── scripts/                     (stage-kernel.sh, patch-health.sh, sync-kernel-version.sh)
│   │   └── vyos-base/                   (vyos-build kernel-config snippets we override)
│   └── flavors/
│       ├── default/
│       │   ├── kernel-config/           (1 fragment)
│       │   └── patches/                 (empty placeholder)
│       ├── vpp/
│       │   ├── kernel-config/           (1 fragment)
│       │   └── patches/                 (empty placeholder)
│       └── ask/
│           ├── kernel-config/           (1 fragment — SDK DPAA + ASK fast-path + CAAM IPsec)
│           ├── oot-modules/             (cdx, fci, auto_bridge, iptables-extensions)
│           ├── patches/
│           │   ├── ask/                 (7 patches — kernel hooks, EHASH, netfilter, bridge, xfrm)
│           │   └── fixes/               (7 patches — INA234 hwmon, swphy 10G, MODULE_SIG, …)
│           └── sdk-sources/             (vendored NXP SDK driver sources w/ ASK-edit markers)
│
├── userspace/                           ← NEW top-level (mirror of kernel/)
│   ├── common/
│   │   ├── vyos-1x/
│   │   │   ├── patches/                 (was data/vyos-1x-NNN-*.patch — 19 patches, all flavors)
│   │   │   └── reftree.cache            (was data/reftree.cache)
│   │   ├── vyos-build/
│   │   │   └── patches/                 (was data/vyos-build-NNN-*.patch — 2 patches, all flavors)
│   │   └── live-build/
│   │       └── hooks/                   (was data/hooks/*.chroot — 92, 95, 96, 99 — flavor-agnostic)
│   └── flavors/
│       ├── default/
│       │   └── live-build/hooks/        (empty placeholder)
│       ├── vpp/
│       │   ├── vyos-1x/patches/         (could host 010-vpp-platform-bus.patch when isolated)
│       │   └── live-build/hooks/        (empty placeholder)
│       └── ask/
│           ├── live-build/hooks/        (97-ask-userspace.chroot moved here)
│           ├── packages/                (consolidated source tree — was ASK/ + ask-userspace/ + data/ask-userspace/)
│           │   ├── auto_bridge/         (sources + .patches/ subdir if needed)
│           │   ├── cdx/
│           │   ├── cmm/
│           │   ├── dpa_app/
│           │   ├── fci/
│           │   ├── fmc/
│           │   │   └── patches/         (was data/ask-userspace/fmc/01-mono-ask-extensions.patch)
│           │   ├── fmlib/
│           │   │   └── patches/         (was data/ask-userspace/fmlib/{01,02}-*.patch)
│           │   ├── libcli/
│           │   ├── libnetfilter-conntrack/
│           │   │   └── patches/
│           │   └── libnfnetlink/
│           │       └── patches/
│           └── userspace-patches/       (ppp, rp-pppoe — already correctly located, leave in place)
│
├── board/                               ← NEW: hardware-specific files used by ALL flavors
│   ├── dtb/                             (was data/dtb/ — DTS sources, mono-gw.dtb fallback, sdk-dtsi/)
│   ├── mok/                             (was data/mok/ — MOK.pem for kernel module signing)
│   ├── scripts/                         (was data/scripts/ — fan-pid, sfp-check, vyos-postinstall, …)
│   ├── systemd/                         (was data/systemd/ — service units, .tmpfiles, .link)
│   └── vyos-config/                     (was data/config.boot.* — default config.boot files)
│
├── ci/                                  ← unchanged (was bin/) — optional rename for clarity
│   ├── ci-build-*.sh
│   ├── ci-setup-*.sh
│   └── common.sh
│
├── .github/workflows/                   ← unchanged
├── plans/                               ← unchanged
├── specs/                               ← unchanged
└── data/                                ← REMOVED entirely (all content migrated above)
```

### Why "board/" as a separate top-level (not under common/)

The `board/` directory holds artifacts that are neither kernel nor userspace patches — they're hardware-physical configuration (DTS sources, EFI signing key, fan-control daemon, hostname/MOTD scripts, default `config.boot.*`). They apply to every flavor on **this specific board** (Mono Gateway Development Kit), but they don't belong under `kernel/` or `userspace/` because:

- They're not patches — they're whole-file artifacts, signing material, runtime daemons, device-tree source.
- A future second-board port (hypothetical) would replace `board/` entirely while keeping `kernel/` and `userspace/` intact.

This matches Yocto's `meta-bsp/` (board support layer) vs `meta-distro/` separation, and OpenWrt's `target/linux/<arch>/` (board) vs `package/` (userspace) separation.

### Why three ASK trees collapse to one (`userspace/flavors/ask/packages/`)

Today there are **three overlapping ASK source trees** that are confusing and partially duplicated:

| Path | What it contains | Why it exists | Status after refactor |
|---|---|---|---|
| `ASK/` | Original NXP source drop: cmm/, dpa_app/, fci/lib/, patches/{fmc,fmlib}/, config/, README, LICENSE | Frozen one-shot port from upstream NXP at SDK absorption | **DELETE** — superseded by `userspace/flavors/ask/packages/` after content reconciliation |
| `ask-userspace/` | NXP upstream sources for fmc, fmlib, libnetfilter-conntrack, libnfnetlink | Used by `bin/ci-build-{fmc,fmlib}.sh` to clone upstream and apply patches at build time | **DELETE** — clone-on-demand should fetch from upstream `nxp-qoriq` GitHub at the pinned tag, not from a vendored mirror in this repo |
| `data/ask-userspace/` | Patches + libcli/auto_bridge/cdx prebuilt sources | Mixed bag — patches AND prebuilt artifacts | **MOVE** patches to `userspace/flavors/ask/packages/<pkg>/patches/`; prebuilt artifacts to `userspace/flavors/ask/packages/<pkg>/` |

The reconciliation rules:
- **Patch source of truth:** `data/ask-userspace/<pkg>/*.patch` (5 files: fmc/01, fmlib/01, fmlib/02, libnetfilter-conntrack/01, libnfnetlink/01). These are what `bin/ci-build-{fmc,fmlib}.sh` actually applies — verified by `grep` of those scripts.
- **`ASK/` and `ask-userspace/` are reference snapshots** of the upstream sources at port-time. The current build clones from upstream GitHub (`nxp-qoriq/{fmlib,fmc}` at tag `lf-6.18.2-1.0.0`) and applies the patches from `data/ask-userspace/`. The vendored copies under `ASK/` and `ask-userspace/` are **never built from** in CI — confirm with `grep -r 'ASK/' bin/ci-*.sh` and `grep -r 'ask-userspace/' bin/ci-*.sh` (excluding `data/ask-userspace/`).
- If the upstream tag is reachable at build time, the vendored copies are dead weight (~tens of MB of duplicate source). Delete them.
- If we want a guaranteed-buildable airgapped fallback, keep ONE copy under `userspace/flavors/ask/packages/<pkg>/upstream-snapshot/` with a README explaining the pin tag — but only if there's a real airgap requirement.

### Why `patches/` at the repo root (`patches/libcli/`) goes away

That single-directory orphan is a vestige. It contains libcli build patches that should live with libcli sources. Move to `userspace/flavors/ask/packages/libcli/patches/` and delete the top-level `patches/` directory.

### Why rename `bin/` → `ci/` (optional)

Industry convention in newer build-system repos (Yocto-derived, kas, etc.) is to use `ci/` or `scripts/` for build-orchestration shell scripts and reserve `bin/` for executables that are installed onto the target. Today `bin/` is misleading — these are CI orchestration scripts, not target binaries. **Optional** — pure cosmetic, would touch every workflow YAML and AGENTS.md reference. Recommend deferring unless we're already touching those files for other reasons.

---

## Comparison: user-proposed vs recommended

| Axis | User's `<flavor>/{kernel,userspace}/` | Recommended `<domain>/{common,flavors/<flavor>}/` |
|---|---|---|
| **Already-implemented kernel side** | ❌ requires moving `kernel/common/*` → `common/kernel/*` and `kernel/flavors/<X>/*` → `<X>/kernel/*` | ✅ no kernel changes, only add `userspace/` |
| **Stage-script complexity** | `find {common,<flavor>}/kernel -name '*.patch'` (two-axis traversal per domain) | `find kernel/{common,flavors/<flavor>} -name '*.patch'` (one subtree per call) |
| **`patch-health.sh` invariant grep** | Has to grep across N flavor trees, one per `--flavor` arg | Greps a single subtree (already works today) |
| **Onboarding question "where do kernel patches live?"** | "depends on which flavor" — N answers | One answer: `kernel/` |
| **New-flavor cost** | mkdir `<new>/kernel/` + `<new>/userspace/` (2 dirs, well-defined slot) | mkdir `kernel/flavors/<new>/{kernel-config,patches}` + `userspace/flavors/<new>/{...}` (4–6 dirs, also well-defined) |
| **Yocto/OpenWrt/Buildroot/Debian convention** | Atypical — flavor-first is rare | Standard — domain-first is the dominant pattern |
| **Mental model for board/ files** | Where do they live? Each flavor's userspace? Common? | Lives at top-level `board/`, orthogonal to flavor — clean separation |
| **Migration churn** | High — kernel side already correct, would need to move it | Low — only userspace side moves |

The recommended layout is strictly better because the kernel side already implements it; flipping the kernel side to match the user-proposed scheme would be a regression in mental model AND a churn cost for zero gain.

---

## Migration plan (incremental, each step independently shippable and testable)

### Phase 0 — pre-flight inventory (no code change)
1. Run `git ls-files | grep -E '^(ASK|ask-userspace|data)/' > /tmp/migration-inventory.txt`. Verify the 3 ASK trees + `data/` content matches what's documented above. Diff against `find` output to catch untracked stragglers.
2. Run `grep -rln 'data/' bin/ .github/workflows/ kernel/common/scripts/ AGENTS.md .clinerules/` to list every file that references `data/`. This is the change surface for path updates.
3. Same for `ASK/` and `ask-userspace/` references — proves what's actually consumed.

### Phase 1 — `board/` extraction (smallest, safest, biggest mental-model win)
1. `git mv data/dtb board/dtb` (renames are detected by Git as copy+delete with full history preserved).
2. `git mv data/mok board/mok`.
3. `git mv data/scripts board/scripts`.
4. `git mv data/systemd board/systemd`.
5. `git mv data/config.boot.* board/vyos-config/` (if present — verify with `ls data/config.boot.*`).
6. Update path references via `sed -i 's|data/dtb|board/dtb|g'` etc. across `bin/`, `.github/workflows/`, `kernel/common/scripts/`, `AGENTS.md`. Use `grep -rl` first to scope.
7. Local sanity: `bash bin/patch-health.sh --source release` should still pass for all flavors.
8. Dispatch a `flavor=default` build via `multi-flavor-release.yml` checkboxes (after that workflow lands) — verify ISO assembles.

### Phase 2 — `userspace/common/` extraction
1. `mkdir -p userspace/common/{vyos-1x,vyos-build,live-build}/patches userspace/common/live-build/hooks`.
2. `git mv data/vyos-1x-*.patch userspace/common/vyos-1x/patches/`.
3. `git mv data/vyos-build-*.patch userspace/common/vyos-build/patches/`.
4. `git mv data/reftree.cache userspace/common/vyos-1x/`.
5. `git mv data/hooks/{92,95,96,99}-*.chroot userspace/common/live-build/hooks/`.
6. Update `bin/ci-setup-vyos1x.sh` to read from `userspace/common/vyos-1x/patches/*.patch` instead of `data/vyos-1x-*.patch`.
7. Update `bin/ci-setup-vyos-build.sh` similarly.
8. Update the `for p in data/vyos-1x-*.patch` glob inside `bin/ci-setup-vyos1x.sh`'s `package.toml` `pre_build_hook` heredoc.
9. Update the cache-key derivation in `bin/ci-build-packages.sh` (`cat "$GITHUB_WORKSPACE"/data/vyos-1x-*.patch` → `cat "$GITHUB_WORKSPACE"/userspace/common/vyos-1x/patches/*.patch`). **Important:** this changes the cache key once, invalidating the existing cache. Add a one-line comment explaining the expected one-time cache miss.
10. Update `data/hooks/*.chroot` glob in `bin/ci-setup-vyos-build.sh` to `userspace/common/live-build/hooks/*.chroot`.
11. Dispatch `flavor=default` and `flavor=vpp` builds; verify both ISOs assemble.

### Phase 3 — `userspace/flavors/<flavor>/` for the empty placeholders
1. `mkdir -p userspace/flavors/{default,vpp}/live-build/hooks` (empty, with `.gitkeep`).
2. `mkdir -p userspace/flavors/{default,vpp}/vyos-1x/patches` (empty, with `.gitkeep`).
3. Update `bin/ci-setup-vyos1x.sh` to ALSO walk `userspace/flavors/${FLAVOR}/vyos-1x/patches/*.patch` (in addition to `userspace/common/vyos-1x/patches/`). Order: common first, then flavor-specific (matches kernel patch ordering).
4. Update `bin/ci-setup-vyos-build.sh` to ALSO copy hooks from `userspace/flavors/${FLAVOR}/live-build/hooks/`.
5. Add `bin/ci-stage-userspace.sh` as a thin wrapper analogous to `bin/ci-stage-kernel.sh` if the staging logic gets non-trivial.
6. No content moves yet — placeholders only. Existing builds unchanged.

### Phase 4 — ASK consolidation (largest, do last)
1. Audit content overlap:
   ```bash
   diff -rq ASK/cmm data/ask-userspace/cmm 2>&1 | head -30
   diff -rq ASK/dpa_app data/ask-userspace/dpa_app 2>&1 | head -30
   diff -rq ASK/fci/lib data/ask-userspace/fci 2>&1 | head -30
   diff -rq ask-userspace/fmc data/ask-userspace/fmc 2>&1 | head -30
   diff -rq ask-userspace/fmlib data/ask-userspace/fmlib 2>&1 | head -30
   ```
2. For each `<pkg>` that is consumed by CI (verify with `grep -r '<pkg>' bin/ci-*.sh`), `git mv` to `userspace/flavors/ask/packages/<pkg>/`.
3. Move `data/ask-userspace/<pkg>/*.patch` → `userspace/flavors/ask/packages/<pkg>/patches/`.
4. Move `data/hooks/97-ask-userspace.chroot` → `userspace/flavors/ask/live-build/hooks/`.
5. Move `patches/libcli/` → `userspace/flavors/ask/packages/libcli/patches/`. Delete top-level `patches/`.
6. Update every `bin/ci-build-{fmc,fmlib,ask-userspace}.sh` reference to `data/ask-userspace/<pkg>/patches/*.patch`.
7. Dispatch `flavor=ask` build; verify ASK ISO assembles, `dpa_app` boots without SIGSEGV (the FMD-UAPI rebuild path in `bin/ci-build-packages.sh` Stage `ASK-consume mode` must continue to work).
8. After ASK build verified clean, **delete** the legacy `ASK/` and `ask-userspace/` top-level trees in a follow-up commit. (Two commits because if there's a regression we keep the trees as recovery material for one extra step.)

### Phase 5 — `data/` removal (after phases 1–4 complete and stable)
1. `find data -type f` should be empty.
2. `rmdir data/dtb data/hooks data/scripts data/systemd data/mok data/ask-userspace data` (whatever's still there — should all be empty by now).
3. `git rm -r data/` if Git still tracks empty dirs.
4. Update `.gitignore` and `AGENTS.md` to reflect the new layout. Update the "Files" table in `AGENTS.md` (~50 entries reference `data/`).

### Phase 6 — kernel-side path-cleanup polish (optional)
1. Today's `kernel/common/patches/{board,fixes,vyos}/` is fine but board/ is a slight asymmetry vs the new `board/` top-level. Consider whether `kernel/common/patches/board/` should move to `board/kernel-patches/`. Probably not — they're patches against the kernel, so they belong with kernel patches even though they describe board behavior.
2. `kernel/common/files/` (fsl_fmd_shim.c, lp5812 driver source) is fine as-is. Could move to `kernel/common/sources/` to match `kernel/flavors/ask/sdk-sources/` naming, but that's pure cosmetic.

---

## Affected scripts and config files

Hard list of every file that needs path updates. Ordered by phase. Each entry annotated with the substitution.

### Phase 1 (board/)
- `bin/ci-build-iso.sh` — `data/dtb/mono-gw.dtb` references → `board/dtb/`
- `bin/ci-compile-mono-dtb.sh` — `data/dtb/mono-gateway-dk*.dts` → `board/dtb/`
- `bin/ci-setup-vyos-build.sh` — `data/scripts/`, `data/systemd/`, `data/mok/` references
- `bin/ci-build-packages.sh` — DTB compile uses `$GITHUB_WORKSPACE/data/dtb/sdk-dtsi`
- `kernel/common/scripts/stage-kernel.sh` — if it references `data/dtb/sdk-dtsi`
- `data/hooks/*.chroot` — if any hook references sibling files (most don't)
- `AGENTS.md` — ~30 file-table entries
- `INSTALL.md` — `data/dtb/`, `data/scripts/vyos-postinstall` references
- `plans/*.md` — many references

### Phase 2 (userspace/common/)
- `bin/ci-setup-vyos1x.sh` — `data/vyos-1x-*.patch` glob (line 26 + heredoc line ~96)
- `bin/ci-setup-vyos-build.sh` — `data/vyos-build-*.patch` and `data/hooks/*.chroot` and `data/reftree.cache`
- `bin/ci-build-packages.sh` — vyos-1x cache key derivation (~line 66)
- `kernel/common/scripts/patch-health.sh` — userspace patch validation (if any)

### Phase 4 (ASK)
- `bin/ci-build-fmc.sh` — `data/ask-userspace/fmc/01-mono-ask-extensions.patch`
- `bin/ci-build-fmlib.sh` — `data/ask-userspace/fmlib/{01,02}-*.patch`
- `bin/ci-build-ask-userspace.sh` — references to `ASK/`, `ask-userspace/`, `data/ask-userspace/`
- `bin/ci-verify-ask-iso.sh` — content checks
- `data/hooks/97-ask-userspace.chroot` — if the hook itself references `data/ask-userspace/` paths

---

## Risks and mitigations

| Risk | Probability | Mitigation |
|---|---|---|
| Cache key invalidation in `ci-build-packages.sh` slows the next build by ~6 min | Certain | One-time cost. Add a comment explaining the expected miss. |
| A `git mv` is missed and a build script reads from a now-empty path | Medium | Inventory step in Phase 0 catches this. Each phase has a mandatory `multi-flavor-release.yml` dispatch as the gate before merging to `main`. |
| AGENTS.md "Files" table goes stale | High | Update it as part of each phase's commit. Include a "files moved this phase" section in commit message so readers can find old paths. |
| Documentation links in `plans/*.md` and `INSTALL.md` go stale | Medium | `grep -rln 'data/' plans/ *.md` after each phase, fix in the same commit. |
| Pull requests against `main` from contributors get rebase conflicts | Low (this is a single-maintainer repo today) | Land Phase 1 in one PR before announcing; subsequent phases are smaller diffs. |
| ASK build regresses because `git mv` of source trees breaks build paths | Medium | Phase 4 has its own dispatch gate. If it fails, revert the phase 4 commit and inspect. The legacy `ASK/` and `ask-userspace/` trees aren't deleted until Phase 4 is verified. |
| The `git mv` history-preservation breaks because Git's rename detection has a 50% similarity threshold and we're moving content that gets edited at the same time | Medium | Do moves and edits in **separate commits** within each phase. `git mv` first (pure rename, 100% similarity), then `git commit -m 'mv' && edit && git commit -m 'edit'`. |

---

## What to NOT do

- **Don't flip the kernel side to flavor-first** to match a user-proposed `<flavor>/kernel/` layout. The kernel side is already correct; the userspace side is what's broken. Fix the broken thing.
- **Don't add a `flavor=common` virtual selector** — `common/` is not a flavor, it's the always-applied baseline. Conflating them confuses everything downstream (`patch-health.sh --flavor common` is meaningless; what would it validate?).
- **Don't move `bin/` to `ci/` in the same PR as the layout refactor.** Pure cosmetic, and it would balloon the diff to every workflow YAML. Defer to a separate small PR if at all.
- **Don't delete `ASK/` and `ask-userspace/` in the same commit as the move.** Wait one Phase to verify Phase 4 builds clean, then delete.
- **Don't introduce a new top-level `patches/`.** The orphan `patches/libcli/` goes away to its rightful home.

---

## Acceptance criteria

After all phases land:

1. `find . -maxdepth 1 -type d -name 'data' -o -name 'ASK' -o -name 'ask-userspace' -o -name 'patches' | wc -l` returns `0`.
2. `find . -maxdepth 1 -type d` lists exactly: `kernel`, `userspace`, `board`, `bin` (or `ci`), `.github`, `plans`, `specs`, plus `.git`/`.gitattributes`/`.gitignore` housekeeping.
3. `bash kernel/common/scripts/patch-health.sh --source release --flavor default` passes.
4. Same for `--flavor vpp` and `--flavor ask`.
5. `gh workflow run` of `multi-flavor-release.yml` with `build_default=true build_vpp=true build_ask=true` produces three ISOs under one tag.
6. Field installs of all three flavors `add system image` from the new release without errors.

---

## Effort estimate

| Phase | LOC moved | LOC edited | Risk | Hours |
|---|---|---|---|---|
| 0 — inventory | 0 | 0 | ⬇ none | 0.5 |
| 1 — board/ | ~100 (renames) | ~80 (sed) | ⬇ low | 2 |
| 2 — userspace/common/ | ~25 patches + 4 hooks | ~30 (sed in 4 scripts) | ⬇ low | 1.5 |
| 3 — userspace/flavors/<X>/ placeholders | 0 | ~20 (script extension for two-bucket walk) | ⬇ low | 1 |
| 4 — ASK consolidation | ~200 files (3 trees collapse) | ~50 (patch ref updates) | ⬇ medium | 3 |
| 5 — data/ removal | 0 | ~50 (AGENTS.md + .md links) | ⬇ low | 1 |
| 6 (optional) — kernel polish | 0 | ~10 | ⬇ trivial | 0.5 |

Total: ~9 hours, 6 commits / 6 PRs (one per phase). All non-overlapping with the multi-flavor-release work — the two refactors can land independently in either order.

**Recommended sequencing relative to multi-flavor-release plan:**
- Phase 1 (board/) and Phase 2 (userspace/common/) can land BEFORE multi-flavor-release implementation. They're pure refactors that don't touch CI behavior, just paths.
- Phase 3 (userspace/flavors/) onwards is best done AFTER multi-flavor-release lands, because we want to validate the layout against actual multi-flavor batches.
- Phase 4 (ASK) can wait until ASK is being actively worked on again.