#!/bin/sh

cmm -c set route interface eth2 add prio 1 srcip 192.168.1.250 input eth0
