# Archive note — ASK-UPSTREAM-SYNC.md

Archived: 2026-05-07.

**Why archived:** This plan compared our then-active `ask-ls1046a-6.6/`
fork against `we-are-mono/ASK@mt-6.12.y` and proposed a sync strategy.
Both inputs are now obsolete:

1. The `ask-ls1046a-6.6/` fork was archived in May 2026 and its contents
   redistributed in-tree:
   - OOT modules (`cdx`, `fci`, `auto_bridge`, `iptables-extensions`)
     and ppp/rp-pppoe userspace patches → producer (`kernel-ls1046a-build`)
     under `release/oot-modules/` and `release/userspace-patches/`.
   - cmm / dpa_app / fmlib / fmc / libcli userspace → this consumer repo
     under `ASK/`.
   See `.clinerules/05-workspace-layout.md` and producer's `AGENTS.md`.

2. `we-are-mono/ASK@mt-6.12.y` is no longer the upstream we sync to. The
   project has pivoted to patching `kernel.org` mainline 6.18 directly
   (see `plans/MIGRATION-PLAN-6.18.md`). The userspace components keep
   their current in-tree home; future updates to `cmm` / `fmlib` / `fmc`
   etc. are direct edits with `ASK-edit` markers, not upstream syncs.

Kept as historical reference only.