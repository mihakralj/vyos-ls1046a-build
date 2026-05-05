#!/bin/sh

vconfig add eth0 1
ifconfig eth0.1 up
cmm -c vlan add eth0.1
cmm -c vlan del eth0.1
vconfig rem eth0.1
