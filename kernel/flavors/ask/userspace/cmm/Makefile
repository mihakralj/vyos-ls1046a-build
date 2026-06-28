# Plain Makefile for CMM (replaces autotools)
# Invoked from top-level: make -C cmm CC=... LIBFCI_DIR=... SYSROOT=...

SRCDIR   := src
BUILDDIR := src

# Patched libs sysroot (built by build/build-patched-libs.sh)
SYSROOT  ?= ../sources/sysroot

# Cross pkg-config against patched sysroot
PKG_CONFIG      ?= pkg-config
PKG_CONFIG_PATH ?= $(SYSROOT)/lib/pkgconfig

NFCT_CFLAGS := $(shell PKG_CONFIG_PATH=$(PKG_CONFIG_PATH) $(PKG_CONFIG) --cflags libnetfilter_conntrack 2>/dev/null)
NFCT_LIBS   := $(shell PKG_CONFIG_PATH=$(PKG_CONFIG_PATH) $(PKG_CONFIG) --libs libnetfilter_conntrack 2>/dev/null)

CFLAGS  += -O2 -g -Wall -Werror
CFLAGS  += $(NFCT_CFLAGS)
CFLAGS  += -I$(SRCDIR)
CFLAGS  += -I$(LIBFCI_DIR)/include
CFLAGS  += -I$(ABM_DIR)/include
CFLAGS  += -DAUTO_BRIDGE -DLS1043 -DWIFI_ENABLE

LDFLAGS += -L$(LIBFCI_DIR) -L$(SYSROOT)/lib
LDLIBS  := -lpthread -lfci -lcli -lpcap $(NFCT_LIBS) -lmnl

SRCS := \
	neighbor_resolution.c \
	module_expt.c \
	cmm.c \
	conntrack.c \
	ffcontrol.c \
	forward_engine.c \
	route_cache.c \
	pppoe.c \
	ffbridge.c \
	client_daemon.c \
	rtnl.c \
	module_rx.c \
	module_tx.c \
	module_qm.c \
	module_prf.c \
	module_vlan.c \
	module_macvlan.c \
	module_mcast.c \
	module_mc6.c \
	module_mc4.c \
	keytrack.c \
	timeout.c \
	module_tunnel.c \
	module_relay.c \
	itf.c \
	module_stat.c \
	module_route.c \
	module_socket.c \
	module_rtp.c \
	module_wifi.c \
	alt_conf.c \
	voicebuf.c \
	module_pktcap.c \
	module_icc.c \
	third_part.c \
	cmm_asym_ff.c \
	module_l2tp.c \
	module_lro.c \
	module_ipsec.c \
	module_ipr.c \
	libcmm.c

OBJS := $(SRCS:%.c=$(BUILDDIR)/%.o)

all: version $(BUILDDIR)/cmm

$(BUILDDIR)/cmm: $(OBJS)
	$(CC) $(LDFLAGS) -o $@ $^ $(LDLIBS)

$(BUILDDIR)/%.o: $(SRCDIR)/%.c
	$(CC) $(CFLAGS) -c -o $@ $<

version:
	@if [ -d .git ]; then \
		V=$$(git describe --always --tags --dirty 2>/dev/null || echo "unknown"); \
		printf '/* Auto-generated */\n#ifndef VERSION_H\n#define VERSION_H\n#define CMM_VERSION "%s"\n#endif\n' "$$V" > $(SRCDIR)/version.h; \
	fi

clean:
	rm -f $(BUILDDIR)/*.o $(BUILDDIR)/cmm

.PHONY: all version clean
