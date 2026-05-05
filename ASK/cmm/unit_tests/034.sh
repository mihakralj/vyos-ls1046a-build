#!/bin/sh

cmm -c relay add 00:00:00:00:01:00 00:00:00:00:00:02 eth2 eth1 1 1
cmm -c relay del 00:00:00:00:01:00 00:00:00:00:00:02 eth2 eth1 1 1

#Error 32002 received from CMM Deamon
#Error 32002 received from CMM Deamon
#32002 means not configured.
#on server-side:
#Received commandCode: (0611) size 48
#00 00 00 00 01 00 00 00 00 00 00 02 74 6e 6c 30 
#00 00 00 00 00 00 00 00 00 00 00 00 65 74 68 31 
#00 00 00 00 00 00 00 00 00 00 00 00 01 00 01 00 
#__itf_find: find interface(11)
#__itf_find: find interface(11)
#__itf_find: find interface(4)
#__itf_find: find interface(4)
#cmmRelayAdd::125:Interface is not programmed to FPP  4
#cmmDaemonThread: Sending ack commandCode: 0611, rc 0x0000, dataSize: 2 
#02 7d 
#cmmDaemonThread: Received commandCode: (0612) size 48
#00 00 00 00 01 00 00 00 00 00 00 02 74 6e 6c 30 
#00 00 00 00 00 00 00 00 00 00 00 00 65 74 68 31 
#00 00 00 00 00 00 00 00 00 00 00 00 01 00 01 00 
#relay_table is NULL
#cmmDaemonThread: Sending ack commandCode: 0612, rc 0x0000, dataSize: 2 
#02 7d 
