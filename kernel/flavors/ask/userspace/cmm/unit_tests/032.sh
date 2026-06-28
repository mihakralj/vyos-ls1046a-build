#!/bin/sh

cmm -c set mc4 interface eth0 del group 1.1.1.1 192.168.0.1 192.168.0.2

#Error 700 received from FPP for CMD_MC4_MULTICAST
#700 means not found
