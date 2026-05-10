# ASK Userspace — Vendored Sources

Post-patch source trees for ASK userspace components. Imported from upstream at
the pinned tag, with the in-tree ASK patch applied. Future edits land directly
on the vendored tree and are annotated with `ASK-edit (askN): rationale`
markers (same discipline the kernel side uses for `kernel/flavors/ask/sdk-sources/`).

| Component | Upstream | Tag | Imported HEAD | Files | Patch (audit copy) |
|---|---|---|---|---|---|
| `libnfnetlink/` | https://git.netfilter.org/libnfnetlink | `libnfnetlink-1.0.2` | `a133e29` | 26 + PATCH.txt | `01-nxp-ask-nonblocking-heap-buffer.patch` |
| `libnetfilter-conntrack/` | https://git.netfilter.org/libnetfilter_conntrack | `libnetfilter_conntrack-1.0.9` | `7a8e1e2` | 118 + PATCH.txt | `01-nxp-ask-comcerto-fp-extensions.patch` |
| `fmlib/` | https://github.com/nxp-qoriq/fmlib | `lf-6.18.2-1.0.0` | `7a58eca` | 40 + PATCH.txt | `01-mono-ask-extensions.patch` |
| `fmc/` | https://github.com/nxp-qoriq/fmc | `lf-6.18.2-1.0.0` | `5b9f4b1` | 56 + PATCH.txt | `01-mono-ask-extensions.patch` |

`PATCH.txt` in each subtree is the original ASK patch as it stood at vendoring
time — kept for audit / divergence tracking, NOT re-applied at build time.

## Why vendored

The previous arrangement was:
- `data/ask-userspace/<comp>/` — prebuilt `.so` + headers (libnfnetlink, libnetfilter-conntrack)
- `bin/ci-build-{fmlib,fmc}.sh` — clones upstream at CI time, applies patch, builds (fmlib, fmc)

Both modes had problems:
1. **Prebuilt blobs** (libnfnetlink, libnetfilter-conntrack): no proof the `.so`
   matches the patch in the repo. Header/symbol drift produces silent ABI
   mismatches with cmm — the same failure class that killed dpa_app pre-fmc-rebuild.
2. **Clone-at-CI** (fmlib, fmc): network-fragile, depends on `git.netfilter.org` /
   `nxp-qoriq` staying online, no offline reproducibility, no way to track
   incremental fixes (every retry re-applies the original patch).

Vendoring eliminates both: source is in this repo, builds from `ask-userspace/<comp>`
directly, network only needed for `git clone` of THIS repo.

## Adding a new in-tree edit

Same discipline as the kernel SDK direct-edit policy. Each delta from the
imported upstream gets a marker comment:

```c
/* ASK-edit (askN): one-line rationale */
```

The grep `grep -rn 'ASK-edit' ask-userspace/` enumerates every divergence vs
upstream, so the audit trail is always in the tree.

## Re-vendoring (refreshing upstream)

If we ever need to bump the upstream tag (e.g. `libnetfilter_conntrack-1.0.9` →
`-1.0.10`):

1. `git clone --branch <newtag> <upstream> /tmp/<comp>-vendor`
2. Apply `ask-userspace/<comp>/PATCH.txt` (or whatever the current ASK delta is)
3. Re-apply any `ASK-edit` markers from the previous vendored tree
4. `rm -rf .git && cp -a /tmp/<comp>-vendor/* ask-userspace/<comp>/`
5. Update this README with the new tag/SHA
6. Commit with `vendor: refresh <comp> <oldtag> -> <newtag>`

Build scripts (`bin/ci-build-<comp>.sh`) consume `ask-userspace/<comp>/` directly,
no network access at CI time.