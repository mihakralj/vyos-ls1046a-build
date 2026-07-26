---
name: debian-pkg
description: Troubleshoot debian packaging, rules, and control files in the VyOS build pipeline
---
# Debian Packaging Protocol

1. Inspect `debian/control`, `debian/rules`, and `debian/changelog`.
2. Ensure build dependencies (`Build-Depends`) correctly distinguish host architecture from target architecture (`Architecture: arm64`).
3. Check post-inst and pre-inst scripts for clean execution without interactive prompts.
