## vyos-1x
- firewall: T7370: Add conntrack log commands
   - PR: vyos/vyos-1x#4459
- T7334: pr mirror trigger workflow added with label creation permission for default github token
   - PR: vyos/vyos-1x#4462
- T6773: RFC-2136 support for Kea DHCP4 server
   - PR: vyos/vyos-1x#4153
- ospf: T7383: Fixed unconfigured redistribution of nhrp into ospf
   - PR: vyos/vyos-1x#4466
- bridge: T7322:  fix slow performance of allowed vlan
   - PR: vyos/vyos-1x#4444
- interface: T4627: support setting of IPv6 Interface Identifier(Token)
   - PR: vyos/vyos-1x#4392
- geoip: T5636: Add geoip for policy route/route6
   - PR: vyos/vyos-1x#4419
- interface: T7375: cleanup SLAAC assigned address and default route after removing SLAAC CLI configuration
   - PR: vyos/vyos-1x#4461
- syslog: T7367: ensure rsyslog is registered as default systemd syslog service
   - PR: vyos/vyos-1x#4460
- interface: T4627: not every interface type supports IPv6 interface-identifiers
   - PR: vyos/vyos-1x#4467
- dhclient: T6253: Respect 
   - PR: vyos/vyos-1x#4465
- router-advert: T7380: Implement auto-ignore-prefix syntax for router advertisements
   - PR: vyos/vyos-1x#4463
- T7316: Add MTU validation for interfaces with MTU less then 1200
   - PR: vyos/vyos-1x#4442
- T7282: op-mode: show firewall group filtering and tab completion update
   - PR: vyos/vyos-1x#4414
- bgp: T7220: Add the option to disable enforce-first-as at peer level
   - PR: vyos/vyos-1x#4469
- firewall: T7358: add offload option to global state policy
   - PR: vyos/vyos-1x#4457
- T7394: add system image raises not iterable error
   - PR: vyos/vyos-1x#4471
- smoketest: T7400: fix unbound variable when checking VXLAN remote and group settings
   - PR: vyos/vyos-1x#4475


## vyos-build
- T6322: Include microcode in amd64 architecture builds
   - PR: vyos/vyos-build#922
- build: T7241: remove fastnetmon from the list of services to be disabled by default
   - PR: vyos/vyos-build#947
- T7334: pr mirror trigger workflow added with label creation permission for default github token
   - PR: vyos/vyos-build#950
- radvd: T7376: upgrade package to v2.20
   - PR: vyos/vyos-build#949
- build: T7372: correctly quote lb_config arguments
   - PR: vyos/vyos-build#948
- T7367: syslog.service links to rsyslog.service - thus disable it, too
   - PR: vyos/vyos-build#951
- Kernel: T5887: update Linux Kernel to v6.6.87
   - PR: vyos/vyos-build#952
- T7384: Radius patch for Dns-Server-IPv6-Address attribute - accel-ppp
   - PR: vyos/vyos-build#926
- T7347: Update prometheus exporters
   - PR: vyos/vyos-build#943


