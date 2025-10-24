## vyos-1x
- smoketest: T7657: check for Kernel option CONFIG_SLUB_DEBUG
   - PR: vyos/vyos-1x#4798
- T7941: fix DHCP client running in VRF with non-word characters
   - PR: vyos/vyos-1x#4800
- kea: T7821: Fix subnet-id accepted range
   - PR: vyos/vyos-1x#4801
- T7942: consistent naming of "memory" in op-mode
   - PR: vyos/vyos-1x#4799
- frrender: T7927: de-nest DHCP and PPPoE interface section for VRFs
   - PR: vyos/vyos-1x#4797
- T7929: VPP: nat44: validate that only self-twice-nat external address is in translation pool
   - PR: vyos/vyos-1x#4805
- T7930: VPP: Changing NAT44 settings resets  to False
   - PR: vyos/vyos-1x#4795
- T5942: Make failover support dhcp-interface
   - PR: vyos/vyos-1x#4783
- T7946: log redirected stdout from FRRender
   - PR: vyos/vyos-1x#4802
- kea: T7925: Improve error handling, validate IPv6 PD prefix length
   - PR: vyos/vyos-1x#4792
- T7948: always call setUp() and tearDown() base class methods
   - PR: vyos/vyos-1x#4803
- frr: T7664: properly set log configuration during daemon startup
   - PR: vyos/vyos-1x#4804
- T3680: protocols: add dhclient hooks for dhcp-interface static routes
   - PR: vyos/vyos-1x#4622


## vyos-build
- build: T7828: use tomli instead of toml in the package build script
   - PR: vyos/vyos-build#1061


