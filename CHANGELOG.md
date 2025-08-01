## vyos-1x
- T7175: Added VPP option
   - PR: vyos/vyos-1x#4621
- T7649: Fix use hyphens instead of underscores for kernel options
   - PR: vyos/vyos-1x#4623
- ipsec: T7581: Fix unsupported 'all' protocol in site-to-site tunnels after upgrade to 1.4.x
   - PR: vyos/vyos-1x#4624
- ipsec: T7593: Add dynamic prefix for local and remote traffic selectors
   - PR: vyos/vyos-1x#4608
- qat: T7662: add PCI ID range for Intel C62x virtual function devices
   - PR: vyos/vyos-1x#4626
- isis: T7639: set SRv6 locator in ISIS
   - PR: vyos/vyos-1x#4625
- T7628: Fix non-TPM backed config encryption
   - PR: vyos/vyos-1x#4627
- T7667: Fix smoketest IPsec DPD tests
   - PR: vyos/vyos-1x#4633
- T7668: Fix image update due to None kernel options
   - PR: vyos/vyos-1x#4631
- T7672: fix field entries for paths shared across .xml files
   - PR: vyos/vyos-1x#4636
- T7651: VPP use pid not ppid as default in vpp-failure-handler.service script
   - PR: vyos/vyos-1x#4630
- op-mode: T7527: eliminate bare shell snippets in op mode commands
   - PR: vyos/vyos-1x#4629
- op-mode: T7403: add an option to forcefully remove a container image
   - PR: vyos/vyos-1x#4590
- serial: T7484: treat unavailable serial console devices as non-fatal
   - PR: vyos/vyos-1x#4638
- op-mode: T7403: fix image deletion when a single image ID is provided
   - PR: vyos/vyos-1x#4640
- T7671: move AWS GLB CLI configuration to a separate package
   - PR: vyos/vyos-1x#4634


## vyos-build
- VPP: T7175: Added sflow plugin to build
   - PR: vyos/vyos-build#962
- T7576: Remove unnecessary code for checking dirty build status
   - PR: vyos/vyos-build#993
- T7647: Bump keepalived version to 2.3.3
   - PR: vyos/vyos-build#991
- T7175: Downgraded vpp-sflow version v0.9.02-2
   - PR: vyos/vyos-build#995
- Kernel: T5887: update Linux Kernel to v6.6.100
   - PR: vyos/vyos-build#996
- T7644: mirror workflow refactoring rollout
   - PR: vyos/vyos-build#997
- frr: T7663: add missing build dependencies libnl-3-dev & libpcre3-dev
   - PR: vyos/vyos-build#1000
- T7639: Add patches to fix FRR reload for IS-IS segment routing
   - PR: vyos/vyos-build#998
- tpm: T7628: Extend test for non-TPM backed encryption
   - PR: vyos/vyos-build#999
- tpm: T7628: Fix typo in test expected string
   - PR: vyos/vyos-build#1003
- T7666: Add script to build accel-ppp-ng binaries
   - PR: vyos/vyos-build#1002


