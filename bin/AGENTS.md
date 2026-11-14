# Bin AGENTS.md

This directory owns the build and verification loop: kernel builds, fixup gates, ISO publishing, and the dev-loop relay flow.

## Commands

```bash
bin/dev-build.sh kernel
bin/dev-build.sh dtb
bin/dev-build.sh iso-live
bin/test-fixups.sh
bin/mutate.py --check --expected=1 'pattern' 'replacement' path/to/file.c
bin/kernel-roundtrip.sh verify
bin/kernel-roundtrip.sh export
```

## Dev-loop and ISO rules

- Use `bin/dev-build.sh` for a fast check on the Cobalt build host; it syncs the artefacts to the TFTP relay.
- Keep the live-boot artefacts (`vmlinuz`, `initrd`, DTB, squashfs) separate from the ISO relay path.
- Publish each successful ISO with both the ISO and the matching `.minisig` symlink refreshed.
- The operator URL is the relay URL; keep the exact `add system image <url>` line for the operator to run.

## Safety rules

- No automation may run `add system image` or `install image` on the DUT. Deployment is the operator's action.
- Do not reintroduce a stale `latest-<flavor>.iso` alias in place of the single canonical `latest.iso`.
- Keep `bin/mutate.py` as the canonical mutation helper; do not drift to raw shell `sed` on kernel C.
- If a build log shows a missing `vyos-1x` deb, treat that as a stop condition, not a warning.

## References

- `bin/ci-build-packages.sh`
- `bin/ci-build-iso.sh`
- `bin/ci-setup-kernel.sh`
- `bin/ci-stage-kernel.sh`
- `plans/DEV-LOOP.md`
