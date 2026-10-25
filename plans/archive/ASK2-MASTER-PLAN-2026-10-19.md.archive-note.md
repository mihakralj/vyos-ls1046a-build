# Master plan snapshot

Exact pre-cleanup text from `plans/ASK2-MASTER-PLAN.md` (v2.33.2, with the
T-M8-3 closure edit), archived 2026-10-19. The live master (v2.34.0) replaced
the following with a closed-gates table in §1.1 and one-line pointers:

- §1.1 release smoke `0357`, fixed-image re-test `1941`/`2012`, installed-image
  gate, 23-cell offload regression battery, factory-default static validation;
- §3 rows 1 and 1a (factory default; bidirectional delta A/B);
- §4.1 per-port disable release gate, factory-default fresh install on `.145`,
  T-PARITY-10 and the same-session `0551`/`2149` A/B, harness verdicts;
- §4.6.4 PPPoE 2026-10-17 ASK2-side fragmentation run, IPsec 2026-10-16 audit
  and 2026-10-17 Tier 1 run, bridge 2026-10-17 B6/B7a repeat and soak;
- §4.7 T-M8-3 counter half, `get-flow` defect and fix, KUnit and production
  checks.

Open gates (F-266, A14, A2/A13, A6, A15, B7b, dispatch, PPPoE, IPsec Tier 2,
etc.) stay in the live master. Current work: `plans/ASK2-MASTER-PLAN.md`.
