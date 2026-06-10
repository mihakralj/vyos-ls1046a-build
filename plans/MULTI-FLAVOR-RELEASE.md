# Multi-Flavor Release Plan: One Tag, Three ISOs

**Goal.** A single CI dispatch produces three ISOs — `default`, `vpp`, and (when ready) `ask` — that share **one `build_version` tag**, are attached to **one GitHub Release**, and update three independent `version-<flavor>.json` feeds atomically.

**Status as of 2026-05-11.** Today the workflow is single-flavor: each `gh workflow run "VyOS LS1046A build (self-hosted)" -f flavor=X` produces one ISO with its own auto-generated `YYYY.MM.DD-HHMM-rolling` tag and its own GitHub Release. Three flavors = three back-to-back dispatches = three different tags, three Releases, three commits to `main` bumping `version-*.json`. That's wrong; we want them coalesced.

---

## Constraints to respect

These are non-negotiable per `AGENTS.md` and live experience — any plan that breaks one is rejected.

1. **One shared self-hosted runner.** The Cobalt 100 ARM64 VM has a single registered runner (`vm-runner-2`, label `ARM64`). Two flavor builds cannot run in parallel on the same VM — their `vyos-build/` working trees, `kernel/` source extractions, and `RUNNER_TOOL_CACHE` paths collide. Builds **must** be serial on that runner.
2. **Idle-deallocate daemon owns VM lifecycle.** CI **never** calls `az vm deallocate` (re-introduces 2026-05-06 race). The 10-min idle timer on the VM handles power-off after the last job finishes.
3. **`auto-build.yml` is reusable-only.** It has `workflow_call:` only, no `workflow_dispatch:`. Don't add `workflow_dispatch` to "test on hosted runners" — burns Actions minutes (AGENTS.md hard rule).
4. **Per-flavor feed JSON files** (`version-default.json`, `version-ask.json`, `version-vpp.json`, plus the legacy `version.json` alias of default) are CI-managed. Each build writes only its own feed. Three builds = three writes. Need to coalesce these into one commit, not three back-to-back commits with stacked `[skip ci]` messages.
5. **Tag and ISO naming already encode flavor.** ISO filename is `vyos-<version>-LS1046A-<flavor>-arm64.iso`. Three flavors at the same `<version>` produce three distinct filenames — no collision when uploaded to one Release.
6. **Don't push `lts-6.6-ls1046a` and `kernel-*` tag in the same `git push`** (AGENTS.md). Doesn't apply directly here but informs the "publish to Release" step ordering.

---

## High-level design

Add a **third workflow** `multi-flavor-release.yml` (dispatchable) that:

1. Computes ONE `build_version` (the timestamp `YYYY.MM.DD-HHMM-rolling`) up front in a hosted-runner setup job.
2. Starts the Azure VM once.
3. Calls `auto-build.yml` (reusable) **three times sequentially** — one per flavor — passing the **same `build_version`** to each. Each call produces its own ISO+`.minisig` artifact.
4. Skips per-flavor publishing inside `auto-build.yml` for runs that are part of a multi-flavor batch (new input `skip_publish: true`).
5. After all three builds succeed, runs ONE final hosted-runner publish job that:
   - downloads all three ISO artifacts,
   - writes all three `version-<flavor>.json` files (+ legacy `version.json` mirror) in a single commit,
   - tags `${build_version}` once,
   - publishes ONE GitHub Release with all six files (3× ISO + 3× `.minisig`).

`self-hosted-build.yml` (single-flavor) stays as-is for ad-hoc per-flavor rebuilds and dev iteration.

---

## Why serial-not-matrix on the same VM

A `strategy.matrix` with `max-parallel: 1` looks tempting but is wrong here:

- A matrix job runs each leg as an independent invocation of the reusable workflow. Each leg re-checks-out, re-runs `bin/ci-set-version.sh`, re-derives `build_version` — **three different timestamps** unless we pin via the input. We'd be relying on `inputs.build_version` propagation, which is fine, but…
- The matrix loses the ability to share an early-exit gate on the `build_version` value. If matrix leg 1 finishes at `2026.05.12-1200`, leg 2 starts at `2026.05.12-1207` and would auto-derive `2026.05.12-1207-rolling` if `build_version` weren't passed.
- Sequencing via `needs:` in three explicit jobs is more readable and gives us individual job names in the Actions UI (`build_default`, `build_vpp`, `build_ask`) without `(matrix)` suffixes.

So: three explicit `needs:`-chained reusable-workflow calls with one shared `build_version`, computed once.

---

## Concrete changes required

### 1. New file `.github/workflows/multi-flavor-release.yml`

```yaml
name: VyOS LS1046A multi-flavor release

on:
  workflow_dispatch:
    inputs:
      BUILD_BY:
        description: 'Builder identifier'
        default: ''
      build_version:
        description: 'Version (auto: YYYY.MM.DD-HHMM-rolling)'
        default: ''
      ASK_KERNEL_TAG:
        description: 'ASK kernel release tag (default: data/ask-kernel.pin)'
        default: ''
      build_default:
        description: 'Build default flavor (mainline DPAA, kernel 6.18.x)'
        type: boolean
        default: true
      build_vpp:
        description: 'Build vpp flavor (AF_XDP/VPP)'
        type: boolean
        default: true
      build_ask:
        description: 'Build ask flavor (NXP SDK + ASK fast-path) — keep OFF until ASK is production-ready'
        type: boolean
        default: false

jobs:
  setup:
    runs-on: ubuntu-latest
    outputs:
      build_version: ${{ steps.ver.outputs.build_version }}
    steps:
      - id: validate
        # Reject the no-op dispatch — three unchecked boxes would skip every
        # build_* job AND the publish job (no leg succeeded), wasting one
        # hosted-runner minute and producing nothing. Fail fast instead.
        run: |
          if [ "${{ inputs.build_default }}" != "true" ] \
          && [ "${{ inputs.build_vpp     }}" != "true" ] \
          && [ "${{ inputs.build_ask     }}" != "true" ]; then
            echo "::error::At least one of build_default / build_vpp / build_ask must be checked."
            exit 1
          fi
      - id: ver
        run: |
          if [ -n "${{ inputs.build_version }}" ]; then
            echo "build_version=${{ inputs.build_version }}" >> "$GITHUB_OUTPUT"
          else
            echo "build_version=$(date -u +%Y.%m.%d-%H%M)-rolling" >> "$GITHUB_OUTPUT"
          fi

  start-vm:
    needs: setup
    uses: ./.github/workflows/_start-vm.yml      # extracted from self-hosted-build.yml (see step 4)

  build_default:
    needs: [setup, start-vm]
    if: inputs.build_default
    uses: ./.github/workflows/auto-build.yml
    permissions: { contents: write }
    with:
      runs_on: ARM64
      flavor: default
      build_version: ${{ needs.setup.outputs.build_version }}
      BUILD_BY: ${{ inputs.BUILD_BY }}
      ASK_KERNEL_TAG: ${{ inputs.ASK_KERNEL_TAG }}
      skip_publish: true                         # NEW input — see step 2
    secrets:
      MOK_KEY: ${{ secrets.MOK_KEY }}
      MINISIGN_PRIVATE_KEY: ${{ secrets.MINISIGN_PRIVATE_KEY }}

  build_vpp:
    # `needs: build_default` enforces serial execution on the shared runner.
    # `if: always() && inputs.build_vpp` means: run if vpp is requested,
    # regardless of whether build_default succeeded, failed, or was skipped.
    # Without `always()`, a skipped or failed build_default would propagate
    # and skip build_vpp too — which would defeat "build vpp only" dispatches.
    needs: [setup, start-vm, build_default]
    if: always() && inputs.build_vpp && needs.start-vm.result == 'success'
    uses: ./.github/workflows/auto-build.yml
    # … same structure, flavor: vpp …

  build_ask:
    needs: [setup, start-vm, build_vpp]
    if: always() && inputs.build_ask && needs.start-vm.result == 'success'
    uses: ./.github/workflows/auto-build.yml
    # … same structure, flavor: ask …

  publish:
    needs: [setup, build_default, build_vpp, build_ask]
    if: always() && (needs.build_default.result == 'success' || needs.build_vpp.result == 'success' || needs.build_ask.result == 'success')
    runs-on: ubuntu-latest
    permissions: { contents: write }
    steps:
      - uses: actions/checkout@v5
      - uses: actions/download-artifact@v8
        with: { path: artifacts }
      - name: Compose multi-flavor release
        run: bin/ci-publish-multi-flavor.sh    # NEW script — see step 3
        env:
          BUILD_VERSION: ${{ needs.setup.outputs.build_version }}
          DEFAULT_RESULT: ${{ needs.build_default.result }}
          VPP_RESULT: ${{ needs.build_vpp.result }}
          ASK_RESULT: ${{ needs.build_ask.result }}
      - uses: stefanzweifel/git-auto-commit-action@v6
        with:
          file_pattern: 'version-*.json version.json'
          commit_message: ${{ needs.setup.outputs.build_version }}
          tagging_message: ${{ needs.setup.outputs.build_version }}
      - uses: softprops/action-gh-release@v2
        with:
          tag_name: ${{ needs.setup.outputs.build_version }}
          body_path: /tmp/release_notes.md
          fail_on_unmatched_files: true
          files: |
            artifacts/**/*.iso
            artifacts/**/*.iso.minisig
      - uses: dev-drprasad/delete-older-releases@v0.3.4
        with: { keep_latest: 10, delete_tags: true }
        env: { GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }} }
```

Key sequencing detail: `build_vpp` `needs: build_default`, `build_ask` `needs: build_vpp`. That guarantees serial execution on the shared runner without a `concurrency:` group hack. If a leg fails, the legs after it are skipped (default `needs:` semantics) but `publish` still runs (`if: always() && …`) and publishes whatever did succeed — partial release is better than no release.

### 2. Add `skip_publish` input to `auto-build.yml`

In `.github/workflows/auto-build.yml`:

- Add `skip_publish: { type: boolean, default: false }` to `workflow_call.inputs`.
- Gate the entire publish block (currently lines ~485–597 — release notes generation, per-flavor feed write, git auto-commit, GH Release publish, delete-older-releases) on `if: ${{ !inputs.skip_publish }}`.
- Keep the artifact upload (`actions/upload-artifact@v4` for the ISO + minisig) UNCONDITIONAL — multi-flavor publish needs to download them.
- Outputs `build_version`, `image_iso`, `image_name`, `flavor` stay populated regardless of `skip_publish` so the caller can wire them up.

This way `self-hosted-build.yml` (single-flavor) keeps its current end-to-end behavior — `skip_publish` defaults to `false`, publish runs, one release per dispatch, exactly as today.

### 3. New helper `bin/ci-publish-multi-flavor.sh`

Owns the "write all three feeds + compose release notes + assemble file list" logic. Pseudocode:

```bash
#!/bin/bash
set -ex
: "${BUILD_VERSION:?}"

# Walk artifacts/ — exactly one subdir per built flavor, named per the
# image_name output of auto-build.yml.
for art in artifacts/*/; do
    iso=$(find "$art" -maxdepth 1 -name '*.iso' -printf '%f\n' | head -1)
    [ -z "$iso" ] && continue
    # Extract flavor from filename: vyos-<ver>-LS1046A-<flavor>-arm64.iso
    flavor=$(echo "$iso" | sed -E 's/.*LS1046A-([^-]+)-arm64\.iso/\1/')
    [ -z "$flavor" ] && { echo "WARN: cannot derive flavor from $iso"; continue; }

    # Mirror the per-flavor feed write that auto-build.yml's gated publish
    # step normally does. Fields match exactly what install/upgrade reads.
    URL="https://github.com/${GITHUB_REPOSITORY}/releases/download/${BUILD_VERSION}/${iso}"
    cat > "version-${flavor}.json" <<EOF
[
  {
    "url": "${URL}",
    "version": "${BUILD_VERSION}",
    "timestamp": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  }
]
EOF
    [ "$flavor" = "default" ] && cp "version-${flavor}.json" version.json
done

# Generate combined release notes covering commits since the OLDEST of the
# three previous-success timestamps, so no commit is missed by any flavor.
# (Each flavor has independent history; pick the earliest to bound the
# changelog window for the joint release.)
PREV_DEFAULT=$(jq -r '.[0].timestamp // "1970-01-01T00:00:00Z"' version-default.json.bak 2>/dev/null || echo "1970-01-01T00:00:00Z")
PREV_VPP=$(jq -r '.[0].timestamp // "1970-01-01T00:00:00Z"' version-vpp.json.bak 2>/dev/null || echo "1970-01-01T00:00:00Z")
PREV_ASK=$(jq -r '.[0].timestamp // "1970-01-01T00:00:00Z"' version-ask.json.bak 2>/dev/null || echo "1970-01-01T00:00:00Z")
PREV_TS=$(printf '%s\n%s\n%s\n' "$PREV_DEFAULT" "$PREV_VPP" "$PREV_ASK" | sort | head -1)

cat > /tmp/release_notes.md <<EOF
## VyOS LS1046A multi-flavor release ${BUILD_VERSION}

Built flavors:
$( [ "$DEFAULT_RESULT" = "success" ] && echo "- ✅ default (mainline DPAA, kernel 6.18.x)" )
$( [ "$VPP_RESULT"     = "success" ] && echo "- ✅ vpp (AF_XDP/VPP)" )
$( [ "$ASK_RESULT"     = "success" ] && echo "- ✅ ask (NXP SDK + ASK fast-path, kernel 6.6.137-askN)" )
$( [ "$DEFAULT_RESULT" != "success" ] && echo "- ❌ default ($DEFAULT_RESULT)" )
$( [ "$VPP_RESULT"     != "success" ] && echo "- ❌ vpp ($VPP_RESULT)" )
$( [ "$ASK_RESULT"     != "success" ] && echo "- ❌ ask ($ASK_RESULT)" )

### Changes since previous successful builds
…   # use mikepenz/release-changelog-builder-action style or git log --since="$PREV_TS"
EOF
```

Notes on the per-flavor feed `.bak` files: before this script runs, the publish job needs to read the *previous* feed values to compute the changelog window. Easiest path is:

```yaml
- name: snapshot previous feeds
  run: cp version-default.json version-default.json.bak; cp version-vpp.json version-vpp.json.bak || true; cp version-ask.json version-ask.json.bak || true
```

…right after `actions/checkout@v5` and before `bin/ci-publish-multi-flavor.sh`.

### 4. Extract `start-vm` into reusable `_start-vm.yml`

Today `self-hosted-build.yml` has the start-vm job inlined. Refactor:

- New file `.github/workflows/_start-vm.yml` with `on: workflow_call:` containing the `start-vm` job verbatim.
- `self-hosted-build.yml`'s `start-vm` becomes `uses: ./.github/workflows/_start-vm.yml`.
- `multi-flavor-release.yml` references the same.

DRY win, no behavior change. The leading underscore in the filename is convention for "reusable building block, don't dispatch directly" — though there's no GitHub-enforced semantics, it visually flags it in the file list.

### 5. Optional: add `flavor` argument to `bin/ci-set-version.sh` propagation

Currently `ci-set-version.sh` reads `FEED="version-${FLAVOR}.json"` to derive `PREVIOUS_SUCCESS_BUILD_TIMESTAMP`. In the multi-flavor publish path that variable is computed differently (in `ci-publish-multi-flavor.sh`), so per-flavor `auto-build.yml` runs with `skip_publish: true` will compute it but it won't be used downstream. That's fine — leave as-is, no change needed.

---

## Picking which flavors to build

The operator picks the flavor combo for each dispatch via three independent **boolean checkboxes** in the GitHub Actions "Run workflow" dropdown:

| Checkbox | Default | Meaning |
|---|---|---|
| `build_default` | ✅ checked | Build default flavor (mainline DPAA, kernel 6.18.x) |
| `build_vpp` | ✅ checked | Build vpp flavor (AF_XDP/VPP) |
| `build_ask` | ⬜ unchecked | Build ask flavor (NXP SDK + ASK fast-path) — keep OFF until ASK is production-ready |

Any subset of the seven non-empty combinations is valid:

| Operator selection | What runs | What's published |
|---|---|---|
| `default` only | `build_default` → `publish` | 1 ISO, `version-default.json` + `version.json` updated |
| `default + vpp` (CURRENT DEFAULT) | `build_default` → `build_vpp` → `publish` | 2 ISOs, `version-default.json`, `version.json`, `version-vpp.json` updated |
| `default + ask` | `build_default` → `build_ask` → `publish` | 2 ISOs, `version-default.json`, `version.json`, `version-ask.json` updated |
| `vpp + ask` | `build_vpp` → `build_ask` → `publish` | 2 ISOs, `version-vpp.json` + `version-ask.json` updated; `version-default.json` and the legacy `version.json` alias **untouched** |
| `vpp` only | `build_vpp` → `publish` | 1 ISO, only `version-vpp.json` updated |
| `ask` only | `build_ask` → `publish` | 1 ISO, only `version-ask.json` updated |
| `default + vpp + ask` | all three → `publish` | 3 ISOs, all three feeds + legacy alias updated |
| (none) | `setup` job validates and **fails fast** | nothing |

Three layers enforce the selection, smallest blast radius first:

1. **Workflow input checkboxes.** The defaults (`true`, `true`, `false`) match the most common dispatch — "build default and vpp, leave ask alone". The operator overrides per-dispatch by checking/unchecking boxes in the GH Actions UI dropdown.

2. **Per-job `if:` gates.** Each `build_<flavor>` job carries `if: always() && inputs.build_<flavor> && needs.start-vm.result == 'success'`. A box left unchecked makes the matching `build_*` job evaluate to `false` and **skipped entirely** (status = `skipped`). Skipped jobs do not consume runner time, do not produce artifacts, and do not appear in `needs.build_<flavor>.result` as a failure. The `always() && ...` form is required so that, e.g., a skipped `build_default` does not propagate-skip a checked `build_vpp` — without `always()`, GH Actions' default `needs:` semantics would propagate the upstream `skipped` status.

3. **Per-flavor `auto-build.yml` gates** already in the source tree (committed pre-multi-flavor). Examples: the `Consume prebuilt ASK kernel` step is `if: env.FLAVOR == 'ask' || env.FLAVOR == ''`; the `Setup ASK fast-path kernel (SDK DPAA + hooks)` step has the same gate; `bin/ci-build-packages.sh`'s ASK userspace block is `if [ "${FLAVOR:-default}" = "ask" ]`. Belt-and-braces — even if a stray `flavor=ask` input slipped through, only ASK-flavor builds touch ASK code paths.

**How `publish` discriminates skipped vs failed.** A skipped `build_*` job reports `result == 'skipped'`, distinct from `success`/`failure`. `bin/ci-publish-multi-flavor.sh` uses tri-state branching so deliberately-omitted flavors are silent in the release notes:

```bash
# Only mention a flavor in release notes if it was actually attempted.
for f in default vpp ask; do
    var="$(echo "$f" | tr a-z A-Z)_RESULT"
    res="${!var}"
    case "$res" in
        success) echo "- ✅ $f" ;;
        skipped) ;;     # silent — operator deliberately omitted this flavor
        *)       echo "- ❌ $f ($res)" ;;   # failure / cancelled / etc.
    esac
done
```

**Feed-write side effect (correct).** `bin/ci-publish-multi-flavor.sh` walks `artifacts/*/` to discover which flavors were built and writes feed JSON only for those that produced an ISO. A skipped job produces no `actions/upload-artifact` output, so its `version-<flavor>.json` is not written, and existing installs of that flavor in the field continue to point at their previous successful release. **No spurious "update available" notifications** for users on a flavor that was deliberately omitted from the latest dispatch.

**Legacy `version.json` alias.** The legacy `version.json` is mirrored from `version-default.json` only when `build_default` was checked AND succeeded. If the operator selects `vpp + ask` only, `version.json` is left untouched — pre-flavor-split installs (whose `set system update-check url …/main/version.json` was baked in) continue to track the previous default-flavor build. This is the desired behavior — those installs are on the default flavor, which was deliberately not rebuilt.

**Re-enabling ASK by default** is a one-line edit: change `build_ask`'s `default: false` to `default: true` in `multi-flavor-release.yml` once ASK reaches production-ready status. No script changes, no job-graph changes — the job is already wired up and gated.

**Validation against the empty-batch dispatch.** The `setup` job's `validate` step rejects the all-three-boxes-unchecked dispatch with `::error::At least one of build_default / build_vpp / build_ask must be checked.` and exits non-zero, preventing a wasted runner allocation. Without this guard, the workflow would launch, skip every `build_*` job (all `if:` false), then skip `publish` (no leg succeeded) — visible in the Actions UI as a confusing all-skipped run.

**Single-flavor ad-hoc builds remain available** via `self-hosted-build.yml` for diagnostic/iteration runs (unchanged behavior — the multi-flavor coalescing only applies to dispatches of `multi-flavor-release.yml`).

---

## Atomicity guarantees

- **Single tag.** `BUILD_VERSION` is computed once in `setup`, passed to all three `auto-build.yml` invocations as `build_version` input. Each `ci-set-version.sh` sees `INPUT_BUILD_VERSION` set and uses it verbatim.
- **Single GitHub Release.** Only the final `publish` job calls `softprops/action-gh-release` with the shared tag. The three build jobs do NOT (because `skip_publish: true`). That action has `tag_name: ${BUILD_VERSION}` and creates-or-updates the release; with all three ISOs in `files:` they upload as one transactional release write.
- **Single commit to `main`.** The `git-auto-commit-action` in `publish` writes all three `version-*.json` files in one commit, message = `${BUILD_VERSION}`, plus `tagging_message` of the same. The three build jobs do NOT commit because `skip_publish: true` skips that block too.
- **Per-flavor failure tolerance.** `publish` uses `if: always() && (any leg succeeded)`. If `build_ask` fails, the release still goes out with `default` + `vpp` ISOs, `version-ask.json` is left untouched (so existing ASK installs keep pointing at the previous successful ASK release), and the release notes mark ASK as failed. Operator inspects logs and re-dispatches.

---

## Failure modes and mitigations

| Failure | Effect today | Effect after | Mitigation |
|---|---|---|---|
| `default` build fails | Release skipped | `vpp` and `ask` build, partial release published | release notes mark failure, operator re-dispatches `default` only via `self-hosted-build.yml` |
| `ask` build fails | (same as above) | `default` + `vpp` released, `version-ask.json` left at previous value | identical mitigation |
| All three fail | No release | No release, no commit, no tag | identical to today |
| VM goes idle mid-batch | Idle daemon waits for runner-idle 10 min — won't kill mid-build | Same (3 sequential builds keep runner busy continuously) | nothing to do |
| User dispatches `multi-flavor-release` while `self-hosted-build` is running | Both queue on the same runner; second waits | Same | GitHub serializes by runner availability |
| User dispatches `multi-flavor-release` twice | Second instance blocks waiting for runner | Add `concurrency: { group: ls1046a-runner, cancel-in-progress: false }` to both workflows so they queue cleanly without cancelling | one-line change |

---

## Migration path (incremental, each step independently shippable)

1. **Step A — extract `_start-vm.yml`.** Mechanical refactor, no behavior change. `self-hosted-build.yml` keeps working byte-identically. Merge to `main`.
2. **Step B — add `skip_publish` input to `auto-build.yml`** with `default: false` and gate the publish block. Merge to `main`. Single-flavor builds via `self-hosted-build.yml` continue to publish exactly as today (because they don't pass `skip_publish`).
3. **Step C — write `bin/ci-publish-multi-flavor.sh`** and unit-test it locally with hand-rolled `artifacts/` directories matching the artifact upload layout. No CI change yet.
4. **Step D — add `multi-flavor-release.yml`.** First dispatch runs all three flavors back-to-back; takes ~25–35 min total (3× ~8–12 min warm). Verify a single GitHub Release appears with 3 ISOs + 3 `.minisig` files and a single `main`-branch commit bumping all three feed JSONs.
5. **Step E (optional) — add `concurrency:` group** to both dispatchable workflows so they queue cleanly when both are triggered.

Steps A and B are pure refactors and can land in either order. C is independent. D depends on A+B+C. E is optional polish.

---

## What I am NOT proposing

- **Don't run flavors in parallel.** One runner, one VM. Parallel attempts collide on `vyos-build/` working tree, kernel source extraction paths, the in-memory build cache, and `RUNNER_TOOL_CACHE`. Serial is correct.
- **Don't add `workflow_dispatch:` to `auto-build.yml`.** AGENTS.md hard rule (re-introduces hosted-runner builds → burns Actions minutes).
- **Don't manage VM start/stop from the new workflow.** The idle daemon already does this correctly. Just `uses: ./.github/workflows/_start-vm.yml` and let the daemon deallocate after the last build finishes.
- **Don't merge `self-hosted-build.yml` into `multi-flavor-release.yml`.** Keep single-flavor as a separate dispatchable for ad-hoc rebuilds (e.g. "rebuild just `vpp` after a hot-fix"). Two clearly-named entry points, each does one thing well.
- **Don't change the per-flavor `version-<flavor>.json` schema.** Existing field installs on every flavor must keep parsing the feed without code changes. The only delta is "all three feeds get bumped in one commit instead of three serial commits".

---

## Open questions

1. **Should the changelog window be per-flavor or unified?** Current single-flavor publish reads `version-<flavor>.json` for `PREVIOUS_SUCCESS_BUILD_TIMESTAMP`. The multi-flavor unified release notes use the OLDEST of the three. Acceptable? Or generate three separate per-flavor changelog blocks under one Markdown header? My read: single window with the oldest cutoff is simpler and avoids accidentally hiding commits from a flavor whose previous build was newer.

2. **Tag prefix for multi-flavor releases?** Today tags are bare `YYYY.MM.DD-HHMM-rolling`. Could prefix multi-flavor with e.g. `release-` to distinguish from single-flavor ad-hoc rebuilds. Probably not needed — the release body already lists which flavors are included.

3. **Should `default` always be in the multi-flavor batch?** The `flavors` workflow input lets the operator omit it. If they pass `vpp,ask` only, the legacy `version.json` alias is NOT updated (only happens when `default` is among the built flavors). Existing default-flavor field installs would not get an update notification from a vpp-only release — which is correct (they're on `default`, that flavor wasn't rebuilt). Document this in the workflow input description.

4. **Cache sharing between flavor builds in the same batch?** The vyos-1x .deb cache (`${RUNNER_TOOL_CACHE}/vyos-1x-cache/`) is keyed on `vyos-1x_<UPSTREAM_SHA>_<PATCH_HASH>`. All three flavors apply the same `data/vyos-1x-*.patch` set (no flavor-specific vyos-1x patches today), so the cache hits across all three. ~6 min saved per flavor after the first one. Already automatic, no change needed.

---

## Effort estimate

| Step | LOC | Risk |
|---|---|---|
| A. Extract `_start-vm.yml` | ~100 lines moved | ⬇ low (pure refactor) |
| B. Add `skip_publish` gate to `auto-build.yml` | ~10 lines | ⬇ low (additive, defaults preserve behavior) |
| C. `bin/ci-publish-multi-flavor.sh` | ~80 lines | ⬇ medium (new script, needs hand-test) |
| D. `multi-flavor-release.yml` | ~150 lines | ⬇ medium (new workflow, depends on A+B+C) |
| E. `concurrency:` group | ~3 lines × 2 files | ⬇ trivial |

Total: ~350 lines of new/moved code. Two CI dispatches to validate (one default-only sanity check via single-flavor path; one full multi-flavor batch).