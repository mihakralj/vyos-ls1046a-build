#!/bin/sh

vconfig add eth0 1
ifconfig eth0.1 up
cmm -c vlan add eth0.1
cmm -c vlan show
cmm -c query vlan
