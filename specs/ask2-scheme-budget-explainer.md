# ASK2 Table and Scheme Budget — Plain Explainer

**Status:** Companion explainer  
**Date:** 2026-08-21  
**Full design:** `specs/ask2-shared-table-multi-protocol-design.md`

This is the short explanation of the corrected ASK2 scaling design.

The vendor's `shared="true"` classification means a shared software/FMC definition. The vendor then creates a distinct `HTNode` and table instance for every port that uses that definition. ASK2 should follow the same ownership model.

## The governing equation

```text
schemes_used ≈ Σ over engaged ports ( distinct CCOBASE-classes that port carries )
```

Every lever reduces one of two factors:

- fewer distinct key/CCOBASE classes;
- fewer ports carrying a given class.

Tables and schemes are related but are not the same resource:

- tables hold records in DDR;
- schemes are the scarce 32-entry hardware selector/extractor resource.

Per-port table instances improve correctness and ownership. They do not reduce scheme count.

## Lever 1 — Fold L4 protocol into the key

TCP and UDP do not need separate classes. The routed key contains the L4 protocol byte, so:

- one IPv4 table instance per port holds IPv4 TCP and UDP;
- one IPv6 table instance per port holds IPv6 TCP and UDP.

This halves the L4 class count compared with separate TCP/UDP tables.

## Lever 2 — Define classes by fixed key layout

A CCOBASE class corresponds to one fixed comparison-key layout, not simply one protocol name.

| Class | Key layout | Folded traffic |
|---|---|---|
| Routed IPv4 | 14-byte IPv4 5-tuple key | IPv4 TCP, UDP |
| Routed IPv6 | 38-byte IPv6 5-tuple key | IPv6 TCP, UDP |
| ESPv4 | 10-byte vendor-reference key | ESP over IPv4 |
| ESPv6 | 22-byte vendor-reference key | ESP over IPv6 |
| Ethernet/L2 | 15-byte vendor-reference key, refined for bridge/VLAN domain | Bridge forwarding |

ESPv4 and ESPv6 remain separate initially because their fixed key sizes differ. A unified padded ESP key is a future experiment, not a current assumption.

## Lever 3 — Arm classes per port on demand

A port gets a table instance and scheme only for classes it actually carries:

- IPv4 when ASK routed offload is enabled;
- IPv6 only on dual-stack ports after the IPv6 family-selection path is production-safe;
- ESP only on IPsec endpoints;
- L2 only on bridge-member ports.

Real routers are asymmetric, so most ports do not need every class.

## Lever 4 — Use one catch-all per port when needed

When protocol schemes use non-zero match vectors, non-IP traffic still needs a safe path for ARP, neighbour discovery, multicast, and unsupported L2 traffic.

One catch-all scheme per affected port covers these frames. Do not allocate one fallback per protocol.

A pure IPv4-only port that retains a single `kgse_mv=0` scheme does not need an additional catch-all.

## Per-port table ownership

For a port carrying IPv4, IPv6, and ESP:

```text
port's RCCB node group:
   node@RCCB+0×16 → this port's IPv4 table instance ← IPv4 scheme
   node@RCCB+1×16 → this port's IPv6 table instance ← IPv6 scheme
   node@RCCB+2×16 → this port's ESP table instance  ← ESP scheme
   catch-all                                            ← kernel fallback
```

The class definitions are shared, but the table objects and records are not:

```text
eth3 IPv4 table != eth4 IPv4 table
eth3 IPv6 table != eth4 IPv6 table
```

This has three major benefits:

1. Identical tuples on different ingress ports cannot alias.
2. Disengaging one port cannot delete another port's records.
3. ASK2 does not depend on an unproven non-zero PORT_ID byte from `kgse_dv0/dv1`.

## Worst-case scheme budget

If every port carried every initial class:

```text
5 ports × (IPv4 + IPv6 + ESPv4 + ESPv6 + L2 + catch-all)
= 5 × 6
= 30 total schemes
```

The 30 includes the five baseline RSS schemes that ASK2 repurposes as the ports' IPv4 schemes. It is not 30 new allocations.

This fits under 32 but leaves almost no headroom, so it is a maximum configuration rather than the normal design point.

## Realistic on-demand budget

Example topology:

```text
WAN  eth4: IPv4 + IPv6 + ESPv4 + ESPv6 + catch-all = 5
LAN  eth0: IPv4 + IPv6 + catch-all                   = 3
LAN  eth1: IPv4 + IPv6 + catch-all                   = 3
LAN  eth2: IPv4 + IPv6 + catch-all                   = 3
brdg eth3: IPv4 + L2 + catch-all                     = 3
                                                     ----
                                                     17 schemes
```

A pure IPv4-only port may use only one scheme, so actual deployments can be smaller.

## Table count

Per-port instances multiply tables, but table records and buckets live in DDR. For the maximum initial set:

```text
5 ports × 5 table classes = 25 table instances
```

Node-row MURAM cost is small:

```text
25 nodes × 16 bytes = 400 bytes
```

DDR bucket arrays are the main per-instance cost and must be sized by expected flow count. Do not copy the vendor's maximum bucket mask to every port without a budget.

## What exists today

| Piece | Status |
|---|---|
| Routed IPv4 key and table implementation | Working on eth3/eth4, currently one global table object |
| Routed IPv6 table plumbing | Allocated and default-OFF; multi-port family selection is not production-safe |
| Per-port schemes and CCOBASE | Implemented for routed classes |
| Per-port table registry | Not implemented |
| Five-port CLI mapping | Not implemented; current CLI allows eth3/eth4 only |
| Per-egress TX FQ resolution | Present, but legacy global eth4 `dedicated_fq` must be removed/generalized |
| ESP tables/actions | Not implemented |
| L2/bridge table/action | Not implemented |
| Catch-all | Experimental implementation exists with IPv6 scheme work |
| Software fallback | Working and required |

## Important caveats

1. Start with five-port routed IPv4. It is the only production-proven class.
2. The current live IPv6 LCV-split path has caused real multi-port failures and must not be treated as production-ready.
3. ESP and bridge are design classes only until each has one silicon-confirmed HIT.
4. Table replication solves ownership and aliasing, but it does not save schemes.
5. Scheme admission must be atomic. A failed class reservation leaves that class in software.
6. Eth0 stays the management lifeline until eth1 or eth2 passes the full 1G test suite.

## Bottom line

The corrected design does not try to save schemes by sharing one DDR table across all ports. It follows the vendor ownership model:

- share class definitions;
- instantiate tables per port;
- keep schemes per port and class;
- fold protocols only when they share one fixed key layout;
- arm classes only where needed;
- keep one catch-all per affected port;
- fall back to software whenever hardware admission or validation fails.

A realistic five-port router with routed IPv4/IPv6, IPsec only on the WAN, and one bridge member uses about 17 schemes — comfortably below 32 — while preserving clean per-port table ownership.

## See also

- `specs/ask2-shared-table-multi-protocol-design.md` — full design proposal, rollout plan, acceptance gates, risks, and rejected alternatives.
- `plans/ASK2-MASTER-PLAN.md` — execution sequencing and live blockers.
- `/mnt/build/ASK/patches/fmc/01-mono-ask-extensions.patch` — vendor `replicateHtNodes()` precedent.
