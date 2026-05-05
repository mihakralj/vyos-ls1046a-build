#!/bin/sh

vconfig add eth0 1
cmm -c vlan add eth0.1
vconfig rem eth0.1
#Error sending CMD_VLAN_ENTRY Register
