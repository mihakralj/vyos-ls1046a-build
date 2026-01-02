## vyos-1x
- bond: T8084: disallow bond members that do not support MAC changes
   - PR: vyos/vyos-1x#4911
- vpp: T8080: Fix handling of configuration system lock after vpp commit failure
   - PR: vyos/vyos-1x#4904
- vpp: T7203: Add op-mode to show bridge-domain
   - PR: vyos/vyos-1x#4913
- T8010: Added the vyos_vpp plugin to the accel-pppd's template
   - PR: vyos/vyos-1x#4910
- bond: T2416: support hot-add/remove of bond member interfaces
   - PR: vyos/vyos-1x#4917
- config: T8124: make get_config_dict() pki={} node purely optional
   - PR: vyos/vyos-1x#4918
- T8133: BGP add local-rib feature for BMP
   - PR: vyos/vyos-1x#4922
- T8100: Refactor smoke test workflows for GitHub's pull_request_target policy change
   - PR: vyos/vyos-1x#4914
- T8100: smoke test workflow fixed whitespace handling with json
   - PR: vyos/vyos-1x#4926


## vyos-build
- T8119: Bump accel-ppp-ng version to 474b63d
   - PR: vyos/vyos-build#1085
- T8121: Bump net-snmp version to 5.9.5.2
   - PR: vyos/vyos-build#1086
- T8100: updated workflows for pull_request_target policy updates
   - PR: vyos/vyos-build#1087
- T8111: cloud-init: Implement smoketests for testing
   - PR: vyos/vyos-build#1083
- T8117: Bump hsflowd version to v2.1.21-1 to fix sample collection on subinterfaces
   - PR: vyos/vyos-build#1084
- Revert "T8121: Bump net-snmp version to 5.9.5.2"
   - PR: vyos/vyos-build#1091
- T8132: Update the APT mirror list before a package build
   - PR: vyos/vyos-build#1089
- Makefile: T8111: add missing --iso argument for testraid, testsb and testtpm
   - PR: vyos/vyos-build#1093


