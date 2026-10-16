# CC-ACL offload plan snapshot

Exact pre-consolidation text of `plans/CC-ACL-OFFLOAD-PLAN.md`, archived
2026-10-15. Preserves the completed work: Phase 0 (patch `0109` ntuple bridge
silicon validation, 2026-09-05), the full CC match-walker root-cause hunt
(EKFC ruled out, trailing-byte window, IC dump, `0x2B` GMASK suspicion, Ghidra
walker decode), the `0193` mask-passthrough and ethtool `m MASK` inversion
gotcha, and Phase 1 (patch `0194` exact-match ntuple on ehash records,
silicon-verified 2026-09-06 on `.185` image 0234 — `decomp/findings.md`
entries 17–25, FQID 514/512 differential, two-port isolation). The active plan
keeps only the open Phase 2 static-ACL work.
