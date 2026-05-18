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

## FMAN_PCD_API_VERSION bump rule

Per spec §13 the in-tree PCD subsystem exposes a single ABI gate macro,
`FMAN_PCD_API_VERSION`, in `drivers/net/ethernet/freescale/fman/fman_pcd.h`.
OOT consumers (today: `ask.ko`; tomorrow potentially other flavors)
`#if FMAN_PCD_API_VERSION < N #error ... #endif` to refuse compiling
against a kernel that predates a feature they need.

**Rule:** any patch that adds a **required** field to an
`fman_pcd_*` struct, or changes the semantics of an existing field, or
adds a new mandatory argument to an exported `fman_pcd_*` function, is an
ABI break and **must** bump `FMAN_PCD_API_VERSION` in the same patch.

Patch 0037 (PR14z, commit `8de3b35`) is the canonical case study:
it added `hmct_used` as a required output field on
`struct fman_pcd_manip` that the chain-primitive validator
(`fman_pcd_manip_chain_create()`, patch 0036) reads. Any OOT caller that
constructed a `struct fman_pcd_manip` without populating `hmct_used`
would silently fail validation. We got away without bumping the version
this time because (a) the only OOT consumer in tree is `ask.ko` and the
encoders are kernel-side, and (b) `hmct_used` is populated by the
in-tree encoders, not by OOT callers. Future field additions that cross
the OOT boundary are not so lucky — **bump the version**.

What counts as a "required field" — examples:

- A field the validator (`fman_pcd_manip_chain_create`,
  `fman_pcd_cc_node_add_key`, `fman_pcd_kg_scheme_set`, etc.) reads on
  the input path. (PR14z `hmct_used` — should have bumped.)
- A field a new MANIP encoder depends on for correctness. (PR14x
  `next_fqid` plumbing — bumped to v1 at introduction.)
- A new mandatory enum value an OOT caller would have to handle
  (`enum fman_pcd_action::FMAN_PCD_ACTION_REPLICATE` — R3, future).

What does **not** count (no bump needed):

- New `EXPORT_SYMBOL_GPL` of a previously-internal helper.
- New `enum` values that are opt-in (caller only sees them if they
  request the feature explicitly).
- New optional fields that the in-tree code treats as `0`-means-default
  on the read path.
- Bug fixes that don't change the published struct layout or call
  signature.

**Procedure when bumping:**

1. In the same patch: `#define FMAN_PCD_API_VERSION N+1` in
   `fman_pcd.h`.
2. In the same patch: add a one-line entry to the version-history
   comment block above the macro recording **what** changed and **why
   it's a break**.
3. Bump the `#if FMAN_PCD_API_VERSION < N` guard in every OOT consumer
   that depends on the new field (`ask.ko` at minimum).
4. Mention the bump in the patch's commit subject line:
   `fman/pcd: bump API to vN+1 — <reason>`. This makes git log of the
   patches/ directory a usable ABI history.

The 6-month forensic nightmare this rule heads off: an OOT module
authored against PCD vN, compiled cleanly against a kernel that
silently shipped vN+1's new field, and then mis-validates a chain at
runtime in a way that only triggers on real traffic. PR14z's
`hmct_bytes_used` was found by validator `-EINVAL` at module init —
the next break may not be so visible.
