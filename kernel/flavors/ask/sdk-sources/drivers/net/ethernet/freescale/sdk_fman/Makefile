#
# Makefile for the Freescale Ethernet controllers
#
ccflags-y           += -DVERSION=\"\"
# The imported vendor FMAN tree is not declaration-clean under current toolchains.
subdir-ccflags-y    += -Wno-missing-prototypes -Wno-missing-declarations
#
#Include netcomm SW specific definitions
include $(srctree)/drivers/net/ethernet/freescale/sdk_fman/ncsw_config.mk
#
obj-y		+= etc/
obj-y		+= Peripherals/FM/
obj-y		+= src/
