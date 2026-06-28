/*
 *
 *  Copyright (C) 2010 Mindspeed Technologies, Inc.
 *  Copyright 2014-2016 Freescale Semiconductor, Inc.
 *  Copyright 2017,2021 NXP
 *
 * SPDX-License-Identifier:    GPL-2.0+
 * The GPL-2.0+ license for this file can be found in the COPYING.GPL file
 * included with this distribution or at http://www.gnu.org/licenses/gpl-2.0.html
 *
 *
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <sys/types.h>
#include <sys/ipc.h>
#include <sys/msg.h>
#include <unistd.h>

#include "libcmm.h"
#include "cmm.h"

#define MAX_PATH 32

struct cmm_handle
{
	long	uniqueid;
	int	tmp_fd;
	int 	queue_id_rx;
	int 	queue_id_tx;
	char	path[MAX_PATH];
};

int getpgid(pid_t _pid); /* XXX: uclibs doesn't declare this function is unistd.h as it has to, 
			  * therefore declare it by ourselves
			  */

static int get_daemon_pid()
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

static int gen_uniqueid(cmm_handle_t *handle)
{
	unsigned i;
        srandom(time(NULL));
	
	for (i = 0; i < 10; i++) {
		handle->uniqueid = random();
		snprintf(handle->path, sizeof(handle->path), "%s.%lu", "/tmp/cmm", handle->uniqueid);
		handle->tmp_fd = open(handle->path, O_CREAT | O_EXCL, 0600);
		if (handle->tmp_fd == -1)
			continue;
		else
			return 0;
	}

        return -1;
}

cmm_handle_t *cmm_open(void)
{
	key_t key;
	int pid = get_daemon_pid();
	cmm_handle_t *handle;

	if (!pid) {
                fprintf(stderr, "Daemon is not running\n");
                return NULL;
        }

	handle = malloc(sizeof(cmm_handle_t));
	if (!handle) {
		fprintf(stderr, "Error allocating CMM handle\n");
		return NULL;
	}

	memset(handle, 0, sizeof(cmm_handle_t));
	
	if (gen_uniqueid(handle) != 0) {
		fprintf(stderr, "Error getting uniqueid\n");
		goto ERR_UNIQUEID;
	}

        pid = ((pid & 0xff) ^ ((pid >> 8) & 0xff)) | 1;
        key = ftok("/tmp", pid);
        if (key == (key_t)-1) {
                fprintf(stderr, "ftok(%d) failed, %s\n", pid, strerror(errno));
                goto ERR_QUEUE;
        }

        handle->queue_id_rx = msgget(key, 0);
        if (handle->queue_id_rx < 0) {
                fprintf(stderr, "rx msgget() failed, %s\n", strerror(errno));
                goto ERR_QUEUE;
        }

	key = ftok("/tmp", pid ^ 0xff);
        if (key == (key_t)-1) {
                fprintf(stderr, "ftok(%d) failed, %s\n", pid, strerror(errno));
                goto ERR_QUEUE;
        }

        handle->queue_id_tx = msgget(key, 0);
        if (handle->queue_id_tx < 0) {
                fprintf(stderr, "tx msgget() failed, %s\n", strerror(errno));
                goto ERR_QUEUE;
        }

	return handle;

ERR_QUEUE:
	close(handle->tmp_fd);
	unlink(handle->path);
ERR_UNIQUEID:
	free(handle);

	return NULL;
}

void cmm_close(cmm_handle_t* handle)
{
	close(handle->tmp_fd);
	unlink(handle->path);
	free(handle);
}

int cmm_send(cmm_handle_t *handle, cmm_command_t* cmd, int nonblocking)
{
	cmd->msg_type = handle->uniqueid;

        return msgsnd(handle->queue_id_tx, 
		      cmd, 
		      sizeof(cmm_command_t) - sizeof(cmd->buf) + cmd->length, 
		      nonblocking ? IPC_NOWAIT : 0);
}

int cmm_recv(cmm_handle_t *handle, cmm_response_t* res, int nonblocking)
{
        int len = msgrcv(handle->queue_id_rx, 
			 res, 
			 sizeof(cmm_response_t), 
			 handle->uniqueid, 
			 nonblocking ? IPC_NOWAIT : 0);
	if (len < 0)
                return len;

	if (res->daemon_errno) {
		errno = res->daemon_errno;
		return -1;
	}

	return len;
}

