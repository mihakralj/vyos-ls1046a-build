 /*
  *  voicebuf.c
  *
  *  Copyright (C) 2010 Mindspeed Technologies, Inc.
  *  Copyright 2014-2016 Freescale Semiconductor, Inc.
  *  Copyright 2017,2021 NXP
 *
 * SPDX-License-Identifier:    GPL-2.0+
 * The GPL-2.0+ license for this file can be found in the COPYING.GPL file
 * included with this distribution or at http://www.gnu.org/licenses/gpl-2.0.html
 *
  */


#include "cmm.h"
#include "fpp.h"
#include "cmmd.h"
#include "voicebuf.h"

static int voice_file_fd[VOICE_FILE_MAX];

int voice_file_load(FCI_CLIENT *fci_handle, cmmd_voice_file_load_cmd_t *cmd, u_int16_t *res_buf, u_int16_t *res_len)
{
	fpp_voice_buffer_load_cmd_t fpp_cmd;
	struct usr_scatter_list sc;
	int file_fd;
	int file_size;
	int buf_fd;
	int i;
	int rc = 0;

	cmm_print(DEBUG_INFO, "%s\n", __func__);
	*res_len = 2;

	if (cmd->file_id >= VOICE_FILE_MAX)
	{
		cmm_print(DEBUG_ERROR, "%s: fileid(%d) out of range\n", __func__, cmd->file_id);
		res_buf[0] = CMMD_ERR_WRONG_COMMAND_PARAM;
		goto err0;
	}

	if (voice_file_fd[cmd->file_id])
	{
		cmm_print(DEBUG_ERROR, "%s: fileid(%d) already loaded\n", __func__, cmd->file_id);
		res_buf[0] = CMMD_ERR_WRONG_COMMAND_PARAM;
		goto err0;
	}

	file_fd = open(cmd->filename, O_RDONLY, 0);
	if (file_fd < 0)
	{
		cmm_print(DEBUG_ERROR, "%s: open(%s) error, %s\n", __func__, cmd->filename, strerror(errno));
		rc = -1;
		goto err0;
	}

	buf_fd = open(MEMBUF_CHAR_DEVNAME, O_WRONLY, 0);
	if (buf_fd < 0)
	{
		cmm_print(DEBUG_ERROR, "%s: open(%s) error, %s\n", __func__, MEMBUF_CHAR_DEVNAME, strerror(errno));
		rc = -1;
		goto err1;
	}

	file_size = lseek(file_fd, 0, SEEK_END);
	if (file_size < 0)
	{
		cmm_print(DEBUG_ERROR, "%s: lseek() error, %s\n", __func__, strerror(errno));
		rc = -1;
		goto err2;
	}

	rc = lseek(buf_fd, file_size, SEEK_SET);
	if (rc < 0)
	{
		cmm_print(DEBUG_ERROR, "%s: lseek() error, %s\n", __func__, strerror(errno));
		goto err2;
	}

	rc = lseek(file_fd, 0, SEEK_SET);
	if (rc < 0)
	{
		cmm_print(DEBUG_ERROR, "%s: lseek() error, %s\n", __func__, strerror(errno));
		goto err2;
	}

	rc = lseek(buf_fd, 0, SEEK_SET);
	if (rc < 0)
	{
		cmm_print(DEBUG_ERROR, "%s: lseek() error, %s\n", __func__, strerror(errno));
		goto err2;
	}

	while (1)
	{
		char buf[4096];
		int len;

		len = read(file_fd, buf, 4096);
		if (!len)
			break;

		if (len < 0)
		{
			if (errno != EINTR)
			{
				cmm_print(DEBUG_ERROR, "%s: read() error, %s\n", __func__, strerror(errno));
				rc = -1;
				goto err2;
			}
			continue;
		}

		while (len)
		{
			rc = write(buf_fd, buf, len);
			if (rc <= 0)
			{
				if (errno != EINTR)
				{
					cmm_print(DEBUG_ERROR, "%s: write() error, %s\n", __func__, strerror(errno));
					goto err2;
				}

				continue;
			}

			len -= rc;
		}
	}

	rc = ioctl(buf_fd, MEMBUF_GET_SCATTER, &sc);
	if (rc < 0)
	{
		cmm_print(DEBUG_ERROR, "%s: ioctl() error, %s\n", __func__, strerror(errno));
		goto err2;
	}

	fpp_cmd.buffer_id = cmd->file_id;
	fpp_cmd.payload_type = cmd->payload_type;
	fpp_cmd.frame_size = cmd->frame_size;

	fpp_cmd.entries = sc.entries;
	fpp_cmd.data_len = file_size;

	for (i = 0; i < sc.entries; i++)
	{
		fpp_cmd.page_order[i] = sc.pg_order[i];
		fpp_cmd.addr[i] = sc.addr[i];

		cmm_print(DEBUG_INFO, "%s: %d %x %x\n", __func__, i, fpp_cmd.page_order[i], fpp_cmd.addr[i]);
	}

	cmm_print(DEBUG_COMMAND, "%s: Send FPP_CMD_VOICE_BUFFER_LOAD\n", __func__);

	rc = fci_cmd(fci_handle, FPP_CMD_VOICE_BUFFER_LOAD, (u_int16_t *) &fpp_cmd, sizeof(fpp_cmd), res_buf, res_len);
	if (rc == 0 && res_buf[0] == FPP_ERR_OK)
	{
		voice_file_fd[cmd->file_id] = buf_fd;
	}
	else
	{
		if (rc < 0)
			cmm_print(DEBUG_ERROR, "%s: FPP_CMD_VOICE_BUFFER_LOAD failed, '%s'\n", __func__, strerror(errno));
		else
			cmm_print(DEBUG_ERROR, "%s: FPP_CMD_VOICE_BUFFER_LOAD failed, %d\n", __func__, res_buf[0]);
		goto err2;
	}

	close(file_fd);

	return rc;

err2:
	close(buf_fd);

err1:
	close(file_fd);

err0:
	return rc;
}


int voice_file_unload(FCI_CLIENT *fci_handle, cmmd_voice_file_unload_cmd_t *cmd, u_int16_t *res_buf, u_int16_t *res_len)
{
	fpp_voice_buffer_unload_cmd_t fpp_cmd;
	int rc = 0;

	cmm_print(DEBUG_INFO, "%s:\n", __func__);

	*res_len = 2;

	if (cmd->file_id >= VOICE_FILE_MAX)
	{
		cmm_print(DEBUG_ERROR, "%s: fileid(%d) out of range\n", __func__, cmd->file_id);
		res_buf[0] = CMMD_ERR_WRONG_COMMAND_PARAM;
		goto err;
	}

	if (!voice_file_fd[cmd->file_id])
	{
		cmm_print(DEBUG_ERROR, "%s: fileid(%d) not loaded\n", __func__, cmd->file_id);
		res_buf[0] = CMMD_ERR_WRONG_COMMAND_PARAM;
		goto err;
	}

	fpp_cmd.buffer_id = cmd->file_id;

	cmm_print(DEBUG_COMMAND, "%s: Send FPP_CMD_VOICE_BUFFER_UNLOAD\n", __func__);

	rc = fci_cmd(fci_handle, FPP_CMD_VOICE_BUFFER_UNLOAD, (u_int16_t *) &fpp_cmd, sizeof(fpp_cmd), res_buf, res_len);
	if (rc == 0 && res_buf[0] == FPP_ERR_OK)
	{
		close(voice_file_fd[cmd->file_id]);
		voice_file_fd[cmd->file_id] = 0;

	}
	else
	{
		if (rc < 0)
			cmm_print(DEBUG_ERROR, "%s: FPP_CMD_VOICE_BUFFER_UNLOAD failed, '%s'\n", __func__, strerror(errno));
		else
			cmm_print(DEBUG_ERROR, "%s: FPP_CMD_VOICE_BUFFER_UNLOAD failed, %d\n", __func__, res_buf[0]);
		goto err;
	}

err:
	return rc;
}

int voice_buffer_reset(FCI_CLIENT *fci_handle)
{
	int rc;
	int i;

	rc = fci_write(fci_handle, FPP_CMD_VOICE_BUFFER_RESET, 0, NULL);

	for (i = 0; i < VOICE_FILE_MAX;i++)
		if (voice_file_fd[i])
		{
			close(voice_file_fd[i]);
			voice_file_fd[i] = 0;
		}

	return rc;
}

static void voice_buffer_set_usage(void)
{
	cmm_print(DEBUG_STDOUT, 
			"Usage: set voicebuf \n"
			"\n"
			"                                  [load] <file_id> <payload_type> <frame_size> <filename>\n"
			"\n"
			"                                  [unload] <file_id>\n"
			"\n"
			"                                  [start] <socket_id> <buffer_id> <seq_number_base> <ssrc> <timestamp_base>\n"
			"\n"
			"                                  [stop] <socket_id>\n"
	          );
}


/* CMM client side voicebuf control */
int cmmVoiceBufSetProcess(int argc, char *argv[], daemon_handle_t daemon_handle)
{
	union u_rxbuf rxbuf;
	int i = 0;
	int rc;
	const char* cmd_str = NULL;
	unsigned int tmp;

	if (argc < 1)
	{
		fprintf(stderr, "%s: missing arguments for voicebuf command\n", __func__);
		goto err;
	}

	if (!strncasecmp(argv[i], "load", 4))
	{
		cmmd_voice_file_load_cmd_t cmd;

		if (argc < 5)
		{
			fprintf(stderr, "%s: missing arguments for voicebuf load command\n", __func__);
			goto err;
		}

		i++;
		tmp = strtoul(argv[i], NULL, 0);
		if (tmp > USHRT_MAX)
			goto err;

		cmd.file_id = tmp;

		i++;
		tmp = strtoul(argv[i], NULL, 0);
		if (tmp > USHRT_MAX)
			goto err;
		cmd.payload_type = tmp;

		i++;
		tmp = strtoul(argv[i], NULL, 0);
		if (tmp > USHRT_MAX)
			goto err;
		cmd.frame_size = tmp;

		i++;
		strncpy(cmd.filename, argv[i], CMMD_VOICE_FILE_MAX_NAMESIZE);
		STR_TRUNC_END(cmd.filename, CMMD_VOICE_FILE_MAX_NAMESIZE);

		rc = cmmSendToDaemon(daemon_handle, CMMD_CMD_VOICE_FILE_LOAD, &cmd, sizeof(cmd), rxbuf.rcvBuffer);
		cmd_str = "CMD_VOICE_FILE_LOAD";
	}
	else if (!strncasecmp(argv[i], "unload", 6))
	{
		cmmd_voice_file_unload_cmd_t cmd;

		if (argc < 2)
		{
			fprintf(stderr, "%s: missing arguments for voicebuf unload command\n", __func__);
			goto err;
		}

		i++;
		tmp = strtoul(argv[i], NULL, 0);
		if (tmp > USHRT_MAX)
			goto err;
		cmd.file_id = tmp;

		rc = cmmSendToDaemon(daemon_handle, CMMD_CMD_VOICE_FILE_UNLOAD, &cmd, sizeof(cmd), rxbuf.rcvBuffer);
		cmd_str = "CMD_VOICE_FILE_UNLOAD";
	}
	else if (!strncasecmp(argv[i], "start", 5))
	{
		fpp_voice_buffer_start_cmd_t cmd;

		if (argc < 6)
		{
			fprintf(stderr, "%s: missing arguments for voicebuf start command\n", __func__);
			goto err;
		}

		i++;
		tmp = strtoul(argv[i], NULL, 0);
		if (tmp > USHRT_MAX)
			goto err;
		cmd.socket_id = tmp;

		i++;
		tmp = strtoul(argv[i], NULL, 0);
		if (tmp > USHRT_MAX)
			goto err;
		cmd.buffer_id = tmp;

		i++;
		tmp = strtoul(argv[i], NULL, 0);
		if (tmp > USHRT_MAX)
			goto err;
		cmd.seq_number_base = tmp;

		i++;
		cmd.ssrc = strtoul(argv[i], NULL, 0);

		i++;
		cmd.timestamp_base = strtoul(argv[i], NULL, 0);

		rc = cmmSendToDaemon(daemon_handle, FPP_CMD_VOICE_BUFFER_START, &cmd, sizeof(cmd),rxbuf.rcvBuffer);
		cmd_str = "CMD_VOICE_BUFFER_START";
	}
	else if (!strncasecmp(argv[i], "stop", 4))
	{
		fpp_voice_buffer_stop_cmd_t cmd;

		if (argc < 2)
		{
			fprintf(stderr, "%s: missing arguments for voicebuf stop command\n", __func__);
			goto err;
		}

		i++;
		tmp = strtoul(argv[i], NULL, 0);
		if (tmp > USHRT_MAX)
			goto err;
		cmd.socket_id = tmp;

		rc = cmmSendToDaemon(daemon_handle, FPP_CMD_VOICE_BUFFER_STOP, &cmd, sizeof(cmd), rxbuf.rcvBuffer);
		cmd_str = "CMD_VOICE_BUFFER_STOP";
	}
	else
		goto err;

	if (rc == 2)
	{
		if (rxbuf.result != CMMD_ERR_OK)
		{
			fprintf(stdout, "%s: error sending %s: %d\n", __func__, cmd_str, rxbuf.result);
			return -1;
		}
	}
	else
	{
		if (rc > 0)
			fprintf(stdout, "%s: unexpected response size received for %s: %d\n", __func__, cmd_str, rc);
		else
			fprintf(stdout, "%s: error sending %s: '%s'\n", __func__, cmd_str, strerror(errno));
		return -1;
	}

	return 0;

err:
	/* print usage */
	voice_buffer_set_usage();

	return -1;
}

