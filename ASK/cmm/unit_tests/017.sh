#!/bin/sh

cmm -c set rx interface wan bridge add da 1:1:1:1:1:1
cmm -c query rx bridge

#Interface eth0 (WAN) Status: OFF
#Interface eth2 (LAN) Status: OFF
#Input=wan    DA=01:01:01:01:01:01 SA=       *          Type=  *  Queue=0 Qmod=none VLANPrio=0 Output=lan

#1 Bridge Table Entries found
