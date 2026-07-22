/* SPDX-License-Identifier: GPL-2.0 */
/*
 * xdp_redirect.c — XDP program for AF_XDP zero-copy redirect on DPAA1.
 *
 * This BPF program is loaded by VPP's af_xdp plugin via the 'prog' parameter.
 * VPP 25.10's built-in xdp-dispatcher.o has no xsks_map, so bpf_redirect_map()
 * silently fails — ZC RX stays at 0 (M4 T-M4-3b root cause).
 *
 * This program provides an xsks_map that VPP populates via
 * xsk_socket__update_xskmap().  Every received frame is redirected to the
 * XSK socket bound to queue 0; if no socket is bound (XSK socket not yet
 * created), frames fall through via XDP_PASS to the kernel network stack.
 *
 * Build:
 *   clang -O2 -target bpf -g -c xdp_redirect.c -o xdp_redirect.o
 *
 * Load (VPP af_xdp plugin):
 *   prog=/usr/share/vpp/xdp_redirect.o
 */

#include <linux/bpf.h>
#include <bpf/bpf_helpers.h>

struct {
	__uint(type, BPF_MAP_TYPE_XSKMAP);
	__uint(key_size, sizeof(int));
	__uint(value_size, sizeof(int));
	__uint(max_entries, 4);  /* one XSK socket per queue, 4 queues on DPAA1 */
} xsks_map SEC(".maps");

SEC("xdp")
int xdp_redirect_func(struct xdp_md *ctx)
{
	/* DPAA1 reports 1 combined channel via ethtool, with
	 * rx_queue_index always 0 (patch-dpaa-xdp-queue-index.py).
	 * All frames are redirected to queue_index 0.
	 */
	int idx = ctx->rx_queue_index;

	/* bpf_redirect_map returns XDP_REDIRECT on success, or XDP_PASS
	 * if no socket is bound at the given index (VPP hasn't started yet).
	 */
	return bpf_redirect_map(&xsks_map, idx, XDP_PASS);
}

char _license[] SEC("license") = "GPL";
