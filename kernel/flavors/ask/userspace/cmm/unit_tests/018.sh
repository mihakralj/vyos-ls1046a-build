#!/bin/sh

cmm -c set rx interface wan bridge add output wan
cmm -c query rx bridge

#Interface eth0 (WAN) Status: OFF
#Interface eth2 (LAN) Status: OFF
#Input=wan    DA=00:00:00:00:00:00 SA=       *          Type=  *  Queue=0 Qmod=none VLANPrio=0 Output=wan
#Input=wan    DA=01:01:01:01:01:01 SA=       *          Type=  *  Queue=0 Qmod=none VLANPrio=0 SessionId=58504 Output=lan

#2 Bridge Table Entries found
