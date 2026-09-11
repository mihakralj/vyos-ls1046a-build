# nxp-sdk ASK 1.x kernel patches

This branch (`nxp-sdk`) is the native NXP ASK 1.x vendor SDK port,
targeting kernel 6.12.49 (NXP `lf-6.12.49-2.2.0`). This is a
**different effort** from the ASK2 mainline `ask.ko` rewrite on
`main`/`ask20` (kernel 6.18.x, which genuinely needs no flavor-specific
kernel patches — don't confuse the two). `kernel/common/scripts/
stage-kernel.sh` applies every `*.patch` file directly under this
directory (plus `patches/ask/` and `patches/fixes/` if present,
`-maxdepth 1`, sorted) for `--flavor ask`.

## Provenance (2026-09-11)

`005`/`006` are local, board-specific additions (populate MAC/proxy
children device nodes for the Mono Gateway DK) — not part of upstream
ASK.

`007-sdk-dpaa-select-phylink` is a local, one-line Kconfig fix: NXP's
vendored `sdk_dpaa/Kconfig` `select`s PHYLIB but not PHYLINK, yet
`sdk_dpaa/mac.c` calls `phylink_interface_max_speed()` directly.
`PHYLINK` is a promptless `tristate` (no config-fragment text can set
it — its value is entirely computed from what `select`s it), so with
`FSL_SDK_DPAA_ETH=y` and no `select PHYLINK`, `CONFIG_PHYLINK` resolves
to `m` and the `vmlinux` link fails with `undefined reference to
'phylink_interface_max_speed'`. Verified via actual link failure and a
`make olddefconfig` reproduction, not assumption. Adding `select
PHYLINK` here (rather than to `kernel/flavors/ask/ask.config`, which
was tried first and does nothing for a promptless symbol) is the
correct fix location and keeps `sdk-sources/` itself exactly what
`sync-ask-kernel.sh` produces.

`010` through the top of the manifest are a **direct, unmodified import**
of the upstream `we-are-mono/ASK` repo at the commit pinned in
`kernel/flavors/ask/ask-version.env` — the same commit the
`cdx.ko`/`fci.ko`/`auto_bridge.ko`/`cmm`/`dpa_app` sources are pulled
from (see `bin/ci-build-ask-modules.sh` / `ci-build-ask-userspace.sh`),
so this patch set is guaranteed mutually consistent with the kernel
modules and userspace daemons.

Synced via `kernel/common/scripts/sync-ask-kernel.sh`, which mirrors
`we-are-mono/openwrt`'s `scripts/mono-sync-ask-kernel.sh`: ASK is the
single pin, and the NXP SDK overlay commit
(`kernel/flavors/ask/sdk-sources/`) is *read* from ASK's own
`pins/nxp-sdk-srcrev.inc` at that commit — never hand-picked — so the
vendored driver source and ASK's patch series can never disagree with
each other. Run `kernel/common/scripts/sync-ask-kernel.sh --check` to
verify the committed tree still matches the pin (drift guard); run it
without `--check` to re-sync after bumping `ASK_VERSION`.

## Why `sdk-sources/` is no longer hand-curated

Before 2026-09-11, `sdk-sources/` was a 228-file snapshot hand-copied
from a third-party "cvandesande" fork, with no recorded upstream commit
and no way to regenerate it. Because ASK's own patches were developed
against *pristine* NXP SDK source, not that snapshot, applying them
collided, and this directory carried a hand-maintained
`ASK_PATCH_PATH_EXCLUDES` map in `stage-kernel.sh` to route around the
collisions file-by-file. That approach caused one real bug: excluding
all of `dpaa_eth_sg.c` silently dropped a genuinely-new exported symbol
(`dpa_add_dummy_eth_hdr`) that `cdx.ko` needs, undetected until a
modpost link failure.

`sync-ask-kernel.sh` replaced the snapshot with fetch-at-pin-time
pristine NXP SDK source (same mechanism, paths, and reasoning as
openwrt's `mono-sync-ask-kernel.sh` — see that script's own comments).
Because the base is now exactly what ASK's patches were developed
against, there is no longer a class of "redundant hunk" to exclude —
`ASK_PATCH_PATH_EXCLUDES` and the old per-file skip list are gone from
`stage-kernel.sh` entirely.

## Patches skipped on this base, and why

Unlike openwrt (which builds on mainline `kernel.org` + a narrow
vendored NXP file set), this branch builds on the **full NXP LSDK
vendor tree** (`fetch-kernel-nxp.sh`'s `lf-6.12.49-2.2.0`). Two
consequences, verified via a from-scratch scratch-repo replay of the
full patch sequence against that real tree (`git init` + the same
Mergiraf `.gitattributes` `stage-kernel.sh` itself uses for `--3way`
fallback, 2026-09-11):

- **`stage-kernel.sh` dynamically skips any ASK patch that
  reverse-applies cleanly** — i.e. its content is already present
  natively in the NXP vendor tree (NXP backports many of the same
  fixes ASK independently carries for the mainline-based case). This
  replaces the old hand-maintained "fully redundant" skip list with a
  self-adapting check, so it can never silently go stale the way a
  static list would.
- **Two patches are skipped outright by name** in
  `stage-kernel.sh`'s `ASK_PATCH_SKIP_LIST_NATIVE`, because they are
  not applicable to this base at all (not merely redundant):
  - `110-sdk-mainline-build-compat.patch` — by its own commit message,
    exists only for consumers that build on mainline kernel.org rather
    than the NXP vendor tag. We are the vendor tag; most of the API/
    wiring gaps it fills already exist natively (verified: applying it
    fails outright against real NXP headers/Makefiles that already have
    what it adds). One exception found via actual link failure, not
    assumption: `sdk_dpaa/mac.c` calls `phylink_interface_max_speed()`
    directly, and NXP's vendored `sdk_dpaa/Kconfig` only `select`s
    PHYLIB, not PHYLINK — with `FSL_SDK_DPAA_ETH=y` (built in) and
    `CONFIG_PHYLINK=m` (module, the general default), that's an
    `undefined reference` at vmlinux link time regardless of base tree.
    Fixed via `007-sdk-dpaa-select-phylink.patch` (see above) rather
    than reviving 110's un-static+export approach.
  - `120-emc2305-dt-fan-control.patch` — an in-kernel DT-driven
    cooling-device rewrite of the EMC2305 fan controller, unrelated to
    ASK/DPAA1 networking and built against Linux v6.12.103 (54 point
    releases past our 6.12.49 base). Superseded by this repo's own
    working `board/scripts/fan-pid` userspace controller for the same
    hardware.
  - `130-thermal-linear-governor.patch` is kept (applies cleanly): a
    generic, opt-in thermal-core governor with no DT properties of its
    own, simply inert without 120's cooling-device wiring to select it.

Four patches needed real context refresh against the actual NXP LSDK
tree (not the pristine-mainline base ASK develops against) — content
identical in intent, hunks re-anchored: `020-ask-bridge-hooks`,
`030-ask-ipv4-ipv6-forwarding`, `040-ask-xfrm-ipsec-offload`,
`097-xfrm-trans-queue-force-dst-refcount`. One patch had a single
new-file hunk removed (`010-ask-fman-dpaa-ehash`'s
`include/linux/fsl_oh_port.h`, byte-identical to what the NXP LSDK
already ships). One patch (`095-sdk_fman-iomem-mem-ops`) was dropped
entirely — it targets a function (`AllocFEObjs`) that no longer exists
anywhere in current ASK's own patch series either (confirmed against
`we-are-mono/openwrt`'s copy at the same pin), so upstream itself has
since dropped it.

## Refreshing this patch set

Bump `ASK_VERSION` in `kernel/flavors/ask/ask-version.env`, run
`kernel/common/scripts/sync-ask-kernel.sh`, then re-run the scratch-repo
replay above against a freshly-fetched NXP kernel tree
(`kernel/common/scripts/fetch-kernel-nxp.sh`) before trusting the
result — the two skip-list entries and the four context-refreshed
patches may need re-checking if upstream ASK or the NXP LSDK baseline
has moved.
