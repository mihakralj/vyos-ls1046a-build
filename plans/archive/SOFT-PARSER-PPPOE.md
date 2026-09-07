# ASK2 Soft Parser — PPPoE & TTL-Punt Roadmap

**Status:** Roadmap item, not yet scheduled. 2026-07-14.

## Motivation

The FMan v3 hard parser recognizes 16 L2-L4 protocol headers at wire speed. The 210.10.1 microcode extends this with a **soft parser** (cap BIT 4 = `PARSER_SOFTSEQ`): a 1984-byte instruction space that the driver programs with a NetPDL bytecode to extend header recognition beyond the 16 built-in protocols.

For a VyOS router, PPPoE WAN is a mainstream deployment. Today, every PPPoE-encapsulated frame is a guaranteed MISS in the FE-VM ehash path — the hard parser does not recognize PPPoE as a routable L3, so the KeyGen never extracts inner IP fields and the 5-tuple key is garbage.

## Proven on Identical Silicon

NXP's RSR 10.3.0.B1 reference stack (official 5.4-era ASK image for LS1046ARDB) ships a 194-line NetPDL protocol at `/etc/cdx_sp.xml` that proves on identical FMan v3 silicon:

- **PPPoE ccbase-slide:** shifts the CC base pointer so the inner IP header becomes the classification target. Enables offload of PPPoE-encapsulated flows.
- **TTL ≤ 1 kernel-punt:** drops frames with TTL=1 before any CC or FE cycles are spent, forwarding TTL-expired traffic to the kernel for ICMP generation.
- **6-in-4 re-dispatch:** re-parses tunneled IPv6 inside IPv4 as a separate OH-port dispatch.

## What to Port

From `cdx_sp.xml` (semantics reverse-engineered, stored in Qdrant):

| Feature | Lines | Impact |
|---|---|---|
| PPPoE schema (PPPoE discovery + session recognition, ccbase-slide) | ~80 | Unblocks PPPoE WAN offload |
| TTL≤1 punt hook | ~30 | Avoids wasting FE-VM cycles on expiring frames |
| 6-in-4 dispatch | ~40 | Enables tunneled-IPv6 classification |
| ESP/PLCR steer | ~30 | Skip until IPsec offload matters |

**Total porting surface:** ~150 NetPDL lines for the PPPoE + TTL-punt core.

## Integration Points

- `fman_pcd_prs.c` — The mainline driver already consumes soft-parser sequences (cap BIT 4 is set). The soft-parser instruction space is written at PCD init time.
- `fman_pcd_fe.c` — Flow insertion already handles MISS for unrecognized frames. PPPoE frames that bypass the soft parser remain on the MISS path (safe degradation).
- No changes to the FE-VM ehash path or DDR flow store are required.

## Dependency

Soft parser is a **P2 item** — it extends classification coverage but does not gate GA throughput. It can be developed independently of the P0 (keysize/self-test) and P1 (multi-FQ, eth3 PHY) items.

## Cross-References

- `arch/fman-microcode-210-programming-reference.md` §13 item 2 (Soft Parser inventory)
- `specs/fman-keygen-flow-key-spec.md` v3.1
- Qdrant: `cdx_sp.xml` semantics, PPPoE ccbase-slide, NetPDL instruction format
- NXP RSR 10.3.0.B1: `RSR/ls1046a-rdb/` in this tree
