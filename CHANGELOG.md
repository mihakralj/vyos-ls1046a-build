## vyos-1x
- T8232: Simplify and extend config_dict construction
   - PR: vyos/vyos-1x#4971
- vpp: T8255: Changed logging level variable name
   - PR: vyos/vyos-1x#4983
- vpp: T8254: Move 'nat44' and 'settings nat44' sections to 'nat nat44'
   - PR: vyos/vyos-1x#4985
- vpp: T8262: Refactor IPsec settings to use only 'ipsec-acceleration' flag
   - PR: vyos/vyos-1x#4987
- vpp: T8258: Move  out of  section
   - PR: vyos/vyos-1x#4986
- lcd: T8213: add support for MTC S16209x
   - PR: vyos/vyos-1x#4989
- T8270: fix yescrypt hash validation regex
   - PR: vyos/vyos-1x#4990
- bgp: T7984: add CLI support for "neighbor <N> remote-as auto"
   - PR: vyos/vyos-1x#4988
- xml: T8271: cleanup duplicate includes and split duplicate code into new building blocks
   - PR: vyos/vyos-1x#4991
- vpp: T8268: Unify CPU settings into a single 'cpu-cores' node under 'resource-allocation'
   - PR: vyos/vyos-1x#4993
- T8257: image install search previous needs to consider legacy bind mount
   - PR: vyos/vyos-1x#4984
- syslog: T8272: remove dead code from previous "file" logging support
   - PR: vyos/vyos-1x#4996
- vpp: T8266: Add global  setting and remove per-interface commands
   - PR: vyos/vyos-1x#4995
- vpp: T8274: Remove dpdk-options section for num-* parameters
   - PR: vyos/vyos-1x#4998
- srv6: T6977: add srv6 encapsulation source-address
   - PR: vyos/vyos-1x#4961
- vpp: T8261: Refactor resource settings into 'resource-allocation' section
   - PR: vyos/vyos-1x#4997
- T6868: Monitoring Loki Basic Authentication Password Length Limit Inc…
   - PR: vyos/vyos-1x#5000


## vyos-build
- T8219: Start building Salt from source. Update to v3006.21, remove Salt repo keys
   - PR: vyos/vyos-build#1119
- T8126: FRR add no traffic-eng command
   - PR: vyos/vyos-build#1120
- T8265: leave config path hint in raw image install_image
   - PR: vyos/vyos-build#1117
- T8267: Extend the vyos-build repo to include Ansible install phony target
   - PR: vyos/vyos-build#1118


