TOP_DIR := $(shell pwd)

# Support both KERNEL_SOURCE (original) and KERNEL_SRC (Yocto)
KERNEL_SOURCE ?= $(KERNEL_SRC)
BOARD_ARCH?=arm

obj-m:=fci.o

all: modules

modules clean modules_install:
	$(MAKE) -C $(KERNEL_SOURCE) ARCH=$(BOARD_ARCH) KBUILD_EXTRA_SYMBOLS="$(KBUILD_EXTRA_SYMBOLS)" M=`pwd` $@
