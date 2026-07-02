# nxp-sdk ASK 1.x kernel patches

This branch (`nxp-sdk`) is the native NXP ASK 1.x vendor SDK port,
targeting kernel 6.12.49 (NXP `lf-6.12.49-2.2.0` + the 266-file SDK
source overlay at `kernel/flavors/ask/sdk-sources/`). This is a
**different effort** from the ASK2 mainline `ask.ko` rewrite on
`main`/`ask20` (kernel 6.18.x, which genuinely needs no flavor-specific
kernel patches — don't confuse the two). `kernel/common/scripts/
stage-kernel.sh` applies every `*.patch` file directly under this
directory (plus `patches/ask/` and `patches/fixes/` if present,
`-maxdepth 1`, sorted) for `--flavor ask`.

## Provenance (2026-07-02)

`005`/`006` are local, board-specific additions (populate MAC/proxy
children device nodes for the Mono Gateway DK) — not part of upstream
ASK.

`010` through `098` are a **direct, unmodified import** of the upstream
`we-are-mono/ASK` repo's `fix/security-hardening` branch
(`patches/kernel/010-*.patch` .. `098-*.patch`) — the same branch/commit
lineage the cdx.ko/fci.ko/auto_bridge.ko/cmm/dpa_app sources are pulled
from (see `bin/ci-build-ask-modules.sh` / `ci-build-ask-userspace.sh`),
so this patch set is guaranteed mutually consistent with the kernel
modules and userspace daemons. This is deliberate: it is the "minimal
changes to NXP reference implementation" path — use the reference
project's own patches verbatim rather than a locally hand-rolled
approximation.

This replaces what used to be here: a monolithic
`002-ask-kernel-hooks.patch` (itself originally a straight copy of
upstream's own OLDER monolithic `002-mono-gateway-ask-kernel_linux_6_12.
patch` from the `mt-6.12.y` branch, before upstream split it), plus
`004-export-dpaa-submit-symbol.patch` and a hand-curated `720-725`
series that reverse-engineered pieces of the same functionality the
monolith already had. That combination had two structural problems: (1)
002 duplicated content the `sdk-sources` overlay already carries baked
into the vendored driver source (the overlay was populated from a
"cvandesande" fork that already has ASK hooks merged in), and (2) 002
ALSO duplicated content the ad-hoc 720-725 series was independently
trying to add (same "comcerto fast-path" conntrack/bridge/xfrm hooks,
different field names, e.g. 002's `underlying_iif` vs 720-725's
`layerscape_underlying_iif`) — so nearly every patch after 002 in
sort order collided with it. Every one of those collisions had to be
manually diagnosed and patched around one CI failure at a time (see
`/memories/repo/ask1-dpa_app-nxp-sdk.md` for the blow-by-blow). Adopting
upstream's own split series eliminates the entire class of collisions
at the root instead of patching around it piecemeal.

## Exclude/skip mechanism (`kernel/common/scripts/stage-kernel.sh`)

Even the properly-split upstream series has two known, verified-safe
exceptions, handled via `ASK_PATCH_PATH_EXCLUDES` / `ASK_PATCH_SKIP_LIST_NATIVE`
in `stage-kernel.sh` (see the comments there for the full rationale):

- **`010-ask-fman-dpaa-ehash.patch`**: the sdk_fman/sdk_dpaa/fsl_qbman/
  fmd-uapi driver-hook hunks are excluded (overlay already has them
  baked in — a handful of files there have genuinely unresolved deep
  structural diffs even so, but the overlay's current state without
  them is the one already boot-tested + `ask-check` 41/41 clean on
  2026-06-27, so it's trusted over blind hunk surgery). The non-overlay
  hunks (mainline `net/Kconfig`, `net/core/*`, `include/linux/
  netdevice.h`/`skbuff.h`, `drivers/net/usb/usbnet.c`, uapi headers) are
  NOT excluded and apply cleanly.
  - **Known gap (found 2026-07-02 via `cdx.ko` modpost failure,
    `ERROR: modpost: "dpa_add_dummy_eth_hdr" ... undefined!`)**: patch
    010 adds a genuinely NEW function, `dpa_add_dummy_eth_hdr()`
    (`EXPORT_SYMBOL`'d), to `drivers/net/ethernet/freescale/sdk_dpaa/
    dpaa_eth_sg.c` — not present anywhere in the pre-010 overlay (unlike
    most of what's excluded there, which duplicates existing content).
    Its declaration lives in `include/linux/netdevice.h` (NOT excluded,
    applies cleanly), so the external `cdx.ko` OOT module (built by
    `bin/ci-build-ask-modules.sh` from a separate `we-are-mono/ASK`
    clone, branch `mt-6.12.y`) expects it to be exported from vmlinux,
    but the DEFINITION was silently dropped by this file-level exclude.
    Fixed by manually adding just this one function (verbatim from the
    patch) directly to the overlay's `dpaa_eth_sg.c`, right after
    `EXPORT_SYMBOL(dpaa_submit_outb_pkt_to_SEC)` — same placement as in
    the patch — rather than un-excluding the whole file (which has other
    unresolved structural conflicts, see above). If a FUTURE CI run hits
    another `modpost: "..." undefined!` error for a different symbol
    from the list of ~70 `EXPORT_SYMBOL`s this patch adds (see `grep
    -oE '^\+EXPORT_SYMBOL\(...\)' 010-*.patch`), the same treatment
    applies: add just that one function/export to the overlay directly,
    don't un-exclude the whole file.
- **`094-sdk-fman-dpaa-qbman-kasan-sanitize-off.patch`**: 12 of 14
  Makefile hunks apply cleanly; 2 are excluded (one path doesn't exist
  in our tree at all, one has a trivial context mismatch against our
  overlay's Makefile) — the same one-line `KASAN_SANITIZE := n` fix is
  applied directly to `kernel/flavors/ask/sdk-sources/drivers/staging/
  fsl_qbman/Makefile` instead.
- **`098-sdk_dpaa-bp-alloc-slab-build-skb.patch`**: skipped entirely —
  the overlay's `dpaa_eth_sg.c` already calls `slab_build_skb()` at the
  exact call site this patch targets.

Full validation before deploying: fetched pristine copies of every
mainline file all 17 upstream patches touch, merged with the actual
`sdk-sources` overlay, and replayed the exact `005 → 006 → 010 → 020 →
... → 098` application sequence (with the excludes above) via
`git apply --3way` in an isolated scratch repo — all 18 entries
succeeded end-to-end before this was pushed.

If you refresh this patch set from upstream again: diff the new
`fix/security-hardening` tree against what's here, re-run the same
scratch-repo validation, and re-check whether the three exceptions
above still apply (upstream may have refreshed their own SDK baseline
by then, changing what's redundant).

