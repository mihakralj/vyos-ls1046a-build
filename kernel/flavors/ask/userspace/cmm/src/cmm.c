/*
 *
 *  Copyright (C) 2007 Mindspeed Technologies, Inc.
 *  Copyright 2014-2016 Freescale Semiconductor, Inc.
 *  Copyright 2017,2021 NXP
 *
 * SPDX-License-Identifier:    GPL-2.0+
 * The GPL-2.0+ license for this file can be found in the COPYING.GPL file
 * included with this distribution or at http://www.gnu.org/licenses/gpl-2.0.html
 *
 *
 */

#include "cmm.h"
#include "forward_engine.h"
#include "keytrack.h"
#include "itf.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sched.h>
#include <signal.h>
#include <stdarg.h>
#include <ucontext.h>
/* backtrace() is a glibc extension, not available in musl or uClibc */
#if defined(__GLIBC__) && !defined(__UCLIBC__)
#include <execinfo.h>
#endif

struct cmm_global globalConf;
unsigned int nf_conntrack_max = CONNTRACK_MAX;

#ifdef ARCH_ARM32
struct kernel_ucontext {
	unsigned long     uc_flags;
	struct kernel_ucontext  *uc_link;
	stack_t           uc_stack;
	struct sigcontext uc_mcontext;
	sigset_t          uc_sigmask;
	/* Allow for uc_sigmask growth.  Glibc uses a 1024-bit sigset_t.  */
	int               __unused[32 - (sizeof (sigset_t) / sizeof (int))];
	/* Last for extensibility.  Eight byte aligned because some
           coprocessors require eight byte alignment.  */
	unsigned long     uc_regspace[128] __attribute__((__aligned__(8)));
};
#endif

static void cmm_crit_err_hdlr(int sig_num, siginfo_t *info, void *ucontext)
{
#ifdef ARCH_ARM32
	struct sigcontext *sigcontext;
#else
	mcontext_t *mctx;
	int i;
#endif
#if defined(__GLIBC__) && !defined(__UCLIBC__)
	void *array[50];
	char **messages;
	int size;
#endif

#ifdef ARCH_ARM32
	sigcontext = &((struct kernel_ucontext *)ucontext)->uc_mcontext;

	fprintf(stderr, "\n%s: signal %d (%s), fault address is %p at %p PID %d\n",
		__func__, sig_num, strsignal(sig_num), info->si_addr,
		(void *)sigcontext->arm_pc, getpid());

	fprintf(stderr, "register dump:\nr0:%08lx r1:%08lx r2:%08lx r3:%08lx r4:%08lx r5:%08lx r6:%08lx r7:%08lx\n",
		sigcontext->arm_r0, sigcontext->arm_r1, sigcontext->arm_r2, sigcontext->arm_r3,
		sigcontext->arm_r4, sigcontext->arm_r5, sigcontext->arm_r6, sigcontext->arm_r7);

	fprintf(stderr, "r8:%08lx r9:%08lx r10:%08lx fp:%08lx ip:%08lx sp:%08lx lr:%08lx pc:%08lx\n",
		sigcontext->arm_r8, sigcontext->arm_r9, sigcontext->arm_r10, sigcontext->arm_fp,
		sigcontext->arm_ip, sigcontext->arm_sp, sigcontext->arm_lr, sigcontext->arm_pc);

	fprintf(stderr, "cpsr:%08lx fault_address:%08lx\n", sigcontext->arm_cpsr, sigcontext->fault_address);
#else
	mctx = &((ucontext_t *)ucontext)->uc_mcontext;

	fprintf(stderr, "\n%s: signal %d (%s), fault address is %p at %p PID %d\n",
                __func__, sig_num, strsignal(sig_num), info->si_addr,
                (void *)mctx->pc, getpid());

	fprintf (stderr, "register dump:");
	for (i = 0; i < 31; i++)
	{
		if (i%8 == 0)
			fprintf(stderr, "\n");
		fprintf(stderr, "r%d:%016llx", i, (unsigned long long)mctx->regs[i]);
	}

        fprintf(stderr, "\npc:%016llx sp:%016llx fault_address:%016llx\n",
		(unsigned long long)mctx->pc, (unsigned long long)mctx->sp,
		(unsigned long long)mctx->fault_address);

#endif

#if defined(__GLIBC__) && !defined(__UCLIBC__)
	size = backtrace(array, 50);

	/* overwrite sigaction with caller's address */
#ifdef ARCH_ARM32
	array[1] = (void *)sigcontext->arm_pc;
#else
	array[1] = (void *)mctx->pc;
#endif

	messages = backtrace_symbols(array, size);

	if (messages)
	{
		for (i = 1; i < size; ++i)
		{
			fprintf(stderr, "[bt]: (%d) %s\n", i, messages[i]);
		}

		free(messages);
	}
	else
	{
		for (i = 1; i < size; ++i)
		{
			fprintf(stderr, "[bt]: (%d) %p\n", i, array[i]);
		}
	}
#endif
	exit(EXIT_FAILURE);
}


void cmm_print_func(int level, const char *format, ...)
{
	va_list args;

	va_start(args, format);

	if (level & DEBUG_CRIT)
	{
		if (globalConf.cli.handle)
			cli_vabufprint(globalConf.cli.handle, (char *)format, args);
		else
			vfprintf(stderr, format, args);
	}
	else if (level & DEBUG_STDOUT)
	{
		if (globalConf.cli.handle)
			cli_vabufprint(globalConf.cli.handle, (char *)format, args);
		else
			vfprintf(stdout, format, args);
	}
	else if (globalConf.cli.handle && (level & globalConf.debug_level))
		cli_vabufprint(globalConf.cli.handle, (char *)format, args);

	if (globalConf.logFile && (level & globalConf.log_level))
	{
		pthread_mutex_lock(&globalConf.logMutex);
		if (!(level & DEBUG_NOTIMESTAMP))
		{
			time_t now;
			struct tm now_tm;
			char date[24];

			time(&now);
			localtime_r(&now, &now_tm);
			strftime(date, 24, "%F %T", &now_tm);
			fprintf(globalConf.logFile, "%s: ", date);
		}

		vfprintf(globalConf.logFile, format, args);
		pthread_mutex_unlock(&globalConf.logMutex);
	}

	va_end(args);
}

/*****************************************************************
 * mac_ntop - converts a mac address in numerical format to presentation format
 *
 *
 *****************************************************************/
const char *mac_ntop(const void *mac, char *buf, size_t len)
{
	snprintf(buf, len, "%02x:%02x:%02x:%02x:%02x:%02x", ((unsigned char *)mac)[0],
		 					((unsigned char *)mac)[1],
		 					((unsigned char *)mac)[2],
		 					((unsigned char *)mac)[3],
		 					((unsigned char *)mac)[4],
		 					((unsigned char *)mac)[5]);

	return buf;
}

/*Print CMM help*/
void cmmHelp()
{
	cmm_print(DEBUG_STDOUT, cmm_help);
}

/*****************************************************************
 * cmmVersion()
 *
 *      Prints cmm version
 *  
 *****************************************************************/
void cmmVersion()
{
	cmm_print(DEBUG_STDOUT, "Cmm version %s\n", CMM_VERSION);
	cmm_print(DEBUG_STDOUT, "Developped by %s\n", developpers);
}

/*****************************************************************
 * cmmIsDaemonRunning()
 *
 *      Check if cmm daemon is running. If so, it returns the pid
 *  
 *****************************************************************/
int cmmIsDaemonRunning()
{
	FILE*fd;
	char buf[10];

	fd = fopen(CMM_PID_FILE_PATH, "r");
	if(fd > 0)
	{
		// Read the pid written in the pid file
		if(fgets(buf, 10, fd) != NULL)
		{
			// Check the daemon is really running
			if (getpgid(atoi(buf)) != -1)
			{
				fclose(fd);
				return atoi(buf);
			}
		}
		fclose(fd);
	}
	// No daemon is running
	return 0;
}

/*****************************************************************
 * cmmCreateDaemonPidFile()
 *
 *    Create a pid file.
 *    This function supposes that no cmm daemon is running and
 *    then removes the old pid file if there is.
 *  
 *****************************************************************/
int cmmCreateDaemonPidFile()
{
	int fp;
	char buf[10+1+1]; /* int can have up to 10 chars + 1 for sign + 1 for trailing \0 */

	fp = open(CMM_PID_FILE_PATH, O_WRONLY | O_CREAT | O_EXCL, 0644);
	if(fp < 0)
	{
		if(errno == EEXIST)
		{
			// A file already exists, delete it and create a new one
			if (remove(CMM_PID_FILE_PATH))
			{
				cmm_print(DEBUG_CRIT, "Unable to delete old %s\n", CMM_PID_FILE_PATH);
				return -1;
			}
			// Now the old file is deleted, we can create a new one
			fp = open(CMM_PID_FILE_PATH, O_WRONLY | O_CREAT | O_EXCL, 0644);
			if (fp < 0)
			{
				cmm_print(DEBUG_CRIT, "Error opening pid file %s\n", CMM_PID_FILE_PATH);
				return -1;
			}
		}
		else
		{
			cmm_print(DEBUG_CRIT, "Error opening pid file %s\n", CMM_PID_FILE_PATH);
			return -1;
		}
	}

	snprintf(buf, sizeof(buf), "%d\n", getpid());
	if(write(fp, buf, strlen(buf)) <= 0)
		cmm_print(DEBUG_CRIT, "Error writing to pid file\n");
	close(fp);

	globalConf.cmmPid = getpid();

	return 0;
}
/*****************************************************************
* sig_term_hdlr
*
*
******************************************************************/
static void sig_term_hdlr(int signum)
{
	int ii;
	int ret = 0;

	cmm_print(DEBUG_INFO, "%s: entered\n", __func__);

	cmm_third_part_exit(globalConf.third_part_data);

	cmmCliExit(&globalConf.cli);

	cmmDaemonExit(&globalConf.daemon);

	cmmCtExit(&globalConf.ct);

	nfct_close(globalConf.nf_conntrack_handle);

	for (ii=0; ii<globalConf.cur_rtnl_bufs; ii++)
	{
		free((void *)globalConf.rtnl_buf_pools[ii]);
	}
	ret = remove(CMM_PID_FILE_PATH);
	if (ret < 0)
		cmm_print(DEBUG_STDERR, "%s: file %s removal failed: %s\n", __func__, CMM_PID_FILE_PATH, strerror(errno));

	cmm_print(DEBUG_INFO, "%s: exiting\n", __func__);

	if (globalConf.logFile)
		fclose(globalConf.logFile);

	/* Killing ...*/
	exit(EXIT_SUCCESS);
}


int main (int argc, char ** argv)
{
	sigset_t block_mask;
	sigset_t timer_mask;
  	extern char *optarg;
	extern int optind;
	char confFilePath[512+1] = "";
	//struct sched_param schedParams;
	struct sigaction action;
	int option,ii;
	char *buf;
	int ret = 0;
	int ch;
	int rc;

	// Forward engine programmation is enabled by default
	globalConf.enable = 1;
	globalConf.debug_level = DEBUG_ERROR;
	globalConf.vlan_policy = ALLOW;
	globalConf.ff_enable = 1;
	globalConf.cli_listenaddr=htonl(INADDR_LOOPBACK);
#ifdef C2000_DPI
	globalConf.dpi_enable = 0;
#endif
	globalConf.asymff_enable = 0;
	globalConf.logFile = NULL;
	globalConf.log_level = 0;
	globalConf.tun_proto = IPPROTO_IPIP; /* Current default handling of TUN interface is an 4o6 tunnel*/
	globalConf.tun_family = AF_INET6;
	globalConf.enable_sam_itfs = 0; /* by default , this option will be disabled */

#ifdef MUTEX_DEBUG
	mutexes = 0;
#endif

	action.sa_sigaction = cmm_crit_err_hdlr;
	sigemptyset (&action.sa_mask);
	action.sa_flags = SA_RESTART | SA_SIGINFO;

	if (sigaction(SIGSEGV, &action, NULL) < 0)
	{
		fprintf(stderr, "sigaction((%s)) failed, %s\n", strsignal(SIGSEGV), strerror(errno));

		exit(EXIT_FAILURE);
	}

	if (sigaction(SIGBUS, &action, NULL) < 0)
	{
		fprintf(stderr, "sigaction((%s)) failed, %s\n", strsignal(SIGBUS), strerror(errno));

		exit(EXIT_FAILURE);
	}

	if (sigaction(SIGFPE, &action, NULL) < 0)
	{
		fprintf(stderr, "sigaction((%s)) failed, %s\n", strsignal(SIGFPE), strerror(errno));

		exit(EXIT_FAILURE);
	}

	if (sigaction(SIGILL, &action, NULL) < 0)
	{
		fprintf(stderr, "sigaction((%s)) failed, %s\n", strsignal(SIGILL), strerror(errno));

		exit(EXIT_FAILURE);
	}

	if (sigaction(SIGABRT, &action, NULL) < 0)
	{
		fprintf(stderr, "sigaction((%s)) failed, %s\n", strsignal(SIGABRT), strerror(errno));

		exit(EXIT_FAILURE);
	}

	// Analyse the command line
	while ((option = getopt(argc, argv, "c:f:n:hv")) != -1)
	{
		switch (option)
		{
			case 'c':	// Launch cmm as a client communicating with the cmm daemon
				cmmClient(optarg, argc-optind, &argv[optind]);
				return 0;

			case 'f':	// Specify configuration file
				//Get the argument
				strncpy(confFilePath, optarg, 512);
				confFilePath[512] = '\0';
				break;

			case 'n':       // Get the max conntrack connections
				ch = sscanf(optarg, "%u", &nf_conntrack_max);
				if (ch == EOF)	{
					fprintf(stderr, "sscanf failed, %s\n", strerror(errno));
					exit(EXIT_FAILURE);
				}
				break;

			case 'h':	// Print help
				cmmHelp();
				return 0;
			case 'v':	// Print version and exit
				cmmVersion();
				return 0;
			default:
				break;
		}
	}
/*	cmm_print(DEBUG_STDOUT, "nf_conntrack_max %u\n", nf_conntrack_max);*/

	// If cmm daemon is already running, return
	if (cmmIsDaemonRunning())
	{
		cmm_print(DEBUG_CRIT, "cmm daemon is already running\n");
		goto err0;
	}

	// Daemonize the application
	if(daemon(0, 1) == -1)
		goto err0;
	//Ensure clean termination
	action.sa_handler = sig_term_hdlr;
	sigemptyset(&action.sa_mask);
	sigaddset(&action.sa_mask, SIGTERM);
	action.sa_flags = 0;

	if (sigaction(SIGTERM, &action, NULL) < 0)
	{
		cmm_print(DEBUG_ERROR, "%s: sigaction() failed %s\n", __func__, strerror(errno));
		goto err0;
	}
	
	// Need to daemonize before creating a Pid File
	if (cmmCreateDaemonPidFile())
		goto err0;

	// Parse the configuration file
	if (cmmFcParser(strlen(confFilePath) ? confFilePath : CONF_FILE_PATH))
		goto err1;

	// Change priority of the process (we need to have a highest priority)
	//memset(&schedParams, 0 , sizeof(schedParams));
	//schedParams.sched_priority = 99;
	//sched_setscheduler(0, SCHED_FIFO, &schedParams);

	//Init process does not set stdout on console
	if(freopen("/dev/console", "w", stdout) == NULL)
		goto err0;		
	sigemptyset(&block_mask);
	sigaddset(&block_mask, SIGTERM);
	sigaddset(&block_mask, SIGPIPE);

	sigprocmask(SIG_BLOCK, &block_mask, NULL);

	// Open a Netfilter socket
	/* Use of the following handle must be protected by ctMutex */
	globalConf.nf_conntrack_handle = nfct_open(CONNTRACK, 0);
	if (!globalConf.nf_conntrack_handle)
	{
		cmm_print(DEBUG_CRIT, "%s: nfct_open()failed, %s\n", __func__, strerror(errno));
		goto err1;
	}

	/* create rtnl buffers pool */
	for (ii=0; ii<CMM_MAX_NUM_THREADS; ii++)
	{
		buf = (char *)calloc(CMM_MAX_64K_BUFF_SIZE + CMM_16B_ALIGN,sizeof(char));
		if (!buf)
			goto err1;
		globalConf.rtnl_buf_pools[ii] = (uint64_t )buf;
		buf = (char *)(((unsigned long)buf + CMM_16B_ALIGN -1) & ~(CMM_16B_ALIGN - 1));
		globalConf.rtnl_buf_pools_align[ii] = (uint64_t )buf;
		globalConf.cur_rtnl_bufs++;
	}

	/* Keep SIGALRM confined to the conntrack thread. Other threads use
	 * blocking syscalls that should not be interrupted by the periodic
	 * conntrack maintenance timer.
	 */
	sigemptyset(&timer_mask);
	sigaddset(&timer_mask, SIGALRM);
	rc = pthread_sigmask(SIG_BLOCK, &timer_mask, NULL);
	if (rc != 0)
	{
		cmm_print(DEBUG_ERROR, "%s: pthread_sigmask(SIG_BLOCK) failed %s\n", __func__, strerror(rc));
		goto err1;
	}

	if (cmmCtInit(&globalConf.ct) < 0)
		goto err1a;

	if (cmmDaemonInit(&globalConf.daemon) < 0)
		goto err2;

	if (cmmCliInit(&globalConf.cli) < 0)
		goto err3;

	/* If callback support is enabled, then CMM calls 3rd Party initialization function */
	globalConf.third_part_data = cmm_third_part_init();

	sigprocmask(SIG_UNBLOCK, &block_mask, NULL);

	/* Loop until sigterm is received */
	while (1)
		pause();

	cmm_print(DEBUG_INFO, "%s: exiting\n", __func__);

	return 0;

err3:
	cmmDaemonExit(&globalConf.daemon);

err2:
	cmmCtExit(&globalConf.ct);

err1a:
	nfct_close(globalConf.nf_conntrack_handle);

err1:
	for (ii=0; ii<globalConf.cur_rtnl_bufs; ii++)
	{
		free((void *)globalConf.rtnl_buf_pools[ii]);
	}
	ret = remove(CMM_PID_FILE_PATH);
	if (ret < 0)
		cmm_print(DEBUG_STDERR, "%s: file %s removal failed: %s\n", __func__, CMM_PID_FILE_PATH, strerror(errno));
err0:
	cmm_print(DEBUG_INFO, "%s: exiting\n", __func__);

	if (globalConf.logFile)
		fclose(globalConf.logFile);

	exit(EXIT_FAILURE);
}

/* function to return 64K buffer from pool, used from cmm_rtnl_listen */
char *cmm_get_rtnl_buf(uint32_t *buf_size)
{
	if (!globalConf.cur_rtnl_bufs)
		return NULL;
	*buf_size = CMM_MAX_64K_BUFF_SIZE;
	return (char *)(globalConf.rtnl_buf_pools_align[--globalConf.cur_rtnl_bufs]);
}

/* function to add 64k buffer to pool, called from cmm_rtnl_listen */
void cmm_free_rtnl_buf(char *buf)
{
	globalConf.rtnl_buf_pools_align[globalConf.cur_rtnl_bufs++] = (uint64_t)buf;
}
