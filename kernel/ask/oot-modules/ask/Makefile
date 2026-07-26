# SPDX-License-Identifier: GPL-2.0
#
# ASK2 - out-of-tree build wrapper
#
# Default target builds against the running kernel's headers. Override
# KDIR and ARCH/CROSS_COMPILE to cross-build for the LS1046A target:
#
#   make KDIR=/home/vyos/kernel-ls1046a-build/work/linux-6.18.26 \
#        ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu-
#
# After build, sign the resulting ask.ko with the kernel's signing key:
#
#   $(KDIR)/scripts/sign-file sha512 \
#       $(KDIR)/certs/signing_key.pem $(KDIR)/certs/signing_key.x509 \
#       ask.ko

KDIR ?= /lib/modules/$(shell uname -r)/build
PWD  := $(shell pwd)

# Force CONFIG_NET_ASK=m for OOT builds (no in-tree Kconfig wiring yet).
EXTRA_KBUILD_FLAGS := CONFIG_NET_ASK=m

.PHONY: default modules clean help install sign

default: modules

modules:
	$(MAKE) -C $(KDIR) M=$(PWD) $(EXTRA_KBUILD_FLAGS) modules

clean:
	$(MAKE) -C $(KDIR) M=$(PWD) clean
	rm -f Module.symvers modules.order

# Convenience target: build then sign with the kernel's signing key.
sign: modules
	$(KDIR)/scripts/sign-file sha512 \
		$(KDIR)/certs/signing_key.pem \
		$(KDIR)/certs/signing_key.x509 \
		ask.ko
	@echo "Signed: $$(modinfo ask.ko | grep -E '^(sig_id|signer|sig_hashalgo)')"

install: modules
	$(MAKE) -C $(KDIR) M=$(PWD) modules_install

help:
	@echo "Targets:"
	@echo "  default / modules  - build ask.ko"
	@echo "  sign               - build and sign with KDIR's signing key"
	@echo "  install            - install into /lib/modules/.../extra"
	@echo "  clean              - remove build artifacts"
	@echo ""
	@echo "Variables:"
	@echo "  KDIR=$(KDIR)"
	@echo ""
	@echo "Cross-build example:"
	@echo "  make KDIR=/path/to/linux-src ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu-"