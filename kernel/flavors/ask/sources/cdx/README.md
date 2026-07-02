# cdx-5.03.1 — Reference copy (NOT the build source)

This directory is a **reference copy** for code review and documentation.
It is **not** compiled by CI or any build script.

**cdx.ko is currently NOT built or shipped by this CI pipeline at all**,
though the gap is narrower than it first looks — see the correction
below. `bin/ci-build-ask-modules.sh` is designed to build it (plus
fci.ko and auto_bridge.ko) from the `we-are-mono/ASK` Git repository
(branch `mt-6.12.y`, pinned to commit
`a211ea865379362058c6656b9c448e4a7050e93c` as of 2026-07-02) — cloning
it to `$RUNNER_TOOL_CACHE/ask-clone-cache/ask-mt-6.12.y/` and building
`cdx/cdx_main.c` (and dependencies) with inline `sed`/Python patches
applied at build time — **but it never actually runs** in the current
`ASK_KERNEL_TAG` pipeline mode:

1. Its invocation in `bin/ci-build-packages.sh` is gated on `NXP_KSRC`
   being set, which requires a *fully built* kernel tree with
   `Module.symvers`. In `ASK_KERNEL_TAG` mode the only kernel artifact CI
   has is the downloaded `linux-headers-*.deb`.
2. Verified 2026-07-02 by downloading and extracting that exact
   `.deb` (`ask-kernel-6.12.49` release): it ships `Module.symvers`,
   `scripts/`, and `include/` — a normal Debian headers package — but
   **no `drivers/` directory at all**. cdx's Kbuild directly `include`s
   `drivers/net/ethernet/freescale/sdk_fman/ncsw_config.mk`, which this
   specific `.deb` doesn't have.
3. **Correction (same day, before this was pushed):** the *source* for
   `drivers/net/ethernet/freescale/sdk_fman/` is NOT actually missing —
   an earlier version of this note incorrectly claimed it wasn't
   published anywhere. It's freely available: `kernel/common/scripts/
   fetch-kernel-nxp.sh` clones `https://github.com/nxp-qoriq/linux.git`
   at tag `lf-6.12.49-2.2.0`, which has `sdk_fman/` natively (confirmed
   directly on GitHub), and `kernel/common/scripts/stage-kernel.sh
   --flavor ask` then overlays `kernel/flavors/ask/sdk-sources/` (266
   files) on top. This staging step (`bin/ci-stage-kernel.sh`, the
   "Stage kernel tree" workflow step) runs **unconditionally** on every
   CI run, `ASK_KERNEL_TAG` or not — so `work/linux-6.12.49` already has
   the full `sdk_fman` source tree every time.
4. The actual, narrower gap: that staged tree is never *compiled* in
   `ASK_KERNEL_TAG` mode (no `make`/`bindeb-pkg` runs against it, since
   the whole `linux-kernel` package build is what `ASK_KERNEL_TAG` skips
   to save ~20 minutes), so it has no `Module.symvers` of its own — and
   the downloaded headers `.deb`'s `Module.symvers` (which DOES match the
   shipped kernel) lives in a tree with no driver source. Building
   `cdx.ko`/`fci.ko`/`auto_bridge.ko` needs *both* the driver source
   (present in `work/linux-6.12.49`) *and* a `Module.symvers` that
   matches the actually-booted kernel (only in the headers `.deb`) —
   currently no single tree has both, and combining them (copying the
   headers-`.deb`'s `Module.symvers`/`certs`/compiled `scripts/sign-file`
   into `work/linux-6.12.49`) is untested and needs hardware validation
   before shipping, since a config/version mismatch between the two
   trees could produce modules that silently fail to load or (worse)
   load with mismatched symbol versions. This was NOT attempted in this
   session — flagging it as the concrete next step rather than guessing.

This is a real, non-trivial gap, but it's a **build-orchestration gap,
not a missing-source gap** — do not repeat the "source isn't published"
claim an earlier version of this note made. Relaxing
`ci-build-ask-modules.sh`'s `Module.symvers` check without actually
supplying a matching one would produce a build that looks like it might
work but can't actually compile or load correctly.

**Editing files here has zero effect on anything shipped**, now or once
the gap above is closed. To change cdx.ko once M2 is reachable again,
either:

1. Modify the inline `sed` commands in `bin/ci-build-ask-modules.sh`
   that are applied to the clone's files before `make`, or
2. Create a `data/kernel-patches/` patch applied via `git apply --3way` 
   (this repo's standard patching convention), or
3. Contribute the fix upstream to `we-are-mono/ASK` and update the pinned
   commit SHA in `bin/ci-build-ask-modules.sh`.

**Last maintained:** 2026-07-02 (added after code-review finding that 
commits `84310a0` and `42f2f4f` targeted this inert copy; corrected same
day after discovering M2 doesn't currently run at all, for the deeper
reason above — not just a dead-code-path bug).
