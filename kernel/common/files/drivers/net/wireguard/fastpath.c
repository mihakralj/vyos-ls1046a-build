// SPDX-License-Identifier: GPL-2.0
/*
 * WireGuard fast path for DPAA1 (plans/ASK2-WIREGUARD-FASTPATH-PLAN.md).
 *
 * Phase P2a, "lab seal": no peer, no handshake.  Frames that FMan flow
 * records steer to the dpaa_wgfp queues (drivers/net/ethernet/freescale/dpaa)
 * are sealed in place with a fixed key into WireGuard transport-data packets
 * and transmitted on a chosen port, never building an sk_buff.  It measures
 * the real cost of the transmit fast path (crypto, header build, enqueue).
 * The key is a lab value, the counter is a plain global, and nothing here
 * runs unless root writes a configuration to the "wgfp_lab" parameter, so it
 * is NOT a usable VPN; P2b replaces the fixed state with a real peer keypair.
 *
 *   echo "<egress> <dst mac> <src mac> <src ip> <dst ip> <sport> <dport> \
 *         <receiver index> <32-byte key as 64 hex digits>" \
 *        > /sys/module/wireguard/parameters/wgfp_lab
 *   echo off > /sys/module/wireguard/parameters/wgfp_lab
 *
 * Input frames must be Ethernet + IPv4; others are declined (the DPAA sink
 * just releases them).
 */

#include <crypto/chacha20poly1305.h>
#include <crypto/poly1305.h>
#include <linux/atomic.h>
#include <linux/bitops.h>
#include <linux/etherdevice.h>
#include <linux/fsl/dpaa_wgfp.h>
#include <linux/hashtable.h>
#include <linux/hex.h>
#include <linux/if_ether.h>
#include <linux/inet.h>
#include <linux/jhash.h>
#include <linux/tcp.h>
#include <linux/ip.h>
#include <linux/ipv6.h>
#include <linux/module.h>
#include <linux/moduleparam.h>
#include <linux/netdevice.h>
#include <linux/rtnetlink.h>
#include <linux/udp.h>
#include <net/checksum.h>
#include <net/ip.h>
#include <net/ipv6.h>

#include <linux/inetdevice.h>
#include <linux/jiffies.h>
#include <linux/workqueue.h>
#include <net/arp.h>
#include <net/neighbour.h>
#include <net/route.h>
#if IS_REACHABLE(CONFIG_NF_CONNTRACK)
#include <net/netfilter/nf_conntrack.h>
#include <net/netfilter/nf_conntrack_core.h>
#include <net/netfilter/nf_conntrack_helper.h>
#include <net/netfilter/nf_conntrack_zones.h>
#endif

#include "allowedips.h"
#include "device.h"
#include "fastpath.h"
#include "messages.h"
#include "noise.h"
#include "peer.h"
#include "queueing.h"
#include "timers.h"

/* Ethernet + IPv4 + UDP + WireGuard transport header, in front of the data. */
#define WGFP_HDR_LEN	(ETH_HLEN + sizeof(struct iphdr) + sizeof(struct udphdr) + \
			 sizeof(struct message_data))
#define WGFP_ADDED	(WGFP_HDR_LEN - ETH_HLEN)

struct wgfp_lab {
	u8 key[CHACHA20POLY1305_KEY_SIZE];
	u8 tmpl[ETH_HLEN + sizeof(struct iphdr) + sizeof(struct udphdr)];
	__le32 rcv_idx;
	int egress;
	atomic64_t counter;
};

static struct wgfp_lab wgfp_lab_state;
static bool wgfp_lab_on;
static DEFINE_MUTEX(wgfp_lab_lock);
static char wgfp_lab_cfg[16] = "off";
#define WGFP_OWNER_TOKEN_LEN	32
static char wgfp_peer_owner[WGFP_OWNER_TOKEN_LEN + 1];
struct wgfp_binds;
static struct wgfp_binds __rcu *wgfp_bs;

static int wgfp_lab_frame(void *priv, struct dpaa_wgfp_frame *f)
{
	struct wgfp_lab *l = priv;
	u8 *data = f->buf + f->off;
	u8 *inner = data + ETH_HLEN;
	const struct iphdr *iph;
	struct message_data *wg;
	struct udphdr *udp;
	struct iphdr *oip;
	u32 ip_len, plain, tot;
	u64 ctr;
	u8 *hdr;

	if (f->len < ETH_HLEN + sizeof(*iph) ||
	    ((struct ethhdr *)data)->h_proto != htons(ETH_P_IP))
		return 0;
	iph = (const struct iphdr *)inner;
	if (iph->version != 4)
		return 0;
	ip_len = ntohs(iph->tot_len);
	if (ip_len < sizeof(*iph) || ETH_HLEN + ip_len > f->len)
		return 0;

	plain = ALIGN(ip_len, 16);
	if (f->off < WGFP_ADDED ||
	    f->off + ETH_HLEN + plain + POLY1305_DIGEST_SIZE > f->buf_size)
		return 0;

	memset(inner + ip_len, 0, plain - ip_len);
	ctr = (u64)atomic64_inc_return(&l->counter) - 1;
	chacha20poly1305_encrypt(inner, inner, plain, NULL, 0, ctr, l->key);

	hdr = inner - WGFP_HDR_LEN;
	memcpy(hdr, l->tmpl, sizeof(l->tmpl));

	tot = sizeof(struct iphdr) + sizeof(struct udphdr) +
	      sizeof(struct message_data) + plain + POLY1305_DIGEST_SIZE;
	oip = (struct iphdr *)(hdr + ETH_HLEN);
	oip->tot_len = htons(tot);
	oip->check = 0;
	oip->check = ip_fast_csum((const u8 *)oip, oip->ihl);
	udp = (struct udphdr *)(oip + 1);
	udp->len = htons(tot - sizeof(struct iphdr));

	wg = (struct message_data *)(udp + 1);
	wg->header.type = cpu_to_le32(MESSAGE_DATA);
	wg->key_idx = l->rcv_idx;
	wg->counter = cpu_to_le64(ctr);

	dpaa_wgfp_xmit(f, l->egress, f->off - WGFP_ADDED,
		       WGFP_HDR_LEN + plain + POLY1305_DIGEST_SIZE);
	return 1;
}

static const struct dpaa_wgfp_ops wgfp_lab_ops = {
	.frame = wgfp_lab_frame,
};

static void wgfp_lab_stop(void)
{
	if (wgfp_lab_on) {
		dpaa_wgfp_unregister();
		wgfp_lab_on = false;
	}
}

static int wgfp_lab_start(const char *cfg)
{
	struct wgfp_lab *l = &wgfp_lab_state;
	char egress[IFNAMSIZ], dmac[24], smac[24], sip[20], dip[20], hex[80];
	struct net_device *dev;
	struct ethhdr *eth;
	struct iphdr *ip;
	struct udphdr *udp;
	u8 mac_d[ETH_ALEN], mac_s[ETH_ALEN];
	__be32 a_s, a_d;
	u16 sport, dport;
	u32 idx;
	int err;

	if (sscanf(cfg, "%15s %23s %23s %19s %19s %hu %hu %u %79s",
		   egress, dmac, smac, sip, dip, &sport, &dport, &idx, hex) != 9)
		return -EINVAL;
	if (!mac_pton(dmac, mac_d) || !mac_pton(smac, mac_s) ||
	    !in4_pton(sip, -1, (u8 *)&a_s, -1, NULL) ||
	    !in4_pton(dip, -1, (u8 *)&a_d, -1, NULL) ||
	    strlen(hex) != 2 * CHACHA20POLY1305_KEY_SIZE ||
	    hex2bin(l->key, hex, CHACHA20POLY1305_KEY_SIZE))
		return -EINVAL;

	dev = dev_get_by_name(&init_net, egress);
	if (!dev)
		return -ENODEV;
	err = dpaa_wgfp_egress(dev);
	dev_put(dev);
	if (err < 0)
		return err;
	l->egress = err;

	eth = (struct ethhdr *)l->tmpl;
	ether_addr_copy(eth->h_dest, mac_d);
	ether_addr_copy(eth->h_source, mac_s);
	eth->h_proto = htons(ETH_P_IP);
	ip = (struct iphdr *)(eth + 1);
	memset(ip, 0, sizeof(*ip));
	ip->version = 4;
	ip->ihl = 5;
	ip->frag_off = htons(IP_DF);
	ip->ttl = 64;
	ip->protocol = IPPROTO_UDP;
	ip->saddr = a_s;
	ip->daddr = a_d;
	udp = (struct udphdr *)(ip + 1);
	memset(udp, 0, sizeof(*udp));
	udp->source = htons(sport);
	udp->dest = htons(dport);	/* check stays 0: legal for IPv4 */
	l->rcv_idx = cpu_to_le32(idx);
	atomic64_set(&l->counter, 0);

	err = dpaa_wgfp_register(&wgfp_lab_ops, l);
	if (err)
		return err;
	wgfp_lab_on = true;
	pr_info("fastpath lab seal on: %s -> %pI4:%u, receiver index %u\n",
		egress, &a_d, dport, idx);
	return 0;
}

static int wgfp_lab_cfg_set(const char *val, const struct kernel_param *kp)
{
	char buf[256];
	int err = 0;

	strscpy(buf, val, sizeof(buf));
	strim(buf);
	mutex_lock(&wgfp_lab_lock);
	if (!strcmp(buf, "off")) {
		wgfp_lab_stop();
		strscpy(wgfp_lab_cfg, "off", sizeof(wgfp_lab_cfg));
	} else if (wgfp_peer_owner[0]) {
		err = -EBUSY;
	} else {
		wgfp_lab_stop();
		err = rcu_access_pointer(wgfp_bs) ? -EBUSY : wgfp_lab_start(buf);
		strscpy(wgfp_lab_cfg, err ? "off" : "on", sizeof(wgfp_lab_cfg));
	}
	mutex_unlock(&wgfp_lab_lock);
	return err;
}

static int wgfp_lab_cfg_get(char *buf, const struct kernel_param *kp)
{
	return scnprintf(buf, PAGE_SIZE, "%s sealed=%lld\n", wgfp_lab_cfg,
			 (long long)atomic64_read(&wgfp_lab_state.counter));
}

static const struct kernel_param_ops wgfp_lab_ops_param = {
	.set = wgfp_lab_cfg_set,
	.get = wgfp_lab_cfg_get,
};
module_param_cb(wgfp_lab, &wgfp_lab_ops_param, NULL, 0600);
MODULE_PARM_DESC(wgfp_lab, "DPAA fast path lab seal (P2a): see fastpath.c; \"off\" disables");

/*
 * Peer mode (P2b/P2c): the same in-place seal, keyed from real WireGuard
 * peers of one or more bound wg devices.  Each frame uses its peer's current
 * keypair (receiver index, key, atomic sending counter), honours
 * REJECT_AFTER_*, triggers a rekey at REKEY_AFTER_*, pads to 16 within the
 * device MTU and feeds the peer timers.
 *
 * Flows, not peers, are the unit (P2b-2): wg_xmit() offers every packet to
 * wg_fastpath_learn(), which learns inner IPv4 TCP/UDP flows of a bound
 * device that arrived on a DPAA port and that the firewall admitted, and
 * installs an FMan record steering the flow's later frames to the sinks.  The
 * sink path finds the flow (lockless, RCU) and through it the per-peer state:
 * the outer Ethernet/IPv4/UDP template, built from the peer's endpoint through
 * the routing table and neighbour cache (any DPAA port can be the egress),
 * refreshed once a second or at once when a frame shows the peer roamed.
 *
 * A frame it cannot seal is punted (P2b-3): copied into an sk_buff and
 * received again on its flow's ingress port, so the stack handles it
 * (handshake queueing, ICMP, PMTU, TTL, AllowedIPs).  AllowedIPs are re-checked
 * for every record each aging period.
 *
 * P3 receive offload is unavailable: wgfp_rx is hard-disabled because the
 * current admission evidence does not prove that the exact tuple and ingress
 * passed INPUT, and sink FDs do not carry ingress identity.  Do not restore an
 * enable control until both properties and policy withdrawal are proven.
 *
 *   echo "wg0 [wg1 ...]" > /sys/module/wireguard/parameters/wgfp_peer
 *   echo off > /sys/module/wireguard/parameters/wgfp_peer
 *
 * Writing a list replaces the bound set (a full teardown and rebuild: flows
 * relearn within a second; the VyOS CLI writes the whole list on commit).
 * A wgfp_peer_lease reservation blocks other peer and lab writers.  A leased
 * bind resolves owner-tagged link altnames; stop retains the reservation until
 * the owner has removed its links and explicitly releases it.
 */

#define WGFP_MAX_BIND	8
#define WGFP_OWNER_ALIAS_MAX	63
#define WGFP_OWNER_ALIASES_SIZE	(WGFP_MAX_BIND * (WGFP_OWNER_ALIAS_MAX + 1))

struct wgfp_tmpl {
	struct rcu_head rcu;
	int egress;		/* dpaa_wgfp_egress() handle */
	int ifindex;		/* that port */
	u8 family;		/* outer: AF_INET or AF_INET6 */
	u8 hlen;		/* Ethernet + IP + UDP */
	u8 hdr[ETH_HLEN + sizeof(struct ipv6hdr) + sizeof(struct udphdr)];
};

struct wgfp_binds {
	unsigned int n;
	struct net_device *wgdev[WGFP_MAX_BIND];	/* references */
	unsigned long invalidated;
	bool stopping;
};

/* One peer with learned flows.  Created by the first flow, freed by the aging
 * pass once it has none; flows point at it (fl->pst) and keep it alive. */
struct wgfp_pst {
	struct hlist_node node;		/* wgfp_psts, under wgfp_flows_lock */
	struct rcu_head rcu;
	struct wg_peer *peer;		/* reference */
	struct net_device *wgdev;
	struct wgfp_tmpl __rcu *tmpl;
	struct delayed_work refresh;
	unsigned long last_timer;
	unsigned int nflows;		/* under wgfp_flows_lock */
	struct wgfp_flow *rxfl;		/* P3 outer record, under the lock */
	bool stopping;
};

static const bool wgfp_rx = false;
static void wgfp_pst_rx_sync(struct wgfp_pst *pst, const struct wgfp_tmpl *t);
static bool wgfp_ct_admitted(struct net *net, struct sk_buff *skb,
			     const struct dpaa_wgfp_tuple *t, bool *absent);

static char wgfp_peer_cfg[WGFP_MAX_BIND * (IFNAMSIZ + 1) + 8] = "off";
static char wgfp_owner_aliases[WGFP_OWNER_ALIASES_SIZE];
static DEFINE_PER_CPU(atomic64_t, wgfp_sealed);
static atomic64_t wgfp_declined, wgfp_rekeys, wgfp_punted;
static DEFINE_PER_CPU(atomic64_t, wgfp_rx_frames);
static DEFINE_PER_CPU(atomic64_t, wgfp_rx_stack);

/* Resolve @dst's neighbour on @dev into @t's Ethernet header; false (and an
 * ARP/ND probe) while it is not valid. */
static bool wgfp_tmpl_l2(struct wgfp_tmpl *t, struct net_device *dev,
			 struct neigh_table *tbl, const void *nh, __be16 proto)
{
	struct ethhdr *eth = (struct ethhdr *)t->hdr;
	struct neighbour *n;

	n = tbl == &arp_tbl ? __ipv4_neigh_lookup(dev, *(const u32 *)nh) :
	    __ipv6_neigh_lookup(dev, nh);
	if (!n || !(n->nud_state & NUD_VALID)) {
		if (n)
			neigh_release(n);
		else
			neigh_event_send(__neigh_create(tbl, nh, dev, true), NULL);
		return false;
	}
	read_lock_bh(&n->lock);
	ether_addr_copy(eth->h_dest, n->ha);
	read_unlock_bh(&n->lock);
	neigh_release(n);
	ether_addr_copy(eth->h_source, dev->dev_addr);
	eth->h_proto = proto;
	t->ifindex = dev->ifindex;
	return true;
}

static struct wgfp_tmpl *wgfp_tmpl4(struct wgfp_pst *pst, const struct endpoint *ep)
{
	struct wg_device *wg = pst->peer->device;
	struct flowi4 fl = {
		.daddr = ep->addr4.sin_addr.s_addr,
		.saddr = ep->src4.s_addr,
		.fl4_dport = ep->addr4.sin_port,
		.flowi4_mark = wg->fwmark,
		.flowi4_proto = IPPROTO_UDP,
	};
	struct wgfp_tmpl *t;
	struct rtable *rt;
	struct iphdr *ip;
	struct udphdr *udp;
	__be32 nh;

	rt = ip_route_output_key(dev_net(pst->wgdev), &fl);
	if (IS_ERR(rt))
		return NULL;
	t = kzalloc(sizeof(*t), GFP_KERNEL);
	if (!t)
		goto out;
	t->egress = dpaa_wgfp_egress(rt->dst.dev);	/* < 0: not a DPAA port */
	nh = rt_nexthop(rt, fl.daddr);
	/* outer family policy: the port under the tunnel */
	if (t->egress < 0 || !dpaa_wgfp_family_ok(rt->dst.dev, AF_INET) ||
	    !wgfp_tmpl_l2(t, rt->dst.dev, &arp_tbl, &nh, htons(ETH_P_IP))) {
		kfree(t);
		t = NULL;
		goto out;
	}
	t->family = AF_INET;
	t->hlen = ETH_HLEN + sizeof(*ip) + sizeof(*udp);
	ip = (struct iphdr *)(t->hdr + ETH_HLEN);
	ip->version = 4;
	ip->ihl = 5;
	ip->frag_off = htons(IP_DF);
	ip->ttl = ip4_dst_hoplimit(&rt->dst);
	ip->protocol = IPPROTO_UDP;
	ip->saddr = fl.saddr ?: inet_select_addr(rt->dst.dev, nh, RT_SCOPE_UNIVERSE);
	ip->daddr = fl.daddr;
	udp = (struct udphdr *)(ip + 1);
	udp->source = htons(wg->incoming_port);
	udp->dest = fl.fl4_dport;
out:
	ip_rt_put(rt);
	return t;
}

/* IPv6 outer (C2): the UDP checksum is mandatory, so the seal path sums the
 * datagram in software after encrypting it. */
static struct wgfp_tmpl *wgfp_tmpl6(struct wgfp_pst *pst, const struct endpoint *ep)
{
#if IS_ENABLED(CONFIG_IPV6)
	struct wg_device *wg = pst->peer->device;
	struct net *net = dev_net(pst->wgdev);
	struct flowi6 fl = {
		.daddr = ep->addr6.sin6_addr,
		.saddr = ep->src6,
		.fl6_dport = ep->addr6.sin6_port,
		.fl6_sport = htons(wg->incoming_port),
		.flowi6_mark = wg->fwmark,
		.flowi6_oif = ep->addr6.sin6_scope_id,
		.flowi6_proto = IPPROTO_UDP,
	};
	const struct in6_addr *nh;
	struct wgfp_tmpl *t = NULL;
	struct dst_entry *dst;
	struct ipv6hdr *ip6;
	struct udphdr *udp;

	dst = ip6_route_output(net, NULL, &fl);
	if (dst->error)
		goto out;
	if (ipv6_addr_any(&fl.saddr) &&
	    ipv6_dev_get_saddr(net, dst->dev, &fl.daddr, 0, &fl.saddr))
		goto out;
	t = kzalloc(sizeof(*t), GFP_KERNEL);
	if (!t)
		goto out;
	t->egress = dpaa_wgfp_egress(dst->dev);
	nh = rt6_nexthop(dst_rt6_info(dst), &fl.daddr);
	if (t->egress < 0 || !dpaa_wgfp_family_ok(dst->dev, AF_INET6) ||
	    !wgfp_tmpl_l2(t, dst->dev, &nd_tbl, nh, htons(ETH_P_IPV6))) {
		kfree(t);
		t = NULL;
		goto out;
	}
	t->family = AF_INET6;
	t->hlen = ETH_HLEN + sizeof(*ip6) + sizeof(*udp);
	ip6 = (struct ipv6hdr *)(t->hdr + ETH_HLEN);
	ip6->version = 6;
	ip6->nexthdr = IPPROTO_UDP;
	ip6->hop_limit = ip6_dst_hoplimit(dst);
	ip6->saddr = fl.saddr;
	ip6->daddr = fl.daddr;
	udp = (struct udphdr *)(ip6 + 1);
	udp->source = fl.fl6_sport;
	udp->dest = fl.fl6_dport;
out:
	dst_release(dst);
	return t;
#else
	return NULL;
#endif
}

static void wgfp_pst_refresh(struct work_struct *w)
{
	struct wgfp_pst *pst = container_of(to_delayed_work(w), struct wgfp_pst,
					    refresh);
	struct wg_peer *peer = pst->peer;
	struct wgfp_tmpl *t = NULL, *old;
	struct endpoint ep;

	if (READ_ONCE(pst->stopping))
		return;
	read_lock_bh(&peer->endpoint_lock);
	ep = peer->endpoint;
	read_unlock_bh(&peer->endpoint_lock);
	/* No usable route, DPAA egress, neighbour or endpoint: no template,
	 * so frames punt to the stack instead of leaving with stale headers. */
	if (ep.addr.sa_family == AF_INET)
		t = wgfp_tmpl4(pst, &ep);
	else if (ep.addr.sa_family == AF_INET6)
		t = wgfp_tmpl6(pst, &ep);
	old = rcu_replace_pointer(pst->tmpl, t, true);
	if (old)
		kfree_rcu(old, rcu);
	wgfp_pst_rx_sync(pst, t);
	if (!READ_ONCE(pst->stopping))
		schedule_delayed_work(&pst->refresh, HZ);
}

/* ---- flow table ---- */

#define WGFP_FLOW_BITS		10
#define WGFP_FLOW_MAX		4096
#define WGFP_PST_BITS		6
#define WGFP_AGE_PERIOD		(2 * HZ)
#define WGFP_AGE_IDLE		5	/* periods without hits */
#define WGFP_FAIL_HOLD		(30 * HZ)
#define WGFP_BATCH		16	/* job arrays live on the stack */
#define WGFP_ADMIT		32	/* slow-path packets before a record */

enum { WGFP_LEARNING, WGFP_PENDING, WGFP_INSTALLING, WGFP_INSTALLED, WGFP_FAILED,
       WGFP_STUCK /* delete failed: hardware may still reference the record */ };

/* Writers hold wgfp_flows_lock; the sink path reads the table under RCU, so
 * entries are unhashed with hash_del_rcu() and freed with kfree_rcu(). */
struct wgfp_flow {
	struct hlist_node node;
	struct rcu_head rcu;
	struct dpaa_wgfp_tuple t;
	struct wgfp_pst *pst;
	int ifindex;
	u32 hash;
	u8 state;
	bool del;
	bool gone;		/* unhashed, waiting for its grace period */
	bool rx;		/* P3: the peer's inbound outer flow */
	u8 idle;
	u64 pkts;
	unsigned long since;
};

static DEFINE_HASHTABLE(wgfp_flows, WGFP_FLOW_BITS);
static DEFINE_HASHTABLE(wgfp_psts, WGFP_PST_BITS);
static DEFINE_SPINLOCK(wgfp_flows_lock);
static unsigned int wgfp_nflows, wgfp_npsts;
static atomic64_t wgfp_installs, wgfp_withdraws, wgfp_install_fail, wgfp_withdraw_fail;
static atomic64_t wgfp_decline_withdraws, wgfp_aip_withdraws;
static void wgfp_flow_work_fn(struct work_struct *w);
static void wgfp_age_fn(struct work_struct *w);
static DECLARE_WORK(wgfp_flow_work, wgfp_flow_work_fn);
static DECLARE_DELAYED_WORK(wgfp_age_work, wgfp_age_fn);

/* Tuples are built zero-initialised, so the whole struct is the identity. */
static bool wgfp_tuple_eq(const struct dpaa_wgfp_tuple *a,
			  const struct dpaa_wgfp_tuple *b)
{
	return !memcmp(a, b, sizeof(*a));
}

static u32 wgfp_tuple_hash(const struct dpaa_wgfp_tuple *t)
{
	return jhash2((const u32 *)t, sizeof(*t) / sizeof(u32), 0);
}

/*
 * The flow of an inner packet at @l3 (@eth_proto from L2): plain IPv4 (no
 * options, not a fragment) or IPv6 without extension headers, carrying TCP or
 * UDP.  @avail bytes are readable at @l3; the packet has @total bytes from
 * @l3 on.  Fills @t and the packet length; false otherwise.
 */
static bool wgfp_parse(const u8 *l3, __be16 eth_proto, u32 avail, u32 total,
		       struct dpaa_wgfp_tuple *t, u32 *len)
{
	const __be16 *ports;

	if (eth_proto == htons(ETH_P_IP)) {
		const struct iphdr *iph = (const struct iphdr *)l3;

		if (avail < sizeof(*iph) + 4 || iph->version != 4 || iph->ihl != 5 ||
		    ip_is_fragment(iph) ||
		    (iph->protocol != IPPROTO_TCP && iph->protocol != IPPROTO_UDP))
			return false;
		*len = ntohs(iph->tot_len);
		if (*len < sizeof(*iph) + 4 || *len > total)
			return false;
		t->family = AF_INET;
		t->saddr = iph->saddr;
		t->daddr = iph->daddr;
		t->proto = iph->protocol;
		ports = (const __be16 *)(l3 + sizeof(*iph));
	} else if (eth_proto == htons(ETH_P_IPV6)) {
		const struct ipv6hdr *ip6 = (const struct ipv6hdr *)l3;

		if (avail < sizeof(*ip6) + 4 || ip6->version != 6 ||
		    (ip6->nexthdr != IPPROTO_TCP && ip6->nexthdr != IPPROTO_UDP))
			return false;
		*len = sizeof(*ip6) + ntohs(ip6->payload_len);
		if (*len < sizeof(*ip6) + 4 || *len > total)
			return false;
		t->family = AF_INET6;
		t->saddr6 = ip6->saddr;
		t->daddr6 = ip6->daddr;
		t->proto = ip6->nexthdr;
		ports = (const __be16 *)(l3 + sizeof(*ip6));
	} else {
		return false;
	}
	t->sport = ports[0];
	t->dport = ports[1];
	return true;
}

/* Writer side, under wgfp_flows_lock. */
static struct wgfp_flow *wgfp_flow_find(const struct dpaa_wgfp_tuple *t,
					int ifindex, u32 hash)
{
	struct wgfp_flow *fl;

	hash_for_each_possible(wgfp_flows, fl, node, hash)
		if (fl->ifindex == ifindex && wgfp_tuple_eq(&fl->t, t))
			return fl;
	return NULL;
}

/* A sink FD has no ingress ID. Keep each tuple unique across all ports. */
static struct wgfp_flow *wgfp_flow_find_tuple(const struct dpaa_wgfp_tuple *t,
					      u32 hash)
{
	struct wgfp_flow *fl;

	hash_for_each_possible(wgfp_flows, fl, node, hash)
		if (wgfp_tuple_eq(&fl->t, t))
			return fl;
	return NULL;
}

/* Families the TX path handles; which of them a port admits is the port's
 * offload policy (dpaa_wgfp_family_ok()). */
static bool wgfp_tx_family_allowed(u8 family)
{
	return family == AF_INET || family == AF_INET6;
}

/* Sink side, under RCU; reject ambiguity rather than selecting a peer. */
static struct wgfp_flow *wgfp_flow_lookup(const struct dpaa_wgfp_tuple *t)
{
	struct wgfp_flow *fl;
	struct wgfp_flow *match = NULL;

	hash_for_each_possible_rcu(wgfp_flows, fl, node, wgfp_tuple_hash(t))
		if (wgfp_tuple_eq(&fl->t, t)) {
			if (match) {
				pr_warn_ratelimited(
					"wgfp: ambiguous sink tuple; dropping frame\n");
				return NULL;
			}
			match = fl;
		}
	return match;
}

static void wgfp_flow_free_locked(struct wgfp_flow *fl)
{
	if (fl->rx && fl->pst->rxfl == fl)
		fl->pst->rxfl = NULL;
	hash_del_rcu(&fl->node);
	fl->gone = true;
	fl->pst->nflows--;
	wgfp_nflows--;
	kfree_rcu(fl, rcu);
}

/* Mark @fl for withdrawal (the flow worker deletes the record). */
static void wgfp_flow_withdraw(struct wgfp_flow *fl, atomic64_t *why)
{
	spin_lock_bh(&wgfp_flows_lock);
	if (!READ_ONCE(fl->pst->stopping) && !fl->gone && !fl->del) {
		fl->del = true;
		if (why)
			atomic64_inc(why);
		queue_work(system_wq, &wgfp_flow_work);
	}
	spin_unlock_bh(&wgfp_flows_lock);
}

/* ---- sink path ---- */

/*
 * P2b-3 slow-path punt: copy the frame into an sk_buff and receive it again on
 * the ingress port of its flow, so the stack forwards it into wg_xmit() (or
 * elsewhere, if routing changed) with every check the fast path skips.  The
 * copy is fine: punts are the exception (handshake gaps, roaming, oversize).
 * With @withdraw (oversize, dead peer) the record goes too, so the flow's later
 * packets take the stack without a detour through the sink.  The DPAA buffer
 * goes back to its pool.  NAPI context, RCU held by the sink.
 */
static int wgfp_punt(struct dpaa_wgfp_frame *f, struct wgfp_flow *fl,
		     bool withdraw)
{
	struct net_device *dev;
	struct sk_buff *skb;

	if (withdraw)
		wgfp_flow_withdraw(fl, &wgfp_decline_withdraws);
	dev = dev_get_by_index_rcu(&init_net, fl->ifindex);
	if (!dev || !(dev->flags & IFF_UP))
		return 0;
	skb = netdev_alloc_skb_ip_align(dev, f->len);
	if (!skb)
		return 0;
	skb_put_data(skb, f->buf + f->off, f->len);
	skb->protocol = eth_type_trans(skb, dev);
	dpaa_wgfp_drop(f);
	atomic64_inc(&wgfp_punted);
	netif_receive_skb(skb);
	return 1;
}

/* Does the template still point at the peer's endpoint?  Lockless. */
static bool wgfp_endpoint_matches(const struct wg_peer *peer,
				  const struct wgfp_tmpl *t)
{
	const struct udphdr *udp = (const struct udphdr *)(t->hdr + t->hlen -
							   sizeof(*udp));

	if (READ_ONCE(peer->endpoint.addr.sa_family) != t->family)
		return false;
	if (t->family == AF_INET)
		return READ_ONCE(peer->endpoint.addr4.sin_addr.s_addr) ==
		       ((const struct iphdr *)(t->hdr + ETH_HLEN))->daddr &&
		       READ_ONCE(peer->endpoint.addr4.sin_port) == udp->dest;
	return READ_ONCE(peer->endpoint.addr6.sin6_port) == udp->dest &&
	       !memcmp(&peer->endpoint.addr6.sin6_addr,
		       &((const struct ipv6hdr *)(t->hdr + ETH_HLEN))->daddr, 16);
}

/*
 * P3: an inbound outer frame of a peer endpoint (the record admits only
 * that exact UDP 5-tuple).  Copy it into an sk_buff and give it to upstream
 * wg_packet_receive() as wg_receive() would, minus the IP/netfilter/UDP layers
 * in front.  Packets that fail the IP or UDP checks go through the full stack.
 * NAPI context, RCU held by the sink.
 */
static int wgfp_rx_frame(struct dpaa_wgfp_frame *f, struct wgfp_flow *fl)
{
	const struct iphdr *iph;
	const struct ipv6hdr *ip6;
	const struct udphdr *udp;
	struct net_device *dev = dev_get_by_index_rcu(&init_net, fl->ifindex);
	struct sk_buff *skb;
	u32 len, hl, udp_len;
	bool plain;

	if (!dev || !(dev->flags & IFF_UP))
		return 0;
	skb = netdev_alloc_skb_ip_align(dev, f->len);
	if (!skb)
		return 0;
	skb_put_data(skb, f->buf + f->off, f->len);
	dpaa_wgfp_drop(f);
	skb->protocol = eth_type_trans(skb, dev);
	iph = (const struct iphdr *)skb->data;
	ip6 = (const struct ipv6hdr *)skb->data;
	/* the tuple matched, so the IP header and the UDP ports are there */
	if (fl->t.family == AF_INET) {
		hl = sizeof(*iph);
		len = ntohs(iph->tot_len);
		plain = iph->ihl == 5 && !ip_is_fragment(iph) &&
			!ip_fast_csum((const u8 *)iph, 5);
	} else {
		hl = sizeof(*ip6);
		len = hl + ntohs(ip6->payload_len);
		plain = ip6->nexthdr == IPPROTO_UDP;
	}
	plain = plain && skb->pkt_type == PACKET_HOST && len <= skb->len &&
		len >= hl + sizeof(*udp);
	if (plain) {
		udp = (const struct udphdr *)(skb->data + hl);
		udp_len = ntohs(udp->len);
		plain = udp_len >= sizeof(*udp) && udp_len <= len - hl;
		if (plain && udp->check) {
			__wsum csum = csum_partial(udp, udp_len, 0);

			plain = fl->t.family == AF_INET ?
				!csum_tcpudp_magic(iph->saddr, iph->daddr, udp_len,
						   IPPROTO_UDP, csum) :
				!csum_ipv6_magic(&ip6->saddr, &ip6->daddr, udp_len,
						 IPPROTO_UDP, csum);
		} else if (fl->t.family == AF_INET6) {
			plain = false;
		}
	}
	if (unlikely(!plain)) {
		dpaa_wgfp_stat_inc(&wgfp_rx_stack);
		netif_receive_skb(skb);
		return 1;
	}
	pskb_trim(skb, len);
	skb_reset_network_header(skb);
	skb_set_transport_header(skb, hl);
	skb->skb_iif = dev->ifindex;
	dpaa_wgfp_stat_inc(&wgfp_rx_frames);
	wg_packet_receive(fl->pst->peer->device, skb);
	return 1;
}

static int wgfp_peer_frame(void *priv, struct dpaa_wgfp_frame *f)
{
	u8 *data = f->buf + f->off;
	u8 *inner = data + ETH_HLEN;
	struct dpaa_wgfp_tuple tup = {};
	struct noise_keypair *kp;
	const struct wgfp_tmpl *t;
	const struct iphdr *iph;
	struct message_data *wg;
	struct wgfp_flow *fl;
	struct wgfp_pst *pst;
	struct wg_peer *peer;
	struct udphdr *udp;
	struct iphdr *oip;
	u32 ip_len, plain, tot, mtu, added;
	bool withdraw = false;
	u64 ctr;
	u8 *hdr;

	/* Only TCP/UDP of a learned flow reaches a sink; anything else (a
	 * stale or hand-made record) is dropped. */
	if (f->len < ETH_HLEN ||
	    !wgfp_parse(inner, ((struct ethhdr *)data)->h_proto,
			f->len - ETH_HLEN, f->len - ETH_HLEN, &tup, &ip_len))
		return 0;
	iph = (const struct iphdr *)inner;
	fl = wgfp_flow_lookup(&tup);
	if (!fl)
		return 0;
	if (!wgfp_tx_family_allowed(tup.family))
		return wgfp_punt(f, fl, true);
	if (fl->rx)
		return wgfp_rx_frame(f, fl);
	pst = fl->pst;
	peer = pst->peer;
	mtu = READ_ONCE(pst->wgdev->mtu);

	/* Oversize (the stack fragments or sends ICMP) and a removed peer are
	 * permanent for the flow: punt and withdraw its record. */
	if (ip_len > mtu || READ_ONCE(peer->is_dead))
		goto decline_flow;
	/* We forward the inner packet: TTL must drop by one, as on the slow
	 * path.  TTL <= 1 is left to the stack (ICMP time exceeded); ASK's
	 * ttl_punt normally sends those to the kernel before the record. */
	if ((tup.family == AF_INET ? iph->ttl :
	     ((const struct ipv6hdr *)inner)->hop_limit) <= 1)
		goto decline;

	t = rcu_dereference(pst->tmpl);
	if (unlikely(!t))
		goto decline;
	/* outer IP + UDP + WireGuard header in front of the inner packet */
	added = t->hlen - ETH_HLEN + sizeof(struct message_data);
	plain = min_t(u32, mtu, ALIGN(ip_len, MESSAGE_PADDING_MULTIPLE));
	if (f->off < added ||
	    f->off + ETH_HLEN + plain + POLY1305_DIGEST_SIZE > f->buf_size)
		goto decline;
	/* Roaming: unlocked compare, as wg_socket_set_peer_endpoint() does; a
	 * torn read costs one punt.  The stopping test pairs with the
	 * synchronize_rcu() before the refresh work is cancelled for good. */
	if (unlikely(!wgfp_endpoint_matches(peer, t))) {
		if (!READ_ONCE(pst->stopping))
			mod_delayed_work(system_wq, &pst->refresh, 0);
		goto decline;
	}
	kp = rcu_dereference(peer->keypairs.current_keypair);
	if (unlikely(!kp || !READ_ONCE(kp->sending.is_valid) ||
		     wg_birthdate_has_expired(kp->sending.birthdate, REJECT_AFTER_TIME)))
		goto decline;
	ctr = (u64)atomic64_inc_return(&kp->sending_counter) - 1;
	if (unlikely(ctr >= REJECT_AFTER_MESSAGES))
		goto decline;
	if (tup.family == AF_INET)
		ip_decrease_ttl((struct iphdr *)iph);
	else
		((struct ipv6hdr *)inner)->hop_limit--;
	memset(inner + ip_len, 0, plain - ip_len);
	chacha20poly1305_encrypt(inner, inner, plain, NULL, 0, ctr,
				 kp->sending.key);

	hdr = inner - added - ETH_HLEN;
	memcpy(hdr, t->hdr, t->hlen);
	tot = sizeof(struct udphdr) + sizeof(struct message_data) + plain +
	      POLY1305_DIGEST_SIZE;		/* the UDP datagram */
	if (t->family == AF_INET) {
		oip = (struct iphdr *)(hdr + ETH_HLEN);
		oip->tot_len = htons(sizeof(*oip) + tot);
		oip->check = 0;
		oip->check = ip_fast_csum((const u8 *)oip, oip->ihl);
		udp = (struct udphdr *)(oip + 1);
	} else {
		((struct ipv6hdr *)(hdr + ETH_HLEN))->payload_len = htons(tot);
		udp = (struct udphdr *)(hdr + ETH_HLEN + sizeof(struct ipv6hdr));
	}
	udp->len = htons(tot);
	wg = (struct message_data *)(udp + 1);
	wg->header.type = cpu_to_le32(MESSAGE_DATA);
	wg->key_idx = kp->remote_index;
	wg->counter = cpu_to_le64(ctr);
	if (t->family == AF_INET6) {
		const struct ipv6hdr *ip6 = (const struct ipv6hdr *)(hdr + ETH_HLEN);

		udp->check = csum_ipv6_magic(&ip6->saddr, &ip6->daddr, tot,
					     IPPROTO_UDP, csum_partial(udp, tot, 0));
		if (!udp->check)
			udp->check = CSUM_MANGLED_0;
	}

	if (time_after(jiffies, READ_ONCE(pst->last_timer))) {
		WRITE_ONCE(pst->last_timer, jiffies + HZ / 100);
		wg_timers_any_authenticated_packet_traversal(peer);
		wg_timers_any_authenticated_packet_sent(peer);
		wg_timers_data_sent(peer);
		/* Same test as keep_key_fresh(); the send is itself rate limited. */
		if (ctr > REKEY_AFTER_MESSAGES ||
		    (kp->i_am_the_initiator &&
		     wg_birthdate_has_expired(kp->sending.birthdate, REKEY_AFTER_TIME))) {
			atomic64_inc(&wgfp_rekeys);
			wg_packet_send_queued_handshake_initiation(peer, false);
		}
	}
	dpaa_wgfp_stat_inc(&wgfp_sealed);
	dpaa_wgfp_xmit(f, t->egress, hdr - f->buf,
		       t->hlen + sizeof(struct message_data) + plain +
		       POLY1305_DIGEST_SIZE);
	return 1;
decline_flow:
	withdraw = true;
decline:
	atomic64_inc(&wgfp_declined);
	return wgfp_punt(f, fl, withdraw);
}

static const struct dpaa_wgfp_ops wgfp_peer_ops = {
	.frame = wgfp_peer_frame,
};

/* A current keypair that can receive: a handshake with the peer completed. */
static bool wgfp_peer_can_receive(struct wg_peer *peer)
{
	struct noise_keypair *kp;
	bool ok;

	rcu_read_lock_bh();
	kp = rcu_dereference_bh(peer->keypairs.current_keypair);
	ok = kp && READ_ONCE(kp->receiving.is_valid) &&
	     !wg_birthdate_has_expired(kp->sending.birthdate, REJECT_AFTER_TIME);
	rcu_read_unlock_bh();
	return ok;
}

/* P3 admission is deliberately inert until exact INPUT and ingress provenance
 * can be established. */
static void wgfp_pst_rx_sync(struct wgfp_pst *pst, const struct wgfp_tmpl *t)
{
	struct dpaa_wgfp_tuple rt = {};
	struct wgfp_flow *fl;
	bool want = false;

	if (t && READ_ONCE(wgfp_rx) && !READ_ONCE(pst->peer->is_dead) &&
	    !READ_ONCE(pst->stopping)) {
		const u8 *l3 = t->hdr + ETH_HLEN;
		const struct udphdr *udp = (const struct udphdr *)(t->hdr + t->hlen -
								   sizeof(*udp));

		pr_warn_once("wireguard: experimental P3 receive offload bypasses INPUT filtering\n");
		if (t->family == AF_INET) {
			rt.saddr = ((const struct iphdr *)l3)->daddr;
			rt.daddr = ((const struct iphdr *)l3)->saddr;
		} else {
			rt.saddr6 = ((const struct ipv6hdr *)l3)->daddr;
			rt.daddr6 = ((const struct ipv6hdr *)l3)->saddr;
		}
		rt.family = t->family;
		rt.sport = udp->dest;
		rt.dport = udp->source;
		rt.proto = IPPROTO_UDP;
		/* Admission is checked once.  With both directions on the fast
		 * path the outer conntrack entry sees no packets and expires;
		 * like an offloaded flowtable flow, the admitted record stays
		 * until the endpoint, the template or the binding changes. */
		spin_lock_bh(&wgfp_flows_lock);
		want = pst->rxfl && pst->rxfl->ifindex == t->ifindex &&
		       wgfp_tuple_eq(&pst->rxfl->t, &rt);
		spin_unlock_bh(&wgfp_flows_lock);
		if (!want) {
			bool absent = false;

			want = wgfp_ct_admitted(dev_net(pst->wgdev), NULL, &rt,
						&absent);
			/*
			 * VyOS normally untracks WireGuard ports. A prior
			 * handshake does not prove INPUT accepted this tuple.
			 */
			if (!want && absent)
				want = wgfp_peer_can_receive(pst->peer);
		}
	}
	spin_lock_bh(&wgfp_flows_lock);
	fl = pst->rxfl;
	if (fl && (!want || fl->ifindex != t->ifindex ||
		   !wgfp_tuple_eq(&fl->t, &rt))) {
		if (!fl->del) {
			fl->del = true;
			queue_work(system_wq, &wgfp_flow_work);
		}
		pst->rxfl = NULL;
		fl = NULL;
	}
	if (want && !fl && wgfp_nflows < WGFP_FLOW_MAX &&
	    !wgfp_flow_find_tuple(&rt, wgfp_tuple_hash(&rt))) {
		fl = kzalloc(sizeof(*fl), GFP_ATOMIC);
		if (fl) {
			fl->t = rt;
			fl->pst = pst;
			fl->rx = true;
			fl->ifindex = t->ifindex;
			fl->hash = wgfp_tuple_hash(&rt);
			fl->state = WGFP_PENDING;
			fl->since = jiffies;
			pst->nflows++;
			pst->rxfl = fl;
			hash_add_rcu(wgfp_flows, &fl->node, fl->hash);
			wgfp_nflows++;
			queue_work(system_wq, &wgfp_flow_work);
		}
	}
	spin_unlock_bh(&wgfp_flows_lock);
}

/* ---- learning ---- */

/*
 * Check for an established conntrack entry without NAT, master or helper.
 * Packets forwarded by the software flowtable may have no attached entry, so
 * look it up by tuple. A tuple-only lookup does not prove INPUT accepted it.
 */
static bool wgfp_ct_admitted(struct net *net, struct sk_buff *skb,
			     const struct dpaa_wgfp_tuple *t, bool *absent)
{
#if IS_REACHABLE(CONFIG_NF_CONNTRACK)
	enum ip_conntrack_info ctinfo;
	struct nf_conn *ct = skb ? nf_ct_get(skb, &ctinfo) : NULL;
	struct nf_conntrack_tuple tuple = {};
	struct nf_conntrack_tuple_hash *h;
	bool ok;

	if (ct)
		return (ctinfo == IP_CT_ESTABLISHED ||
			ctinfo == IP_CT_ESTABLISHED_REPLY) &&
		       !(ct->status & IPS_NAT_MASK) && !ct->master && !nfct_help(ct);
	tuple.src.l3num = t->family;
	if (t->family == AF_INET6) {
		tuple.src.u3.in6 = t->saddr6;
		tuple.dst.u3.in6 = t->daddr6;
	} else {
		tuple.src.u3.ip = t->saddr;
		tuple.dst.u3.ip = t->daddr;
	}
	tuple.src.u.all = t->sport;
	tuple.dst.u.all = t->dport;
	tuple.dst.protonum = t->proto;
	tuple.dst.dir = IP_CT_DIR_ORIGINAL;
	h = nf_conntrack_find_get(net, &nf_ct_zone_dflt, &tuple);
	if (!h) {
		if (absent)
			*absent = true;
		return false;
	}
	ct = nf_ct_tuplehash_to_ctrack(h);
	ok = test_bit(IPS_SEEN_REPLY_BIT, &ct->status) &&
	     !(ct->status & IPS_NAT_MASK) && !ct->master && !nfct_help(ct);
	nf_ct_put(ct);
	return ok;
#else
	return false;	/* no conntrack: no proof the flow was admitted */
#endif
}

/* Under wgfp_flows_lock, atomic context. */
static struct wgfp_pst *wgfp_pst_get_locked(struct wg_peer *peer,
					    struct net_device *wgdev)
{
	struct wgfp_pst *pst;

	hash_for_each_possible(wgfp_psts, pst, node, (unsigned long)peer)
		if (pst->peer == peer && !pst->stopping)
			return pst;
	pst = kzalloc(sizeof(*pst), GFP_ATOMIC);
	if (!pst)
		return NULL;
	pst->peer = wg_peer_get(peer);
	pst->wgdev = wgdev;
	INIT_DELAYED_WORK(&pst->refresh, wgfp_pst_refresh);
	hash_add(wgfp_psts, &pst->node, (unsigned long)peer);
	wgfp_npsts++;
	schedule_delayed_work(&pst->refresh, 0);
	return pst;
}

/*
 * Phase P2b-2, automatic flow install.  wg_xmit() offers every packet to
 * wg_fastpath_learn().  An inner IPv4 TCP/UDP flow of a bound device that
 * arrived on a DPAA port, whose conntrack entry is established, not NAT'ed,
 * not related to a master and has no helper, is tracked from that packet on;
 * after WGFP_ADMIT slow-path packets it gets an FMan record (installed by a
 * worker; the record API sleeps) that steers its later frames on that port
 * straight to a sink (round-robin), where wgfp_peer_frame() seals them.  Mice
 * (an iperf3 control connection, DNS) never take a record or a sink.  Only
 * the first packet needs the conntrack proof (wgfp_ct_admitted()).  A tracked
 * flow that does not reach WGFP_ADMIT within 10 s is forgotten.  TCP SYN
 * is never learned; FIN/RST (punted to the kernel by ASK's tcp_ctl_punt)
 * withdraws the record, as does 10 s without silicon hits or unbinding.
 *
 * Like an offloaded flowtable flow, a fast-pathed flow skips the firewall
 * after it was admitted: rule changes apply when the record is withdrawn.
 */
void wg_fastpath_learn(struct wg_peer *peer, struct sk_buff *skb)
{
	struct wgfp_binds *bs = rcu_dereference_bh(wgfp_bs);
	struct net_device *wgdev = NULL;
	struct dpaa_wgfp_tuple t = {};
	struct wgfp_pst *pst;
	struct wgfp_flow *fl;
	u8 hb[sizeof(struct ipv6hdr) + 4];
	unsigned int thoff, nhoff, i;
	const u8 *l3;
	bool fin = false, in_ok;
	u32 hash, len, total;

	if (!bs || READ_ONCE(bs->stopping) || !skb->skb_iif ||
	    (skb->protocol != htons(ETH_P_IP) && skb->protocol != htons(ETH_P_IPV6)))
		return;
	for (i = 0; i < bs->n; i++)
		if (netdev_priv(bs->wgdev[i]) == peer->device)
			wgdev = bs->wgdev[i];
	if (!wgdev)
		return;
	nhoff = skb_network_offset(skb);
	if (skb->len <= nhoff)
		return;
	total = skb->len - nhoff;
	l3 = skb_header_pointer(skb, nhoff, min_t(u32, sizeof(hb), total), hb);
	if (!l3 || !wgfp_parse(l3, skb->protocol, min_t(u32, sizeof(hb), total),
			       total, &t, &len))
		return;
	if (!wgfp_tx_family_allowed(t.family))
		return;
	/* Inner family policy: the ingress port's `offload ipv4|ipv6`. */
	rcu_read_lock();
	in_ok = dpaa_wgfp_family_ok(dev_get_by_index_rcu(&init_net, skb->skb_iif),
				    t.family);
	rcu_read_unlock();
	if (!in_ok)
		return;
	thoff = nhoff + (t.family == AF_INET ?
		sizeof(struct iphdr) : sizeof(struct ipv6hdr));
	if (t.proto == IPPROTO_TCP) {
		struct tcphdr _th;
		const struct tcphdr *th = skb_header_pointer(skb, thoff,
							     sizeof(_th), &_th);

		if (!th || th->syn)
			return;
		fin = th->fin || th->rst;
	}
	hash = wgfp_tuple_hash(&t);

	/* ponytail: one global lock per slow-path packet of a bound device;
	 * an RCU fast check first if it ever shows in a profile. */
	spin_lock_bh(&wgfp_flows_lock);
	fl = wgfp_flow_find(&t, skb->skb_iif, hash);
	if (fl) {
		if (fin && !fl->del) {
			fl->del = true;
			queue_work(system_wq, &wgfp_flow_work);
		} else if (fl->state == WGFP_LEARNING && ++fl->pkts >= WGFP_ADMIT) {
			fl->pkts = 0;
			fl->state = WGFP_PENDING;
			queue_work(system_wq, &wgfp_flow_work);
		}
		goto out;
	}
	if (fin || wgfp_nflows >= WGFP_FLOW_MAX ||
	    wgfp_flow_find_tuple(&t, hash))
		goto out;
	if (!wgfp_ct_admitted(dev_net(skb->dev), skb, &t, NULL))
		goto out;
	pst = wgfp_pst_get_locked(peer, wgdev);
	if (!pst)
		goto out;
	fl = kzalloc(sizeof(*fl), GFP_ATOMIC);
	if (!fl)
		goto out;	/* an empty pst is freed by the next aging pass */
	fl->t = t;
	fl->pst = pst;
	fl->ifindex = skb->skb_iif;
	fl->hash = hash;
	fl->state = WGFP_LEARNING;
	fl->pkts = 1;
	fl->since = jiffies;
	pst->nflows++;
	hash_add_rcu(wgfp_flows, &fl->node, hash);
	wgfp_nflows++;
out:
	spin_unlock_bh(&wgfp_flows_lock);
}

/* ---- install, withdraw, aging ---- */

/* Install PENDING records, withdraw del-marked ones.  Only this worker and
 * the teardown (after cancelling it) free entries; nobody frees an
 * INSTALLING entry, so the job pointers stay valid across the unlock. */
static void wgfp_flow_work_fn(struct work_struct *w)
{
	struct { struct wgfp_flow *fl; struct dpaa_wgfp_tuple t; int ifindex;
		 bool add; } job[WGFP_BATCH];
	/* ponytail: round-robin sink choice; per-sink load (silicon counters)
	 * if long-lived flows pile up on one sink after churn. */
	static unsigned int rr;
	struct wgfp_flow *fl;
	struct hlist_node *tmp;
	unsigned int n = 0, i, bkt;
	bool more = false;

	spin_lock_bh(&wgfp_flows_lock);
	hash_for_each_safe(wgfp_flows, bkt, tmp, fl, node) {
		bool add;

		if (fl->state == WGFP_INSTALLING || fl->state == WGFP_STUCK)
			continue;
		if (fl->del) {
			if (fl->state != WGFP_INSTALLED) {
				wgfp_flow_free_locked(fl);
				continue;
			}
			add = false;
		} else if (fl->state == WGFP_PENDING) {
			add = true;
		} else {
			continue;
		}
		if (n == WGFP_BATCH) {
			more = true;
			break;
		}
		fl->state = WGFP_INSTALLING;
		job[n].fl = fl;
		job[n].t = fl->t;
		job[n].ifindex = fl->ifindex;
		job[n].add = add;
		n++;
	}
	spin_unlock_bh(&wgfp_flows_lock);

	for (i = 0; i < n; i++) {
		struct net_device *dev = dev_get_by_index(&init_net, job[i].ifindex);
		int err = -ENODEV;

		if (dev) {
			err = job[i].add ?
			      dpaa_wgfp_flow_add(dev, &job[i].t, rr++) :
			      dpaa_wgfp_flow_del(dev, &job[i].t);
			dev_put(dev);
		}
		spin_lock_bh(&wgfp_flows_lock);
		fl = job[i].fl;
		if (job[i].add) {
			if (err < 0) {
				atomic64_inc(&wgfp_install_fail);
				fl->state = WGFP_FAILED;
			} else {
				atomic64_inc(&wgfp_installs);
				fl->state = WGFP_INSTALLED;
			}
			fl->since = jiffies;
			if (fl->del)
				more = true;	/* FIN while installing */
		} else if (err < 0) {
			/* not provably unlinked: keep the entry so the tuple is
			 * neither relearned nor reinserted over a live record */
			atomic64_inc(&wgfp_withdraw_fail);
			fl->state = WGFP_STUCK;
		} else {
			atomic64_inc(&wgfp_withdraws);
			wgfp_flow_free_locked(fl);
		}
		spin_unlock_bh(&wgfp_flows_lock);
	}
	if (more)
		queue_work(system_wq, &wgfp_flow_work);
}

/* Free peers without flows: unhash under the lock, then let every sink-path
 * reader that may still see them finish before the refresh work is cancelled
 * for good (the sink path re-arms it only while !stopping). */
static void wgfp_psts_reap(bool all)
{
	struct wgfp_pst *dead[WGFP_BATCH], *pst;
	struct hlist_node *tmp;
	unsigned int n = 0, i, bkt;

	do {
		n = 0;
		spin_lock_bh(&wgfp_flows_lock);
		hash_for_each_safe(wgfp_psts, bkt, tmp, pst, node) {
			if ((pst->nflows && !all) || n == WGFP_BATCH)
				continue;
			pst->stopping = true;
			hash_del(&pst->node);
			wgfp_npsts--;
			dead[n++] = pst;
		}
		spin_unlock_bh(&wgfp_flows_lock);
		if (!n)
			return;
		synchronize_rcu();
		for (i = 0; i < n; i++) {
			pst = dead[i];
			cancel_delayed_work_sync(&pst->refresh);
			kfree_rcu(rcu_dereference_protected(pst->tmpl, true), rcu);
			wg_peer_put(pst->peer);
			kfree_rcu(pst, rcu);
		}
	} while (n == WGFP_BATCH);
}

/* Withdraw records whose silicon counter stopped moving or whose inner
 * destination no longer maps to their peer (AllowedIPs changed); forget
 * failures and flows that never reached WGFP_ADMIT; free peers without flows.
 * The teardown cancels this work before it frees anything else. */
static void wgfp_age_fn(struct work_struct *w)
{
	struct { struct wgfp_flow *fl; struct dpaa_wgfp_tuple t; int ifindex;
		 u32 hash; struct wgfp_pst *pst; } job[WGFP_BATCH];
	static unsigned int cursor;
	unsigned int n = 0, i, bkt, seen = 0;
	struct wgfp_flow *fl;
	struct hlist_node *tmp;
	struct sk_buff *probe;
	bool kick = false;

	/* wg_allowedips_lookup_dst() takes an skb; one IP header suffices. */
	probe = alloc_skb(sizeof(struct ipv6hdr), GFP_KERNEL);
	if (probe) {
		skb_reset_network_header(probe);
		skb_put_zero(probe, sizeof(struct ipv6hdr));
	}

	spin_lock_bh(&wgfp_flows_lock);
	hash_for_each_safe(wgfp_flows, bkt, tmp, fl, node) {
		if ((fl->state == WGFP_FAILED && !fl->del &&
		     time_after(jiffies, fl->since + WGFP_FAIL_HOLD)) ||
		    (fl->state == WGFP_LEARNING &&
		     time_after(jiffies, fl->since + WGFP_AGE_IDLE * WGFP_AGE_PERIOD))) {
			wgfp_flow_free_locked(fl);
			continue;
		}
		/* the outer record lives as long as its peer state */
		if (fl->state != WGFP_INSTALLED || fl->del || fl->rx)
			continue;
		/* round-robin over passes when there are more than a batch */
		if (seen++ < cursor || n == WGFP_BATCH)
			continue;
		job[n].fl = fl;
		job[n].t = fl->t;
		job[n].ifindex = fl->ifindex;
		job[n].hash = fl->hash;
		job[n].pst = fl->pst;	/* only this work frees psts */
		n++;
	}
	cursor = (n == WGFP_BATCH) ? cursor + n : 0;
	spin_unlock_bh(&wgfp_flows_lock);

	for (i = 0; i < n; i++) {
		struct net_device *dev = dev_get_by_index(&init_net, job[i].ifindex);
		struct wgfp_pst *pst = job[i].pst;
		bool foreign = false, family = false;
		u64 pkts = 0;
		int err = -ENODEV;

		if (dev) {
			err = dpaa_wgfp_flow_packets(dev, &job[i].t, &pkts);
			/* the port may have dropped this family since install */
			family = dpaa_wgfp_family_ok(dev, job[i].t.family);
			dev_put(dev);
		}
		if (probe) {
			struct wg_peer *p;

			if (job[i].t.family == AF_INET6) {
				probe->protocol = htons(ETH_P_IPV6);
				ipv6_hdr(probe)->daddr = job[i].t.daddr6;
			} else {
				probe->protocol = htons(ETH_P_IP);
				ip_hdr(probe)->daddr = job[i].t.daddr;
			}
			p = wg_allowedips_lookup_dst(&pst->peer->device->peer_allowedips,
						     probe);
			foreign = p != pst->peer;
			wg_peer_put(p);
		}
		spin_lock_bh(&wgfp_flows_lock);
		/* the flow may have been withdrawn and freed meanwhile: only
		 * touch it if it is still the entry the table holds */
		fl = wgfp_flow_find(&job[i].t, job[i].ifindex, job[i].hash);
		if (fl == job[i].fl && fl->state == WGFP_INSTALLED && !fl->del) {
			if (!family) {
				fl->del = true;
				kick = true;
			} else if (foreign) {
				fl->del = true;
				kick = true;
				atomic64_inc(&wgfp_aip_withdraws);
			} else if (err || pkts == fl->pkts) {
				if (++fl->idle >= WGFP_AGE_IDLE) {
					fl->del = true;
					kick = true;
				}
			} else {
				fl->idle = 0;
				fl->pkts = pkts;
			}
		}
		spin_unlock_bh(&wgfp_flows_lock);
	}
	kfree_skb(probe);
	if (kick)
		queue_work(system_wq, &wgfp_flow_work);
	wgfp_psts_reap(false);
	if (rcu_access_pointer(wgfp_bs))
		schedule_delayed_work(&wgfp_age_work, WGFP_AGE_PERIOD);
}

/* Teardown: the binding is marked stopping and RCU learning readers drained. */
static int wgfp_flows_flush(void)
{
	struct wgfp_flow *fl;
	struct wgfp_pst *pst;
	struct hlist_node *tmp;
	unsigned int bkt;
	int first_err = 0;

	cancel_delayed_work_sync(&wgfp_age_work);
	spin_lock_bh(&wgfp_flows_lock);
	hash_for_each(wgfp_psts, bkt, pst, node)
		WRITE_ONCE(pst->stopping, true);
	spin_unlock_bh(&wgfp_flows_lock);
	/* The aging worker is stopped, so peer-state entries remain stable here. */
	hash_for_each(wgfp_psts, bkt, pst, node)
		cancel_delayed_work_sync(&pst->refresh);
	cancel_work_sync(&wgfp_flow_work);
	/* no worker runs now; nothing else frees entries, so drop the lock
	 * around the sleeping record delete */
	spin_lock_bh(&wgfp_flows_lock);
	hash_for_each_safe(wgfp_flows, bkt, tmp, fl, node) {
		int err = 0;

		if (fl->state == WGFP_INSTALLED) {
			struct dpaa_wgfp_tuple t = fl->t;
			int ifindex = fl->ifindex;
			struct net_device *dev;

			spin_unlock_bh(&wgfp_flows_lock);
			dev = dev_get_by_index(&init_net, ifindex);
			if (dev) {
				err = dpaa_wgfp_flow_del(dev, &t);
				dev_put(dev);
			} else {
				err = -ENODEV;
			}
			spin_lock_bh(&wgfp_flows_lock);
			if (err < 0) {
				atomic64_inc(&wgfp_withdraw_fail);
				pr_warn_ratelimited("wgfp: teardown could not delete a record (%d); it may still steer packets\n", err);
				fl->state = WGFP_STUCK;
				if (!first_err)
					first_err = err;
			} else {
				atomic64_inc(&wgfp_withdraws);
				wgfp_flow_free_locked(fl);
			}
		} else if (fl->state == WGFP_STUCK) {
			pr_warn_ratelimited("wgfp: teardown with an unresolved record\n");
			if (!first_err)
				first_err = -EBUSY;
		} else {
			wgfp_flow_free_locked(fl);
		}
	}
	spin_unlock_bh(&wgfp_flows_lock);
	return first_err;
}

/* ---- binding ---- */

/*
 * Peer mode holds references on the bound wg devices; drop them when one goes
 * away (ip link del wg0), else unregister would wait forever.  The notifier
 * runs under RTNL and the stop sleeps in the record API: defer it.
 */
static int wgfp_peer_stop(void);
static int wgfp_peer_start(char *names, bool distinct);

static void wgfp_stop_work_fn(struct work_struct *w)
{
	char names[sizeof(wgfp_peer_cfg)] = "";
	struct wgfp_binds *bs;
	bool gone = false;
	unsigned int i;

	mutex_lock(&wgfp_lab_lock);
	bs = rcu_dereference_protected(wgfp_bs, lockdep_is_held(&wgfp_lab_lock));
	if (bs) {
		for (i = 0; i < bs->n; i++) {
			if (test_bit(i, &bs->invalidated) ||
			    READ_ONCE(bs->wgdev[i]->reg_state) != NETREG_REGISTERED ||
			    dev_net(bs->wgdev[i]) != &init_net) {
				pr_info("fastpath: %s unregistered or moved, unbinding it\n",
					bs->wgdev[i]->name);
				gone = true;
				continue;
			}
			strlcat(names, bs->wgdev[i]->name, sizeof(names));
			strlcat(names, " ", sizeof(names));
		}
	}
	if (gone) {
		char copy[sizeof(names)];
		int err;

		err = wgfp_peer_stop();
		if (err) {
			pr_warn_ratelimited("wgfp: keeping binding and consumer after failed teardown (%d)\n",
					    err);
			mutex_unlock(&wgfp_lab_lock);
			return;
		}
		strscpy(wgfp_peer_cfg, "off", sizeof(wgfp_peer_cfg));
		wgfp_owner_aliases[0] = '\0';
		strscpy(copy, strim(names), sizeof(copy));
		if (!wgfp_peer_owner[0] && copy[0] &&
		    !wgfp_peer_start(names, false))	/* consumes names */
			strscpy(wgfp_peer_cfg, copy, sizeof(wgfp_peer_cfg));
	}
	mutex_unlock(&wgfp_lab_lock);
}
static DECLARE_WORK(wgfp_stop_work, wgfp_stop_work_fn);

static int wgfp_netdev_event(struct notifier_block *nb, unsigned long event,
			     void *ptr)
{
	struct net_device *dev = netdev_notifier_info_to_dev(ptr);
	struct wgfp_binds *bs;
	unsigned int i;

	if (event != NETDEV_UNREGISTER)
		return NOTIFY_DONE;
	rcu_read_lock();
	bs = rcu_dereference(wgfp_bs);
	for (i = 0; bs && i < bs->n; i++)
		if (bs->wgdev[i] == dev) {
			set_bit(i, &bs->invalidated);
			schedule_work(&wgfp_stop_work);
		}
	rcu_read_unlock();
	return NOTIFY_DONE;
}

static struct notifier_block wgfp_netdev_nb = {
	.notifier_call = wgfp_netdev_event,
};

static int wgfp_peer_stop(void)
{
	struct wgfp_binds *bs = rcu_dereference_protected(wgfp_bs,
					lockdep_is_held(&wgfp_lab_lock));
	unsigned int i;
	int err;

	if (!bs)
		return 0;
	WRITE_ONCE(bs->stopping, true);
	synchronize_rcu();
	err = wgfp_flows_flush();
	if (err)
		return err;
	RCU_INIT_POINTER(wgfp_bs, NULL);	/* learning runs under RCU-bh */
	unregister_netdevice_notifier(&wgfp_netdev_nb);
	synchronize_rcu();
	dpaa_wgfp_unregister();		/* synchronize_rcu(): no sink path left */
	wgfp_psts_reap(true);
	for (i = 0; i < bs->n; i++)
		dev_put(bs->wgdev[i]);
	kfree(bs);
	module_put(THIS_MODULE);
	return 0;
}

/* @names: space-separated wg devices.  Called with wgfp_lab_lock held. */
static int wgfp_peer_start(char *names, bool distinct)
{
	struct wgfp_binds *bs;
	char *name, *resolved_names[WGFP_MAX_BIND];
	unsigned int i;
	bool module_ref = false;
	int err;

	bs = kzalloc(sizeof(*bs), GFP_KERNEL);
	if (!bs)
		return -ENOMEM;
	while ((name = strsep(&names, " \t\n"))) {
		struct net_device *dev;

		if (!*name)
			continue;
		err = -E2BIG;
		if (bs->n == WGFP_MAX_BIND)
			goto err;
		err = -ENODEV;
		dev = dev_get_by_name(&init_net, name);
		if (!dev)
			goto err;
		if (distinct) {
			for (i = 0; i < bs->n; i++) {
				if (bs->wgdev[i] == dev) {
					dev_put(dev);
					err = -EINVAL;
					goto err;
				}
			}
		}
		resolved_names[bs->n] = name;
		bs->wgdev[bs->n++] = dev;
		err = -EINVAL;
		if (!dev->rtnl_link_ops || strcmp(dev->rtnl_link_ops->kind, "wireguard"))
			goto err;
	}
	err = -EINVAL;
	if (!bs->n)
		goto err;
	err = -ENODEV;
	/* The sink callback must remain resident until all records are withdrawn. */
	if (!try_module_get(THIS_MODULE))
		goto err;
	module_ref = true;
	err = dpaa_wgfp_register(&wgfp_peer_ops, NULL);
	if (err)
		goto err;
	err = register_netdevice_notifier(&wgfp_netdev_nb);
	if (err)
		goto err_consumer;
	rtnl_lock();
	for (i = 0; i < bs->n; i++) {
		if (READ_ONCE(bs->wgdev[i]->reg_state) != NETREG_REGISTERED ||
		    dev_net(bs->wgdev[i]) != &init_net ||
		    __dev_get_by_name(&init_net, resolved_names[i]) != bs->wgdev[i]) {
			err = -ENODEV;
			break;
		}
	}
	if (!err)
		rcu_assign_pointer(wgfp_bs, bs);
	rtnl_unlock();
	if (err) {
		unregister_netdevice_notifier(&wgfp_netdev_nb);
		goto err_consumer;
	}
	schedule_delayed_work(&wgfp_age_work, WGFP_AGE_PERIOD);
	pr_info("fastpath peer mode on: %u device(s)\n", bs->n);
	return 0;
err_consumer:
	dpaa_wgfp_unregister();
err:
	if (module_ref)
		module_put(THIS_MODULE);
	while (bs->n)
		dev_put(bs->wgdev[--bs->n]);
	kfree(bs);
	return err;
}

static int wgfp_peer_cfg_set(const char *val, const struct kernel_param *kp)
{
	char buf[sizeof(wgfp_peer_cfg)], copy[sizeof(wgfp_peer_cfg)];
	int err = 0;

	strscpy(buf, val, sizeof(buf));
	strim(buf);
	strscpy(copy, buf, sizeof(copy));
	mutex_lock(&wgfp_lab_lock);
	if (wgfp_peer_owner[0]) {
		err = -EBUSY;
		goto out;
	}
	err = wgfp_peer_stop();
	if (err)
		goto out;
	strscpy(wgfp_peer_cfg, "off", sizeof(wgfp_peer_cfg));
	if (!strcmp(buf, "off") || !buf[0]) {
		/* unbound */
	} else if (wgfp_lab_on) {
		err = -EBUSY;
	} else {
		err = wgfp_peer_start(copy, false);
		if (!err)
			strscpy(wgfp_peer_cfg, buf, sizeof(wgfp_peer_cfg));
	}
out:
	mutex_unlock(&wgfp_lab_lock);
	return err;
}

static char *wgfp_next_arg(char **args)
{
	char *arg;

	while ((arg = strsep(args, " \t\n")) && !*arg)
		;
	return arg;
}

static bool wgfp_owner_token_valid(const char *token)
{
	size_t i;

	if (!token || strlen(token) != WGFP_OWNER_TOKEN_LEN)
		return false;
	for (i = 0; i < WGFP_OWNER_TOKEN_LEN; i++)
		if (!((token[i] >= '0' && token[i] <= '9') ||
		      (token[i] >= 'a' && token[i] <= 'f')))
			return false;
	return true;
}

static bool wgfp_owner_alias_valid(const char *alias, const char *token)
{
	const char *suffix;
	size_t len = strlen(alias);

	if (len <= WGFP_OWNER_TOKEN_LEN + 5 || len > WGFP_OWNER_ALIAS_MAX ||
	    strncmp(alias, "wgo-", 4) ||
	    strncmp(alias + 4, token, WGFP_OWNER_TOKEN_LEN) ||
	    alias[WGFP_OWNER_TOKEN_LEN + 4] != '-')
		return false;
	for (suffix = alias + WGFP_OWNER_TOKEN_LEN + 5; *suffix; suffix++)
		if (!((*suffix >= 'a' && *suffix <= 'z') ||
		      (*suffix >= '0' && *suffix <= '9') ||
		      *suffix == '-'))
			return false;
	return true;
}

static int wgfp_peer_lease_set(const char *val, const struct kernel_param *kp)
{
	char aliases[WGFP_OWNER_ALIASES_SIZE] = "";
	char copy[WGFP_OWNER_ALIASES_SIZE];
	char *items[WGFP_MAX_BIND], *input, *args, *action, *token, *arg;
	struct wgfp_binds *bs;
	unsigned int n = 0, i;
	int err = 0;

	input = kstrdup(val, GFP_KERNEL);
	if (!input)
		return -ENOMEM;
	args = strim(input);
	action = wgfp_next_arg(&args);
	token = wgfp_next_arg(&args);
	if (!action || !wgfp_owner_token_valid(token)) {
		err = -EINVAL;
		goto out_free;
	}
	if (!strcmp(action, "bind")) {
		while ((arg = wgfp_next_arg(&args))) {
			if (n == WGFP_MAX_BIND) {
				err = -E2BIG;
				goto out_free;
			}
			if (!wgfp_owner_alias_valid(arg, token)) {
				err = -EINVAL;
				goto out_free;
			}
			for (i = 0; i < n; i++)
				if (!strcmp(items[i], arg)) {
					err = -EINVAL;
					goto out_free;
				}
			items[n++] = arg;
			if ((n > 1 && strlcat(aliases, " ", sizeof(aliases)) >=
				      sizeof(aliases)) ||
			    strlcat(aliases, arg, sizeof(aliases)) >= sizeof(aliases)) {
				err = -E2BIG;
				goto out_free;
			}
		}
		if (!n) {
			err = -EINVAL;
			goto out_free;
		}
	} else if ((strcmp(action, "reserve") && strcmp(action, "stop") &&
		    strcmp(action, "release")) || wgfp_next_arg(&args)) {
		err = -EINVAL;
		goto out_free;
	}

	mutex_lock(&wgfp_lab_lock);
	if (!strcmp(action, "reserve")) {
		if (wgfp_peer_owner[0]) {
			if (strcmp(wgfp_peer_owner, token))
				err = -EBUSY;
		} else if (rcu_access_pointer(wgfp_bs) || wgfp_lab_on) {
			err = -EBUSY;
		} else {
			strscpy(wgfp_peer_owner, token, sizeof(wgfp_peer_owner));
		}
		goto out_unlock;
	}
	if (strcmp(wgfp_peer_owner, token)) {
		err = -EPERM;
		goto out_unlock;
	}
	if (!strcmp(action, "stop")) {
		err = wgfp_peer_stop();
		if (!err) {
			strscpy(wgfp_peer_cfg, "off", sizeof(wgfp_peer_cfg));
			wgfp_owner_aliases[0] = '\0';
		}
	} else if (!strcmp(action, "release")) {
		if (rcu_access_pointer(wgfp_bs)) {
			err = -EBUSY;
		} else {
			wgfp_peer_owner[0] = '\0';
			wgfp_owner_aliases[0] = '\0';
		}
	} else if (rcu_access_pointer(wgfp_bs)) {
		bs = rcu_dereference_protected(wgfp_bs,
					       lockdep_is_held(&wgfp_lab_lock));
		if (READ_ONCE(bs->stopping) ||
		    strcmp(wgfp_owner_aliases, aliases))
			err = -EBUSY;
	} else if (wgfp_lab_on) {
		err = -EBUSY;
	} else {
		strscpy(copy, aliases, sizeof(copy));
		err = wgfp_peer_start(copy, true);
		if (!err) {
			bs = rcu_dereference_protected(wgfp_bs,
						lockdep_is_held(&wgfp_lab_lock));
			wgfp_peer_cfg[0] = '\0';
			for (i = 0; i < bs->n; i++) {
				if (i)
					strlcat(wgfp_peer_cfg, " ",
						sizeof(wgfp_peer_cfg));
				strlcat(wgfp_peer_cfg, bs->wgdev[i]->name,
					sizeof(wgfp_peer_cfg));
			}
			strscpy(wgfp_owner_aliases, aliases,
				sizeof(wgfp_owner_aliases));
		}
	}
out_unlock:
	mutex_unlock(&wgfp_lab_lock);
out_free:
	kfree(input);
	return err;
}

static int wgfp_peer_lease_get(char *buf, const struct kernel_param *kp)
{
	int len;

	mutex_lock(&wgfp_lab_lock);
	if (wgfp_peer_owner[0])
		len = scnprintf(buf, PAGE_SIZE, "owned=%s dev=%s\n",
				wgfp_peer_owner, wgfp_peer_cfg);
	else
		len = scnprintf(buf, PAGE_SIZE, "unowned\n");
	mutex_unlock(&wgfp_lab_lock);
	return len;
}

static const struct kernel_param_ops wgfp_peer_lease_ops = {
	.set = wgfp_peer_lease_set,
	.get = wgfp_peer_lease_get,
};
module_param_cb(wgfp_peer_lease, &wgfp_peer_lease_ops, NULL, 0600);
MODULE_PARM_DESC(wgfp_peer_lease,
		 "Atomic WGFP reservation: reserve, bind, stop or release with an owner token");

static int wgfp_peer_cfg_get(char *buf, const struct kernel_param *kp)
{
	struct wgfp_binds *bs;
	struct wgfp_pst *pst;
	bool stopping;
	unsigned int bkt, tmpl = 0;

	rcu_read_lock();
	bs = rcu_dereference(wgfp_bs);
	if (!bs) {
		rcu_read_unlock();
		return scnprintf(buf, PAGE_SIZE, "off\n");
	}
	stopping = READ_ONCE(bs->stopping);
	rcu_read_unlock();
	spin_lock_bh(&wgfp_flows_lock);
	hash_for_each(wgfp_psts, bkt, pst, node)
		tmpl += rcu_access_pointer(pst->tmpl) != NULL;
	spin_unlock_bh(&wgfp_flows_lock);
	return scnprintf(buf, PAGE_SIZE, "on stopping=%u sealed=%lld declined=%lld punted=%lld rekeys=%lld rx=%lld rx_stack=%lld peers=%u tmpl=%u flows=%u installs=%lld install_fail=%lld withdraws=%lld withdraw_fail=%lld decline_withdraws=%lld aip_withdraws=%lld dev=%s\n",
			 stopping,
			 (long long)dpaa_wgfp_stat_sum(&wgfp_sealed),
			 (long long)atomic64_read(&wgfp_declined),
			 (long long)atomic64_read(&wgfp_punted),
			 (long long)atomic64_read(&wgfp_rekeys),
			 (long long)dpaa_wgfp_stat_sum(&wgfp_rx_frames),
			 (long long)dpaa_wgfp_stat_sum(&wgfp_rx_stack),
			 READ_ONCE(wgfp_npsts), tmpl, READ_ONCE(wgfp_nflows),
			 (long long)atomic64_read(&wgfp_installs),
			 (long long)atomic64_read(&wgfp_install_fail),
			 (long long)atomic64_read(&wgfp_withdraws),
			 (long long)atomic64_read(&wgfp_withdraw_fail),
			 (long long)atomic64_read(&wgfp_decline_withdraws),
			 (long long)atomic64_read(&wgfp_aip_withdraws),
			 wgfp_peer_cfg);
}

static const struct kernel_param_ops wgfp_peer_param_ops = {
	.set = wgfp_peer_cfg_set,
	.get = wgfp_peer_cfg_get,
};
module_param_cb(wgfp_peer, &wgfp_peer_param_ops, NULL, 0600);
MODULE_PARM_DESC(wgfp_peer, "DPAA fast path for the peers of these wg devices: \"wg0 [wg1 ...]\"; \"off\" disables");

void wg_fastpath_uninit(void)
{
	int err;

	mutex_lock(&wgfp_lab_lock);
	err = wgfp_peer_stop();
	if (err)
		pr_err("wgfp: refusing peer teardown with unresolved hardware records (%d)\n",
		       err);
	else
		wgfp_lab_stop();
	mutex_unlock(&wgfp_lab_lock);
	cancel_work_sync(&wgfp_stop_work);
}
