## vyos-1x
- smoketest: T7248: ensure there is no Yacc/Bison error in wide-dhcpv6-client
   - PR: vyos/vyos-1x#4394
- T5400: initialize OPAM environment where it's really needed
   - PR: vyos/vyos-1x#4393
- T7247: removed pr number input for target repo mirror workflow call
   - PR: vyos/vyos-1x#4395
- console-server: T7217: generate Dropbear SSH keys if they do not exist
   - PR: vyos/vyos-1x#4401
- T6353: Add password complexity validation for system login user
   - PR: vyos/vyos-1x#4390
- bgp: T7157: Allow using route-maps for VRF route leaking in BGP
   - PR: vyos/vyos-1x#4404
- T7252: Allow vpptun and vpptap for constraint validator
   - PR: vyos/vyos-1x#4399
- pki: T7249: fix shebang to support CLI backend
   - PR: vyos/vyos-1x#4405
- T7121: Set up communication vyconfd to vyos-commitd
   - PR: vyos/vyos-1x#4398
- T7246: do not pass unneeded version string to parser
   - PR: vyos/vyos-1x#4406
- T7246: update libvyosconfig hash and add nosetest
   - PR: vyos/vyos-1x#4407
- wireguard: T7246: verify Base64 encoded 32byte boundary on keys
   - PR: vyos/vyos-1x#4402
- T7138: Fix show qos
   - PR: vyos/vyos-1x#4400
- T861: rename Secure Boot MOK (Machine Owner Key) file
   - PR: vyos/vyos-1x#4397


## vyos-build
- T7233: Fix wrong MOK certs path in the script of build-kernel.sh
   - PR: vyos/vyos-build#925
- Docker: T5400: do not initialize OPAM environment at all
   - PR: vyos/vyos-build#928
- T7247: removed pr number from private mirror workflow call
   - PR: vyos/vyos-build#929
- hooks: T7217: remove Dropbear SSH host keys at image build time
   - PR: vyos/vyos-build#933
- T7121: Set up communication vyconfd to vyos-commitd
   - PR: vyos/vyos-build#931
- T7226: Added FRR patch with option that disables LDP hello
   - PR: vyos/vyos-build#932
- T861: minor improvements to secure-boot certificate handling
   - PR: vyos/vyos-build#930


