[![VyOS arm64 build](https://github.com/huihuimoe/vyos-arm64-build/actions/workflows/auto-build.yml/badge.svg)](https://github.com/huihuimoe/vyos-arm64-build/actions/workflows/auto-build.yml)

# About

Scheduled VyOS image build for arm64 - once per week (currently 01:00 UTC at Friday).

Successfull builds will be published as GitHub Release.

The build is untested (no official test suite for arm64), so use with caution!

For amd64 users, refer the official nightly builds on <https://github.com/vyos/vyos-nightly-build>.

# Usage

Embedded a basic [config.boot](./data/config.boot.default) file that sets up DHCP for the eth0 interface and enables SSH access with the default username `vyos` and password `a_strong-p@ssword`.

Can basically running in a qemu virtio environment(VGA needs to be `virtio-gpu`).

Tested to be compatible with Proxmox/HetznerCloud/Scaleway.
