#!/bin/sh

cmm -c set mc4 interface eth0 add group 1.1.1.1 192.168.0.1 192.168.0.2
cmm -c query mc4
cmm -c set mc4 interface eth0 del group 1.1.1.1 192.168.0.1 192.168.0.2
cmm -c query mc4

#IPv4 Multicast Entries:
#0000: Src addr: 192.168.0.1  src_mask_len: 1   Dst addr: 192.168.0.2  
#output interfaces:  eth0  
#ERROR: FPP Multicast IPV4 table empty
