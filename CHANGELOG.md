## vyos-1x
- fixup: T7295: add an explicit dependency on tzdata > 2025 to prevent APT from pulling that package from buster
   - PR: vyos/vyos-1x#4421
- T7294: preserve /config symlinks on image upgrade
   - PR: vyos/vyos-1x#4423
- T7291: disabled codeowners review
   - PR: vyos/vyos-1x#4418
- T7286: Add CLI option to disable LDP establish packets
   - PR: vyos/vyos-1x#4416
- syslog: T7270: fix typos in rsyslog.conf
   - PR: vyos/vyos-1x#4409
- T7289: add libvyosconfig as git submodule for builds from private repos
   - PR: vyos/vyos-1x#4417
- T7296: update hash for restrict opam ppx_deriving_yojson <= v3.9.1
   - PR: vyos/vyos-1x#4426
- T7289: fix typo in git submodule status check
   - PR: vyos/vyos-1x#4425
- pki: T7299: race condition for acme requested certificates / CA chain
   - PR: vyos/vyos-1x#4424
- virtual-ethernet: T7293: add support to define interface MTU
   - PR: vyos/vyos-1x#4420
- installer: T7301: remove support for GnuPG signatures
   - PR: vyos/vyos-1x#4429
- login: T7159: limit the "not a production version" to dev builds
   - PR: vyos/vyos-1x#4428
- T7289: fix relative path for git submodule url
   - PR: vyos/vyos-1x#4432
- T7290: Fix VPN IPsec log level processing
   - PR: vyos/vyos-1x#4431


## vyos-build
- T7291: disabled codeowners review
   - PR: vyos/vyos-build#937
- Kernel: T6230: add ipt_NETFLOW out-of-tree module
   - PR: vyos/vyos-build#924
- T7292: add build dependency python3-protobuf
   - PR: vyos/vyos-build#938
- build: T7301: do not include the GPG image signing key in images
   - PR: vyos/vyos-build#940


