# Board AGENTS.md

Board work is operationally sensitive: the DUT is a live router, and mistakes can break the SSH or serial path in a way the build system cannot recover from automatically.

## Required conduct

- Use `eth3` for dataplane experiments; `eth0` is the ssh lifeline.
- Open serial or SSH only as the session requires; keep each command short and inspect each result.
- Verify any hardware claim against the board before relying on it in code or docs.
- For any silicon or boot-path bug, get an independent review before fixing and before reporting done.

## Boot and install handling

- `install image` is operator-only, never run from an installed system.
- `add system image <url>` is the supported deployment path; the operator runs it.
- Do not `rm -rf /boot/<image>` on a running board.
- `fw_setenv` and direct `/boot/vyos.env` edits are ask-first changes; prefer the repo-side boot scripts and documented procedure.

## Storage and environment

- Board boot uses the documented U-Boot path and the `vyos-postinstall` boot script; do not simplify this by hand.
- Keep `vyos.env`/bootcmd self-healing behavior intact; do not hardcode stale TFTP boot arguments.
- Respect the eMMC and live USB partition flow; the live install path is not the same as the installed path.

## Thermal and device-tree safety

- Do not reintroduce the stale `ethX` or HDMI-like legacy names that break the panel order mapping.
- Keep the board thermal and fan logic in sync with the actual DTS and hwmon nodes.
- If a device-tree or SFP issue is suspected, check the board and the board DTB before patching the kernel or configuration.

## Crash and debug posture

- A warm reboot does not clear BMI or MURAM state. Cold-boot before silicon experiments.
- If a bug appears to be pre-existing, prove it with `git log -S` or `git merge-base --is-ancestor` before calling it historical.
- Keep debug and recovery logs separate from durable repo instructions; use `board/BOOT-DIAGNOSTICS.md` for the ignore/act table.

## References

- `board/BOOT-DIAGNOSTICS.md`
- `INSTALL.md`
- `UBOOT.md`
- `HARDWARE.md`
