/*
 *
 *  Copyright 2018,2021 NXP
 *
 * SPDX-License-Identifier:    GPL-2.0+
 * The GPL-2.0+ license for this file can be found in the COPYING.GPL file
 * included with this distribution or at http://www.gnu.org/licenses/gpl-2.0.html
 *
 */

#if defined(LS1043) 
#include "cmm.h"
#include "itf.h"
#include "fpp.h"
#include "cmmd.h"

#include <net/if.h>
#include <linux/if_vlan.h>
#include <linux/sockios.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/types.h>
#include <sys/ioctl.h>

#define IPR_DEBUG_INFO_DISPLAY 1

/*
** cmmIprStatsProcessClientCmd
** Daemon side demux.
** receives command from client side, processes it and sends response back.
** Return code is a length of a response in bytes, not including 2 bytes of command rc.
** To prevent daemon issuing commands to fpp - return code must be greater then 1
*/
int cmmIprStatsProcessClientCmd(FCI_CLIENT *fci_handle, int function_code, u_int8_t *cmd_buf, 
	u_int16_t cmd_len, u_int16_t *res_buf, u_int16_t *res_len)
{
	int rc = 0;
	res_buf[0] = CMMD_ERR_OK;
	rc = fci_cmd(fci_handle, function_code, (u_int16_t*)cmd_buf, cmd_len, res_buf, res_len);
	return rc;
}

/*****************************************************************
 * * cmmIprStatsQuery
 * *
 * *
 * ******************************************************************/
struct ipr_statistics {
        uint16_t ackstats; //mandated by fci/cmm
	struct ip_reassembly_info info;
};

#define CMD_OK 0
static inline int cmmIprStatsQuery(char ** keywords, int tabStart, daemon_handle_t daemon_handle, 
		uint32_t type)
{
        int rcvBytes = 0;
	struct ipr_statistics iprstats;

	iprstats.ackstats = 0xaa55;
        rcvBytes = cmmSendToDaemon(daemon_handle, type, 
			(void *)&iprstats, sizeof(struct ipr_statistics), 
			(void *)&iprstats);

	if (rcvBytes < sizeof(uint16_t)) {
		cmm_print(DEBUG_STDERR,
                            "ERROR: received bytes less than ackstatus size:%d\n", rcvBytes);
		return CLI_OK;
	}
	if (iprstats.ackstats != CMD_OK) {
		cmm_print(DEBUG_STDERR,
                            "ERROR: error in getting stats %x\n", iprstats.ackstats);
		return CLI_OK;
	}
	if (type == CMMD_CMD_IPR_V4_STATS)
		cmm_print(DEBUG_STDOUT, "ipv4 reassembly stats\n");
	else
		cmm_print(DEBUG_STDOUT, "ipv6 reassembly stats\n");
	cmm_print(DEBUG_STDOUT,"number fragments received  \t%" PRIu64 "\n",
                __bswap_64(iprstats.info.num_frag_pkts));
        cmm_print(DEBUG_STDOUT,"num of reassemblies        \t%" PRIu64 "\n",
                __bswap_64(iprstats.info.num_reassemblies));
        cmm_print(DEBUG_STDOUT, "num of compl reassly      \t%" PRIu64 "\n",
                __bswap_64(iprstats.info.num_completed_reassly));
        cmm_print(DEBUG_STDOUT,"num of sess matches        \t%" PRIu64 "\n",
                __bswap_64(iprstats.info.num_sess_matches));
        cmm_print(DEBUG_STDOUT,"err frag too small         \t%" PRIu64 "\n",
                __bswap_64(iprstats.info.num_frags_too_small));
        cmm_print(DEBUG_STDOUT,"err reassm failures        \t%" PRIu64 "\n",
                __bswap_64(iprstats.info.num_reassm_timeouts));
        cmm_print(DEBUG_STDOUT,"err overlap frags          \t%" PRIu64 "\n",
                __bswap_64(iprstats.info.num_overlapping_frags));
        cmm_print(DEBUG_STDOUT,"err too many frags         \t%" PRIu64 "\n",
                __bswap_64(iprstats.info.num_too_many_frags));
        cmm_print(DEBUG_STDOUT,"err failed bufallocs       \t%" PRIu64 "\n",
                __bswap_64(iprstats.info.num_failed_bufallocs));
        cmm_print(DEBUG_STDOUT,"err failed ctxallocs       \t%" PRIu64 "\n",
                __bswap_64(iprstats.info.num_failed_ctxallocs));
        cmm_print(DEBUG_STDOUT,"err failed ctxdeallocs     \t%" PRIu64 "\n",
                __bswap_64(iprstats.info.num_failed_ctxdeallocs));
        cmm_print(DEBUG_STDOUT,"num fatal errors           \t%" PRIu64 "\n",
                __bswap_64(iprstats.info.num_fatal_errors));
	cmm_print(DEBUG_STDOUT, "config:\n");
        cmm_print(DEBUG_STDOUT,"ipr timeout value          \t%d\n",
                __bswap_32(iprstats.info.timeout_val));
        cmm_print(DEBUG_STDOUT,"max frags allowed          \t%d\n",
                __bswap_32(iprstats.info.max_frags));
        cmm_print(DEBUG_STDOUT,"min frag size allowed      \t%d\n",
                __bswap_32(iprstats.info.min_frag_size));
        cmm_print(DEBUG_STDOUT,"max sessions allowed       \t%d\n",
                __bswap_32(iprstats.info.max_con_reassm));
#ifdef IPR_DEBUG_INFO_DISPLAY
	cmm_print(DEBUG_STDOUT, "debug:\n");
        cmm_print(DEBUG_STDOUT,"active sessions            \t%d\n",
                __bswap_32(iprstats.info.curr_sessions));
        cmm_print(DEBUG_STDOUT,"hash tbl size              \t%d\n",
                __bswap_32(iprstats.info.table_mask) + 1);
        cmm_print(DEBUG_STDOUT,"ipr ctx bpid               \t%d\n",
                __bswap_32(iprstats.info.reassem_bpid));
        cmm_print(DEBUG_STDOUT,"ipr ctx bp size            \t%d\n",
                __bswap_32(iprstats.info.reassem_bsize));
        cmm_print(DEBUG_STDOUT,"ipr frag bpid              \t%d\n",
                __bswap_32(iprstats.info.frag_bpid));
        cmm_print(DEBUG_STDOUT,"ipr frag bp size           \t%d\n",
                __bswap_32(iprstats.info.frag_bsize));
        cmm_print(DEBUG_STDOUT,"ipr frag txc fqid          \t0x%x\n",
                (__bswap_32(iprstats.info.txc_fqid) & 0xffffff));
        cmm_print(DEBUG_STDOUT,"timer tinfo                \t0x%x\n",
                __bswap_32(iprstats.info.timer_tnum));
        cmm_print(DEBUG_STDOUT,"timer tick val             \t%d\n",
                __bswap_32(iprstats.info.ipr_timer));
        cmm_print(DEBUG_STDOUT,"ipr debug area             \t0x%x\n",
                __bswap_32(iprstats.info.reassly_dbg));
        cmm_print(DEBUG_STDOUT,"ipr bucket base            \t0x%x\n",
                __bswap_32(iprstats.info.bucket_base));
#endif
        return CLI_OK;
}

int cmmIpr4StatsQuery(char ** keywords, int tabStart, daemon_handle_t daemon_handle)
{
	return(cmmIprStatsQuery(keywords, tabStart, daemon_handle, CMMD_CMD_IPR_V4_STATS));
}
int cmmIpr6StatsQuery(char ** keywords, int tabStart, daemon_handle_t daemon_handle)
{
	return(cmmIprStatsQuery(keywords, tabStart, daemon_handle, CMMD_CMD_IPR_V6_STATS));
}
#endif
