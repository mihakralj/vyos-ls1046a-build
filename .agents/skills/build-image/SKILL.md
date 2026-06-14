---
name: build-image
description: Dispatch (or locate) a VyOS LS1046A CI build, retrieve the latest ISO artifact from the GitHub Actions run, deploy it to the lxc200 TFTP/HTTP relay, and emit the exact `add system image <url>` command the operator runs on the DUT.
---

# build-image

Drives the **only** CI build path for this repo end-to-end: from triggering (or
finding) a GitHub Actions run, through pulling the produced ISO artifact, to
publishing it on the lxc200 HTTP relay so an operator can install it on the
LS1046A Mono Gateway DK with a single `add system image <url>` command.

This skill encodes the **"ISO deployment invariant"** from `AGENTS.md`. Read
that rule before deviating — the rsync flags, the `latest.iso` symlink (plus the
back-compat `latest-{default,vpp,ask}.iso` aliases), and the canonical install
URL are load-bearing.

## Usage

Use this skill when the user asks to:

- "build an ISO" / "kick off a build" / "deploy the latest build to the DUT"
- "get the latest ISO onto lxc200"
- "give me the `add system image` URL for the newest build"
- "deploy run `<id>` to the device"

If the user only wants a **local dev-loop** TFTP kernel (not a full installable
ISO), this is the WRONG skill — use `bin/dev-build.sh iso-live` instead (it
pushes `vmlinuz`/`initrd.img`/`mono-gw.dtb`/`*.squashfs` to `/srv/tftp/`, NOT
`/srv/tftp/iso/`). This skill is specifically for the **installable ISO** that
`add system image` consumes.

## Key facts (do not re-derive)

- **Only one dispatchable workflow:** `self-hosted-build.yml`
  ("VyOS LS1046A build (self-hosted)"). It spins up the Azure ARM64 VM, calls
  `auto-build.yml` (reusable-only) on the self-hosted runner, then the VM's own
  idle-deallocate daemon stops it. **Never** add `az vm deallocate` or
  `workflow_dispatch:` to `auto-build.yml`.
- **The ISO is always an Actions artifact** (`actions/upload-artifact@v7`,
  retention 15 days). The artifact `name` is the build's `image_name`; it
  contains `manifest.json`, `<image>.iso`, and `<image>.iso.minisig`. This
  works on **every** branch, including feature branches like `dpaa1`.
- **GitHub Release assets only exist on `main`.** The `publish` job is gated
  `if: github.ref_name == 'main'`. On `dpaa1`/feature branches there is NO
  release — you MUST pull the Actions artifact, not a release asset.
- **ISO filename is flavor-neutral:** `vyos-<version>-LS1046A-arm64.iso`. The
  single image ships every dataplane (mainline DPAA + VPP AF_XDP + dormant
  `ask.ko`), selected at runtime — there is no per-flavor ISO.
- **lxc200 relay:** `admin@192.168.1.137` over Tailscale, SSH key
  `~/.ssh/admin_key`. ISOs live at `/srv/tftp/iso/`. A persistent HTTP server
  (`python3 -m http.server 8080 --directory /srv/tftp`) exposes them.
- **Canonical DUT install URL (what the operator pastes):**
  `http://192.168.1.137:8080/iso/latest.iso`. The back-compat aliases
  `latest-{default,vpp,ask}.iso` resolve to the same image for installs that
  pinned an old per-flavor URL. The versioned URL
  `http://192.168.1.137:8080/iso/<versioned-filename>.iso` is also retained for
  pinning/reproducibility.
- **SSH MCP `ssh_upload_file` TIMES OUT** on the 575+ MB ISO. Use `rsync` over
  SSH. This is mandatory, not a preference.

## Steps

### 1. Determine / trigger the build

If the user gave a run ID, skip to step 2 with that ID. Otherwise:

- **To find the latest run** (any status) for the current branch:
  ```bash
  gh run list --workflow "VyOS LS1046A build (self-hosted)" --branch "$(git rev-parse --abbrev-ref HEAD)" --limit 5
  ```
- **To dispatch a new build** (only if the user asked to build, or no usable
  recent run exists):
  ```bash
  gh workflow run "VyOS LS1046A build (self-hosted)" --ref "$(git rev-parse --abbrev-ref HEAD)"
  ```
  Then poll until complete:
  ```bash
  gh run list --workflow "VyOS LS1046A build (self-hosted)" --limit 1
  gh run watch <run-id> --exit-status   # blocks until done; non-zero on failure
  ```
  Warm caches → ~5–10 min. Do **not** `git push` while a build runs (the
  publish job updates `version*.json` → merge conflict; `git pull --rebase` if
  it happens).

### 2. Resolve the target run and verify it succeeded

```bash
gh run view <run-id> --json status,conclusion,headBranch,databaseId
```
Require `status == completed` and `conclusion == success`. If still running,
`gh run watch <run-id> --exit-status` first. If it failed, surface the failing
step (`gh run view <run-id> --log-failed`) and stop — do not deploy a broken
build.

### 3. Download the ISO artifact

The artifact name is the build's `image_name`. List artifacts on the run, then
download into a scratch dir:

```bash
WORK=$(mktemp -d)
gh run download <run-id> --dir "$WORK"
# The artifact unpacks to: $WORK/<image_name>/{manifest.json,<image>.iso,<image>.iso.minisig}
ISO=$(find "$WORK" -name '*.iso' | head -n1)
SIG="${ISO}.minisig"
BASENAME=$(basename "$ISO")                 # vyos-<ver>-LS1046A-arm64.iso
test -f "$ISO" && test -f "$SIG" || { echo "FATAL: ISO or .minisig missing"; exit 1; }
echo "ISO=$ISO"
```

If `gh run download` reports the artifact expired (>15 days), the build must be
re-dispatched (step 1) — there is no release fallback on feature branches.

### 4. Deploy to lxc200 (rsync — NOT ssh_upload_file)

Push BOTH the ISO and its `.minisig` sidecar to `/srv/tftp/iso/`, using
`sudo rsync` on the remote side and an explicit key (the agent shell does not
always pick up `~/.ssh/config`):

```bash
rsync -av --rsync-path='sudo rsync' -e "ssh -i ~/.ssh/admin_key" \
  "$ISO" "$SIG" admin@192.168.1.137:/srv/tftp/iso/
```

Then refresh the canonical `latest.iso` symlink (plus the back-compat per-flavor
aliases, for installs that pinned an old URL) atomically:

```bash
ssh -i ~/.ssh/admin_key admin@192.168.1.137 \
  "sudo ln -sfn '$BASENAME' /srv/tftp/iso/latest.iso && \
   for a in default vpp ask; do sudo ln -sfn '$BASENAME' /srv/tftp/iso/latest-\$a.iso; done"
```

### 5. Verify the HTTP relay serves it

Confirm both the versioned and `latest.iso` URLs return 200 and the right size:

```bash
curl -sI "http://192.168.1.137:8080/iso/${BASENAME}"   | head -n1
curl -sI "http://192.168.1.137:8080/iso/latest.iso"    | head -n1
```
Both must be `HTTP/1.0 200 OK`. If the HTTP server is down, (re)start it on
lxc200: `python3 -m http.server 8080 --directory /srv/tftp` (persistent).

### 6. Emit the operator install instructions

Print the exact command the operator runs **on the DUT** (VyOS op-mode). Lead
with the stable `latest.iso` URL; include the pinned versioned URL as the
reproducible alternative:

```
On the DUT (192.168.1.190), from VyOS op-mode:

    add system image http://192.168.1.137:8080/iso/latest.iso

  (pinned / reproducible alternative:)
    add system image http://192.168.1.137:8080/iso/<versioned-filename>.iso

Then select it and reboot:

    set system image default-boot <new-image-name>   # configure mode, optional
    reboot
```

NEVER instruct the operator to run `install image` from an installed system —
that repartitions the eMMC and destroys the install. `install image` is for USB
live boot ONLY. Installed systems use `add system image <url>`.

## Guardrails

- Deploy ONLY a `completed`/`success` run. Never a failed or in-progress build.
- Always push the `.minisig` alongside the ISO — VyOS verifies the signature.
- Never use SSH MCP `ssh_upload_file` for the ISO (it times out on 575+ MB).
- Never conflate `/srv/tftp/iso/` (this skill, `add system image`) with
  `/srv/tftp/` (the `bin/dev-build.sh iso-live` TFTP live-boot artefacts).
- On feature branches, the source of truth is the **Actions artifact**, not a
  GitHub Release (releases are `main`-only).
- After deploying, store a concise note in Qdrant (run ID, ISO filename, date,
  kernel version) per `.clinerules/70-qdrant-memory.md` if this was a
  validation-relevant deploy.