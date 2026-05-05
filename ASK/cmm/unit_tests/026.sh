#!/bin/sh

cmm -c tunnel tnl0 del
cmm -c tunnel tnl0 add ethipoip6 ipsec 1

#Error 3 received from CMM Deamon for CMD_CMMTD_TUNNEL_ADD - bug #57854
