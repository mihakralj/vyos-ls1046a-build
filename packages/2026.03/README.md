# VyOS Circinus 2026.03 Source Bundle

This directory **tracks** the complete VyOS Circinus 2026.03 GPL source dump from
`/tmp/vyos-stream-src` and `/tmp/vyos-stream-tarballs`.

## Structure

| File | Purpose |
|------|---------|
| `packages.toml` | Full manifest: 541 Debian source packages + 60 vendored tarballs |
| `SUMMARY.md` | Human-readable inventory |
| `tarballs/<name>/source.toml` | Per-tarball reference: bundle path, size, file count |
| `.gitignore` | Prevents accidental commit of large binary sources |

## How tracking works

- **Manifest (`packages.toml`)** is the single source of truth — it maps every package
  to its exact bundle path, version, and size.
- **Tarball references** (`tarballs/*/source.toml`) track each of the 60 VyOS-specific
  packages (kernel, vyos-1x, vyos-build, frr, kea, etc.).
- **Large files stay in `/tmp`** — the `.orig` source trees and vendored tarballs
  are referenced by path in the manifest, not committed to git.
- **Build scripts** resolve sources via the manifest at build time.

## Why not commit the sources directly?

The bundle is 12.4 GB (3.1 GB kernel, 1.7 GB firmware, hundreds of source trees).
Git is a version control system for text, not a binary storage system. The manifest
approach gives us deterministic reproducibility without the overhead.

## Using the manifest in build scripts

```bash
# Read a specific tarball path
grep -A1 '^\[tarball\.vyos-1x\]' packages.toml | grep '^path'

# List all VyOS packages that need building
grep -A1 '^\[tarball\.' packages.toml | grep '^path'
```
