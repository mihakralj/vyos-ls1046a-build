# Data AGENTS.md

The `data/` tree carries the build-time patch stack and runtime defaults. Keep it reproducible and keep all generated outputs out of the repo.

## Patch discipline

- Patches live under `data/` and `kernel/common/patches/` in git-format form.
- Use `git apply --3way --whitespace=nowarn`; do not rewrite patches with raw `patch -p1`.
- A patch that changes kernel C must be validated by the fixup helper and the round-trip check.
- Work from a canonical upstream tree; generated patch hunks are not a source of truth.

## Build inputs

- Default configs must be valid VyOS config files with no comments inside `{}` blocks.
- Keep the single-image runtime dataplane model: no build-time flavor switch, no `version-default.json`/`version-vpp.json` alias reintroduction.
- Keep only canonical `version.json` and independent `version-nxp.json` feeds; these are CI-managed, not hand-edited.

## Hook and live-build rules

- Hardware hooks are staged under `data/hooks/` and copied into the live-build hook directory by the setup scripts.
- A hook is not active until it has been copied into the live-build tree and validated by the build.
- The dev loop and ISO deploy path separate TFTP artefacts from the ISO relay.

## References

- `bin/ci-setup-vyos-build.sh`
- `bin/ci-setup-vyos1x.sh`
- `bin/dev-build.sh`
- `board/vyos-config/config.boot.default`
