## vyos-1x
- igmp-proxy: T8295: render appropriate systemd unit file
   - PR: vyos/vyos-1x#5005
- T8302: Add Router Advertisement (RA) base-interface support for IPv6 wildcard prefix derivation.
   - PR: vyos/vyos-1x#5007
- T8120: Add support AMA console for ARM devices
   - PR: vyos/vyos-1x#4915
- T8310: Re-enable service monitoring telegraf for arm64 arch
   - PR: vyos/vyos-1x#5009
- vpp: T8297: Fixed double enabling of VPP
   - PR: vyos/vyos-1x#5008
- T8120: Fix grub for ARM use consistent string comparison operator
   - PR: vyos/vyos-1x#5012
- vpp: T8283: Move bonding interface from vpp section to 'interfaces vpp bonding'
   - PR: vyos/vyos-1x#5001
- vpp: T8318: Consolidate recent migrations into a single downgrade migration (target version 6)
   - PR: vyos/vyos-1x#5013
- T8279: recover config data provided by legacy image upgrade tools
   - PR: vyos/vyos-1x#4999


## vyos-build
- Makefile: T8294: autodetect CPU architecture for smoketests
   - PR: vyos/vyos-build#1122
- T8303: Missing build dependencies for ARM64 radvd package
   - PR: vyos/vyos-build#1123
- T8311: De-hardcode architecture for live-build-config
   - PR: vyos/vyos-build#1124
- T8311: add missing bootstrap binary dependency on dpkg-dev
   - PR: vyos/vyos-build#1125


