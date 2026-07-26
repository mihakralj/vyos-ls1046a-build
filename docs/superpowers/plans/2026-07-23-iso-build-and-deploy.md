# ASK2 ISO Build and Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a fresh VyOS LS1046A ISO containing recent kernel, `ask.ko` hardware opcode chain commits, and `vyos-1x` CLI updates, and deploy it to `lxc200` (`http://192.168.1.137:8080/iso/latest.iso`) for hardware verification on DUT `.185`.

**Architecture:** Runs `./bin/dev-build.sh iso` (or `./bin/local-build.sh`) to build all VyOS packages and live ISO natively on the Cobalt 100 VM, signs/links the output ISO, and synchronizes the published artifacts to `lxc200` (`/srv/tftp/iso/`).

**Tech Stack:** `live-build`, `debian-package`, `rsync`, HTTP relay on `lxc200:8080`.

## Global Constraints

- Must output a valid `vyos-*-LS1046A-arm64.iso` image.
- Must publish to `/srv/tftp/iso/` on `lxc200`.
- Must update `/srv/tftp/iso/latest.iso` and `latest-ask.iso` symlinks.

---

### Task 1: Build ISO Image

**Files:**
- Execute: `bin/dev-build.sh`

- [ ] **Step 1: Execute ISO build**

Run: `FLAVOR=ask ./bin/dev-build.sh iso`
Expected: Live-build completes and produces `vyos-*-LS1046A-arm64.iso`.

- [ ] **Step 2: Verify ISO output file**

Run: `ls -la vyos-*-LS1046A-arm64.iso /tmp/vyos-*-LS1046A-arm64.iso 2>/dev/null`
Expected: Output ISO file exists and size is ~550–590 MB.

---

### Task 2: Deploy & Publish ISO to `lxc200`

**Files:**
- Modify: Remote `/srv/tftp/iso/` on `lxc200`

- [ ] **Step 1: Rsync ISO to `lxc200`**

Run: `rsync -avz --progress vyos-*-LS1046A-arm64.iso admin@192.168.1.137:/srv/tftp/iso/`
Expected: File transferred successfully.

- [ ] **Step 2: Update `latest.iso` symlinks on `lxc200`**

Run: `ssh lxc200 "cd /srv/tftp/iso && sudo ln -sfn \$(ls -1t vyos-*-LS1046A-arm64.iso | head -1) latest.iso && sudo ln -sfn \$(ls -1t vyos-*-LS1046A-arm64.iso | head -1) latest-ask.iso"`
Expected: `latest.iso` points to the newly built ISO.

---

### Task 3: Verify HTTP Accessibility

- [ ] **Step 1: Test HTTP header response for `latest.iso`**

Run: `curl -I http://192.168.1.137:8080/iso/latest.iso`
Expected: `HTTP/1.0 200 OK` with non-zero `Content-Length`.
