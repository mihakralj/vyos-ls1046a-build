# Workflow AGENTS.md

CI is not a second build path. The single production release workflow is `self-hosted-build.yml`; `auto-build.yml` is reusable-only.

## Required split

- `self-hosted-build.yml` handles VM lifecycle and dispatch only.
- `auto-build.yml` owns the real build logic and does not accept `workflow_dispatch`.
- No stop-vm or `az vm deallocate` logic is allowed in CI.

## Feed and release invariants

- `version.json` is the canonical main release feed.
- `version-nxp.json` is a separate track for the NXP SDK branch; keep it independent.
- A full ISO is produced in CI; the local dev loop is not a substitute for a release build.

## Safety

- Do not reintroduce build-time dataplane switches or flavor-specific aliases.
- Avoid external repo waits in build scripts; the shared VM is not a free-for-all runner pool.
- Keep the build output in the repo-scoped workspace; do not use older absolute `/build` or `/work` locations.

## References

- `.github/workflows/self-hosted-build.yml`
- `.github/workflows/auto-build.yml`
- `.github/workflows/patch-rot-check.yml`
