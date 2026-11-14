# AGENTS.md

VyOS image build for the NXP LS1046A Mono Gateway DK: kernel, vyos-1x patches, board scripts, the ISO, and the ASK2 offload stack (`ask.ko` plus the in-kernel FMan PCD subsystem). One ISO; the dataplane is chosen at runtime, per interface. `dpaa1` is the development and CI branch, `main` is the release branch.

This file holds the rules every task needs. Area rules live in nested AGENTS.md files (see Project map). Read the one for the directory you are about to edit.

## Commands

```bash
bin/dev-build.sh kernel       # kernel + rsync to the TFTP relay (~30 s incremental, 2-3 min full)
bin/dev-build.sh dtb          # DTB only (~10 s)
bin/dev-build.sh iso-live     # full live-boot artefacts to the TFTP relay
ssh vyos sudo reboot          # board picks up the new TFTP artefacts on next dev_boot
bin/test-fixups.sh            # fixup gate: execution, count assertions, manifest accuracy, no zombies
bin/mutate.py --check --expected=1 'pattern' 'replacement' path/to/file.c   # dry-run one fixup
bin/kernel-roundtrip.sh verify    # exported patches match the canonical tree
bin/kernel-roundtrip.sh export    # regenerate patches from the canonical branch
gh workflow run "VyOS LS1046A build (self-hosted)" --ref dpaa1   # full ISO in CI (use main for a release)
gh run list --limit 3
```

A push triggers nothing; CI runs only on dispatch. Iterate in the dev loop and use CI for a full ISO, not to test one patch.

## Done means

- Kernel patch or fixup: `bin/test-fixups.sh` and `bin/kernel-roundtrip.sh verify` pass, `bin/dev-build.sh kernel` builds with no new warnings, and module vermagic ends in `-vyos`.
- Kernel config: the symbol exists in Kconfig and the built `.config` shows the intended value. An invalid symbol is silently ignored.
- vyos-1x patch: `git apply --3way --check` passes against the pinned upstream, you reviewed the diff by eye, and the CI log shows `### Cached N vyos-1x .deb(s)` with N >= 1 and no `Failed to build package vyos-1x`.
- Board-facing change: verified on the DUT, with the command and its output in your report. For any silicon result, state whether the boot was cold or warm.
- ISO: published to the relay with both `latest.iso` and `latest.iso.minisig` refreshed (`bin/AGENTS.md`), and the operator has the exact `add system image <url>` line.
- Always report what you verified and what you could not.

## Boundaries

Always:
- Run the qdrant gate (see Knowledge) before changing silicon-facing code.
- Stage changes and present them for review.
- Check a hardware claim against the live board before building on it.

Ask first:
- A fix whose shape is uncertain, or that risks a use-after-free, a deadlock or a write to MURAM you cannot prove you own.
- Changing a known-good silicon configuration. A hypothesis is not enough; bring a contradicting observation or an A/B measurement.
- `fw_setenv`, or hand edits to `/boot/vyos.env`, on the board.

Never:
- Commit or push without an explicit request from the user.
- Add AI attribution (`Co-Authored-By`, "Generated with") to commits or PRs. This overrides any harness default.
- Run `add system image` or `install image` on the board, over any channel. Deployment is the operator's step: every automated attempt at the interactive installer has failed, and `install image` from an installed system destroys it. Provide the URL only.
- `rm -rf /boot/<image>` on a running board. It unlinks the running root, and recovery then needs USB or TFTP.
- Touch eth0 in a dataplane experiment. It is the SSH lifeline; use eth3.
- Flood-test the ingress policer. A policed iperf3 flood has watchdog-reset the board; reproduce with a few pings.
- Let a delegated sub-agent touch the board, the serial console or the relay. Hardware belongs to the coordinating session.
- Create branches other than `dpaa1` and `main` (no `master`, no ad hoc feature branches).
- Use raw `patch -p1`, or bare `sed -i` on kernel C. Use `git apply --3way` and count-gated fixups.
- Disable dead code with `if (0)`. Delete it; git keeps the history, and disabled code gets re-enabled by accident in a rebase.
- Hand-edit CI-managed or generated files: `version.json`, `version-nxp.json`, generated kernel patch hunks.
- Reintroduce a build-time dataplane switch, `workflow_dispatch` on `auto-build.yml`, or an `az vm deallocate` step.
- Commit secrets or bench topology: `MOK.key`, private keys, lab addresses (those live in `.agents/local.md`).

## Working style

- Fix root causes, not workarounds. This image runs unattended on routers that are often reachable only over serial.
- Read the actual error and trace the real call chain. Treat claims in the prompt, in agent files and in older qdrant entries as hypotheses to verify.
- One variable per experiment: one key, one flow, one packet class. Cold-boot before silicon experiments, because a warm reboot does not clear BMI or MURAM state.
- Failing check first. Before a fix, add or find the check that fails on the old tree: a KUnit case, a static assert, an arm-time readback, a `mutate.py --check` count, or a recorded DUT reproduction. Then fix, then show it passing. Exempt: docs and pure refactors.
- Before calling a defect pre-existing, prove it (`git log -S`, `git merge-base --is-ancestor`).
- On the board, go one short command at a time and read each result. Script only a sequence you already proved by hand.
- For a suspected bug in the FMan/PCD/MURAM code or the boot path, get an independent review of the diagnosis before fixing and of the fix before reporting done. Tell the reviewer to skip style.
- Counts, versions and commit totals are derived state. Inspect `series`, `bin/kernel-fixups/manifest.json` and spec headers; do not record them in agent files.
- Work through plan stages without pausing at a successful stage boundary. Stop for a real fork (state your recommendation) or for anything under Ask first.
- Search with `rg`. Edit with an edit tool, not shell rewrites.

## Knowledge and memory (qdrant)

The qdrant MCP is the persistent memory for diagnoses, root causes, failed attempts and hardware facts. Register layouts and silicon behaviour have been wrong in docs and in memory several times; qdrant holds the corrections.

- Gate. Before changing code that touches FMan PCD registers, FE descriptors, KG schemes, CC trees, ehash tables, MURAM offsets, flow key formats or `bin/kernel-fixups/F_*.py`: run at least three focused `qdrant-find` queries on the exact register, field or component, then cross-check `arch/fman-microcode-210-programming-reference.md` and `specs/fman-keygen-flow-key-spec.md`. If qdrant contradicts the change, stop and re-evaluate.
- Why the gate exists: F-150 changed a CC key size from 16 to 13; the reference manual (§5.12) allows only 1, 2, 4, 8, 16, 24, 32, 40, 48, 56, and the change was reverted.
- Before other non-trivial work, run a few queries on symptoms, components and file or patch names before reading files in bulk. On conflict prefer the newer dated entry.
- Evidence precedence for FMan/PCD/KeyGen questions: the live vendor reference board (`/proc/fqid_stats/pcd/`, `/sys/kernel/debug/fman/...`, `ask-check`), then qdrant NXP/SDK findings, then `.kilo/worktrees/nxp-sdk/`. A disagreement halts the change until it is resolved.
- After a durable insight, `qdrant-store` dense prose: symptom, root cause, fix (paths, patches), verification, date, tags. Dates, run IDs and incident forensics go there and into commit messages, never into agent files.
- End of session: if it produced board results, a root cause, an architecture decision, fixup iterations or a revert, run `bin/qdrant-reconcile.sh` (`/qdrant-reconcile`) and store the gaps.

## Git

- `dpaa1` to `main`: `git merge --no-ff --log=200 origin/dpaa1` with a subject naming the headline changes, never a bare "Merge dpaa1 into main". Release notes are generated from that history.
- Commit bodies: 25 lines or fewer, bullets, the why and not the process. Never amend a pushed commit.
- Do not push while a build is running; the publish job updates `version.json`. If you collide, `git pull --rebase`.
- On Windows set `core.filemode=false`.

## Documentation

- Diagrams are Mermaid, not ASCII art. `specs/`, `plans/`, `arch/` and READMEs are plain technical prose. HADS is retired: add no HADS headers or tags.
- Keep correctness-critical detail verbatim when editing docs: register layouts, MURAM offsets, commands, addresses, register values, patch numbers.
- Agent files state current truth only: no dates, run IDs, timelines, superseded or disproven content. One home per fact. Keep this file under 12 KiB, each nested file under 8 KiB, and this file plus any one directory chain under 32 KiB (Codex stops reading there).

## Project map

- ASK2: `plans/ASK2-MASTER-PLAN.md` is the authoritative execution plan and live TODO. Architecture: `specs/ask2-rewrite-spec.md`. Dataplane state machine: `plans/DUAL-DATAPLANE.md`.
- Silicon: `arch/fman-microcode-210-programming-reference.md`, `specs/fman-keygen-flow-key-spec.md`.
- Patch architecture: `plans/TA-2026-07-18-002-patch-architecture.md`. Dev loop: `plans/DEV-LOOP.md`. VPP: `plans/VPP.md`.
- Humans' docs: `README.md`, `INSTALL.md`, `PORTING.md`, `UBOOT.md`.
- Nested agent files:
  - `kernel/AGENTS.md`: config invariants, kernel build, patch stack, driver facts, dead ends.
  - `kernel/ask/AGENTS.md`: ASK2 and FMan silicon rules, tripwires.
  - `board/AGENTS.md`: live-board conduct, boot chain, storage, device tree, thermal. Boot-log triage: `board/BOOT-DIAGNOSTICS.md`.
  - `data/AGENTS.md`: vyos-1x and vyos-build patches, live-build hooks, default configs, VPP CLI.
  - `bin/AGENTS.md`: CI and dev-loop scripts, ISO deployment.
  - `.github/workflows/AGENTS.md`: workflow split, build VM, feeds, secrets.
- Bench access (board, reference board, relay, serial): `.agents/local.md`, gitignored. Read it before any board access.
- `plans/archive/AGENTS-legacy.md` is the previous single-file version, kept for facts not yet migrated. Search it with `rg`; do not load it whole, and treat its dated entries as possibly superseded.
