/* SPDX-License-Identifier: GPL-2.0 */
/*
 * ASK2 - tracepoints
 *
 * Module-private trace events. We intentionally do NOT use the
 * TRACE_EVENT()/CREATE_TRACE_POINTS machinery from <trace/define_trace.h>
 * here, because that requires a separate per-system header in
 * include/trace/events/ which (a) cannot live inside an out-of-tree
 * module's source tree without ugly Kbuild gymnastics, and (b) creates
 * symbol-export issues for ask_kunit.ko which is built as a
 * second .ko from the same source.
 *
 * Instead we use lightweight static inlines that wrap trace_printk()
 * in the kunit/devel build (always-on for now) and become no-ops in
 * the production build once CONFIG_NET_ASK_TRACE is set to "off". This
 * gives subsystem code a single ABI to call (trace_ask_*) and lets PR6
 * golden-hex tests assert on the encoder output without depending on
 * the in-kernel tracing infrastructure.
 *
 * When ask.ko eventually moves in-tree, these wrappers swap to real
 * TRACE_EVENT() definitions in include/trace/events/ask.h and the
 * call-sites stay byte-identical.
 */
#ifndef _ASK_TRACE_H
#define _ASK_TRACE_H

#include <linux/kernel.h>
#include <linux/types.h>

#ifndef ASK_TRACE_QUIET
#define ASK_TRACE_QUIET 0
#endif

static inline void trace_ask_hostcmd_send(u8 op, u16 len, const void *payload)
{
	if (ASK_TRACE_QUIET)
		return;
	pr_debug("ask: hostcmd send op=0x%02x len=%u payload=%p\n",
		 op, (unsigned int)len, payload);
}

static inline void trace_ask_hostcmd_recv(u8 op, u16 len, int rc,
					  const void *payload)
{
	if (ASK_TRACE_QUIET)
		return;
	pr_debug("ask: hostcmd recv op=0x%02x len=%u rc=%d payload=%p\n",
		 op, (unsigned int)len, rc, payload);
}

#endif /* _ASK_TRACE_H */