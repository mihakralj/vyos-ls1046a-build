# libcli patches

Patches applied to the upstream `dparrish/libcli` clone (cloned at `$REPO_ROOT/libcli/`)
before `make` in `bin/ci-build-ask-userspace.sh` (Stage 1).

Patches are sorted by filename and applied with `git apply` (or `patch -p1`).
Each patch must be self-contained and idempotent (applier uses `git apply --check`
to skip already-applied patches).

## Inventory

| File | Audit | Description |
|---|---|---|
| `0001-bound-completion-strcpy.patch` | C3 B6 P0.3 | Bound `strcpy((cmd + l), comphelp.entries[0])` at `libcli.c:1472` by `CLI_MAX_LINE_LENGTH` to prevent heap overrun on a long completion entry. |
