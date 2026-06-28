#!/bin/sh

cmm -c tunnel tnl0 del
ifconfig tnl0 down
ip -6 tunnel del tnl0
