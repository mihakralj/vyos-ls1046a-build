## vyos-1x
- op-mode: T8350: Move VPP op-mode 'show vpp interfaces' to 'show interfaces vpp'
   - PR: vyos/vyos-1x#5033
- T8347: enable pylint for all files under python/* path
   - PR: vyos/vyos-1x#5031
- op-mode: T8362: "compare" command lacks catch_broken_pipe decorator
   - PR: vyos/vyos-1x#5037
- vpp: T8354: Move 'ignore-kernel-routes' option out of resource-allocation section
   - PR: vyos/vyos-1x#5034
- T8370: VPP cosmetic fix for verify buffers path
   - PR: vyos/vyos-1x#5040
- vpp: T8356: Fix traceback when adding a VPP bridge without members
   - PR: vyos/vyos-1x#5038
- T8371: VPP add op-mode command show runtime
   - PR: vyos/vyos-1x#5042
- vyos.ifconfig: T8358: clear qdiscs when deleting mirror CLI node
   - PR: vyos/vyos-1x#5036
- T8357: Allow prefix vpp for show interfaces
   - PR: vyos/vyos-1x#5044
- vpp: T8276: Add verification for virtual interfaces in PPPoE server configuration
   - PR: vyos/vyos-1x#5021
- T8186: netflow: disable IPv6 for netflow protocol version 5
   - PR: vyos/vyos-1x#5035
- vpp: T8342: Add verification of members for bond and bridge interfaces
   - PR: vyos/vyos-1x#5028
- T8351: VPP add GRE tunnel key configuration
   - PR: vyos/vyos-1x#5045
- openvpn: T8304: fix migration for des, bf128 or bf256 cipher; use 3des instead
   - PR: vyos/vyos-1x#5029


## vyos-build
- T8363: Ensure FRR does not see duplicate local routes on interface changes
   - PR: vyos/vyos-build#1136
- T8373: check-qemu-install: increase BOOTLOADER_SLEEP time
   - PR: vyos/vyos-build#1137
- T8334: arm64: updating image via 'add system image' fails with vmlinuz not found error
   - PR: vyos/vyos-build#1138
- oci: T8366: remove features from container image which are not supported
   - PR: vyos/vyos-build#1135


