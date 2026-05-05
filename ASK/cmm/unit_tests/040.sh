#!/bin/sh

cmm -c set socket6 open sock_id 10 type fpp saddr 2000::1 daddr 2000::2 sport 1 dport 2 proto udp queue 0 dscp 0
cmm -c show socket sock_id 10
cmm -c set socket6 close sock_id 10
