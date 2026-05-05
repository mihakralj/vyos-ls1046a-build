#!/bin/sh

cmm -c set mc6 interface eth0 add group 1.1.1.1 2000::1 2000::2
cmm -c query mc6
cmm -c set mc6 interface eth0 del group 1.1.1.1 2000::1 2000::2

#IPv6 Multicast Entries:
#0000: Src addr: 2000::1  src_mask_len: 1   Dst addr: 2000::2  
#output interfaces:  eth0  
