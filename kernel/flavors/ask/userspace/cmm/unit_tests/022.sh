#!/bin/sh

cmm -c set socket open sock_id 1 type fpp saddr 1.1.1.1 daddr 2.2.2.2 sport 1 dport 2 proto udp queue 0 dscp 0
#should return "cmmSocketSetProcess: error sending message to daemon"
