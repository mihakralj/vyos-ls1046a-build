# Patches needing forward-port to linux-6.18.x

Parked here during PR 3 because they were authored against linux-6.6.x and
have non-trivial context drift against the new 6.18.28 base.

| Patch | Original target | 6.18.28 status | Action needed |
|---|---|---|---|
| _(none currently parked)_ | | | |

## Completed forward-ports

- **`100-hwmon-ina234-support.patch`** (linux-6.6.x) — forward-ported 2026-06-07
  and re-introduced as
  `kernel/common/patches/board/4002-hwmon-ina2xx-add-ina234-support.patch`
  (linux-6.18.x). The 6.18 `ina2xx.c` is a config-table over chip IDs and
  registers via `devm_hwmon_device_register_with_info`, so the 6.6 patch's
  `ina226_group`/`data->groups[]` probe hunk was dropped (alerts now exposed
  via `has_alerts`) and the bus-voltage scaling was corrected to
  `bus_voltage_shift=4` + `bus_voltage_lsb=1600` (the 6.6 `lsb=25600` matched
  the old `(regval*lsb)>>shift` formula and over-reads 16× under the new
  `(regval>>shift)*lsb` math). See the INA234 bullet in `AGENTS.md`.

## When to handle

PR 9 (`plans/PATCH-MIGRATION-3WAY.md`) — the existing `git apply --3way` migration
plan covers this systematically. Do NOT hand-fix here in PR 3 / PR 4 (default-flavor
green-build PRs).

## Re-introducing

Once forward-ported, move back into `kernel/common/patches/board/`. Re-run
`bash kernel/common/scripts/integration-test.sh` to confirm `Pass:` count goes up
by exactly 1 and remains green.
