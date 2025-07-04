## vyos-1x
- T7355: periodical cleanup of unused Python3 import statements
   - PR: vyos/vyos-1x#4571
- T7531: Add FRR no bgp ipv6-auto-ra option
   - PR: vyos/vyos-1x#4568
- pki: T7573: fix TypeError when HAProxy is not in use
   - PR: vyos/vyos-1x#4572
- pki: T7574: add optional force argument to renew certbot-issued certificates
   - PR: vyos/vyos-1x#4573
- T7570: add missing list of scripts to be committed, needed for configdep
   - PR: vyos/vyos-1x#4570
- T7561: simplify op-mode-definitions XML cache generation
   - PR: vyos/vyos-1x#4562
- wan-load-balancing: T7567: Write health-status on first run
   - PR: vyos/vyos-1x#4575
- op-mode: T7560: add support for virtual tag nodes
   - PR: vyos/vyos-1x#4565
- build: T7578: fail the package build if there are non-unique op mode nodes
   - PR: vyos/vyos-1x#4578
- vrf: T7544: Ensure correct quoting for VRF ifnames in nftables
   - PR: vyos/vyos-1x#4581
- build: T7580: add support for standalone and virtual tag nodes to the op mode cache generator
   - PR: vyos/vyos-1x#4580
- T7587: Fix uuidgen warning if DMI doesn't have product_serial or it empty
   - PR: vyos/vyos-1x#4583
- T7589: Add no-split-gso and ack-filter for CAKE
   - PR: vyos/vyos-1x#4584
- T7592: added pyproject.toml for quote linting configuration
   - PR: vyos/vyos-1x#4589
- T7579: added workflow for CLA
   - PR: vyos/vyos-1x#4579
- T7591: remove copyright years from source files
   - PR: vyos/vyos-1x#4585


## vyos-build
- T7424: Create vyos.smoketests.hint file before starting vyos-configd
   - PR: vyos/vyos-build#980
- T7424: Restart vyos-configd after vyos.smoketests.hint file creation
   - PR: vyos/vyos-build#983
- Docker: T7568: clean apt cache + clean some /tmp files
   - PR: vyos/vyos-build#979
- iso: T7610: include a file with ISO9660 string to prevent upgrade failures from 1.3.x
   - PR: vyos/vyos-build#985
- T7606: Split VPP test for smoketest
   - PR: vyos/vyos-build#984


