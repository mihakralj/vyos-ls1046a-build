# Multi-Flavor Release Plan: One Tag, Three ISOs — ⚠️ RETIRED 2026-06-14
**Version 1.0.0** · Status as of 2026-05-11 · 2026-06-09 · **RETIRED 2026-06-14** · HADS 1.0.0

---

## ⚠️ RETIRED (2026-06-14) — DO NOT IMPLEMENT

**[SPEC]**
The three-flavor (`default | ask | vpp`) build split this plan coordinates **was retired on 2026-06-14**. CI now produces a **single flavor-neutral ISO** (`vyos-<version>-LS1046A-arm64.iso`) and a **single `version.json` feed**; `version-{default,ask,vpp}.json` are kept as identical byte-for-byte aliases so fielded installs keep updating. Both dataplanes (VPP AF_XDP and, once it lands, `ask.ko`) ship in that one image, dormant until selected at runtime per `specs/dual-dataplane.md`. **The multi-flavor release machinery described below is historical** and is retained only for context — do **not** implement it.

---

## AI READING INSTRUCTION

Read `[SPEC]` and `[BUG]` blocks for authoritative facts.
Read `[NOTE]` only if additional context is needed.
`[?]` blocks are unverified — treat with lower confidence.

---

## 1. GOAL & STATUS

**[SPEC]**
- Goal: a single CI dispatch produces three ISOs — `default`, `vpp`, and (when ready) `ask` — that share one `build_version` tag, attach to one GitHub Release, and update three independent `version-<flavor>.json` feeds atomically.

**[NOTE]**
Status as of 2026-05-11: today the workflow is single-flavor — each `gh workflow run "VyOS LS1046A build (self-hosted)" -f flavor=X` produces one ISO with its own `YYYY.MM.DD-HHMM-rolling` tag and its own GitHub Release. Three flavors = three back-to-back dispatches = three tags, three Releases, three `version-*.json` commits. That is wrong; the flavors should be coalesced.

---

## 2. CONSTRAINTS TO RESPECT

**[SPEC]**
Non-negotiable per `AGENTS.md` and live experience — any plan that breaks one is rejected:
1. One shared self-hosted runner. The Cobalt 100 ARM64 VM has a single registered runner (`vm-runner-2`, label `ARM64`). Two flavor builds cannot run in parallel — their `vyos-build/` trees, `kernel/` extractions, and `RUNNER_TOOL_CACHE` paths collide. Builds MUST be serial on that runner.
2. Idle-deallocate daemon owns VM lifecycle. CI NEVER calls `az vm deallocate` (re-introduces the 2026-05-06 race). The 10-min idle timer powers off after the last job.
3. `auto-build.yml` is reusable-only — `workflow_call:` only, no `workflow_dispatch:`. Don't add `workflow_dispatch` (burns Actions minutes; AGENTS.md hard rule).
4. Per-flavor feed JSON files (`version-default.json`, `version-ask.json`, `version-vpp.json`, plus the legacy `version.json` alias of default) are CI-managed; each build writes only its own feed. Coalesce into one commit, not three back-to-back `[skip ci]` commits.
5. Tag and ISO naming already encode flavor. ISO filename is `vyos-<version>-LS1046A-<flavor>-arm64.iso`; three flavors at the same `<version>` produce three distinct filenames — no collision in one Release.
6. Don't push `lts-6.6-ls1046a` and `kernel-*` tag in the same `git push` (AGENTS.md) — informs the publish-step ordering.

---

## 3. HIGH-LEVEL DESIGN

**[SPEC]**
Add a third workflow `multi-flavor-release.yml` (dispatchable) that:
1. Computes ONE `build_version` (timestamp `YYYY.MM.DD-HHMM-rolling`) up front in a hosted-runner setup job.
2. Starts the Azure VM once.
3. Calls `auto-build.yml` (reusable) three times sequentially — one per flavor — passing the same `build_version`. Each call produces its own ISO + `.minisig` artifact.
4. Skips per-flavor publishing inside `auto-build.yml` for batch runs (new input `skip_publish: true`).
5. After all builds succeed, runs ONE final hosted-runner publish job that downloads all ISO artifacts, writes all `version-<flavor>.json` (+ legacy `version.json` mirror) in a single commit, tags `${build_version}` once, and publishes ONE GitHub Release with all six files (3× ISO + 3× `.minisig`).

**[SPEC]**
- `self-hosted-build.yml` (single-flavor) stays as-is for ad-hoc per-flavor rebuilds and dev iteration.

---

## 4. WHY SERIAL-NOT-MATRIX ON THE SAME VM

**[NOTE]**
A `strategy.matrix` with `max-parallel: 1` is wrong here: each matrix leg re-checks-out and re-runs `bin/ci-set-version.sh`, re-deriving three different timestamps unless pinned via input; the matrix loses a shared early-exit gate on `build_version`; and sequencing via `needs:` in three explicit jobs is more readable and gives individual job names (`build_default`, `build_vpp`, `build_ask`) without `(matrix)` suffixes.

**[SPEC]**
- Use three explicit `needs:`-chained reusable-workflow calls with one shared `build_version`, computed once.

---

## 5. CONCRETE CHANGES REQUIRED

### 5.1 New file `.github/workflows/multi-flavor-release.yml`

**[SPEC]**
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

**[SPEC]**
- Key sequencing: `build_vpp` `needs: build_default`, `build_ask` `needs: build_vpp` — guarantees serial execution on the shared runner without a `concurrency:` hack.
- If a leg fails, later legs are skipped (default `needs:` semantics) but `publish` still runs (`if: always() && …`) and publishes whatever succeeded — partial release is better than no release.

### 5.2 Add `skip_publish` input to `auto-build.yml`

**[SPEC]**
- Add `skip_publish: { type: boolean, default: false }` to `workflow_call.inputs`.
- Gate the entire publish block (release notes, per-flavor feed write, git auto-commit, GH Release publish, delete-older-releases) on `if: ${{ !inputs.skip_publish }}`.
- Keep the artifact upload (`actions/upload-artifact@v4` for ISO + minisig) UNCONDITIONAL — multi-flavor publish needs to download them.
- Outputs `build_version`, `image_iso`, `image_name`, `flavor` stay populated regardless of `skip_publish`.
- `self-hosted-build.yml` keeps current end-to-end behavior (`skip_publish` defaults to `false`).

### 5.3 New helper `bin/ci-publish-multi-flavor.sh`

**[SPEC]**
Owns "write all three feeds + compose release notes + assemble file list". Pseudocode:
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

**[SPEC]**
- Snapshot the previous feeds (for the changelog window) right after `actions/checkout@v5` and before the script:
```yaml
- name: snapshot previous feeds
  run: cp version-default.json version-default.json.bak; cp version-vpp.json version-vpp.json.bak || true; cp version-ask.json version-ask.json.bak || true
```

### 5.4 Extract `start-vm` into reusable `_start-vm.yml`

**[SPEC]**
- New file `.github/workflows/_start-vm.yml` with `on: workflow_call:` containing the `start-vm` job verbatim.
- `self-hosted-build.yml`'s `start-vm` becomes `uses: ./.github/workflows/_start-vm.yml`; `multi-flavor-release.yml` references the same.

**[NOTE]**
DRY win, no behavior change. The leading underscore is convention for "reusable building block, don't dispatch directly" — no GitHub-enforced semantics, just a visual flag.

### 5.5 Optional: `flavor` argument to `bin/ci-set-version.sh` propagation

**[SPEC]**
- `ci-set-version.sh` reads `FEED="version-${FLAVOR}.json"` to derive `PREVIOUS_SUCCESS_BUILD_TIMESTAMP`. In the multi-flavor path that variable is computed in `ci-publish-multi-flavor.sh`, so per-flavor `auto-build.yml` runs with `skip_publish: true` compute it but it isn't used downstream. Leave as-is — no change needed.

---

## 6. PICKING WHICH FLAVORS TO BUILD

**[SPEC]**
Three independent boolean checkboxes in the "Run workflow" dropdown:

| Checkbox | Default | Meaning |
|---|---|---|
| `build_default` | ✅ checked | Build default flavor (mainline DPAA, kernel 6.18.x) |
| `build_vpp` | ✅ checked | Build vpp flavor (AF_XDP/VPP) |
| `build_ask` | ⬜ unchecked | Build ask flavor (NXP SDK + ASK fast-path) — keep OFF until ASK is production-ready |

**[SPEC]**
Any subset of the seven non-empty combinations is valid:

| Operator selection | What runs | What's published |
|---|---|---|
| `default` only | `build_default` → `publish` | 1 ISO, `version-default.json` + `version.json` updated |
| `default + vpp` (CURRENT DEFAULT) | `build_default` → `build_vpp` → `publish` | 2 ISOs, `version-default.json`, `version.json`, `version-vpp.json` updated |
| `default + ask` | `build_default` → `build_ask` → `publish` | 2 ISOs, `version-default.json`, `version.json`, `version-ask.json` updated |
| `vpp + ask` | `build_vpp` → `build_ask` → `publish` | 2 ISOs, `version-vpp.json` + `version-ask.json` updated; `version-default.json` and legacy `version.json` **untouched** |
| `vpp` only | `build_vpp` → `publish` | 1 ISO, only `version-vpp.json` updated |
| `ask` only | `build_ask` → `publish` | 1 ISO, only `version-ask.json` updated |
| `default + vpp + ask` | all three → `publish` | 3 ISOs, all three feeds + legacy alias updated |
| (none) | `setup` job validates and **fails fast** | nothing |

**[SPEC]**
Three enforcement layers, smallest blast radius first:
1. Workflow input checkboxes — defaults (`true`, `true`, `false`) match the most common dispatch.
2. Per-job `if:` gates — each `build_<flavor>` carries `if: always() && inputs.build_<flavor> && needs.start-vm.result == 'success'`. An unchecked box makes the job `skipped` (no runner time, no artifacts, not a failure). `always() && ...` is required so a skipped upstream doesn't propagate-skip a checked downstream.
3. Per-flavor `auto-build.yml` gates already in the source tree — e.g. `Consume prebuilt ASK kernel` is `if: env.FLAVOR == 'ask' || env.FLAVOR == ''`; `bin/ci-build-packages.sh` ASK userspace block is `if [ "${FLAVOR:-default}" = "ask" ]`. Belt-and-braces.

**[SPEC]**
`publish` discriminates skipped vs failed via tri-state branching (skipped flavors are silent in release notes):
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

**[SPEC]**
- Feed-write side effect (correct): the script walks `artifacts/*/` and writes feed JSON only for flavors that produced an ISO. A skipped job produces no artifact, so its `version-<flavor>.json` is not written and existing field installs keep their previous release — no spurious "update available" notifications.
- Legacy `version.json` alias is mirrored from `version-default.json` only when `build_default` was checked AND succeeded; a `vpp + ask`-only dispatch leaves `version.json` untouched (pre-flavor-split installs continue tracking the previous default build).
- Re-enabling ASK by default is a one-line edit: `build_ask` `default: false` → `default: true` once ASK is production-ready (no script/job-graph changes).
- Empty-batch dispatch (all three unchecked) is rejected by the `setup` `validate` step with `::error::At least one of build_default / build_vpp / build_ask must be checked.` and a non-zero exit, preventing a wasted runner allocation.
- Single-flavor ad-hoc builds remain available via `self-hosted-build.yml`.

---

## 7. ATOMICITY GUARANTEES

**[SPEC]**
- Single tag: `BUILD_VERSION` computed once in `setup`, passed to all three `auto-build.yml` invocations; each `ci-set-version.sh` sees `INPUT_BUILD_VERSION` and uses it verbatim.
- Single GitHub Release: only the final `publish` job calls `softprops/action-gh-release` with the shared tag; all three ISOs in `files:` upload as one transactional release write.
- Single commit to `main`: `git-auto-commit-action` in `publish` writes all three `version-*.json` in one commit (message + `tagging_message` = `${BUILD_VERSION}`).
- Per-flavor failure tolerance: `publish` uses `if: always() && (any leg succeeded)`. If `build_ask` fails, the release ships `default` + `vpp`, `version-ask.json` is untouched, and release notes mark ASK failed.

---

## 8. FAILURE MODES AND MITIGATIONS

**[SPEC]**

| Failure | Effect today | Effect after | Mitigation |
|---|---|---|---|
| `default` build fails | Release skipped | `vpp` and `ask` build, partial release published | release notes mark failure, operator re-dispatches `default` only via `self-hosted-build.yml` |
| `ask` build fails | (same as above) | `default` + `vpp` released, `version-ask.json` left at previous value | identical mitigation |
| All three fail | No release | No release, no commit, no tag | identical to today |
| VM goes idle mid-batch | Idle daemon waits for runner-idle 10 min — won't kill mid-build | Same (3 sequential builds keep runner busy continuously) | nothing to do |
| User dispatches `multi-flavor-release` while `self-hosted-build` is running | Both queue on the same runner; second waits | Same | GitHub serializes by runner availability |
| User dispatches `multi-flavor-release` twice | Second instance blocks waiting for runner | Add `concurrency: { group: ls1046a-runner, cancel-in-progress: false }` to both workflows so they queue cleanly without cancelling | one-line change |

---

## 9. MIGRATION PATH (INCREMENTAL, EACH STEP INDEPENDENTLY SHIPPABLE)

**[SPEC]**
1. Step A — extract `_start-vm.yml`. Mechanical refactor, no behavior change. Merge to `main`.
2. Step B — add `skip_publish` input to `auto-build.yml` (`default: false`) and gate the publish block. Merge to `main`. Single-flavor builds continue to publish as today.
3. Step C — write `bin/ci-publish-multi-flavor.sh` and unit-test locally with hand-rolled `artifacts/` directories. No CI change yet.
4. Step D — add `multi-flavor-release.yml`. First dispatch runs all three flavors back-to-back (~25–35 min total, 3× ~8–12 min warm). Verify one Release with 3 ISOs + 3 `.minisig` and a single `main` commit bumping all three feeds.
5. Step E (optional) — add `concurrency:` group to both dispatchable workflows.

**[NOTE]**
Steps A and B are pure refactors (either order). C is independent. D depends on A+B+C. E is optional polish.

---

## 10. WHAT I AM NOT PROPOSING

**[SPEC]**
- Don't run flavors in parallel (one runner, one VM — parallel collides on `vyos-build/`, kernel source extraction, build cache, `RUNNER_TOOL_CACHE`).
- Don't add `workflow_dispatch:` to `auto-build.yml` (AGENTS.md hard rule — burns Actions minutes).
- Don't manage VM start/stop from the new workflow (the idle daemon does this; just `uses: ./.github/workflows/_start-vm.yml`).
- Don't merge `self-hosted-build.yml` into `multi-flavor-release.yml` (keep single-flavor for ad-hoc rebuilds).
- Don't change the per-flavor `version-<flavor>.json` schema (field installs must keep parsing without code changes; the only delta is one commit instead of three).

---

## 11. OPEN QUESTIONS

**[?]**
1. Should the changelog window be per-flavor or unified? The multi-flavor unified release notes use the OLDEST of the three previous timestamps. Read: single window with the oldest cutoff is simpler and avoids hiding commits.
2. Tag prefix for multi-flavor releases? Today tags are bare `YYYY.MM.DD-HHMM-rolling`; could prefix `release-`. Probably not needed — the release body lists included flavors.
3. Should `default` always be in the batch? Operator can omit it; a `vpp,ask`-only dispatch leaves the legacy `version.json` alias untouched (correct — default wasn't rebuilt). Document in the input description.
4. Cache sharing between flavor builds in a batch? The vyos-1x .deb cache (`${RUNNER_TOOL_CACHE}/vyos-1x-cache/`) is keyed on `vyos-1x_<UPSTREAM_SHA>_<PATCH_HASH>`; all three flavors apply the same `data/vyos-1x-*.patch` set, so the cache hits across all three (~6 min saved per flavor after the first). Already automatic.

---

## 12. EFFORT ESTIMATE

**[SPEC]**

| Step | LOC | Risk |
|---|---|---|
| A. Extract `_start-vm.yml` | ~100 lines moved | ⬇ low (pure refactor) |
| B. Add `skip_publish` gate to `auto-build.yml` | ~10 lines | ⬇ low (additive, defaults preserve behavior) |
| C. `bin/ci-publish-multi-flavor.sh` | ~80 lines | ⬇ medium (new script, needs hand-test) |
| D. `multi-flavor-release.yml` | ~150 lines | ⬇ medium (new workflow, depends on A+B+C) |
| E. `concurrency:` group | ~3 lines × 2 files | ⬇ trivial |

- Total: ~350 lines of new/moved code. Two CI dispatches to validate (one default-only sanity check via single-flavor path; one full multi-flavor batch).