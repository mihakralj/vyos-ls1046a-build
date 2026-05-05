# AUDIT-accel-ppp

Verification audit of the **consumer-side** accel-ppp source patches
in this repository. The brief listed accel-ppp as Component B and
asked us to enumerate any local source modifications applied during
the build.

The audit was driven by the three discovery commands the brief
mandated, plus a read of `bin/ci-build-accel-ppp.sh` (the only
accel-ppp build helper in the tree).

No source files were modified.

## Findings summary

| Severity | Count |
|---|---|
| P0 | 0 |
| P1 | 1 |
| P2 | 1 |

Plus 0 optimization candidates (no consumer-side source to optimise).

## Inventory

### Discovery commands (brief-mandated)

```text
$ find /root/vyos-ls1046a-build -path '*/accel-ppp*' -name '*.patch' 2>/dev/null
(no output — zero matches)

$ find /root/vyos-ls1046a-build -name 'vyos-1x-*' -type d 2>/dev/null
(no output — zero matches)

$ grep -rl 'accel-ppp\|accel_ppp' /root/vyos-ls1046a-build/bin/ \
                                  /root/vyos-ls1046a-build/data/ \
                                  /root/vyos-ls1046a-build/ASK/patches/ 2>/dev/null
bin/ci-pick-packages.sh
bin/ci-setup-vyos1x.sh
bin/ci-build-packages.sh
bin/ci-build-accel-ppp.sh
```

Conclusion: **there are no accel-ppp source patches in this
repository.** No `*.patch` files anywhere under `accel-ppp*`, no
`vyos-1x-*` directories, and the only files mentioning accel-ppp at
all are CI scripts.

### `ASK/patches/` content (brief-flagged note)

`ASK/patches/` does contain two PPP-stack-adjacent patches, but
*neither targets accel-ppp*:

- `ASK/patches/ppp/01-nxp-ask-ifindex.patch` — modifies `ppp` (the
  Paul-Mackerras pppd, a separate project).
- `ASK/patches/rp-pppoe/01-nxp-ask-cmm-relay.patch` — modifies
  `rp-pppoe` (the Roaring Penguin client, also separate).

Per the brief these must be called out explicitly: **do not confuse
these with accel-ppp.** accel-ppp / accel-ppp-ng is a third,
unrelated daemon (`github.com/accel-ppp/accel-ppp-ng`) used by VyOS
for PPPoE/IPoE/L2TP server-side termination. The pppd and rp-pppoe
patches are NOT part of accel-ppp's codebase or build.

### Build script — `bin/ci-build-accel-ppp.sh`

The script (~150 lines) is the only place where accel-ppp source
appears in the build pipeline. Relevant excerpts:

- **Source:** clones upstream
  `https://github.com/accel-ppp/accel-ppp-ng.git`, pinned to commit
  `3e30d9b` (matching VyOS upstream `package.toml`).
- **Patch step (script lines 95–104):**
  ```bash
  PATCH_DIR="$WORKSPACE/vyos-build/scripts/package-build/linux-kernel/patches/accel-ppp-ng"
  if [ -d "$PATCH_DIR" ]; then
    cd "$ACCEL_SRC"
    for patch in "$PATCH_DIR"/*; do
      [ -f "$patch" ] || continue
      echo "I: Apply patch: $(basename "$patch")"
      patch -p1 < "$patch" || echo "WARNING: $(basename "$patch") failed"
    done
  fi
  ```
  This loop pulls patches from a *vyos-build* checkout sibling
  directory — not from this repository. If the sibling directory is
  absent (e.g. the script is invoked outside the canonical
  `$GITHUB_WORKSPACE` layout), zero patches are applied and the
  warning never fires (the `if [ -d ]` test silently skips).
- **Build:** cmake + make + cpack to produce
  `accel-ppp-ng_<ver>_arm64.deb` (daemon plus optional
  `ipoe.ko`/`vlan_mon.ko`). VPP plugin is unconditionally disabled —
  the script comment explains VPP source is not buildable on ARM64.
- **Daemon-only fallback:** if kernel-module build fails, the script
  reconfigures with `-DBUILD_IPOE_DRIVER=FALSE` and ships only the
  user-space daemon — silently dropping `ipoe.ko`/`vlan_mon.ko` from
  the deb. See P1.1 below.

### Patch enumeration

| Patch | Hunks | Notes |
|---|---|---|
| (none) | (n/a) | No accel-ppp patches exist in this repository. |

The reference table is intentionally empty — there is nothing to
cite by `patch:hunk` because no consumer-side source delta exists.

## P0 findings

None. The absence of patches is not a security defect on its own —
the upstream accel-ppp-ng@3e30d9b is the literal codebase that
ships.

## P1 findings

### P1.1 — Build can silently produce a daemon-only `.deb` with no kernel modules
- **File:** `bin/ci-build-accel-ppp.sh:118-133`
- **Snippet:**
  ```bash
  if ! make -j$(nproc) 2>&1; then
      echo "### make FAILED. … Retrying WITHOUT kernel modules (daemon-only)..."
      rm -rf "$ACCEL_SRC/build"
      mkdir -p "$ACCEL_SRC/build"
      cd "$ACCEL_SRC/build"
      cmake -DBUILD_IPOE_DRIVER=FALSE \
            -DBUILD_VLAN_MON_DRIVER=FALSE \
            ...
      make -j$(nproc) 2>&1 || { echo "ERROR: make daemon-only also failed"; exit 1; }
      echo "### Built daemon-only (no ipoe.ko/vlan_mon.ko kernel modules)"
  fi
  ```
- **Defect class:** silent feature regression in build output.
- **Trigger:** Any compile error in the IPoE or VLAN-mon kernel
  module sources (e.g. a kernel-API drift across LTS bumps) drops the
  fallback path. The resulting deb is *named identically* to a
  full-featured deb (`accel-ppp-ng_${VER}_arm64.deb`), but IPoE
  termination silently does not work on the device.
- **Recommendation:** (a) tag the daemon-only deb with a distinct
  filename (e.g. `…_arm64-daemonly.deb`); (b) emit a non-zero CI
  failure unless an explicit env-var (`ALLOW_ACCEL_PPP_DAEMON_ONLY=1`)
  is set; (c) record the fallback in the build manifest so the ISO
  packager refuses to ship if IPoE features are advertised.
- **Fix routing:** local change in `bin/ci-build-accel-ppp.sh`. No
  upstream component touched.

## P2 findings

### P2.1 — Patch directory in CI script points outside the repo and silently no-ops if absent
- **File:** `bin/ci-build-accel-ppp.sh:95-104` (see above).
- **Defect class:** D17 — silent error-path. The
  `if [ -d "$PATCH_DIR" ]` guard hides the situation where a future
  ASK-specific patch is forgotten.
- **Recommendation:** if patches are ever required, mirror the
  fmc/ppp/rp-pppoe pattern — keep them in
  `ASK/patches/accel-ppp/NN-…patch` under this repo and have the
  script apply *those*, then enforce a non-empty result. Currently
  zero patches are expected, so make that fact explicit:
  ```bash
  ASK_PATCH_DIR="$WORKSPACE/ASK/patches/accel-ppp"
  if [ -d "$ASK_PATCH_DIR" ]; then
      for p in "$ASK_PATCH_DIR"/*.patch; do
          [ -f "$p" ] || continue
          patch -p1 < "$p" || { echo "ERROR: $p failed"; exit 1; }
      done
  fi
  ```
  Note the `exit 1` on failure — currently the script only
  `WARNING`s.
- **Fix routing:** local CI script change.

## Optimization candidates

None. There is no consumer-side accel-ppp source in this repository
to optimise. Upstream accel-ppp-ng performance is out of scope for
this audit.

## Recommendations

1. **Confirm intent.** The brief asks us to flag this scenario:
   *"If no consumer accel-ppp source patches exist, document that
   explicitly and recommend confirming whether upstream accel-ppp
   covers ASK requirements (or capturing patches into
   `ASK/patches/accel-ppp/`)."*
   - The plain reading of the repo is that ASK currently relies on
     stock upstream `accel-ppp-ng@3e30d9b` plus VyOS's own patch set
     (pulled at build time from a *separate* `vyos-build` checkout).
   - Action item: a maintainer should confirm that none of the ASK
     features (NXP DPAA-aware PPPoE termination? IPoE on FMan
     interfaces?) require source-level changes that are not already
     covered by VyOS upstream. If they do, those changes should be
     captured as numbered patches under
     `ASK/patches/accel-ppp/NN-nxp-ask-…patch`, mirroring the
     existing `ASK/patches/ppp/` and `ASK/patches/rp-pppoe/`
     conventions.
2. **Do not fold pppd / rp-pppoe patches into "accel-ppp"
   discussions.** `ASK/patches/ppp/01-nxp-ask-ifindex.patch` and
   `ASK/patches/rp-pppoe/01-nxp-ask-cmm-relay.patch` modify
   *different* upstream projects; they do not belong in any
   accel-ppp audit or fix patch.
3. **Harden the CI build script** per P1.1 and P2.1 (clearer naming
   of the daemon-only fallback deb; explicit `ASK/patches/accel-ppp`
   directory with a fail-fast loop).
4. **Record the upstream pin.** The script hard-codes
   `ACCEL_COMMIT="3e30d9b"`. Add a note in `version.json` (or a
   sibling manifest) so reproducible-build audits can verify the
   exact upstream snapshot without scraping the script.
