#!/bin/sh

ip -6 tunnel add tnl0 mode ethipip6 remote 2000::1 local 2000::2 dev eth0
ifconfig tnl0 up
cmm -c tunnel tnl0 add ethipoip6 ipsec 1
cmm -c tunnel tnl0 show

#Details for tunnel tnl0
#Tunnel name        : tnl0
#Protocol           : etherip over ip6 (97)
#Local address      : 2000::2
#Remote address     : 2000::1
#Output device      : lo
#Flags              : 1
#Secure          : yes
#Encap limit     : 4
#Status             : running
