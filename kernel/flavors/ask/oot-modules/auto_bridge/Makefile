#
#
#  Copyright (C) 2007 Mindspeed Technologies, Inc.
#  Copyright 2015-2016 Freescale Semiconductor, Inc.
#  Copyright 2017,2021 NXP
#
# SPDX-License-Identifier:    GPL-2.0+
# The GPL-2.0+ license for this file can be found in the COPYING.GPL file
# included with this distribution or at http://www.gnu.org/licenses/gpl-2.0.html
#

TOP_DIR := $(shell pwd)

# Support both KERNEL_SOURCE (original) and KERNEL_SRC (Yocto)
KERNEL_SOURCE ?= $(KERNEL_SRC)

obj-m:=auto_bridge.o

PLATFORM?=LS1024A
ifeq ($(PLATFORM),LS1024A)
TARG_ARCH=arm
else
TARG_ARCH=arm64
endif

#PLATFORM LS1043A refers family of LS104x
ifeq ($(PLATFORM),LS1043A)
EXTRA_CFLAGS+=-DLS104X
endif

#Enable VLAN_FILTER macro to support vlan filter feature on bridge.
#With vlan filtering, one can avoid creation of multiple vlan interfaces.
ifeq ($(ENABLE_VLAN_FILTER),y)
EXTRA_CFLAGS+=-DVLAN_FILTER
EXTRA_CFLAGS+=-I$(KERNEL_SOURCE)/net/bridge/
endif

all:
	$(MAKE) -C $(KERNEL_SOURCE) ARCH=$(TARG_ARCH) M=`pwd` modules

clean:
	$(MAKE) -C $(KERNEL_SOURCE) ARCH=$(TARG_ARCH) M=`pwd` clean
