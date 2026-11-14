# Kernel AGENTS.md

Kernel work is a patch stack, not a one-off edit. Treat the canonical branch and the generated patch stack as the authoritative state; do not hand-edit generated hunks.

## Required checks

- Run `bin/test-fixups.sh` before a kernel fixup or patch change.
- Run `bin/kernel-roundtrip.sh verify` after patch edits that touch canonical kernel state.
- Use `bin/mutate.py --check --expected=N ...` for a dry-run count-gated change.
- `git apply --3way --check` is required for any patch that must apply cleanly.

## Build invariants

- `scripts/config --enable` is not enough to convert `=m` to `=y`; use `scripts/config --set-val NAME y`.
- New kernel `CONFIG_*` values belong at the end of the defconfig append block, not in place of the upstream config.
- `CONFIG_NR_CPUS=4` and `CONFIG_QORIQ_CPUFREQ=y` are not optional in this board path.
- For DPAA1, the stack must stay built-in; `=m` is a failure mode.
- `LOCALVERSION` must end in `-<rev>-vyos` on every kernel build path; no mismatched vermagic.

## Patch and fixup rules

- Core kernel edits live in the canonical tree or the series patch stack; no bare `sed -i` edits on C files.
- Fixups go through `bin/kernel-fixups/manifest.json` and the count-gated helper; no raw `patch -p1`.
- Count assertions are part of validation: the helper must report the expected number of matches.
- If a patch drifted or a hunk looks malformed, stop and re-run the 3-way apply flow instead of forcing it.

## Driver and silicon safety

- `fsl_dpaa_mac`, `fsl_dpa` and `fsl_dpaa_eth` are not interchangeable.
- `CONFIG_FSL_XGMAC_MDIO=y` is required for the FMan ports; no SFP or MAC links means the wrong config path.
- `fsl_dpaa_fman.fsl_fm_max_frm=9600` is the correct module param name; the wrong name is silently ignored.
- For anything touching FMan PCD, FE descriptors, KG schemes, ehash tables or MURAM ownership, run the qdrant gate first.
- A silicon change is not accepted on a hypothesis alone; reproduce the effect, capture the evidence, and keep the boot type noted.

## Project map

- Canonical patch architecture: `plans/TA-2026-07-18-002-patch-architecture.md`
- Kernel build logic: `bin/ci-setup-kernel.sh`, `bin/ci-stage-kernel.sh`, `bin/ci-build-packages.sh`
- Silicon reference: `arch/fman-microcode-210-programming-reference.md`, `specs/fman-keygen-flow-key-spec.md`
- Legacy facts: `plans/archive/AGENTS-legacy.md`
