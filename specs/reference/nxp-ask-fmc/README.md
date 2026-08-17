# NXP ASK FMC reference (vendor oracle, not runtime config)

These are **verbatim** vendor FMC configuration files kept as a byte-level
reference oracle for the ASK2 breadth work (see `plans/ASK2-MASTER-PLAN.md`
§4.6). They are **not** used at build time or runtime — ASK2 forbids the FMC /
`dpa_app` / XML-as-runtime-configuration path. They exist only so the ASK2
kernel implementation can be validated against the vendor's proven semantics.

## Provenance

- Source repo: `https://github.com/we-are-mono/ASK`
- Commit: `fe36f30`
- Original path: `dpa_app/files/etc/`
- License: GPL-2.0 (the ASK repo ships `LICENSE` = GNU GPL v2), compatible with
  this project's GPL-2.0 kernel/VyOS stack.

## Files

| File | What it defines |
|---|---|
| `cdx_sp.xml` | NetPDL soft parser: 7 `before` schemas — PPPoE ccbase-slide, OH Ethernet correction, IPv4/IPv6 TTL/hop-limit punt + multicast stop + 6-in-4, UDP NAT-T/ISAKMP punt, TCP SYN/FIN/RST punt, ESP/non-PPPoE policer steering |
| `cdx_pcd.xml` | NetPCD PCD graph: 16 classifications/distributions (tcp4/udp4/tcp6/udp6/esp4/esp6/multicast4/6/ethernet/pppoe/tuple3×4/frag4/frag6), the external-hash tables, header-manip, policer profiles |
| `cdx_cfg.xml`, `cdx_cfg_dgw.xml`, `cdx_cfg_ls1046_rdb.xml` | Port → policy binding variants |

## SHA-256

```
321efa2b33d1a8d5fc2121f0ba0166669e075966f4e24e8de1b7751e7821dbe2  cdx_sp.xml
ad4c3364b0d0708abdedce9b6522876d71833ba054ff5f6a2a7048c42897027c  cdx_pcd.xml
b87921f78ccd79e36daed28efaaabe56cd58f1cf518d74645235ffa3a40a2401  cdx_cfg.xml
60b5473f0fb4499d46a65c6450bb1215877686aa0db5f05aae62d4cb5b18ac66  cdx_cfg_dgw.xml
fef9f06d95cc380f31f1a0a38b03cf92c66b76367b91495406a736db0b45fdf6  cdx_cfg_ls1046_rdb.xml
```

## Usage rule

Treat every literal in these files (`$ccbase + 0x30`, NIA `0x4C0000` /
`0x500002`, FQIDs, MURAM offsets, port IDs) as **vendor-topology-specific**.
ASK2 must resolve its own owned objects and verify readback — never copy a
literal offset into an ASK2 implementation.
