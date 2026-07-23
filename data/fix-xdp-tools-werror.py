#!/usr/bin/env python3
# fix-xdp-tools-werror.py — inject a source fix for xdp-tools 1.5.5's
# iface_get_xdp_feature_flags() build failure into VPP's external package
# build recipe (build/external/packages/xdp-tools.mk).
#
# xdp-tools 1.5.5's lib/util/util.c defines:
#     int iface_get_xdp_feature_flags(int ifindex, __u64 *feature_flags)
#     {
#     #ifdef HAVE_LIBBPF_BPF_XDP_QUERY
#         ... uses ifindex, feature_flags ...
#     #else
#         return -EOPNOTSUPP;   // neither parameter used on this branch
#     #endif
#     }
# xdp-tools' own build (lib/defines.mk) unconditionally appends
# "-Wextra -Werror" to CFLAGS, so when HAVE_LIBBPF_BPF_XDP_QUERY isn't
# defined (as happens building the vendored static libbpf here), this
# is a hard build failure, not a warning. Run this script against a
# freshly-checked-out VPP tree (cwd = scripts/package-build/vpp/vpp,
# i.e. one level up from build/external/) before its own build_cmd
# calls `make ... pkg-deb` — it inserts a sed step into
# xdp-tools_build_cmds that marks both parameters explicitly unused in
# the downloaded tarball, before xdp-tools' own Makefile runs.
import sys
from pathlib import Path

mk_path = Path('build/external/packages/xdp-tools.mk')
src = mk_path.read_text()

old_cmd = (
    "define  xdp-tools_build_cmds\n"
    "\t@cd ${xdp-tools_src_dir} && $(MAKE) CC=gcc V=1 BUILD_STATIC_ONLY=y "
    "> $(xdp-tools_build_log)\n"
    "endef\n"
)
fix_line = (
    "\t@sed -i "
    "'s/int iface_get_xdp_feature_flags(int ifindex, __u64 \\*feature_flags)/"
    "int iface_get_xdp_feature_flags(int ifindex __attribute__((unused)), "
    "__u64 *feature_flags __attribute__((unused)))/' "
    "${xdp-tools_src_dir}/lib/util/util.c\n"
)
new_cmd = (
    "define  xdp-tools_build_cmds\n"
    + fix_line
    + "\t@cd ${xdp-tools_src_dir} && $(MAKE) CC=gcc V=1 BUILD_STATIC_ONLY=y "
    "> $(xdp-tools_build_log)\n"
    "endef\n"
)

if new_cmd in src:
    print(f"### {mk_path}: fix already present — no-op")
    sys.exit(0)

if old_cmd not in src:
    print(f"ERROR: {mk_path} xdp-tools_build_cmds block doesn't match expected "
          "text — xdp-tools.mk has drifted, refresh this script", file=sys.stderr)
    sys.exit(1)

mk_path.write_text(src.replace(old_cmd, new_cmd))
print(f"### {mk_path}: injected xdp-tools util.c unused-parameter fix")
