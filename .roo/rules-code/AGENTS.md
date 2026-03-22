# Code Mode Rules (Non-Obvious Only)

- **Only edit `auto-build.yml` for build changes** — it is the single workflow; there are no other CI files
- **Kernel config appended, not replaced:** New `CONFIG_*` lines go at the END of the `printf` block in `auto-build.yml` — `vyos_defconfig` is upstream and our additions are appended after checkout
- **Patch numbering:** `data/vyos-1x-NNN-*.patch` and `data/vyos-build-NNN-*.patch` use 3-digit sequential numbering with gaps (001, 003, 005, 006, 007). Pick the next available number; existing patches are applied in filesystem sort order
- **Patches must use `--no-backup-if-mismatch`** — the workflow applies them with `patch --no-backup-if-mismatch -p1 -d`
- **config.boot.default has NO comments inside blocks** — VyOS config parser fails on `//` and `/* */` inside `{}`. Comments only at file-level outside blocks
- **Console must be `ttyS0` not `ttyAMA0`** — the workflow does `sed -i 's/ttyAMA0/ttyS0/g'` on two upstream files. If adding new serial references, use ttyS0
- **All DPAA1 configs must be `=y`** — never `=m`. FMan needs early init before rootfs mount
- **version.json is CI-managed** — do not manually edit; it's overwritten every build by the publish job
- **DTB goes in `data/dtb/`** — copied to `includes.binary/` during build, lands at ISO root
- **DTS must match nix reference:** `data/dtb/mono-gateway-dk.dts` must have `compatible = "mono,gateway-dk", "fsl,ls1046a"` and ethernet aliases. Canonical source: `nix/pkgs/kernel/dts/mono-gateway-dk.dts`
- **Port remapping uses .link files:** Systemd `.link` files in the workflow match DT node addresses (e.g., `Path=*1ae8000*` → `Name=eth0`). DTS ethernet aliases provide secondary enforcement. Do not change one without updating the other.
- **MOK.key is a secret** — only `MOK.pem` is in the repo; the private key comes from `${{ secrets.MOK_KEY }}`
- **vyos-postinstall is board-gated** — the script checks `/proc/device-tree/compatible` for `fsl,ls1046a` and exits early on non-matching hardware. Safe to include in every ISO.
