## vyos-1x
- T7579: fix of the run trigger
   - PR: vyos/vyos-1x#4600
- T7624: smoketest: Fix typing QEMU in test_protocols_static.py
   - PR: vyos/vyos-1x#4597
- T7595: Support PROXY protocol for haproxy
   - PR: vyos/vyos-1x#4586
- T7623: Add test route static dhcp in VRF
   - PR: vyos/vyos-1x#4598
- T7627: fix regression to allow load without arg to load default config
   - PR: vyos/vyos-1x#4606
- T7613: pr mirror workflow manual run option added with closed choice
   - PR: vyos/vyos-1x#4599
- T7625: load-balancing: prune limit key if not configured
   - PR: vyos/vyos-1x#4605


## vyos-build
- T7424: Create vyos.smoketests.hint file before starting vyos-configd
   - PR: vyos/vyos-build#980
- T7424: Restart vyos-configd after vyos.smoketests.hint file creation
   - PR: vyos/vyos-build#983
- Docker: T7568: clean apt cache + clean some /tmp files
   - PR: vyos/vyos-build#979
- iso: T7610: include a file with ISO9660 string to prevent upgrade failures from 1.3.x
   - PR: vyos/vyos-build#985
- T7606: Split VPP test for smoketest
   - PR: vyos/vyos-build#984
- T7579: added workflow for CLA
   - PR: vyos/vyos-build#986
- Docker: T7568: add apt-get update as last command
   - PR: vyos/vyos-build#987
- Kernel: T5887: update Linux Kernel to v6.6.96
   - PR: vyos/vyos-build#988
- CI: T7579: fix of the run trigger for CLA
   - PR: vyos/vyos-build#989


