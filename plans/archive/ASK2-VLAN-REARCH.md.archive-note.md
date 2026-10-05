# Archive Note: ASK2-VLAN-REARCH.md

**Date archived:** 2026-10-05 · **Superseded by:** `plans/ASK2-REWRITE-PLAN.md` Phase 1 / E3 (inline FE-VM VLAN path, patches 0215–0218)

The CC-leaf → NADEN → HMTD VLAN path ("Option A", 2026-08-26) was silicon-validated but gave no cross-port benefit over software (2026-10-02). The vendor actually forwards routed VLAN via inline ehash opcodes. That path was revived at `40ace3f0` and fixed on 2026-10-04/05: the ask.ko TCI/TPID byte order, the 0215 `04 11` prefix (0x11 must not be opcode 0), the 0216 stats block, the 0217 vendor 96 B RX margin (needed for push-only), and 0218 BMI discard of physical errors. VLAN↔VLAN bidir now runs at 15.6–15.9 Gbit/s. `ask_vlan_cc.c` (the CC path) has no callers.
