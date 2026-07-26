# ASK2 in-tree kernel patches — archives only

**There are no active patches in this directory.** Both subdirectories are
frozen archives:

- `archive-2026-06-21-pre-6.18.34/` — the legacy 6.18.28-era ASK patch series,
  archived 2026-06-21 once the common board stack absorbed its features.
- `archive-grafted-2026-05-24/` — the earlier graft-model series.

ASK2's in-tree kernel surface lives entirely in
[`kernel/common/patches/board/`](../../common/patches/board/) (patches
0092–0164: FMan PCD subsystem, KeyGen, CC, MANIP, Policer, FE-VM ehash),
with source-level fixups in `bin/kernel-fixups/F_*.py`. Everything else is
the out-of-tree `ask.ko` under `../oot-modules/ask/`.

Nothing here is staged into a build. The `bin/ci-setup-kernel.sh` block that
used to copy `*.patch` from this directory (renaming them to `1xxx-` to avoid
colliding with vyos-build's reserved `0001-*`/`0003-*` filenames) was gated on
`FLAVOR=ask` and was removed on 2026-07-26 along with the `FLAVOR` variable —
the gate had not fired since the flavor split was retired on 2026-06-14, and
there had been no top-level `*.patch` here since 2026-06-21 regardless.

`patch-health.sh` probes only `kernel/common/patches/` and is unaffected.

If you need the old ASK patch series for archaeology or backports, use the
archived copies. New ASK-specific kernel work should start from the current
common-patched kernel and add fresh patches under
`kernel/common/patches/board/`.
