/*
 *  Copyright (c) 2011, 2014 Freescale Semiconductor, Inc.
 *
 * SPDX-License-Identifier:    GPL-2.0+
 * The GPL-2.0+ license for this file can be found in the COPYING.GPL file
 * included with this distribution or at http://www.gnu.org/licenses/gpl-2.0.html
 *
 */

#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <unistd.h>
#include <limits.h>
#include <sched.h>
#include <signal.h>
#include <string.h>
#include <fcntl.h>
#include <sys/ioctl.h>
#include "cdx_ioctl.h"
#include <libcli.h>

#define ETHER_ADDR_LEN	6

#define IPV4_CONN_SIP_START     0x0a0a0a01
#define IPV4_CONN_DIP_START     0x14141401
#define SPORT_START             10000
#define DPORT_START             20000
#define MAX_CONNECTIONS         2
#ifdef T4240RDB
#define IFACE_1        		(char *)"fm2-mac1"
#define IFACE_2        		(char *)"fm2-mac2"
#else
#define IFACE_1        		(char *)"eth2"
#define IFACE_2        		(char *)"eth3"
#endif


#define FWD_ARP_ENTRY           0
#define REV_ARP_ENTRY           1
#define PROTOCOL                17
#define TEST_MTU                1500


//cdx device driver handle
extern int cdx_dev_handle;


//mac addresses of the gateways
static char test_arp_entries[2][ETHER_ADDR_LEN] =
{
	{0x6e, 0x5f, 0x98, 0x37, 0x0c, 0x07}, //FWD
	{0x00, 0x07, 0xe9, 0x09, 0xf1, 0xaa}, //REV
};

#define  USE_SPORT_NAT 	1
#define  USE_DPORT_NAT 	1
//#define  USE_SIP_NAT 	1
//#define  USE_DIP_NAT 	1

//add static connections to DP
static int add_connections(void)
{	
	int ii;
        struct add_conn_info add_conn;
        struct test_conn_info conn_info;
        struct test_flow_info *fwd_flow_info;
        struct test_flow_info *rev_flow_info;
		uint32_t dip_start;

        add_conn.num_conn = 1;
        add_conn.conn_info = &conn_info;
		dip_start = IPV4_CONN_DIP_START;
        for (ii = 0; ii < MAX_CONNECTIONS; ii++) {
                memset(&conn_info, 0, sizeof(struct test_conn_info));
                conn_info.proto = PROTOCOL;
                fwd_flow_info = &conn_info.fwd_flow;
                rev_flow_info = &conn_info.rev_flow;
                fwd_flow_info->sport = (SPORT_START + ii);
                fwd_flow_info->dport = (DPORT_START + ii);
                fwd_flow_info->ipv4_saddr = (IPV4_CONN_SIP_START);
                fwd_flow_info->ipv4_daddr = dip_start;
                fwd_flow_info->ingress_port = IFACE_1;
                fwd_flow_info->egress_port = IFACE_2;
                fwd_flow_info->mtu = TEST_MTU;
				if ((dip_start & 0xff) == 0xfe) 
					dip_start += 2;
				else
					dip_start++;
			
                memcpy(&fwd_flow_info->dest_mac[0],
                        &test_arp_entries[FWD_ARP_ENTRY][0], ETHER_ADDR_LEN);
                fwd_flow_info->dest_mac[(ETHER_ADDR_LEN - 1)] += ii;
#ifdef USE_SPORT_NAT
                rev_flow_info->sport = (fwd_flow_info->dport + 1000);
#else
                rev_flow_info->sport = fwd_flow_info->dport;
#endif
#ifdef USE_SPORT_NAT
                rev_flow_info->dport = (fwd_flow_info->sport + 1000);
#else
                rev_flow_info->dport = fwd_flow_info->sport;
#endif
#ifdef USE_SIP_NAT
                rev_flow_info->ipv4_saddr = (fwd_flow_info->ipv4_daddr + 0x0a000000);
#else
                rev_flow_info->ipv4_saddr = fwd_flow_info->ipv4_daddr;
#endif
#ifdef USE_DIP_NAT
                rev_flow_info->ipv4_daddr = (fwd_flow_info->ipv4_saddr + 0x0a000000);
#else
                rev_flow_info->ipv4_daddr = fwd_flow_info->ipv4_saddr;
#endif
                rev_flow_info->ingress_port = IFACE_2;
                rev_flow_info->egress_port = IFACE_1;
                rev_flow_info->mtu = TEST_MTU;
                memcpy(&rev_flow_info->dest_mac[0],
                        &test_arp_entries[REV_ARP_ENTRY][0], ETHER_ADDR_LEN);
                rev_flow_info->dest_mac[(ETHER_ADDR_LEN - 1)] += ii;
                if (ioctl(cdx_dev_handle, CDX_CTRL_DPA_CONNADD,
                                &add_conn) < 0) {
                        printf("%s:connadd ioctl failed conn id ::ii %d\n", 
					__FUNCTION__,
					ii);
                        return -1;
                }
        }
        return 0;
}

//test application init
int test_app_init(void)
{
	return add_connections();
}
#define MAX_MURAM_SIZE  ((384 * 1024) + 100) //384 kB on LS1043A
#define PRINT_BUF_SIZE 128
int show_muram(struct cli_def *cli, char *command, char *argv[], int argc)
{
#ifdef DPAA_DEBUG_ENABLE
        uint32_t ii;
        char *src_ptr;
        char  *dst_ptr;
        struct muram_data muram_data;
        char print_data[PRINT_BUF_SIZE];

        muram_data.size = MAX_MURAM_SIZE;
        muram_data.buff = calloc(1, MAX_MURAM_SIZE);
        if (!muram_data.buff) {
                cli_print(cli, "unable to alloc mem for muram data\n");
                return CLI_OK;
        }
        if (ioctl(cdx_dev_handle, CDX_CTRL_DPA_GET_MURAM_DATA,
                                &muram_data) < 0) {
                cli_print(cli, "get muram data  ioctl failed");
                return CLI_OK;
        }
        cli_print(cli, "muram data size %d\n", muram_data.size);
        src_ptr = (char *)muram_data.buff;
        memset(print_data, 0, PRINT_BUF_SIZE);
        dst_ptr = print_data;
        for (ii = 0; ii < muram_data.size; ii++) {
                if (!(ii % 16)) {
                        cli_print(cli, "%s", print_data);
                        memset(print_data, 0, PRINT_BUF_SIZE);
                        dst_ptr = print_data;
                        dst_ptr += sprintf(dst_ptr, "%04x:%02x ", ii, *src_ptr);
                }
                else
                        dst_ptr += sprintf(dst_ptr, "%02x ", *src_ptr);
                src_ptr++;
        }
        if (ii % 16)
                cli_print(cli, "%s", print_data);
        free(muram_data.buff);
#else
        cli_print(cli, "enable DPAA_DEBUG_ENABLE");
#endif
        return CLI_OK;
}

void show_muram_temp(void)
{
        uint32_t ii;
        char *src_ptr;
        char  *dst_ptr;
        struct muram_data muram_data;
        char print_data[PRINT_BUF_SIZE];

        muram_data.size = MAX_MURAM_SIZE;
        muram_data.buff = calloc(1, MAX_MURAM_SIZE);
        if (!muram_data.buff) {
                printf("unable to alloc mem for muram data\n");
                return;
        }
        if (ioctl(cdx_dev_handle, CDX_CTRL_DPA_GET_MURAM_DATA,
                                &muram_data) < 0) {
                printf("get muram data  ioctl failed\n");
                return;
        }
        printf("muram data size %d\n", muram_data.size);
        src_ptr = (char *)muram_data.buff;
        memset(print_data, 0, PRINT_BUF_SIZE);
        dst_ptr = print_data;
        for (ii = 0; ii < muram_data.size; ii++) {
                if (!(ii % 16)) {
                        printf("%s\n", print_data);
                        memset(print_data, 0, PRINT_BUF_SIZE);
                        dst_ptr = print_data;
                        dst_ptr += sprintf(dst_ptr, "%04x:%02x ", ii, *src_ptr);
                }
                else
                        dst_ptr += sprintf(dst_ptr, "%02x ", *src_ptr);
                src_ptr++;
        }
        if (ii % 16)
                printf("%s\n", print_data);
        free(muram_data.buff);
}
