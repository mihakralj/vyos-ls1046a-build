## vyos-1x
- netplug: T7346: only call interface helpers if interface is not removed
   - PR: vyos/vyos-1x#4443
- T7355: cleanup unused Python3 imports
   - PR: vyos/vyos-1x#4448
- T7359: confirm image name is available before proceeding with image installation
   - PR: vyos/vyos-1x#4451
- vyos-router: T7356: unset ANSI bold control character during boot
   - PR: vyos/vyos-1x#4450
- firewall: T7333: Use separate cache keys per inet family
   - PR: vyos/vyos-1x#4440
- kea: T7310: add support for RFC-5417 (option 138)
   - PR: vyos/vyos-1x#4430
- T7321: Replace legacy operations in configsession.py with vyconf client operations
   - PR: vyos/vyos-1x#4445
- T7353: T7360: netplug: behavior change 1.3.8 -> 1.4 when interface with DHCP address looses carrier
   - PR: vyos/vyos-1x#4449
- ids: T7241: remove Fastnetmon from the base system
   - PR: vyos/vyos-1x#4214
- kea: T7281: Add ping-check, use built-in option for classless static routes
   - PR: vyos/vyos-1x#4412
- dhcpv6-client: T6113: add proper startup/shutdown order for systemd units
   - PR: vyos/vyos-1x#4454
- T7343: IPsec add traffic-selector handling for VTI interfaces
   - PR: vyos/vyos-1x#4446
- grub: T7327: honor "system option kernel" settings during image upgrade
   - PR: vyos/vyos-1x#4453


## vyos-build
- vyos-build: T7357: add libnss-mapuser package
   - PR: vyos/vyos-build#945
- T7353: netplug.service is started by vyos-router
   - PR: vyos/vyos-build#944
- kea: T7281: Update Kea to 2.6.1-2
   - PR: vyos/vyos-build#936


