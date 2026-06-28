#!/bin/sh

cmm -c set mc6 interface eth0 del group 1.1.1.1 2000::1 2000::2

#Error 700 received from FPP for CMD_MC6_MULTICAST
#700 means not found
