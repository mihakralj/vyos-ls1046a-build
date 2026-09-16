# SPDX-License-Identifier: GPL-2.0
#
# DPAA1 AF_XDP zero-copy pool flavor module
#

FMAN = $(srctree)/drivers/net/ethernet/freescale/fman
DPAA = $(srctree)/drivers/net/ethernet/freescale/dpaa
ccflags-y += -I$(FMAN) -I$(DPAA)

obj-$(CONFIG_DPAA_AF_XDP_POOL) += af_xdp_pool.o

af_xdp_pool-objs := af_xdp_pool_main.o