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

/*
 * Trace event stubs intentionally empty. The §12 host-command opcode
 * layer (ask_hostcmd.c) that owned trace_ask_hostcmd_send/recv was
 * deleted in v1.3 Phase 3 — the Path A architecture bypasses the
 * wire-format encoder layer entirely and calls fman_pcd_cc_node_*
 * directly. New trace_ask_* helpers will be added back here once the
 * in-tree TRACE_EVENT() machinery (post-0048 migration) lands.
 */

#endif /* _ASK_TRACE_H */
