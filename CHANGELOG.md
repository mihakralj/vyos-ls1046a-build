## vyos-1x
- T7397: add "system kernel option quiet" to suppress boot messages
   - PR: vyos/vyos-1x#4477
- tech-support: T7410: handle possible errors when executing lsusb
   - PR: vyos/vyos-1x#4479
- T7382: adds podman log driver configuration option
   - PR: vyos/vyos-1x#4464
- node_exporter: T7416: Add missing backslash in node_exporter.service
   - PR: vyos/vyos-1x#4486
- https: T7393: set listen-address bind fails silently without restart
   - PR: vyos/vyos-1x#4485
- session: T6696: 'clear session' to 'reset session'
   - PR: vyos/vyos-1x#4476
- T7364: Fixing Route reflector client check not working for peer-group
   - PR: vyos/vyos-1x#4452
- T7396: Return the old script to generate tech-support archive
   - PR: vyos/vyos-1x#4472
- installer: T7420: pass image download credentials in environment variables
   - PR: vyos/vyos-1x#4489
- router-advert: T7389: Duplicate prefix safeguard
   - PR: vyos/vyos-1x#4470
- T7412: Allow privileged containers
   - PR: vyos/vyos-1x#4481
- T7408: add mokutil in arm64
   - PR: vyos/vyos-1x#4478
- bgp: T7157: Fixed error with the unknown key in the verification
   - PR: vyos/vyos-1x#4488
- Revert "vyos-router: T7356: unset ANSI bold control character during boot"
   - PR: vyos/vyos-1x#4492
- T7122: pki: unable to switch from custom cert to ACME when HAProxy service is running with 'redirect-http-to-https' option
   - PR: vyos/vyos-1x#4480
- installer: T7049: Fix GRUB boot with RAID1
   - PR: vyos/vyos-1x#4387
- interfaces: T7268: Add op-mode command for show all interfaces on system
   - PR: vyos/vyos-1x#4447
- frr: T7431: missing logging options after rewrite to frrender class
   - PR: vyos/vyos-1x#4495
- T7417: check existence of paths before set_tag/return_value in migration scripts vrf/1-to-2; vrf/2-to-3
   - PR: vyos/vyos-1x#4493
- utils: T7095: make  and  arguments aware of the shell
   - PR: vyos/vyos-1x#4323
- nat66: T7051: snat group as destination
   - PR: vyos/vyos-1x#4484


## vyos-build
- systemd: T7356: use short service names to avoid truncation
   - PR: vyos/vyos-build#959
- Kernel: T7428: remove io_uring support
   - PR: vyos/vyos-build#958
- T7406: add virtio-gpu and pl011 console support for arm64
   - PR: vyos/vyos-build#955


