## vyos-1x
- pppoe-server: T8143: Set 'vpp-cp' option automatically if interface is in VPP
   - PR: vyos/vyos-1x#4945
- T7866: Fix not all CPUs have model name key
   - PR: vyos/vyos-1x#4956
- T6734: Nginx disable software version reporting
   - PR: vyos/vyos-1x#4957
- op-mode: T8154: fix tcpdump KeyboardInterrupt on Ctrl+C
   - PR: vyos/vyos-1x#4951
- vpp: T8202: Remove XDP driver and options from CLI and config
   - PR: vyos/vyos-1x#4955


## vyos-build
- T8162: Migrate to ipt-NETFLOW fork
   - PR: vyos/vyos-build#1099
- T8174: Disable CHELSIO_FCOE kernel module due to issues
   - PR: vyos/vyos-build#1100
- T8065: Dehardcode qemu settings to allow run tests on arch arm64
   - PR: vyos/vyos-build#1092
- T8181: Add kernel CONFIG_MODULE_SIG for arm64 architecture
   - PR: vyos/vyos-build#1102
- T8185: correct the install path of config.boot.default entries defined in flavor files
   - PR: vyos/vyos-build#1103
- apt: T8092: remove the Kea repository key
   - PR: vyos/vyos-build#1107
- T7664: Upgrade frr 10.5
   - PR: vyos/vyos-build#1090
- build: T8193, T8194: improve error handling in the build script
   - PR: vyos/vyos-build#1104
- T8135: backport the fix for CVE-2025-68615 (DoS in snmptrapd)
   - PR: vyos/vyos-build#1105
- T8198: Use zabbix-agent2 from Debian backports
   - PR: vyos/vyos-build#1108
- Kernel: T8203: Update Linux Kernel to 6.6.121
   - PR: vyos/vyos-build#1109
- T8210: ARM: Add missing kernel modules for Marvell CN9130 platform
   - PR: vyos/vyos-build#1111


