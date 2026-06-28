/*
 * Minimal harness for cmm_parse_rtattr().
 *
 * Feed arbitrary bytes as argv[1] hex. The process should not read beyond
 * the supplied buffer and must tolerate malformed trailing attributes.
 *
 * Copyright (c) 2026 Mono Technologies Inc.
 *
 * SPDX-License-Identifier: GPL-2.0+
 */
#include <ctype.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <linux/rtnetlink.h>

#include "rtnl_parse.h"

void cmm_parse_rtattr_overflow(const char *func, int line, int len_remaining)
{
	(void)func;
	(void)line;
	(void)len_remaining;
}

static int hexval(int c)
{
	if (c >= '0' && c <= '9')
		return c - '0';
	if (c >= 'a' && c <= 'f')
		return c - 'a' + 10;
	if (c >= 'A' && c <= 'F')
		return c - 'A' + 10;
	return -1;
}

static size_t decode_hex(const char *hex, uint8_t **out)
{
	size_t n = strlen(hex);
	size_t bytes = 0;
	uint8_t *buf;

	if (n & 1)
		return 0;

	buf = calloc(n / 2, 1);
	if (!buf)
		return 0;

	for (size_t i = 0; i < n; i += 2) {
		int hi = hexval((unsigned char)hex[i]);
		int lo = hexval((unsigned char)hex[i + 1]);

		if (hi < 0 || lo < 0) {
			free(buf);
			return 0;
		}

		buf[bytes++] = (uint8_t)((hi << 4) | lo);
	}

	*out = buf;
	return bytes;
}

int main(int argc, char **argv)
{
	struct rtattr *tb[256];
	uint8_t *buf = NULL;
	size_t len;

	if (argc != 2) {
		fprintf(stderr, "usage: %s <hex-bytes>\n", argv[0]);
		return 2;
	}

	len = decode_hex(argv[1], &buf);
	if (!len)
		return 1;

	cmm_parse_rtattr(tb, 255, (struct rtattr *)buf, (int)len);
	free(buf);
	return 0;
}
