# fci

This repository owns the shared packaged FCI source consumed by both the
OpenWrt `ask-fci` kernel package and the `libfci` userspace library package.

The source boundary is intentionally shared and lives under `fci-9.00.12/`.
OpenWrt fetches one pinned git revision of this repository, unpacks it into
`build_dir`, and then builds:
- `ask-fci` from `fci-9.00.12/`
- `libfci` from `fci-9.00.12/lib`

`openwrt/package/kernel/ask-fci/` and `openwrt/package/libs/libfci/` remain
responsible for:
- package metadata and dependency declarations
- OpenWrt init/service integration under `ask-fci/files/`
- exceptional OpenWrt-local integration patches only when a change truly cannot
  live in this source repo

For reproducible packaging, OpenWrt should pin an exact commit or tag from this
repository through `PKG_SOURCE_VERSION` and verify the generated source archive
with `PKG_MIRROR_HASH`. Tags should identify packageable source states; commits
referenced by released packages must remain immutable.

Durable FCI/libfci behavior changes should be made directly in `fci-9.00.12/`,
then packaged by updating the OpenWrt pin and mirror hash. Avoid carrying normal
FCI source fixes as OpenWrt package patches.
