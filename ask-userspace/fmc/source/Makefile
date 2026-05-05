#==========================================================================
#
# The MIT License (MIT)
# Copyright 2009-2012 Freescale Semiconductor, Inc.
#
# Permission is hereby granted, free of charge, to any person obtaining
# a copy of this software and associated documentation files (the
# "Software"), to deal in the Software without restriction, including
# without limitation the rights to use, copy, modify, merge, publish,
# distribute, sublicense, and/or sell copies of the Software, and to
# permit persons to whom the Software is furnished to do so, subject to
# the following conditions:
#
# The above copyright notice and this permission notice shall be included
# in all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
# EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
# MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.
# IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY
# CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT,
# TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE
# SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

#==========================================================================
# For host mode only compilation, set FMCHOSTMODE variable. For example:
#     make FMCHOSTMODE=1
#
# Also, use variables below in accordance to your environment

FMD_USPACE_HEADER_PATH ?= /usr/include/fmd
FMD_USPACE_LIB_PATH    ?= /usr/lib
LIBXML2_HEADER_PATH    ?= /usr/include/libxml2
TCLAP_HEADER_PATH      ?= /usr/include

PKG_NAME = fmc-0.9.34
PKG_RELEASE = 1

PLATFORM_FLAG = -DLS1043

ifeq ($(MACHINE),p1023rds)
  PLATFORM_FLAG = -DP1023
endif

ifeq ($(MACHINE),p4080ds)
  PLATFORM_FLAG = -DP4080
endif

ifeq ($(MACHINE),b4860qds)
  PLATFORM_FLAG = -DFMAN_V3H
endif

ifeq ($(MACHINE),t4240qds)
  PLATFORM_FLAG = -DFMAN_V3H
endif

ifeq ($(MACHINE),t1040qds)
  PLATFORM_FLAG = -DFMAN_V3L
endif

ifeq ($(MACHINE),ls1043)
  PLATFORM_FLAG = -DLS1043
endif

ifeq ($(MACHINE),ls1046)
  PLATFORM_FLAG = -DLS1043
endif

CFLAGS += $(PLATFORM_FLAG)

ARFLAGS = -rvU

ifdef M64BIT
  CFLAGS += -m64
endif

CFLAGS += -DNCSW_LINUX -O2 -D__STDC_LIMIT_MACROS\
          -I$(TCLAP_HEADER_PATH) \
          -I$(LIBXML2_HEADER_PATH) \
          -I$(FMD_USPACE_HEADER_PATH) \
          -I$(FMD_USPACE_HEADER_PATH)/etc \
          -I$(FMD_USPACE_HEADER_PATH)/Peripherals \
          -I$(FMD_USPACE_HEADER_PATH)/integrations
LDLIBS   = -lfmc -lfm -lxml2 -lm

ifdef FMCHOSTMODE
  LDFLAGS += -L.
  LDLIBS  += -lz
else
  CFLAGS += -fPIC
  LDFLAGS += -L. -L$(FMD_USPACE_LIB_PATH)
endif

ifdef M64BIT
  LDFLAGS += -m64
endif

CXXFLAGS = $(CFLAGS)

.PHONY: default
ifdef FMCHOSTMODE
default: libfm.a libfmc.a fmc
else
default: libfmc.a fmc
endif

objects = ./libfmc.o ./fmc_exec.o                                       \
     ./FMCCFGReader.o ./FMCPDLReader.o ./FMCPCDReader.o                 \
     ./FMCSPCreateCode.o ./FMCSPDumpCode.o ./FMCSPDumpIR.o              \
     ./FMCSPDumpParse.o ./FMCSPIR.o ./FMCPDLReader.o                    \
     ./FMCSPReviseCode.o ./FMCSPReviseIR.o ./FMCSP.o                    \
     ./FMCSPExpr.o ./FMCSPExprLexer.o                                   \
     ./FMCPCDModel.o ./FMCCModelOutput.o                                \
     ./FMCTaskDef.o ./FMCUtils.o ./FMCGenericError.o                    \
     ./spa/dll.o ./spa/fm_sp.o ./spa/fm_sp_assembler.tab.o              \
     ./spa/fm_sp_private.o ./spa/htbl.o ./spa/lex._fmsp_assembler_yy.o

headers = fmc.h FMCCFGReader.h FMCCModelOutput.h FMCGenericError.h \
          FMCPCDModel.h FMCPCDReader.h FMCPDLReader.h FMCSP.h      \
          FMCSPCreateCode.h FMCSPIR.h FMCTaskDef.h FMCUtils.h      \
          FMCSPExpr.hpp logger.hpp

other_files = ./fmc ./fmc_config ./fmc_config.c ./fmc_config.o ./softparse.h \
	          ./libfm.a ./libfmc.a ./FMCDummyDriver.o ./FMC.o

fmc : ./FMC.o ./FMCUtils.o ./FMCGenericError.o libfmc.a
	$(CXX) $(LDFLAGS) -o $@ $^ $(LDLIBS)

$(objects) : $(headers)

libfmc.a : $(objects)
	$(AR) $(ARFLAGS) $@ $^

fmc_config : fmc_config.o libfmc.a libfm.a

libfm.a : FMCDummyDriver.o
	$(AR) $(ARFLAGS) $@ $^

.PHONY: clean
clean:
	rm -f $(other_files) $(objects)

.PHONY: dist
dist: clean
	mkdir -p $(PKG_NAME);
	mkdir -p $(PKG_NAME)/source
	mkdir -p $(PKG_NAME)/source/spa;
	mkdir -p $(PKG_NAME)/etc
	cp *.c *.cpp *.h *.hpp Makefile    $(PKG_NAME)/source
	cp spa/*.c spa/*.h                 $(PKG_NAME)/source/spa
	cp -r ../etc/*                     $(PKG_NAME)/etc
	cp ../INSTALL ../LICENSE ../README $(PKG_NAME)
	find $(PKG_NAME) -name  CVS   -print0 | xargs -0 /bin/rm -R -f
	find $(PKG_NAME) -name .svn   -print0 | xargs -0 /bin/rm -R -f
	find $(PKG_NAME) -name \*.o   -print0 | xargs -0 /bin/rm -R -f
	find $(PKG_NAME) -name \*.bak -print0 | xargs -0 /bin/rm -R -f
	find $(PKG_NAME) -name \*.org -print0 | xargs -0 /bin/rm -R -f
	tar -czf $(PKG_NAME)-$(PKG_RELEASE).tgz $(PKG_NAME)
	rm -Rf $(PKG_NAME)
