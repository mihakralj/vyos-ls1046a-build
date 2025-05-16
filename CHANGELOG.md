## vyos-1x
- haproxy: T7429: remove unsupported logging facility and log level
   - PR: vyos/vyos-1x#4494
- frr: T7411: preserve FRR config on service restart if it exists
   - PR: vyos/vyos-1x#4500
- T7386: firewall: Allow IPv6 member in firewall remote-groups
   - PR: vyos/vyos-1x#4483
- T7157: bgp: Added verification of the route-map existence in vrf import
   - PR: vyos/vyos-1x#4503
- T7443: Un-restricting non-root logins after scheduled reboot/shutdown via pam_nologin
   - PR: vyos/vyos-1x#4502
- T7450: update commit hash for Use PCRE2 instead of PCRE
   - PR: vyos/vyos-1x#4507


## vyos-build
- build: T6949: add missing build trigger for blackbox exporter
   - PR: vyos/vyos-build#960
- docker: T7450: use PCRE2 for vyos-utils and libvyosconfig
   - PR: vyos/vyos-build#964
- Kernel: T7253: build linux-perf- package
   - PR: vyos/vyos-build#961


