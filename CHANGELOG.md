## vyos-1x
- wireguard: T7246: verify Base64 encoded 32byte boundary on keys
   - PR: vyos/vyos-1x#4402
- T7138: Fix show qos
   - PR: vyos/vyos-1x#4400
- T861: rename Secure Boot MOK (Machine Owner Key) file
   - PR: vyos/vyos-1x#4397
- firewall: T5493: Implement remote-group
   - PR: vyos/vyos-1x#4326
- T7278: Remove cracklib hack from postconfig script template
   - PR: vyos/vyos-1x#4413
- T7246: update hash for strip version on config load
   - PR: vyos/vyos-1x#4415


## vyos-build
- hooks: T7217: remove Dropbear SSH host keys at image build time
   - PR: vyos/vyos-build#933
- T7121: Set up communication vyconfd to vyos-commitd
   - PR: vyos/vyos-build#931
- T7226: Added FRR patch with option that disables LDP hello
   - PR: vyos/vyos-build#932
- T861: minor improvements to secure-boot certificate handling
   - PR: vyos/vyos-build#930
- T7226: Fixed ldpd crash with tLDP message for disabled LDP hello message
   - PR: vyos/vyos-build#934
- T7278: Fix python3-cracklib database creation on update
   - PR: vyos/vyos-build#935


