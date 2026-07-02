# nxp-sdk ASK 1.x kernel patches

**This README previously described the wrong "ask" flavor.** The text
below about "relies on the common board patch stack... no flavor-
specific patches are active" describes the ASK2 (mainline `ask.ko`
rewrite) flavor on `main`/`ask20`, which targets kernel 6.18.x and
genuinely doesn't need flavor-specific kernel patches. **This branch
(`nxp-sdk`) is a different effort** — the native NXP ASK 1.x vendor SDK
port, targeting kernel 6.12.49 (NXP `lf-6.12.49-2.2.0` + the 266-file
SDK source overlay at `kernel/flavors/ask/sdk-sources/`) — and it
absolutely DOES need the patches in this directory. `kernel/common/
scripts/stage-kernel.sh` applies every `*.patch` file directly under
this directory (plus `patches/ask/` and `patches/fixes/` if present,
`-maxdepth 1`, sorted) for `--flavor ask`.

Someone (likely reasoning from the stale text above, written for the
other flavor) moved five load-bearing patches — `002-ask-kernel-
hooks.patch` (17.9k lines: adds `wifi_offload_dev`/`expt_pkt`/`qm_ctx`
etc. fields cdx.ko's SDK code directly depends on), `004-export-dpaa-
submit-symbol.patch`, and three of the six-patch `720-725` series
(`720`, `722`, `724` — the other three, `721`/`723`/`725`, stayed here
the whole time) — into `failed-conntrack-experiments/`, which
`stage-kernel.sh`'s `-maxdepth 1` scan never reaches. Without them,
cdx.ko fails to compile with dozens of "has no member named
wifi_offload_dev" / "implicit declaration of function fm_get_fw_rev" /
etc. errors (CI run 28560879051, 2026-07-02) — restored to this
directory the same day.

`failed-conntrack-experiments/` still correctly holds the genuinely
speculative/unverified `04xx` conntrack patches
(`nf-conntrack-enable-hooks-true` and friends) — those were never
proven to work (see `specs/conntrack-root-cause-analysis.md` and the
`enable_hooks` sed-in-`auto-build.yml` note in `AGENTS.md`-style repo
memory) and should stay excluded until re-verified.

If you archive or move anything out of this directory again: check
whether `nxp-sdk`'s own from-source kernel build actually needs it
before assuming the ASK2-flavor README text above applies here too.

