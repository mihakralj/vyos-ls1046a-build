/*
 *
 *  Copyright (C) 2007 Mindspeed Technologies, Inc.
 *  Copyright 2014-2016 Freescale Semiconductor, Inc.
 *  Copyright 2017-2018,2021 NXP
 *
 * SPDX-License-Identifier:    GPL-2.0+
 * The GPL-2.0+ license for this file can be found in the COPYING.GPL file
 * included with this distribution or at http://www.gnu.org/licenses/gpl-2.0.html
 *
 *
 */

#ifndef __CMM_H__
#define __CMM_H__

	#define _GNU_SOURCE

	#include <stdio.h>
	#include <stdlib.h>
	#include <inttypes.h>
	#include <unistd.h>
	#include <errno.h>
	#include <inttypes.h>
	#include <sys/types.h>
	#include <sys/socket.h>
	#include <arpa/inet.h>
	#include <net/if.h>
	#include <linux/netlink.h>
	#include <linux/rtnetlink.h>
	#include <linux/ip.h>
	#include <string.h>
	#include <fcntl.h>
	#include <pthread.h>
	#include <limits.h>

	#include <libnetfilter_conntrack/libnetfilter_conntrack.h>
	#include <libnetfilter_conntrack/libnetfilter_conntrack_tcp.h>
	#include <libcli.h>

	#include <libfci.h>
	#include "libcmm.h"
#ifdef AUTO_BRIDGE
	#include <auto_bridge.h>
#endif
	#define NEW_IPC		1

/* defining IPSEC_FLOW_CACHE if IPSEC_NO_FLOW_CACHE defined in cmm package makefile */
#ifndef IPSEC_NO_FLOW_CACHE
#define	IPSEC_FLOW_CACHE
#endif
	
#ifdef NEW_IPC
	typedef cmm_handle_t *daemon_handle_t;
#else
	typedef int daemon_handle_t;
#endif

	#include "forward_engine.h"
	#include "rtnl.h"
	#include "pppoe.h"
	#include "conntrack.h"
	#include "ffcontrol.h"
	#include "client_daemon.h"
	#include "module_rx.h"
	#include "module_qm.h"
	#include "module_prf.h"
	#include "module_vlan.h"
	#include "module_mcast.h"
	#include "module_ipr.h"
	#include "module_macvlan.h"
	#include "module_mc4.h"
	#include "module_mc6.h"
	#include "timeout.h" 
	#include "module_tunnel.h"
	#include "module_relay.h"
	#include "module_stat.h"
	#include "module_route.h"
	#include "alt_conf.h"
	#include "module_expt.h"
	#include "module_socket.h"
	#include "module_rtp.h"
	#include "module_pktcap.h"
	#include "module_icc.h"
	#include "module_ipsec.h"
	#include "third_part.h"
#ifdef WIFI_ENABLE
	#include "module_wifi.h"
#endif
	#include "module_l2tp.h"
	#include "version.h"

	/***** Defines *****/

	extern unsigned int nf_conntrack_max ;
	#define NFNL_SOCK_SIZE 	(256 * nf_conntrack_max) // ( 1024 * nf_conntrack_max / 4)



	#define	developpers 		"Freescale Semiconductor <www.freescale.com>"
	#define cmm_help 	"Usage : cmm [-c command] [-f configurationfile]-h -v \n" \
							"-c command \tRun cmm to send a command. Need cmm daemon to be running\n" \
							"-f conffile\tTell cmm to use the following configuration file. Available in daemon mode only\n" \
							"-h         \tPrint this help\n" \
							"-v         \tPrint cmm version\n"

	#define cmm_print(level, format, args...)							\
		do {											\
			if (level & (DEBUG_CRIT | DEBUG_STDOUT | globalConf.debug_level | globalConf.log_level))	\
				cmm_print_func(level, format, ##args);				\
		} while (0)

	/* DEBUG_{COMMAND,ERROR,WARNING,INFO} can be set by the user to print out debug messages on 
	 * the user console, or from the config file to log debug messages into a log file.
	 * DEBUG_STDOUT messages are not logged into a file.
	 * DEBUG_STDERR messages will be logged into a file if a logfile has been set in the configuration.
	 * Both DEBUG_STDOUT and DEBUG_STDERR messages are printed out on the console or CLI.
	 */
	#define DEBUG_COMMAND		(1 << 0)
	#define DEBUG_ERROR		(1 << 1)
	#define DEBUG_WARNING		(1 << 2)	/* This flag is used when we need to print a message about an unexpected behavior*/
	#define DEBUG_INFO		(1 << 3)
	#define DEBUG_STDOUT		(1 << 4)	/* Used for messages in reply to user actions (display commands, etc) */
	#define DEBUG_CRIT		(1 << 5)	/* Equivalent to DEBUG_STDERR */
	#define DEBUG_STDERR		DEBUG_CRIT 
	#define DEBUG_NOTIMESTAMP	(1 << 7)

	/* This debug is not controlled via CLI */
	#define IPSEC_DBG

	//#define MUTEX_DEBUG

	#ifdef MUTEX_DEBUG
	
	extern pthread_mutex_t ctMutex;
	extern pthread_mutex_t rtMutex;
	extern pthread_mutex_t neighMutex;
#ifdef IPSEC_FLOW_CACHE
	extern pthread_mutex_t flowMutex;
#endif /* IPSEC_FLOW_CACHE */
	
	int mutexes;
	#define __pthread_mutex_lock(mutex)		\
		({	\
			if (mutex == &ctMutex) mutexes |= 0x1; \
			else if (mutex == &rtMutex) mutexes |= 0x10; \
			else if (mutex == &neighMutex) mutexes |= 0x100; \
			else if (mutex == &flowMutex) mutexes |= 0x1000; \
			cmm_print(DEBUG_CRIT, "0x%04x: lock at %s %u\n", mutexes, __func__, __LINE__); \
			pthread_mutex_lock (mutex);	\
		})
	#define __pthread_mutex_unlock(mutex)		\
		({	\
			if (mutex == &ctMutex) mutexes &= ~0x1; \
			else if (mutex == &rtMutex) mutexes &= ~0x10; \
			else if (mutex == &neighMutex) mutexes &= ~0x100; \
			else if (mutex == &flowMutex) mutexes &= ~0x1000; \
			cmm_print(DEBUG_CRIT, "0x%04x: unlock at %s %u\n", mutexes, __func__, __LINE__); \
			pthread_mutex_unlock (mutex);	\
		})

	#else
	#define __pthread_mutex_lock pthread_mutex_lock
	#define __pthread_mutex_unlock pthread_mutex_unlock
	#endif


        /* This macro is used for PPPoE Auto mode */
        #define PPPOE_AUTO_ENABLE       1


#ifndef IPSEC_FLOW_CACHE
/* this macro indicates how many SAs information can be received per direction in netlink message */
/* direction can be (FWD or IN) and OUT for originator and replier */
#define MAX_SAs_INFO_PER_DIR_IN_NL_MSG 2
#endif /* IPSEC_FLOW_CACHE */

	#define CMM_PID_FILE_PATH "/var/run/cmm.pid"

	/* The following define is enabled if 3rd party callback support is required */
	//#define CMM_THIRD_PART
	
	struct cmm_ct {
		pthread_t pthread;

		FCI_CLIENT *fci_handle;
		FCI_CLIENT *fci_catch_handle;

		FCI_CLIENT *fci_key_handle;
		FCI_CLIENT *fci_key_catch_handle;

		struct nfct_handle *handle;
		struct nfct_handle *catch_handle;
		struct nfct_handle *get_handle;

		struct rtnl_handle rth_neigh;
		struct rtnl_handle rth_link;
		struct rtnl_handle rth_ifaddr;
		struct rtnl_handle rth_route;
		struct rtnl_handle rth_rule;
		struct rtnl_handle rth_abm;
	};

	struct cmm_daemon {
		pthread_t pthread;
		FCI_CLIENT *fci_handle;				/* fci library Handler used by msg daemon thread*/
		FCI_CLIENT *fci_key_handle;
		int queueIdRx;
		int queueIdTx;
	};

	struct cmm_cli {
		pthread_t pthread;
		FCI_CLIENT *fci_handle;
		daemon_handle_t daemon_handle;
		int sock;
		int sock2;
		struct cli_def *handle;
	};

	union u_rxbuf {
                uint16_t result;
                char rcvBuffer[CMM_BUF_SIZE];
        };

	union u_rxbuf1024 {
                uint16_t result;
                char rcvBuffer[1024];
        };

	union u_txbuf {
                uint16_t result;
                char SndBuffer[CMM_BUF_SIZE];
        };

#define CMM_MAX_NUM_THREADS 4
#define CMM_MAX_64K_BUFF_SIZE 64 *1024
#define CMM_16B_ALIGN 16

	/***** Global structure *****/
	struct cmm_global
	{
		pid_t cmmPid;
		FILE *logFile;
		pthread_mutex_t logMutex;

		int enable;						/*Forward engine can be programmed or not*/

		int ff_enable;						/* Fast-forward enable/disable, all packets go through ACP but all control path is enabled */
#ifdef C2000_DPI
		int dpi_enable;						/* DPI enable/disable, CMM pushes connections to FPP normally if disabled */
#endif
		int asymff_enable;					/* Asymmetric Fastpath enable/disable*/
		char debug_level;
		char log_level;
		enum _t_vlan_policy {
		        ALLOW,
			PROHIBIT,
		        MANUAL
		} vlan_policy;

		char tun_family;
		char tun_proto;
		uint8_t enable_sam_itfs; /* option to enable 4rd support for SAM interfaces */

		struct nfct_handle *nf_conntrack_handle;	/* Used to send messages to nf_conntrack -- use must be protected by ctMutex */

		struct cmm_ct ct;
		struct cmm_daemon daemon;
		struct cmm_cli cli;
		uint64_t   rtnl_buf_pools[CMM_MAX_NUM_THREADS];
		uint64_t   rtnl_buf_pools_align[CMM_MAX_NUM_THREADS];
		uint8_t	   cur_rtnl_bufs;

		int *third_part_data;
		int auto_bridge;
		unsigned int cli_listenaddr;
	};

	/*
	 * This structure is used to exchange messages
	 * between cmm daemon and cmm client
	 */
	struct cmm_msg
	{
		long mtype; 				// This field is mandatory to use IPC message queues
		char buffer[CMM_BUF_SIZE];		// A buffer where the data are stored
	};
	/*
	 * Common parser defines/helpers
	 */
	#define CMD_BIT(x)  (1 << ((x) & 0xFF))
	#define TEST_CMD_BIT(y, x) (y & (CMD_BIT(x)))

	extern struct cmm_global globalConf;

	int cmmIsDaemonRunning(void);
	void cmm_print_func(int level, const char *format, ...);

	#define MAC_ADDRSTRLEN	18
	const char *mac_ntop(const void *mac, char *buf, size_t len);


	#define STR_TRUNC_COPY(dst, src, len)        do { strncpy((char *)dst, (char *)src, len - 1); *( (unsigned char*) dst + (len - 1) ) = '\0'; } while (0)

	#define STR_TRUNC_END(dst , len)        do { *( (unsigned char*) dst + (len - 1) ) = '\0'; } while (0)

char *cmm_get_rtnl_buf(uint32_t *buf_size);
void cmm_free_rtnl_buf(char *buf);

#endif
