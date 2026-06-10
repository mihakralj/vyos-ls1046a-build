# Patches needing forward-port to linux-6.18.x

Parked here during PR 3 because they were authored against linux-6.6.x and
have non-trivial context drift against the new 6.18.28 base.

| Patch | Original target | 6.18.28 status | Action needed |
|---|---|---|---|
| `100-hwmon-ina234-support.patch` | linux-6.6.x | drivers/hwmon/Kconfig + ina2xx.c restructured (now also handles ina260, sy24655). Hunk @2018 misses; hunk @98 misses. | Re-port against 6.18.28 ina2xx.c (which is now a switch over chip IDs); split INA234-specific scaling out. Ideally upstream the original Ian Ray patch via netdev. |

## When to handle

PR 9 (`plans/PATCH-MIGRATION-3WAY.md`) — the existing `git apply --3way` migration
plan covers this systematically. Do NOT hand-fix here in PR 3 / PR 4 (default-flavor
green-build PRs).

## Re-introducing

Once forward-ported, move back into `kernel/common/patches/board/`. Re-run
`bash kernel/common/scripts/integration-test.sh` to confirm `Pass:` count goes up
by exactly 1 and remains green.
