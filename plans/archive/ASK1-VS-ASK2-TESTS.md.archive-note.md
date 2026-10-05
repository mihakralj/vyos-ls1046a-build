# Archive Note: ASK1-VS-ASK2-TESTS.md

**Date archived:** 2026-10-05 · **Superseded by:** `bin/testrig-combo-matrix.sh` (dell1–DUT–dell2 rig) and `plans/ASK2-REWRITE-PLAN.md` E3 (vendor `.106` comparison)

The ASK 1.0 (`.110`, OpenWrt) vs ASK2 (`.185`) harness ran on the heidi/HELGA fabric. `.110` is no longer reachable, and the lab moved to the dell1/dell2 SFP+ rig, where the same matrix runs against either `.185` (ASK2) or `.106` (vendor NXP ASK) by moving the cables. The 2026-08-24 sustained results recorded here (14.47 vs 14.17 Gbit/s aggregate) remain valid history. The `ASK1-VS-ASK2-TESTS/artifacts/` directory moved with it; it is gitignored and local only.
