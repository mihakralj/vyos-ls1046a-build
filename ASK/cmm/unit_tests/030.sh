#!/bin/sh

cmm -c set mc6 interface eth0 add group 1.1.1.1 2000::1 2000::2
cmm -c set mc6 interface eth0 update group 1.1.1.2 2001::1 2000::2
cmm -c query mc6
