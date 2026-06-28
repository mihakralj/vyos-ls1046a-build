#!/bin/sh

cmm -c set socket open sock_id 2 type fpp saddr 3.3.3.3 daddr 4.4.4.4 sport 3 dport 4 proto udp queue 0 dscp 0
