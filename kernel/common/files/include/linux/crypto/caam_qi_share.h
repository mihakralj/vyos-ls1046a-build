/* SPDX-License-Identifier: GPL-2.0 */
/*
 * CAAM/QI external descriptor sharing.
 *
 * Allows an in-kernel consumer (e.g. NXP LS1046A FMan 210 ucode driving
 * an offline port for IPsec hardware fast-path) to dequeue completed
 * frames from a CAAM/QI request context's response side directly,
 * instead of going through the standard kernel-crypto-API callback.
 *
 * The CAAM driver context is created in the normal way (via
 * caam_drv_ctx_init() out of caamalg_qi.c). After registration the
 * CAAM hardware is reprogrammed to enqueue completed frames to a
 * caller-supplied sink FQID instead of the per-CPU response FQ.
 *
 * Single-consumer per ctx. Reference-counted. Idempotent release.
 *
 * See specs/ask2-rewrite-spec.md §8.1 (vyos-ls1046a-build) for the
 * design rationale.
 *
 * Copyright 2026 Mono Networks / VyOS LS1046A maintainers.
 */

#ifndef _LINUX_CRYPTO_CAAM_QI_SHARE_H
#define _LINUX_CRYPTO_CAAM_QI_SHARE_H

#include <linux/types.h>

struct caam_drv_ctx;

/**
 * caam_qi_ext_consumer_register - share a CAAM/QI descriptor's response path
 *				   with an external dequeuer.
 * @ctx:		descriptor context previously created via
 *			caam_drv_ctx_init() (typically by caamalg_qi.c on
 *			behalf of an xfrm SA install).
 * @consumer_name:	diagnostic string for tracepoints / dev_dbg, e.g.
 *			"ask:fman0:op1:spi-0x12345678". Must be non-NULL.
 * @sink_fqid:		FQID that CAAM should enqueue completed (response)
 *			frames to. Caller-owned (typically a 210-managed
 *			FMan offline-port RX FQ). REPLACES the per-CPU
 *			response path; while an external consumer is
 *			registered, in-kernel crypto-API callers of @ctx will
 *			NOT receive completions via the normal callback.
 * @caam_req_fqid:	[out] FQID of the CAAM request queue that the
 *			external producer (e.g. FMan 210 ucode) must enqueue
 *			encrypted ESP frames to. This is the request FQ's
 *			current FQID, exposed read-only to the caller. May
 *			change across register/release cycles (see Notes).
 *
 * Single-consumer: returns -EBUSY if @ctx already has an external
 * consumer registered.
 *
 * Refcount: increments @ctx->refcnt on success. The caller must invoke
 * caam_qi_ext_consumer_release() to balance it.
 *
 * Notes:
 * - Implementation parks the existing request FQ, allocates a fresh one
 *   programmed with sink_fqid as its response target, and atomically
 *   swaps it in (mirroring caam_drv_ctx_update()). The new FQ has a
 *   different (dynamically allocated) FQID; that is the FQID returned
 *   in *@caam_req_fqid.
 * - Frames already in flight when this is called complete on the OLD
 *   response path; only frames enqueued after the swap reach
 *   @sink_fqid.
 * - Caller is responsible for any synchronisation needed against the
 *   external dequeuer when sink_fqid is shared (e.g. parking the FMan
 *   offline port before release).
 *
 * Return: 0 on success, -EINVAL on bad arguments, -EBUSY if an external
 * consumer is already registered, -ENOMEM on allocation failure, or any
 * negative errno propagated from the underlying QMan FQ operations.
 */
int caam_qi_ext_consumer_register(struct caam_drv_ctx *ctx,
				  const char *consumer_name,
				  u32 sink_fqid,
				  u32 *caam_req_fqid);

/**
 * caam_qi_ext_consumer_release - undo caam_qi_ext_consumer_register().
 * @ctx: descriptor context that was previously registered.
 *
 * Restores the default per-CPU response path so subsequent
 * caam_qi_enqueue() calls on @ctx are dispatched to the normal
 * caam_qi_cbk callback again.
 *
 * Idempotent: a no-op if no external consumer is currently registered
 * on @ctx (or if @ctx is NULL / IS_ERR).
 *
 * In-flight frames already queued on the previous sink FQ are NOT
 * retroactively redirected. Decrements @ctx->refcnt.
 */
void caam_qi_ext_consumer_release(struct caam_drv_ctx *ctx);

#endif /* _LINUX_CRYPTO_CAAM_QI_SHARE_H */
