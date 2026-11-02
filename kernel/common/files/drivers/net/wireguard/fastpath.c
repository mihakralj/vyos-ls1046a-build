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
#include <linux/etherdevice.h>
#include <linux/fsl/dpaa_wgfp.h>
#include <linux/hashtable.h>
#include <linux/hex.h>
#include <linux/if_ether.h>
#include <linux/inet.h>
#include <linux/jhash.h>
#include <linux/tcp.h>
#include <linux/ip.h>
#include <linux/moduleparam.h>
#include <linux/netdevice.h>
#include <linux/udp.h>
#include <net/checksum.h>
#include <net/ip.h>

#include <linux/inetdevice.h>
#include <linux/jiffies.h>
#include <linux/workqueue.h>
#include <net/arp.h>
#include <net/neighbour.h>
#include <net/route.h>
#if IS_REACHABLE(CONFIG_NF_CONNTRACK)
#include <net/netfilter/nf_conntrack.h>
#include <net/netfilter/nf_conntrack_helper.h>
#endif

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
	atomic64_t counter;
};

static struct wgfp_lab wgfp_lab_state;
static bool wgfp_lab_on;
static DEFINE_MUTEX(wgfp_lab_lock);
static char wgfp_lab_cfg[16] = "off";
struct wgfp_peer_state;
static struct wgfp_peer_state *wgfp_ps;

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

	dpaa_wgfp_xmit(f, f->off - WGFP_ADDED,
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
	err = dpaa_wgfp_set_egress(dev);
	dev_put(dev);
	if (err)
		return err;

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
	} else {
		wgfp_lab_stop();
		err = wgfp_ps ? -EBUSY : wgfp_lab_start(buf);
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
 * Phase P2b-1, "peer mode": the same in-place seal, but keyed from a real
 * WireGuard peer.  Each frame uses the peer's current keypair (receiver
 * index, key, atomic sending counter), honours REJECT_AFTER_*, triggers a
 * rekey at REKEY_AFTER_MESSAGES, pads to 16 within the device MTU, and feeds
 * the peer timers.  The outer Ethernet/IPv4/UDP template comes from the
 * peer's endpoint through the routing table and neighbour cache, refreshed
 * once a second.  Frames it cannot seal are declined (the sink drops them
 * and counts them); a punt to the slow path is P2b-3.  AllowedIPs are NOT
 * checked: only flows an operator steered to the sink queues reach it.
 *
 *   echo "<wg device>" > /sys/module/wireguard/parameters/wgfp_peer
 *   echo off > /sys/module/wireguard/parameters/wgfp_peer
 *
 * The first peer of the device is bound.
 */

struct wgfp_tmpl {
	struct rcu_head rcu;
	u8 hdr[ETH_HLEN + sizeof(struct iphdr) + sizeof(struct udphdr)];
};

struct wgfp_peer_state {
	struct wg_peer *peer;
	struct net_device *wgdev;
	struct net_device *egress;
	struct wgfp_tmpl __rcu *tmpl;
	struct delayed_work refresh;
	unsigned long last_timer;
	unsigned int mtu;
	atomic64_t sealed, declined, rekeys;
	bool stopping;
};

static struct wgfp_peer_state *wgfp_ps;
static char wgfp_peer_cfg[IFNAMSIZ + 8] = "off";

static void wgfp_peer_refresh(struct work_struct *w)
{
	struct wgfp_peer_state *ps = container_of(to_delayed_work(w),
						  struct wgfp_peer_state, refresh);
	struct wg_peer *peer = ps->peer;
	struct wgfp_tmpl *t, *old;
	struct neighbour *n = NULL;
	struct flowi4 fl = {};
	struct rtable *rt;
	struct ethhdr *eth;
	struct iphdr *ip;
	struct udphdr *udp;
	__be32 nh;

	if (ps->stopping)
		return;

	read_lock_bh(&peer->endpoint_lock);
	if (peer->endpoint.addr.sa_family != AF_INET) {
		read_unlock_bh(&peer->endpoint_lock);
		goto again;
	}
	fl.daddr = peer->endpoint.addr4.sin_addr.s_addr;
	fl.saddr = peer->endpoint.src4.s_addr;
	fl.fl4_dport = peer->endpoint.addr4.sin_port;
	read_unlock_bh(&peer->endpoint_lock);
	fl.flowi4_proto = IPPROTO_UDP;

	rt = ip_route_output_key(&init_net, &fl);
	if (IS_ERR(rt))
		goto again;
	if (rt->dst.dev != ps->egress) {
		ip_rt_put(rt);
		goto again;
	}
	nh = rt_nexthop(rt, fl.daddr);
	n = __ipv4_neigh_lookup(rt->dst.dev, (__force u32)nh);
	if (!n || !(n->nud_state & NUD_VALID)) {
		if (n)
			neigh_release(n);
		else
			neigh_event_send(__neigh_create(&arp_tbl, &nh, rt->dst.dev, true), NULL);
		ip_rt_put(rt);
		goto again;
	}

	t = kzalloc(sizeof(*t), GFP_KERNEL);
	if (t) {
		eth = (struct ethhdr *)t->hdr;
		read_lock_bh(&n->lock);
		ether_addr_copy(eth->h_dest, n->ha);
		read_unlock_bh(&n->lock);
		ether_addr_copy(eth->h_source, rt->dst.dev->dev_addr);
		eth->h_proto = htons(ETH_P_IP);
		ip = (struct iphdr *)(eth + 1);
		ip->version = 4;
		ip->ihl = 5;
		ip->frag_off = htons(IP_DF);
		ip->ttl = ip4_dst_hoplimit(&rt->dst);
		ip->protocol = IPPROTO_UDP;
		ip->saddr = fl.saddr ?: inet_select_addr(rt->dst.dev, nh, RT_SCOPE_UNIVERSE);
		ip->daddr = fl.daddr;
		udp = (struct udphdr *)(ip + 1);
		udp->source = htons(peer->device->incoming_port);
		udp->dest = fl.fl4_dport;
		old = rcu_replace_pointer(ps->tmpl, t, true);
		if (old)
			kfree_rcu(old, rcu);
	}
	neigh_release(n);
	ip_rt_put(rt);
again:
	if (!ps->stopping)
		schedule_delayed_work(&ps->refresh, HZ);
}

static void wgfp_withdraw_frame(const struct iphdr *iph, u32 len);

static int wgfp_peer_frame(void *priv, struct dpaa_wgfp_frame *f)
{
	struct wgfp_peer_state *ps = priv;
	struct wg_peer *peer = ps->peer;
	struct noise_keypair *kp;
	const struct wgfp_tmpl *t;
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
		goto decline;
	iph = (const struct iphdr *)inner;
	if (iph->version != 4)
		goto decline;
	ip_len = ntohs(iph->tot_len);
	if (ip_len < sizeof(*iph) || ip_len > ps->mtu || ETH_HLEN + ip_len > f->len)
		goto decline_flow;
	/* We forward the inner packet: TTL must drop by one, as on the slow
	 * path.  TTL <= 1 is left to the stack (ICMP time exceeded); ASK's
	 * ttl_punt normally sends those to the kernel before the record. */
	if (iph->ttl <= 1 || READ_ONCE(peer->is_dead))
		goto decline_flow;

	plain = min_t(u32, ps->mtu, ALIGN(ip_len, MESSAGE_PADDING_MULTIPLE));
	if (f->off < WGFP_ADDED ||
	    f->off + ETH_HLEN + plain + POLY1305_DIGEST_SIZE > f->buf_size)
		goto decline;

	t = rcu_dereference(ps->tmpl);
	if (unlikely(!t))
		goto decline;
	kp = rcu_dereference(peer->keypairs.current_keypair);
	if (unlikely(!kp || !READ_ONCE(kp->sending.is_valid) ||
		     wg_birthdate_has_expired(kp->sending.birthdate, REJECT_AFTER_TIME)))
		goto decline;
	ctr = (u64)atomic64_inc_return(&kp->sending_counter) - 1;
	if (unlikely(ctr >= REJECT_AFTER_MESSAGES))
		goto decline;
	ip_decrease_ttl((struct iphdr *)iph);
	memset(inner + ip_len, 0, plain - ip_len);
	chacha20poly1305_encrypt(inner, inner, plain, NULL, 0, ctr,
				 kp->sending.key);

	hdr = inner - WGFP_HDR_LEN;
	memcpy(hdr, t->hdr, sizeof(t->hdr));
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
	wg->key_idx = kp->remote_index;
	wg->counter = cpu_to_le64(ctr);

	if (time_after(jiffies, READ_ONCE(ps->last_timer))) {
		WRITE_ONCE(ps->last_timer, jiffies + HZ / 100);
		wg_timers_any_authenticated_packet_traversal(peer);
		wg_timers_any_authenticated_packet_sent(peer);
		wg_timers_data_sent(peer);
		/* Same test as keep_key_fresh(); the send is itself rate limited. */
		if (ctr > REKEY_AFTER_MESSAGES ||
		    (kp->i_am_the_initiator &&
		     wg_birthdate_has_expired(kp->sending.birthdate, REKEY_AFTER_TIME))) {
			atomic64_inc(&ps->rekeys);
			wg_packet_send_queued_handshake_initiation(peer, false);
		}
	}
	atomic64_inc(&ps->sealed);
	dpaa_wgfp_xmit(f, f->off - WGFP_ADDED,
		       WGFP_HDR_LEN + plain + POLY1305_DIGEST_SIZE);
	return 1;
decline_flow:
	wgfp_withdraw_frame(iph, f->len - ETH_HLEN);
decline:
	atomic64_inc(&ps->declined);
	return 0;
}

static const struct dpaa_wgfp_ops wgfp_peer_ops = {
	.frame = wgfp_peer_frame,
};

/*
 * Phase P2b-2, automatic flow install.  wg_xmit() offers every packet for
 * the bound peer to wg_fastpath_learn().  An inner IPv4 TCP/UDP flow that
 * arrived on a DPAA port, whose conntrack entry is established, not NAT'ed,
 * not related to a master and has no helper, gets an FMan record (installed
 * by a worker; the record API sleeps) that steers its later frames on that
 * port straight to the sinks, where wgfp_peer_frame() seals them.  TCP SYN
 * is never learned; FIN/RST (punted to the kernel by ASK's tcp_ctl_punt)
 * withdraws the record, as does 10 s without silicon hits or peer stop.
 *
 * Like an offloaded flowtable flow, a fast-pathed flow skips the firewall
 * after it was admitted: rule changes apply when the record is withdrawn.
 */
#define WGFP_FLOW_BITS		10
#define WGFP_FLOW_MAX		4096
#define WGFP_AGE_PERIOD		(2 * HZ)
#define WGFP_AGE_IDLE		5	/* periods without hits */
#define WGFP_FAIL_HOLD		(30 * HZ)
#define WGFP_BATCH		16	/* job arrays live on the stack */

enum { WGFP_PENDING, WGFP_INSTALLING, WGFP_INSTALLED, WGFP_FAILED };

struct wgfp_flow {
	struct hlist_node node;
	struct dpaa_wgfp_tuple t;
	int ifindex;
	u32 hash;
	u8 state;
	bool del;
	u8 idle;
	u64 pkts;
	unsigned long since;
};

static DEFINE_HASHTABLE(wgfp_flows, WGFP_FLOW_BITS);
static DEFINE_SPINLOCK(wgfp_flows_lock);
static unsigned int wgfp_nflows;
static atomic64_t wgfp_installs, wgfp_withdraws, wgfp_install_fail;
static void wgfp_flow_work_fn(struct work_struct *w);
static void wgfp_age_fn(struct work_struct *w);
static DECLARE_WORK(wgfp_flow_work, wgfp_flow_work_fn);
static DECLARE_DELAYED_WORK(wgfp_age_work, wgfp_age_fn);

static bool wgfp_tuple_eq(const struct dpaa_wgfp_tuple *a,
			  const struct dpaa_wgfp_tuple *b)
{
	return a->saddr == b->saddr && a->daddr == b->daddr &&
	       a->sport == b->sport && a->dport == b->dport &&
	       a->proto == b->proto;
}

static u32 wgfp_tuple_hash(const struct dpaa_wgfp_tuple *t)
{
	return jhash_3words((__force u32)t->saddr, (__force u32)t->daddr,
			    ((__force u32)t->sport << 16 | (__force u32)t->dport) ^
			    t->proto, 0);
}

/* ifindex < 0 matches any ingress (the sink does not know the port). */
static struct wgfp_flow *wgfp_flow_find(const struct dpaa_wgfp_tuple *t,
					int ifindex, u32 hash)
{
	struct wgfp_flow *fl;

	hash_for_each_possible(wgfp_flows, fl, node, hash)
		if ((ifindex < 0 || fl->ifindex == ifindex) &&
		    wgfp_tuple_eq(&fl->t, t))
			return fl;
	return NULL;
}

/*
 * A frame of a learned flow that the seal path declined (too big for the
 * tunnel MTU, TTL, no keypair, no template) is dropped by the sink.  Until a
 * slow-path punt exists (P2b-3), withdraw that flow's record so its later
 * packets take the stack again, which fragments or sends ICMP as needed
 * (else an oversized DF flow would black-hole).  NAPI context.
 */
static atomic64_t wgfp_decline_withdraws;

static void wgfp_withdraw_frame(const struct iphdr *iph, u32 len)
{
	struct dpaa_wgfp_tuple t = {};
	const __be16 *ports = (const __be16 *)((const u8 *)iph + sizeof(*iph));
	struct wgfp_flow *fl;

	if (len < sizeof(*iph) + 4 || iph->ihl != 5 ||
	    (iph->protocol != IPPROTO_TCP && iph->protocol != IPPROTO_UDP))
		return;
	t.saddr = iph->saddr;
	t.daddr = iph->daddr;
	t.proto = iph->protocol;
	t.sport = ports[0];
	t.dport = ports[1];
	spin_lock_bh(&wgfp_flows_lock);
	fl = wgfp_flow_find(&t, -1, wgfp_tuple_hash(&t));
	if (fl && !fl->del) {
		fl->del = true;
		atomic64_inc(&wgfp_decline_withdraws);
		queue_work(system_wq, &wgfp_flow_work);
	}
	spin_unlock_bh(&wgfp_flows_lock);
}

void wg_fastpath_learn(struct wg_peer *peer, struct sk_buff *skb)
{
	struct wgfp_peer_state *ps = READ_ONCE(wgfp_ps);
	struct dpaa_wgfp_tuple t = {};
	struct wgfp_flow *fl;
	bool fin = false;
	const struct iphdr *iph;
	unsigned int thoff;
	u32 hash;

	if (!ps || ps->peer != peer || skb->protocol != htons(ETH_P_IP) ||
	    !skb->skb_iif)
		return;
	iph = ip_hdr(skb);
	if (iph->ihl != 5 || ip_is_fragment(iph) ||
	    (iph->protocol != IPPROTO_TCP && iph->protocol != IPPROTO_UDP))
		return;
	thoff = skb_network_offset(skb) + sizeof(*iph);
	if (iph->protocol == IPPROTO_TCP) {
		struct tcphdr _th;
		const struct tcphdr *th = skb_header_pointer(skb, thoff,
							     sizeof(_th), &_th);

		if (!th || th->syn)
			return;
		fin = th->fin || th->rst;
		t.sport = th->source;
		t.dport = th->dest;
	} else {
		struct udphdr _uh;
		const struct udphdr *uh = skb_header_pointer(skb, thoff,
							     sizeof(_uh), &_uh);

		if (!uh)
			return;
		t.sport = uh->source;
		t.dport = uh->dest;
	}
	t.saddr = iph->saddr;
	t.daddr = iph->daddr;
	t.proto = iph->protocol;
	hash = wgfp_tuple_hash(&t);

	/* ponytail: one global lock per slow-path packet of the bound peer;
	 * an RCU lookup if it ever shows in a profile. */
	spin_lock_bh(&wgfp_flows_lock);
	fl = wgfp_flow_find(&t, skb->skb_iif, hash);
	if (fl) {
		if (fin && !fl->del) {
			fl->del = true;
			queue_work(system_wq, &wgfp_flow_work);
		}
		goto out;
	}
	if (fin || wgfp_nflows >= WGFP_FLOW_MAX)
		goto out;
#if IS_REACHABLE(CONFIG_NF_CONNTRACK)
	{
		enum ip_conntrack_info ctinfo;
		struct nf_conn *ct = nf_ct_get(skb, &ctinfo);

		if (!ct || (ctinfo != IP_CT_ESTABLISHED &&
			    ctinfo != IP_CT_ESTABLISHED_REPLY) ||
		    (ct->status & IPS_NAT_MASK) || ct->master || nfct_help(ct))
			goto out;
	}
#else
	goto out;	/* no conntrack: no proof the flow was admitted */
#endif
	fl = kzalloc(sizeof(*fl), GFP_ATOMIC);
	if (!fl)
		goto out;
	fl->t = t;
	fl->ifindex = skb->skb_iif;
	fl->hash = hash;
	fl->state = WGFP_PENDING;
	fl->since = jiffies;
	hash_add(wgfp_flows, &fl->node, hash);
	wgfp_nflows++;
	queue_work(system_wq, &wgfp_flow_work);
out:
	spin_unlock_bh(&wgfp_flows_lock);
}

static void wgfp_flow_free_locked(struct wgfp_flow *fl)
{
	hash_del(&fl->node);
	wgfp_nflows--;
	kfree(fl);
}

/* Install PENDING records, withdraw del-marked ones.  Only this worker and
 * the teardown (after cancelling it) free entries. */
static void wgfp_flow_work_fn(struct work_struct *w)
{
	struct { struct wgfp_flow *fl; struct dpaa_wgfp_tuple t; int ifindex;
		 u32 hash; bool add; } job[WGFP_BATCH];
	struct wgfp_flow *fl;
	struct hlist_node *tmp;
	unsigned int n = 0, i, bkt;
	bool more = false;

	spin_lock_bh(&wgfp_flows_lock);
	hash_for_each_safe(wgfp_flows, bkt, tmp, fl, node) {
		bool add;

		if (fl->state == WGFP_INSTALLING)
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
		job[n].hash = fl->hash;
		job[n].add = add;
		n++;
	}
	spin_unlock_bh(&wgfp_flows_lock);

	for (i = 0; i < n; i++) {
		struct net_device *dev = dev_get_by_index(&init_net, job[i].ifindex);
		int err = -ENODEV;

		if (dev) {
			err = job[i].add ?
			      dpaa_wgfp_flow_add(dev, &job[i].t, job[i].hash) :
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
		} else {
			atomic64_inc(&wgfp_withdraws);
			wgfp_flow_free_locked(fl);
		}
		spin_unlock_bh(&wgfp_flows_lock);
	}
	if (more)
		queue_work(system_wq, &wgfp_flow_work);
}

/* Withdraw records whose silicon counter stopped moving; forget failures. */
static void wgfp_age_fn(struct work_struct *w)
{
	struct { struct wgfp_flow *fl; struct dpaa_wgfp_tuple t; int ifindex; }
		job[WGFP_BATCH];
	static unsigned int cursor;
	unsigned int n = 0, i, bkt, seen = 0;
	struct wgfp_flow *fl;
	struct hlist_node *tmp;
	bool kick = false;

	spin_lock_bh(&wgfp_flows_lock);
	hash_for_each_safe(wgfp_flows, bkt, tmp, fl, node) {
		if (fl->state == WGFP_FAILED && !fl->del &&
		    time_after(jiffies, fl->since + WGFP_FAIL_HOLD)) {
			wgfp_flow_free_locked(fl);
			continue;
		}
		if (fl->state != WGFP_INSTALLED || fl->del)
			continue;
		/* round-robin over passes when there are more than a batch */
		if (seen++ < cursor || n == WGFP_BATCH)
			continue;
		job[n].fl = fl;
		job[n].t = fl->t;
		job[n].ifindex = fl->ifindex;
		n++;
	}
	cursor = (n == WGFP_BATCH) ? cursor + n : 0;
	spin_unlock_bh(&wgfp_flows_lock);

	for (i = 0; i < n; i++) {
		struct net_device *dev = dev_get_by_index(&init_net, job[i].ifindex);
		u64 pkts = 0;
		int err = -ENODEV;

		if (dev) {
			err = dpaa_wgfp_flow_packets(dev, &job[i].t, &pkts);
			dev_put(dev);
		}
		spin_lock_bh(&wgfp_flows_lock);
		fl = job[i].fl;
		/* only this worker and the flow worker touch INSTALLED entries;
		 * the flow worker frees only del-marked ones */
		if (fl->state == WGFP_INSTALLED && !fl->del) {
			if (err || pkts == fl->pkts) {
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
	if (kick)
		queue_work(system_wq, &wgfp_flow_work);
	if (READ_ONCE(wgfp_ps))
		schedule_delayed_work(&wgfp_age_work, WGFP_AGE_PERIOD);
}

/* Teardown: learning already stopped (wgfp_ps NULL, RCU synced). */
static void wgfp_flows_flush(void)
{
	struct wgfp_flow *fl;
	struct hlist_node *tmp;
	unsigned int bkt;

	cancel_delayed_work_sync(&wgfp_age_work);
	cancel_work_sync(&wgfp_flow_work);
	/* no worker runs now; nothing else frees entries, so drop the lock
	 * around the sleeping record delete */
	spin_lock_bh(&wgfp_flows_lock);
	hash_for_each_safe(wgfp_flows, bkt, tmp, fl, node) {
		if (fl->state == WGFP_INSTALLED) {
			struct dpaa_wgfp_tuple t = fl->t;
			int ifindex = fl->ifindex;
			struct net_device *dev;

			spin_unlock_bh(&wgfp_flows_lock);
			dev = dev_get_by_index(&init_net, ifindex);
			if (dev) {
				dpaa_wgfp_flow_del(dev, &t);
				dev_put(dev);
			}
			atomic64_inc(&wgfp_withdraws);
			spin_lock_bh(&wgfp_flows_lock);
		}
		wgfp_flow_free_locked(fl);
	}
	spin_unlock_bh(&wgfp_flows_lock);
}

static void wgfp_peer_stop(void)
{
	struct wgfp_peer_state *ps = wgfp_ps;

	if (!ps)
		return;
	WRITE_ONCE(wgfp_ps, NULL);	/* wg_fastpath_learn() runs under RCU-bh */
	synchronize_rcu();
	wgfp_flows_flush();
	dpaa_wgfp_unregister();
	ps->stopping = true;
	cancel_delayed_work_sync(&ps->refresh);
	synchronize_rcu();
	kfree(rcu_dereference_protected(ps->tmpl, true));
	wg_peer_put(ps->peer);
	dev_put(ps->wgdev);
	dev_put(ps->egress);
	kfree(ps);
}

static int wgfp_peer_start(const char *name, const char *egress)
{
	struct wgfp_peer_state *ps;
	struct net_device *wgdev, *eg;
	struct wg_device *wg;
	struct wg_peer *peer;
	int err;

	wgdev = dev_get_by_name(&init_net, name);
	if (!wgdev)
		return -ENODEV;
	if (wgdev->rtnl_link_ops == NULL || strcmp(wgdev->rtnl_link_ops->kind, "wireguard")) {
		dev_put(wgdev);
		return -EINVAL;
	}
	eg = dev_get_by_name(&init_net, egress);
	if (!eg) {
		dev_put(wgdev);
		return -ENODEV;
	}
	wg = netdev_priv(wgdev);
	mutex_lock(&wg->device_update_lock);
	peer = list_first_entry_or_null(&wg->peer_list, struct wg_peer, peer_list);
	if (peer)
		peer = wg_peer_get_maybe_zero(peer);
	mutex_unlock(&wg->device_update_lock);
	if (!peer) {
		err = -ENOENT;
		goto err_dev;
	}
	ps = kzalloc(sizeof(*ps), GFP_KERNEL);
	if (!ps) {
		err = -ENOMEM;
		goto err_peer;
	}
	ps->peer = peer;
	ps->wgdev = wgdev;
	ps->egress = eg;
	ps->mtu = wgdev->mtu;
	INIT_DELAYED_WORK(&ps->refresh, wgfp_peer_refresh);
	err = dpaa_wgfp_set_egress(eg);
	if (err)
		goto err_ps;
	err = dpaa_wgfp_register(&wgfp_peer_ops, ps);
	if (err)
		goto err_ps;
	WRITE_ONCE(wgfp_ps, ps);
	schedule_delayed_work(&ps->refresh, 0);
	schedule_delayed_work(&wgfp_age_work, WGFP_AGE_PERIOD);
	pr_info("fastpath peer mode on: %s via %s\n", name, egress);
	return 0;
err_ps:
	kfree(ps);
err_peer:
	wg_peer_put(peer);
err_dev:
	dev_put(eg);
	dev_put(wgdev);
	return err;
}

static int wgfp_peer_cfg_set(const char *val, const struct kernel_param *kp)
{
	char buf[64], name[IFNAMSIZ], egress[IFNAMSIZ];
	int err = 0;

	strscpy(buf, val, sizeof(buf));
	strim(buf);
	mutex_lock(&wgfp_lab_lock);
	wgfp_peer_stop();
	if (!strcmp(buf, "off")) {
		strscpy(wgfp_peer_cfg, "off", sizeof(wgfp_peer_cfg));
	} else if (wgfp_lab_on) {
		err = -EBUSY;
	} else if (sscanf(buf, "%15s %15s", name, egress) != 2) {
		err = -EINVAL;
	} else {
		err = wgfp_peer_start(name, egress);
		strscpy(wgfp_peer_cfg, err ? "off" : "on", sizeof(wgfp_peer_cfg));
	}
	mutex_unlock(&wgfp_lab_lock);
	return err;
}

static int wgfp_peer_cfg_get(char *buf, const struct kernel_param *kp)
{
	struct wgfp_peer_state *ps = wgfp_ps;

	if (!ps)
		return scnprintf(buf, PAGE_SIZE, "off\n");
	return scnprintf(buf, PAGE_SIZE, "on sealed=%lld declined=%lld rekeys=%lld tmpl=%d flows=%u installs=%lld install_fail=%lld withdraws=%lld decline_withdraws=%lld\n",
			 (long long)atomic64_read(&ps->sealed),
			 (long long)atomic64_read(&ps->declined),
			 (long long)atomic64_read(&ps->rekeys),
			 rcu_access_pointer(ps->tmpl) != NULL, READ_ONCE(wgfp_nflows),
			 (long long)atomic64_read(&wgfp_installs),
			 (long long)atomic64_read(&wgfp_install_fail),
			 (long long)atomic64_read(&wgfp_withdraws),
			 (long long)atomic64_read(&wgfp_decline_withdraws));
}

static const struct kernel_param_ops wgfp_peer_param_ops = {
	.set = wgfp_peer_cfg_set,
	.get = wgfp_peer_cfg_get,
};
module_param_cb(wgfp_peer, &wgfp_peer_param_ops, NULL, 0600);
MODULE_PARM_DESC(wgfp_peer, "DPAA fast path bound to a real peer (P2b-1): \"<wg dev> <egress dev>\"; \"off\" disables");

void wg_fastpath_uninit(void)
{
	mutex_lock(&wgfp_lab_lock);
	wgfp_peer_stop();
	wgfp_lab_stop();
	mutex_unlock(&wgfp_lab_lock);
}
