#
 #  Copyright (c) 2011, 2014 Freescale Semiconductor, Inc.
 #
 # SPDX-License-Identifier:    GPL-2.0+
 # The GPL-2.0+ license for this file can be found in the COPYING.GPL file
 # included with this distribution or at http://www.gnu.org/licenses/gpl-2.0.html
 #
#

#/*
#* Copyright (c) 2014 Freescale Semiconductor, Inc. All rights reserved.
#*/
OBJS = main.o dpa.o testapp.o
TARGET = dpa_app

all:	$(TARGET)

LDFLAGS += -lpthread -lcli
CFLAGS += -DDPAA_DEBUG_ENABLE

$(TARGET): $(OBJS)
	$(CC) $(CFLAGS) -o $(TARGET) $(OBJS) $(LDFLAGS)

%.o: %.c
	$(CC) $(CFLAGS) -o $@ -c $<

clean:
	rm -f *.o $(TARGET)
