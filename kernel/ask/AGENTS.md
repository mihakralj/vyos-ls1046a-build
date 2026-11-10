# ASK2 AGENTS.md

ASK2 is a silicon-integrated dataplane, not a normal feature branch. Treat FMan, PCD and KG layouts as hardware-contract code.

## Required gate

Before changing FE descriptors, KG schemes, CC trees, ehash layouts, MURAM offsets or `bin/kernel-fixups/F_*.py`:

- run at least three focused `qdrant-find` queries on the exact field or component;
- check `arch/fman-microcode-210-programming-reference.md` and `specs/fman-keygen-flow-key-spec.md`;
- stop if qdrant disagrees with the change.

## Current project truth

- Dataplane selection is runtime per interface; do not reintroduce a build-time dataplane switch.
- ASK2 uses the live `ask.ko` and the in-kernel FMan PCD path, with mainline/RSS as S0 and ASK as the engaged path.
- A hardware claim is not enough until it is checked against a live DUT or the vendor reference board.
- `set interfaces ethernet ethN offload ...` is the control plane; do not confuse this with a module build flag.

## Safety rules

- No `if (0)` dead code in silicon paths; delete it and keep the history in git.
- No raw writes to MURAM or FE workspace without ownership proven from the allocator.
- Read back the programmed register state after a FE/KGSE write and compare it before declaring success.
- One variable per experiment: one key, one flow, one packet class.
- Freeze the boot type in the report for silicon work: cold-boot or warm-reboot.

## Ask-first and never

Ask first:
- changing a known-good silicon layout or flow key order;
- a use-after-free, deadlock, or unowned MURAM write;
- `fw_setenv` or board bootenv edits outside the documented procedure.

Never:
- touch eth0 in a dataplane experiment;
- flood-test the ingress policer;
- disable or hide a crash by wrapping a bug in `if (0)`;
- run `install image` or `add system image` from the DUT.

## References

- `plans/ASK2-MASTER-PLAN.md`
- `plans/DUAL-DATAPLANE.md`
- `specs/ask2-rewrite-spec.md`
- `specs/fman-keygen-flow-key-spec.md`
