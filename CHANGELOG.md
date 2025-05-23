## vyos-1x
- T7414: Fix conntrack ignore rules for using several ports
   - PR: vyos/vyos-1x#4510
- T7458: Fix VPN IPsec unexpected passthrough logic bug
   - PR: vyos/vyos-1x#4509
- bonding: T7466: fix the 802.3ad regex
   - PR: vyos/vyos-1x#4517
- snmp: T7464: fix the community string validation regex for compatibility with PCRE2
   - PR: vyos/vyos-1x#4516
- openconnect: T7287:  VPN Openconnect does not check dictionary key se…
   - PR: vyos/vyos-1x#4513
- policy: T5069: large-community-list regex validator disallows whitespace
   - PR: vyos/vyos-1x#4482
- xml: T7467: remove ^/$ wrapping from validation regexes
   - PR: vyos/vyos-1x#4518
- wireguard: T7387: Optimise wireguard peer handling
   - PR: vyos/vyos-1x#4468
- T7335: Fix typo for HAproxy help redirect-location path
   - PR: vyos/vyos-1x#4511
- T7348: Add config CPU thread-count for accel-ppp services
   - PR: vyos/vyos-1x#4499
- opennhrp: T7462: Removed unused opennhrp files and configurations
   - PR: vyos/vyos-1x#4519
- prometheus: T7435: Ensure only configured exporters are started
   - PR: vyos/vyos-1x#4498
- pppoe: T7463: Added restart if CoA is changed
   - PR: vyos/vyos-1x#4515
- T7445: added open prs conflict checker caller workflow
   - PR: vyos/vyos-1x#4520
- T1771: automatic reboot of system into previous image
   - PR: vyos/vyos-1x#4501
- ipoe_server: T6997:  Do not require to create client ip pool when dhcp-relay is used
   - PR: vyos/vyos-1x#4514
- ipoe_server: T7472:  Add validation for giaddr if dhcp-relay is defined
   - PR: vyos/vyos-1x#4521
- T7363: Add vyconf aware initialization of Config
   - PR: vyos/vyos-1x#4505


## vyos-build
- T1771: enable "reboot-on-upgrade-failure" for new VyOS installations
   - PR: vyos/vyos-build#965
- T7352: add arg to test script for running smoketests under vyconfd/commitd
   - PR: vyos/vyos-build#963


