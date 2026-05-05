#!/bin/sh

ip -6 tunnel add tnl0 mode ethipip6 remote 2000::1 local 2000::2 dev eth0
ifconfig tnl0 up
cmm -c tunnel tnl0 add ethipoip6 ipsec 1
cmm -c tunnel tnl0 show
