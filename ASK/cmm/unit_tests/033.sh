#!/bin/sh

cmm -c set mc4 interface eth0 add group 1.1.1.1 192.168.0.1 192.168.0.2
cmm -c set mc4 interface eth0 update group 1.1.1.2 192.168.0.3 192.168.0.2
cmm -c query mc4

