# Phase 6 — Algorithm Extraction Targets

**Status: PLANNED** — the payoff phase. Ranked by value to the project.

## What "extraction" means here

Per target routine: recovered pseudocode + a prose algorithm description in
`decomp/out/NN-<name>.md`, cross-checked against (a) the register/AD-level
contract in `arch/fman-microcode-210-programming-reference.md` and (b) at
least one board observation. The doc gets corrections where the code
contradicts it; qdrant gets the deltas.

## Ranked targets

### 1. FE-VM ehash interpreter loop (highest value)

Regions: dispatch slots 6 (w8574), 7 (w12124), 19 (w8621, 210-only),
22 (w12388); the `0x0421` instruction class.
- Exact EXT_HASH descriptor semantics, HIT/MISS decision chain, MUX/ENQ/EXIT
  dispatch, aging math, collision walk.
- **Root-cause NXP's documented "port-lockup on flow-table collision" known
  bug from source** — currently we only design around its symptoms (F-063
  keysize stall, M3-3b). A source-level cause tells us exactly which
  descriptor shapes are safe.
- Residual EKFC/workspace questions if any remain after fe_probe.

### 2. KeyGen HC handler (slot 1, w605)

How NXP's own SDK stack programs schemes at runtime. Cross-validates
arch doc §4 (indirect FMKG_AR protocol, EKFC/KGSE encodings) against the
consumer the microcode was built for. Cheapest high-confidence target —
documented HCOR semantics + shared with 106/108.

### 3. CC match-table walker (slot 12, w27 region + general CC update slot 3)

Nesting rules, next-engine resolution, per-key stats update protocol,
the ≤3-nested-lookup ceiling's enforcement mechanism.

### 4. Policer path (slot 0, w585)

srTCM/trTCM token-bucket state update math, color marking, the FMPL
EN/STEN relationship (we enable the block driver-side via `plcr_enable_block`;
the code shows what the microcode expects to be already on).

### 5. HC interface in 106/108 (absent in 210)

What exactly was removed: vestigial dispatch entries, dead code, or a clean
excision. Tells us whether any HC path is *callable* on 210 despite
caps bit 3 being clear (DUT probe says no — the code settles why).

### 6. Parser gross-error + BMI FIFO management paths

The failure modes that have bitten this project: deaf-port (accumulated BMI
corruption), F-063 stall, `ecir.fqid=0x0` storms. Where frames leak, where
buffers park, what the timeout paths do.

### 7. Soft-parser sequencer interface (bounded scope)

Only the *interface* — how the 1984-byte soft-parser instruction space is
entered/exited (RSR's `/etc/cdx_sp.xml` runs here). **Not** the soft-parser
ISA itself: documented via FMC tooling and a known time-sink if treated as a
route into controller code.

## Exit criteria for the program

- Targets 1–3 recovered and cross-validated → program succeeded.
- Target 1 root-causes the port-lockup bug → program exceeded.
- Anything beyond targets 1–7 is scope creep; the observability stack covers
  production needs.
