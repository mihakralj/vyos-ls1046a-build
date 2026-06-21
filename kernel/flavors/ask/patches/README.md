# ASK2 in-tree kernel patches

The ASK flavor now relies on the common board patch stack. All
ASK-only kernel patches that previously lived in this directory have
been archived under `archive-2026-06-21-pre-6.18.34/` (the legacy
6.18.28-era stack). patch-health for `FLAVOR=ask` now passes with just
the common patches applied; no flavor-specific patches are active.

If you need the old ASK patch series for archaeology or backports, use
the archived copies. Any new ASK-specific kernel work should start from
the current common-patched kernel and add fresh patches here as
needed.
