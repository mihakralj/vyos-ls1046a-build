## vyos-1x
- T7815: VPP: NAT44 rules with port requires protocol specification and vice versa
   - PR: vyos/vyos-1x#4754
- kea: T7854: Use helper for Kea VRF systemd units
   - PR: vyos/vyos-1x#4744
- smoketest: T7858: add PPPoE client tests with IPv4, IPv6 and DHCPv6-PD
   - PR: vyos/vyos-1x#4760
- boot-config-loader: T7889: Inform user during login of config-load issues
   - PR: vyos/vyos-1x#4763
- syslog: T4251: Add TLS support to syslog
   - PR: vyos/vyos-1x#4734
- op-mode: T7868: fix op-cmd "reset ip arp table" is not working
   - PR: vyos/vyos-1x#4768
- vyos-dhcp: T7895: rename "DHCP Server" column to "Lease Time"
   - PR: vyos/vyos-1x#4764
- T7884: VPP: dependency issue when set interface address and NAT44 address translation interface in one commit
   - PR: vyos/vyos-1x#4761
- op-mode: T7871: add support for op mode command argument constraints
   - PR: vyos/vyos-1x#4758
- T7852: Switch to yescrypt password encryption
   - PR: vyos/vyos-1x#4739
- kea: T7823: DHCP-server lease cannot be cleared
   - PR: vyos/vyos-1x#4741
- container: T7863: Add user-defined MAC option for containers
   - PR: vyos/vyos-1x#4762
- pki: T7885: check_port_availability() can't be used during system boot
   - PR: vyos/vyos-1x#4769


## vyos-build
- Kernel: T5887: update Linux Kernel to v6.6.108
   - PR: vyos/vyos-build#1048
- T7870: Kernel add option CONFIG_NO_HZ_FULL
   - PR: vyos/vyos-build#1045
- T7843: Remove accel-ppp form the packages
   - PR: vyos/vyos-build#1038
- T7873: Bump Suricata version to 7.0.10
   - PR: vyos/vyos-build#1046
- T7878: Using mergify rule to handle conflict checks for private repo
   - PR: vyos/vyos-build#1047
- T7830: Add signing key into vyos-dev.list file, apt-key is deprecated
   - PR: vyos/vyos-build#1051
- T7892: Bump accel-ppp-ng version to d8c4d38
   - PR: vyos/vyos-build#1050
- T7830: Fix GPG key file extension
   - PR: vyos/vyos-build#1052


