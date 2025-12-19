## vyos-1x
- op-mode: T7810: fix broken 'reset connection' command
   - PR: vyos/vyos-1x#4888
- salt: T8056: add a deprecation warning
   - PR: vyos/vyos-1x#4885
- tech-support: T7134: add topology snapshot generation using  package
   - PR: vyos/vyos-1x#4891
- T8011: VPP fix log duplication in systemd journal
   - PR: vyos/vyos-1x#4882
- vpp: T7819: do not override driver if it is already done
   - PR: vyos/vyos-1x#4857
- T8078: dhcpv6: allow lease renew for pd & parameters
   - PR: vyos/vyos-1x#4889
- vpp: T7972: Make  feature automatically configurable
   - PR: vyos/vyos-1x#4880
- smoketest: T8087: reorganize folderstructure for embedded configttests
   - PR: vyos/vyos-1x#4893
- firewall: T8089: "geoip country-code" should get a completion helper
   - PR: vyos/vyos-1x#4894
- op-mode: T8096: fix command names that contain capital letters
   - PR: vyos/vyos-1x#4897
- isis: T8094: bugfix config migration from 1.3.0-rc1 -> 1.4
   - PR: vyos/vyos-1x#4898
- T8100: Update pull_request_target branch filters for GitHub Actions policy change
   - PR: vyos/vyos-1x#4900
- ssh: T8090: T8098: add support for config test mode (sshd -t)
   - PR: vyos/vyos-1x#4896
- T8103: add root to those allowed to call op-run commands directly
   - PR: vyos/vyos-1x#4901
- login: T8086: replace getpwall() based user enumeration to avoid NSS/TACACS timeouts
   - PR: vyos/vyos-1x#4892
- T8026: Fixed session commit for the generate router
   - PR: vyos/vyos-1x#4899
- vpp: T7954: Add op-mode commands to show LACP
   - PR: vyos/vyos-1x#4890
- T7995: Add capability to start VPP dataplane during system deployment
   - PR: vyos/vyos-1x#4875
- ssh: T8098: migrate "rijndael-cbc@lysator.liu.se" to "aes256-cbc" cipher
   - PR: vyos/vyos-1x#4903
- T8082: VPP fails to start with buffers page-size 1G
   - PR: vyos/vyos-1x#4895


## vyos-build
- T8085: Do not hardcode values in kernel build scripts
   - PR: vyos/vyos-build#1075
- smoketest: T8087: reorganize folderstructure for embedded configttests
   - PR: vyos/vyos-build#1076
- smoketest: T8087: bugfix missing f-ormat string indicator
   - PR: vyos/vyos-build#1077
- T8101: Add secure boot Kernel kexec options
   - PR: vyos/vyos-build#1078
- T8106: ARM: Add missing kernel modules for Marvell CN9130 platform support
   - PR: vyos/vyos-build#1079


