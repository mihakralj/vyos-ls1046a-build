// SPDX-License-Identifier: GPL-2.0
/* Host ASan/UBSan verification of fastpath.c's wgfp_ct_admitted() and
 * wgfp_ct_orig_tuple() including the skb->_nfct == NULL fallback used when
 * nf_flow_offload (VYOS_FLOWTABLE_FT) forwards NAT44/NAT66 packets to wgw0. */
#include <arpa/inet.h>
#include <assert.h>
#include <linux/in6.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef uint8_t u8;
typedef uint16_t u16;
typedef uint32_t u32;
typedef uint64_t u64;
typedef uint16_t __be16;
typedef uint32_t __be32;

#define IS_REACHABLE(x) 1
#define CONFIG_NF_CONNTRACK 1
#define READ_ONCE(x) (*(const volatile typeof(x) *)&(x))
#define spin_lock_bh(l) ((void)(l))
#define spin_unlock_bh(l) ((void)(l))
#define unlikely(x) (x)
#define likely(x) (x)

/* Match include/linux/fsl/dpaa_wgfp.h */
struct dpaa_wgfp_tuple {
	u8 family;
	u8 proto;
	__be16 sport;
	__be16 dport;
	__be16 vlan_vid;
	__be16 pppoe_sid;
	u16 pad;
	union {
		struct { __be32 saddr, daddr; };
		struct { struct in6_addr saddr6, daddr6; };
	};
};

#define WGFP_NAT_SRC_IP		(1U << 0)
#define WGFP_NAT_DST_IP		(1U << 1)
#define WGFP_NAT_SRC_PORT	(1U << 2)
#define WGFP_NAT_DST_PORT	(1U << 3)

struct wgfp_nat {
	u8 flags;
	u8 pad;
	__be16 sport;
	__be16 dport;
	union {
		struct { __be32 saddr, daddr; };
		struct { struct in6_addr saddr6, daddr6; };
	};
};

/* Minimal netfilter conntrack mocks matching Linux 6.18 layout/semantics. */
enum ip_conntrack_dir {
	IP_CT_DIR_ORIGINAL = 0,
	IP_CT_DIR_REPLY = 1,
	IP_CT_DIR_MAX = 2,
};

enum ip_conntrack_info {
	IP_CT_ESTABLISHED = 0,
	IP_CT_RELATED = 1,
	IP_CT_NEW = 2,
	IP_CT_IS_REPLY = 3,
	IP_CT_ESTABLISHED_REPLY = IP_CT_ESTABLISHED + IP_CT_IS_REPLY,
};

#define CTINFO2DIR(ctinfo) ((ctinfo) >= IP_CT_IS_REPLY ? IP_CT_DIR_REPLY : IP_CT_DIR_ORIGINAL)

enum {
	IPS_SEEN_REPLY_BIT = 1,
	IPS_SRC_NAT_BIT = 4,
	IPS_DST_NAT_BIT = 5,
	IPS_SEQ_ADJUST_BIT = 6,
};
#define IPS_SRC_NAT (1UL << IPS_SRC_NAT_BIT)
#define IPS_DST_NAT (1UL << IPS_DST_NAT_BIT)
#define IPS_NAT_MASK (IPS_SRC_NAT | IPS_DST_NAT)
#define IPS_SEQ_ADJUST (1UL << IPS_SEQ_ADJUST_BIT)

#define TCP_CONNTRACK_ESTABLISHED 3
#define TCP_CONNTRACK_FIN_WAIT 4
#define IP_CT_TCP_FLAG_BE_LIBERAL 0x04

static inline bool test_bit(int bit, const unsigned long *addr)
{
	return ((*addr) & (1UL << bit)) != 0;
}

union nf_inet_addr {
	__be32 ip;
	struct in6_addr in6;
	u32 all[4];
};

struct nf_conntrack_tuple {
	struct {
		union nf_inet_addr u3;
		union { __be16 all; } u;
		u16 l3num;
	} src;
	struct {
		union nf_inet_addr u3;
		union { __be16 all; } u;
		u8 protonum;
		u8 dir;
	} dst;
};

struct nf_conntrack_tuple_hash {
	struct nf_conntrack_tuple tuple;
};

#define NF_CT_DIRECTION(h) ((enum ip_conntrack_dir)(h)->tuple.dst.dir)

struct nf_conntrack_zone {
	u16 id;
};
static const struct nf_conntrack_zone nf_ct_zone_dflt = { .id = 0 };

struct ip_ct_tcp_state {
	u8 flags;
};

struct nf_conn {
	int use;
	unsigned long status;
	struct nf_conntrack_tuple_hash tuplehash[IP_CT_DIR_MAX];
	struct nf_conntrack_zone zone;
	bool dying;
	bool expired;
	void *master;
	void *help;
	int lock;
	struct {
		struct {
			u8 state;
			struct ip_ct_tcp_state seen[2];
		} tcp;
	} proto;
};

struct net { int dummy; };
struct sk_buff {
	struct nf_conn *ct;
	enum ip_conntrack_info ctinfo;
};

static struct nf_conn *nf_ct_get(const struct sk_buff *skb,
				 enum ip_conntrack_info *ctinfo)
{
	*ctinfo = skb->ctinfo;
	return skb->ct;
}

static bool nf_ct_zone_equal_any(const struct nf_conn *ct,
				 const struct nf_conntrack_zone *zone)
{
	return ct->zone.id == zone->id;
}

static bool nf_ct_is_dying(const struct nf_conn *ct) { return ct->dying; }
static bool nf_ct_is_expired(const struct nf_conn *ct) { return ct->expired; }
static void *nfct_help(const struct nf_conn *ct) { return ct->help; }

static struct nf_conn *nf_ct_tuplehash_to_ctrack(const struct nf_conntrack_tuple_hash *h)
{
	return (struct nf_conn *)((char *)h -
		offsetof(struct nf_conn, tuplehash[h->tuple.dst.dir]));
}

static void nf_ct_put(struct nf_conn *ct)
{
	assert(ct->use > 0);
	ct->use--;
}

/* Mock conntrack table matching Linux 6.18 nf_ct_tuple_equal() semantics
 * (compares src/dst IP, ports, l3num, protonum; ignores dst.dir). */
static struct nf_conn *mock_cts[8];
static size_t mock_ncts;

static bool mock_tuple_eq(const struct nf_conntrack_tuple *a,
			  const struct nf_conntrack_tuple *b)
{
	if (a->src.l3num != b->src.l3num ||
	    a->dst.protonum != b->dst.protonum ||
	    a->src.u.all != b->src.u.all ||
	    a->dst.u.all != b->dst.u.all)
		return false;
	if (a->src.l3num == AF_INET6)
		return !memcmp(&a->src.u3.in6, &b->src.u3.in6, 16) &&
		       !memcmp(&a->dst.u3.in6, &b->dst.u3.in6, 16);
	return a->src.u3.ip == b->src.u3.ip && a->dst.u3.ip == b->dst.u3.ip;
}

static struct nf_conntrack_tuple_hash *
nf_conntrack_find_get(struct net *net, const struct nf_conntrack_zone *zone,
		      const struct nf_conntrack_tuple *tuple)
{
	(void)net;
	for (size_t i = 0; i < mock_ncts; i++) {
		struct nf_conn *ct = mock_cts[i];
		if (ct->zone.id != zone->id)
			continue;
		for (int d = 0; d < IP_CT_DIR_MAX; d++) {
			if (mock_tuple_eq(&ct->tuplehash[d].tuple, tuple)) {
				ct->use++;
				return &ct->tuplehash[d];
			}
		}
	}
	return NULL;
}

#include "wgfp_ct_admit_body.h"

static void set_v4_ct(struct nf_conn *ct, u8 proto,
		      const char *os, u16 osp, const char *od, u16 odp,
		      const char *rs, u16 rsp, const char *rd, u16 rdp,
		      unsigned long status)
{
	memset(ct, 0, sizeof(*ct));
	ct->use = 1;
	ct->status = status;
	ct->proto.tcp.state = TCP_CONNTRACK_ESTABLISHED;
	ct->tuplehash[IP_CT_DIR_ORIGINAL].tuple.src.l3num = AF_INET;
	ct->tuplehash[IP_CT_DIR_ORIGINAL].tuple.dst.protonum = proto;
	ct->tuplehash[IP_CT_DIR_ORIGINAL].tuple.dst.dir = IP_CT_DIR_ORIGINAL;
	assert(inet_pton(AF_INET, os, &ct->tuplehash[IP_CT_DIR_ORIGINAL].tuple.src.u3.ip) == 1);
	assert(inet_pton(AF_INET, od, &ct->tuplehash[IP_CT_DIR_ORIGINAL].tuple.dst.u3.ip) == 1);
	ct->tuplehash[IP_CT_DIR_ORIGINAL].tuple.src.u.all = htons(osp);
	ct->tuplehash[IP_CT_DIR_ORIGINAL].tuple.dst.u.all = htons(odp);

	ct->tuplehash[IP_CT_DIR_REPLY].tuple.src.l3num = AF_INET;
	ct->tuplehash[IP_CT_DIR_REPLY].tuple.dst.protonum = proto;
	ct->tuplehash[IP_CT_DIR_REPLY].tuple.dst.dir = IP_CT_DIR_REPLY;
	assert(inet_pton(AF_INET, rs, &ct->tuplehash[IP_CT_DIR_REPLY].tuple.src.u3.ip) == 1);
	assert(inet_pton(AF_INET, rd, &ct->tuplehash[IP_CT_DIR_REPLY].tuple.dst.u3.ip) == 1);
	ct->tuplehash[IP_CT_DIR_REPLY].tuple.src.u.all = htons(rsp);
	ct->tuplehash[IP_CT_DIR_REPLY].tuple.dst.u.all = htons(rdp);
}

static void set_v6_ct(struct nf_conn *ct, u8 proto,
		      const char *os, u16 osp, const char *od, u16 odp,
		      const char *rs, u16 rsp, const char *rd, u16 rdp,
		      unsigned long status)
{
	memset(ct, 0, sizeof(*ct));
	ct->use = 1;
	ct->status = status;
	ct->proto.tcp.state = TCP_CONNTRACK_ESTABLISHED;
	ct->tuplehash[IP_CT_DIR_ORIGINAL].tuple.src.l3num = AF_INET6;
	ct->tuplehash[IP_CT_DIR_ORIGINAL].tuple.dst.protonum = proto;
	ct->tuplehash[IP_CT_DIR_ORIGINAL].tuple.dst.dir = IP_CT_DIR_ORIGINAL;
	assert(inet_pton(AF_INET6, os, &ct->tuplehash[IP_CT_DIR_ORIGINAL].tuple.src.u3.in6) == 1);
	assert(inet_pton(AF_INET6, od, &ct->tuplehash[IP_CT_DIR_ORIGINAL].tuple.dst.u3.in6) == 1);
	ct->tuplehash[IP_CT_DIR_ORIGINAL].tuple.src.u.all = htons(osp);
	ct->tuplehash[IP_CT_DIR_ORIGINAL].tuple.dst.u.all = htons(odp);

	ct->tuplehash[IP_CT_DIR_REPLY].tuple.src.l3num = AF_INET6;
	ct->tuplehash[IP_CT_DIR_REPLY].tuple.dst.protonum = proto;
	ct->tuplehash[IP_CT_DIR_REPLY].tuple.dst.dir = IP_CT_DIR_REPLY;
	assert(inet_pton(AF_INET6, rs, &ct->tuplehash[IP_CT_DIR_REPLY].tuple.src.u3.in6) == 1);
	assert(inet_pton(AF_INET6, rd, &ct->tuplehash[IP_CT_DIR_REPLY].tuple.dst.u3.in6) == 1);
	ct->tuplehash[IP_CT_DIR_REPLY].tuple.src.u.all = htons(rsp);
	ct->tuplehash[IP_CT_DIR_REPLY].tuple.dst.u.all = htons(rdp);
}

int main(void)
{
	struct net net = {};
	struct nf_conn ct;
	struct sk_buff skb = {};
	struct dpaa_wgfp_tuple post = {}, orig = {};
	struct wgfp_nat nat = {};
	__be32 exp_lan, exp_wan, exp_nat;

	assert(inet_pton(AF_INET, "10.99.2.113", &exp_lan) == 1);
	assert(inet_pton(AF_INET, "10.99.230.112", &exp_wan) == 1);
	assert(inet_pton(AF_INET, "10.99.230.1", &exp_nat) == 1);

	/* 1. wgwan-up-v4 SNAT44 forwarded by software flowtable (skb->_nfct == NULL). */
	set_v4_ct(&ct, IPPROTO_TCP,
		  "10.99.2.113", 40923, "10.99.230.112", 5201,
		  "10.99.230.112", 5201, "10.99.230.1", 40923,
		  (1UL << IPS_SEEN_REPLY_BIT) | IPS_SRC_NAT);
	mock_cts[0] = &ct;
	mock_ncts = 1;
	memset(&post, 0, sizeof(post));
	post.family = AF_INET;
	post.proto = IPPROTO_TCP;
	post.saddr = exp_nat;
	post.daddr = exp_wan;
	post.sport = htons(40923);
	post.dport = htons(5201);
	skb.ct = NULL;

	assert(wgfp_ct_admitted(&net, &skb, &post, &orig, &nat));
	assert(orig.saddr == exp_lan && orig.daddr == exp_wan);
	assert(orig.sport == htons(40923) && orig.dport == htons(5201));
	assert(nat.flags == WGFP_NAT_SRC_IP && nat.saddr == exp_nat);
	assert(ct.proto.tcp.seen[0].flags & IP_CT_TCP_FLAG_BE_LIBERAL);
	assert(ct.proto.tcp.seen[1].flags & IP_CT_TCP_FLAG_BE_LIBERAL);
	assert(ct.use == 1);

	/* 2. Port-mapped SNAT44 (src IP + src port rewritten) with skb->_nfct == NULL. */
	set_v4_ct(&ct, IPPROTO_TCP,
		  "10.99.2.113", 40923, "10.99.230.112", 5201,
		  "10.99.230.112", 5201, "10.99.230.1", 55555,
		  (1UL << IPS_SEEN_REPLY_BIT) | IPS_SRC_NAT);
	post.sport = htons(55555);
	assert(wgfp_ct_admitted(&net, &skb, &post, &orig, &nat));
	assert(orig.saddr == exp_lan && orig.sport == htons(40923));
	assert(nat.flags == (WGFP_NAT_SRC_IP | WGFP_NAT_SRC_PORT));
	assert(nat.saddr == exp_nat && nat.sport == htons(55555));
	assert(ct.use == 1);

	/* 3. DNAT44 port-forward reply (LAN -> WAN, REPLY dir) with skb->_nfct == NULL. */
	set_v4_ct(&ct, IPPROTO_TCP,
		  "10.99.230.112", 50000, "10.99.230.1", 5201,
		  "10.99.2.113", 5201, "10.99.230.112", 50000,
		  (1UL << IPS_SEEN_REPLY_BIT) | IPS_DST_NAT);
	memset(&post, 0, sizeof(post));
	post.family = AF_INET;
	post.proto = IPPROTO_TCP;
	post.saddr = exp_nat;
	post.daddr = exp_wan;
	post.sport = htons(5201);
	post.dport = htons(50000);
	assert(wgfp_ct_admitted(&net, &skb, &post, &orig, &nat));
	assert(orig.saddr == exp_lan && orig.daddr == exp_wan);
	assert(orig.sport == htons(5201) && orig.dport == htons(50000));
	assert(nat.flags == WGFP_NAT_SRC_IP && nat.saddr == exp_nat);
	assert(ct.use == 1);

	/* 4. SNAT66 with skb->_nfct == NULL. */
	set_v6_ct(&ct, IPPROTO_UDP,
		  "fd99:2::113", 41000, "fd99:230::112", 5201,
		  "fd99:230::112", 5201, "fd99:230::1", 41000,
		  (1UL << IPS_SEEN_REPLY_BIT) | IPS_SRC_NAT);
	memset(&post, 0, sizeof(post));
	post.family = AF_INET6;
	post.proto = IPPROTO_UDP;
	assert(inet_pton(AF_INET6, "fd99:230::1", &post.saddr6) == 1);
	assert(inet_pton(AF_INET6, "fd99:230::112", &post.daddr6) == 1);
	post.sport = htons(41000);
	post.dport = htons(5201);
	assert(wgfp_ct_admitted(&net, &skb, &post, &orig, &nat));
	struct in6_addr exp_lan6;
	assert(inet_pton(AF_INET6, "fd99:2::113", &exp_lan6) == 1);
	assert(!memcmp(&orig.saddr6, &exp_lan6, 16));
	assert(nat.flags == WGFP_NAT_SRC_IP && !memcmp(&nat.saddr6, &post.saddr6, 16));
	assert(ct.use == 1);

	/* 5. Non-NAT IPv6 (both ORIG and REPLY) with skb->_nfct == NULL. */
	set_v6_ct(&ct, IPPROTO_TCP,
		  "fd99:2::113", 41000, "fd99:230::112", 5201,
		  "fd99:230::112", 5201, "fd99:2::113", 41000,
		  (1UL << IPS_SEEN_REPLY_BIT));
	post.proto = IPPROTO_TCP;
	post.saddr6 = exp_lan6;
	assert(wgfp_ct_admitted(&net, &skb, &post, &orig, &nat));
	assert(nat.flags == 0 && !memcmp(&orig.saddr6, &exp_lan6, 16));
	assert(ct.use == 1);

	/* 6. Reject cases on skb->_nfct == NULL (and zero refcount leaks). */
	set_v4_ct(&ct, IPPROTO_TCP,
		  "10.99.2.113", 40923, "10.99.230.112", 5201,
		  "10.99.230.112", 5201, "10.99.230.1", 40923,
		  (1UL << IPS_SEEN_REPLY_BIT) | IPS_SRC_NAT);
	memset(&post, 0, sizeof(post));
	post.family = AF_INET;
	post.proto = IPPROTO_TCP;
	post.saddr = exp_nat;
	post.daddr = exp_wan;
	post.sport = htons(40923);
	post.dport = htons(5201);

	ct.dying = true;
	assert(!wgfp_ct_admitted(&net, &skb, &post, &orig, &nat) && ct.use == 1);
	ct.dying = false;

	ct.expired = true;
	assert(!wgfp_ct_admitted(&net, &skb, &post, &orig, &nat) && ct.use == 1);
	ct.expired = false;

	ct.status |= IPS_SEQ_ADJUST;
	assert(!wgfp_ct_admitted(&net, &skb, &post, &orig, &nat) && ct.use == 1);
	ct.status &= ~IPS_SEQ_ADJUST;

	ct.proto.tcp.state = TCP_CONNTRACK_FIN_WAIT;
	assert(!wgfp_ct_admitted(&net, &skb, &post, &orig, &nat) && ct.use == 1);
	ct.proto.tcp.state = TCP_CONNTRACK_ESTABLISHED;

	ct.status &= ~(1UL << IPS_SEEN_REPLY_BIT);
	assert(!wgfp_ct_admitted(&net, &skb, &post, &orig, &nat) && ct.use == 1);
	ct.status |= (1UL << IPS_SEEN_REPLY_BIT);

	ct.master = (void *)0x1;
	assert(!wgfp_ct_admitted(&net, &skb, &post, &orig, &nat) && ct.use == 1);
	ct.master = NULL;

	ct.help = (void *)0x1;
	assert(!wgfp_ct_admitted(&net, &skb, &post, &orig, &nat) && ct.use == 1);
	ct.help = NULL;

	ct.zone.id = 7;
	assert(!wgfp_ct_admitted(&net, &skb, &post, &orig, &nat) && ct.use == 1);
	ct.zone.id = 0;

	/* 7. Attached skb->_nfct path still works identically. */
	skb.ct = &ct;
	skb.ctinfo = IP_CT_ESTABLISHED;
	assert(wgfp_ct_admitted(&net, &skb, &post, &orig, &nat));
	assert(orig.saddr == exp_lan && nat.flags == WGFP_NAT_SRC_IP);
	assert(ct.use == 1);

	/* 8. FIN/RST wgfp_ct_orig_tuple recovers pre-NAT tuple even with skb->_nfct == NULL. */
	skb.ct = NULL;
	memset(&orig, 0, sizeof(orig));
	wgfp_ct_orig_tuple(&net, &skb, &post, &orig);
	assert(orig.saddr == exp_lan && orig.daddr == exp_wan);
	assert(orig.sport == htons(40923) && orig.dport == htons(5201));
	assert(ct.use == 1);

	printf("wgfp_ct_admit: all checks passed\n");
	return 0;
}
