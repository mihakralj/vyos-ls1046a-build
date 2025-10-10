## vyos-1x
- T7905: Add system login to config-sync
   - PR: vyos/vyos-1x#4773
- vyos.utils.network: T7898: check if system UUID is available before trying to use it for host identity generation
   - PR: vyos/vyos-1x#4774
- wlb: T7902: remove explicit calls to sudo
   - PR: vyos/vyos-1x#4770
- Revert "bgp: T7760: remove per vrf instance system-as node"
   - PR: vyos/vyos-1x#4778
- T7818: remove uneeded calls of get_cli_kernel_options causing regression
   - PR: vyos/vyos-1x#4777
- T7907: archive config file on first boot to avoid misleading log entry
   - PR: vyos/vyos-1x#4775
- T7800: VPP: Bonding interface fails when change vpp configuration
   - PR: vyos/vyos-1x#4779
- T7803: Make failover route vrf-aware
   - PR: vyos/vyos-1x#4749
- image: T5455: Add migration of SSH  files during upgrade
   - PR: vyos/vyos-1x#4678


## vyos-build
- T7864: Bump HostAP version to 2.11
   - PR: vyos/vyos-build#1044
- T6516: frr: fix isisd advertise-passive-only 
   - PR: vyos/vyos-build#1040
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
- T7857: Add Realtek RTL8126 5Gbit driver
   - PR: vyos/vyos-build#1043


