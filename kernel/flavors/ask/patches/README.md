# ASK2 in-tree kernel patches

Per `specs/ask2-rewrite-spec.md` §10.1, ASK2 needs three small
in-tree kernel patches. They are listed and applied in numeric order:

| File                                  | Spec ref | Replaced in |
|---------------------------------------|----------|-------------|
| `0001-caam-qi-share.patch`            | §8.1     | PR10 (M2.1) |
| `0002-dpaa-eth-flow-block.patch`      | §10.2    | PR11 (M2.2) |
| `0003-fman-host-command-api.patch`    | §10.3    | PR12 (M2.3) |

## Status (PR2 — M0.2)

These are **placeholder stubs** authored against `linux-6.18.28`. Each
adds the symbol the OOT `ask.ko` module needs at link time, but every
function returns `-EOPNOTSUPP` and carries a `#warning` line so the
kernel build chatters about the deferred work. The placeholders exist
so PR3 (M0.3) can wire the build pipeline end-to-end while the real
implementations land later as part of M2.

The real PR10/11/12 patches will overwrite these files in place; the
file names and patch numbers stay stable so `bin/ci-setup-kernel.sh`
does not need to change again.

## Authoring rules

Per `plans/PATCH-MIGRATION-3WAY.md`:

- Every patch is a `git format-patch` output (full git-format unified
  diff with blob SHA index lines).
- Apply order is `bin/ci-setup-kernel.sh`'s alphabetical sort. To stay
  out of vyos-build's own `0001-*` and `0003-*` reserved range, these
  files get **renamed at staging time** to `1001-`/`1002-`/`1003-` —
  see PR3 for the staging logic.
- Verify any future edit applies cleanly to a stock kernel tree:
  ```sh
  cd /tmp && tar xf /path/to/linux-6.18.x.tar.xz
  cd linux-6.18.x
  git init -q && git add -A && git commit -q -m base --no-verify
  git apply --3way --check /path/to/0001-caam-qi-share.patch
  ```
- Mergiraf is wired as the merge driver via repo `.gitattributes` for
  `*.c`/`*.h`/`*.py`/`*.json`/`*.yaml`/`*.toml`/`*.xml`, so
  `git apply --3way` will fall back to AST-aware merge on context drift.