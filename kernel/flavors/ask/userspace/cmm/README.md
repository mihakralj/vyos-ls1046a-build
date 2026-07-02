# cmm — Reference copy (NOT the build source)

This directory is a **reference copy** for code review and documentation.
It is **not** compiled by CI or any build script.

**The actual cmm binary shipped in the ISO is built from the
`we-are-mono/ASK` Git repository** (branch `mt-6.12.y`, pinned to commit
`a211ea865379362058c6656b9c448e4a7050e93c` as of 2026-07-02). The CI build
script `bin/ci-build-ask-userspace.sh` clones that repo to
`$RUNNER_TOOL_CACHE/ask-clone-cache/ask-mt-6.12.y/` (resetting it to the
pinned commit on every run — a persistent self-hosted runner cache
directory was previously left un-reset between runs, causing prior
CT-TRACE mutations to accumulate and break the build, see CI run
28557820493) and builds `cmm/src/*.c` from there. CT-TRACE diagnostics
and bug fixes are applied via Python injection into the clone's
`conntrack.c` and `callback.c` before `make`.

**Editing files here has zero effect on the shipped binary.** To change
the CMM binary, either:

1. Modify the Python injection in `bin/ci-build-ask-userspace.sh`
   (the `#### PYPATCH` block), or
2. Create a patch file and add a `patch -p1` step before `make`, or
3. Contribute the fix upstream to `we-are-mono/ASK` and update
   `ASK_COMMIT` in both `bin/ci-build-ask-userspace.sh` and
   `bin/ci-build-ask-modules.sh`.

**Last maintained:** 2026-07-02 (added after code-review finding that 
eight commits of CT-TRACE injections targeted this inert copy before 
the CI-side Python injection was adopted in `b3c6579`; updated same day
after fixing the unreset-clone-cache bug and pinning to a fixed commit).
