// SPDX-License-Identifier: GPL-2.0
/* Host regression tests for S5(1) same-MAC neighbour-event cache (ask_neigh.c). */
#include <assert.h>
#include <errno.h>
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
typedef uint32_t __be32;

#define ETH_ALEN 6
#define GFP_KERNEL 0
#define GFP_ATOMIC 1
#define ASK_FLOW_L3_IPV4 1
#define ASK_FLOW_L3_IPV6 2
#define NETEVENT_NEIGH_UPDATE 1
#define NETDEV_DOWN 2
#define NETDEV_UNREGISTER 6
#define NOTIFY_DONE 0
#define NUD_VALID 0x02
#define READ_ONCE(x) (x)
#define WRITE_ONCE(x, v) ((x) = (v))
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
#define container_of(ptr, type, member) \
	((type *)((char *)(ptr) - offsetof(type, member)))
#define ask_pr_warn(...) ((void)0)
#define net_ratelimit() false

/* Minimal kernel list / hlist / lock / atomic / netns shims. */
struct list_head { struct list_head *next, *prev; };
#define LIST_HEAD_INIT(name) { &(name), &(name) }
#define LIST_HEAD(name) struct list_head name = LIST_HEAD_INIT(name)
static inline void INIT_LIST_HEAD(struct list_head *l) { l->next = l; l->prev = l; }
static inline bool list_empty(const struct list_head *h) { return h->next == h; }
static inline void __list_add(struct list_head *n, struct list_head *p, struct list_head *nx)
{
	nx->prev = n; n->next = nx; n->prev = p; p->next = n;
}
static inline void list_add(struct list_head *n, struct list_head *h) { __list_add(n, h, h->next); }
static inline void list_add_tail(struct list_head *n, struct list_head *h) { __list_add(n, h->prev, h); }
static inline void list_del(struct list_head *e)
{
	e->next->prev = e->prev;
	e->prev->next = e->next;
	e->next = e->prev = NULL;
}
static inline void list_move(struct list_head *e, struct list_head *h)
{
	list_del(e);
	list_add(e, h);
}
#define list_entry(ptr, type, member) container_of(ptr, type, member)
#define list_first_entry_or_null(ptr, type, member) \
	(!list_empty(ptr) ? list_entry((ptr)->next, type, member) : NULL)
#define list_last_entry(ptr, type, member) list_entry((ptr)->prev, type, member)
#define list_for_each_entry(pos, head, member) \
	for (pos = list_entry((head)->next, typeof(*pos), member); \
	     &pos->member != (head); \
	     pos = list_entry(pos->member.next, typeof(*pos), member))
#define list_for_each_entry_safe(pos, n, head, member) \
	for (pos = list_entry((head)->next, typeof(*pos), member), \
	     n = list_entry(pos->member.next, typeof(*pos), member); \
	     &pos->member != (head); \
	     pos = n, n = list_entry(n->member.next, typeof(*n), member))

struct hlist_node { struct hlist_node *next, **pprev; };
struct hlist_head { struct hlist_node *first; };
#define DECLARE_HASHTABLE(name, bits) struct hlist_head name[1 << (bits)]
#define HASH_SIZE(name) ARRAY_SIZE(name)
static inline void hash_init(struct hlist_head *ht, unsigned int sz)
{
	memset(ht, 0, sz * sizeof(*ht));
}
static inline void hlist_add_head(struct hlist_node *n, struct hlist_head *h)
{
	struct hlist_node *first = h->first;
	n->next = first;
	if (first)
		first->pprev = &n->next;
	h->first = n;
	n->pprev = &h->first;
}
static inline void hlist_del(struct hlist_node *n)
{
	struct hlist_node *next = n->next;
	struct hlist_node **pprev = n->pprev;
	*pprev = next;
	if (next)
		next->pprev = pprev;
	n->next = NULL;
	n->pprev = NULL;
}
#define hlist_entry(ptr, type, member) container_of(ptr, type, member)
#define hlist_entry_safe(ptr, type, member) \
	({ typeof(ptr) ____ptr = (ptr); ____ptr ? hlist_entry(____ptr, type, member) : NULL; })
#define hlist_for_each_entry(pos, head, member) \
	for (pos = hlist_entry_safe((head)->first, typeof(*(pos)), member); \
	     pos; \
	     pos = hlist_entry_safe((pos)->member.next, typeof(*(pos)), member))

typedef int spinlock_t;
typedef int rwlock_t;
#define DEFINE_SPINLOCK(x) spinlock_t x = 0
static inline void spin_lock_bh(spinlock_t *l) { assert(*l == 0); *l = 1; }
static inline void spin_unlock_bh(spinlock_t *l) { assert(*l == 1); *l = 0; }
static inline void read_lock_bh(rwlock_t *l) { (void)l; }
static inline void read_unlock_bh(rwlock_t *l) { (void)l; }

typedef struct { int v; } atomic_t;
#define ATOMIC_INIT(x) { (x) }
static inline void atomic_inc(atomic_t *a) { a->v++; }
static inline int atomic_inc_return(atomic_t *a) { return ++a->v; }
static inline int atomic_read(const atomic_t *a) { return a->v; }

struct net { int id; };
static struct net init_net = { .id = 0 };
static struct net other_net = { .id = 1 };
static inline bool net_eq(const struct net *a, const struct net *b) { return a == b; }

struct work_struct { int dummy; };
static int work_scheduled;
static inline bool schedule_work(struct work_struct *w) { (void)w; work_scheduled++; return true; }

struct net_device { int ifindex; int refs; struct net *net; };
static inline struct net *dev_net(const struct net_device *d) { return d->net; }
static inline void dev_hold(struct net_device *d) { d->refs++; }
static inline void dev_put(struct net_device *d) { assert(d->refs > 0); d->refs--; }

struct notifier_block {
	int (*notifier_call)(struct notifier_block *, unsigned long, void *);
};
static inline struct net_device *netdev_notifier_info_to_dev(void *ptr)
{
	return (struct net_device *)ptr;
}

struct neigh_table { unsigned int key_len; };
static struct neigh_table arp_tbl = { .key_len = 4 };
static struct neigh_table nd_tbl = { .key_len = 16 };
struct neighbour {
	struct neigh_table *tbl;
	struct net_device *dev;
	rwlock_t lock;
	u8 nud_state;
	u8 ha[ETH_ALEN];
	u8 primary_key[16];
};

static inline unsigned int ask_flow_l3_addr_len(u8 proto)
{
	return proto == ASK_FLOW_L3_IPV6 ? 16 : 4;
}
static inline bool ether_addr_equal(const u8 *a, const u8 *b)
{
	return memcmp(a, b, ETH_ALEN) == 0;
}
static inline void ether_addr_copy(u8 *dst, const u8 *src)
{
	memcpy(dst, src, ETH_ALEN);
}

static u32 jhash(const void *key, u32 length, u32 initval)
{
	const u8 *p = key;
	u32 h = initval ^ length;
	for (u32 i = 0; i < length; i++)
		h = h * 33u ^ p[i];
	return h;
}

static bool fail_alloc;
static int live_allocs;
static void *kzalloc(size_t sz, int gfp)
{
	(void)gfp;
	if (fail_alloc)
		return NULL;
	void *p = calloc(1, sz);
	assert(p);
	live_allocs++;
	return p;
}
static void kfree(void *p)
{
	if (!p)
		return;
	assert(live_allocs > 0);
	live_allocs--;
	free(p);
}

static int resolved_calls;
static int mac_changed_calls;
static bool mac_changed_ok = true;
static u8 last_mac[ETH_ALEN];
static u8 last_proto;
static int last_ifindex;

static void ask_flow_neigh_resolved(struct net_device *dev, __be32 dst_ip)
{
	(void)dev;
	(void)dst_ip;
	resolved_calls++;
}

static bool ask_flow_neigh_mac_changed(struct net_device *dev, const u8 *dst_ip,
				       u8 l3_proto, const u8 *new_mac)
{
	(void)dst_ip;
	mac_changed_calls++;
	last_ifindex = dev->ifindex;
	last_proto = l3_proto;
	ether_addr_copy(last_mac, new_mac);
	return mac_changed_ok;
}

#include "neigh_cache_body.h"

static void fire_neigh(struct net_device *dev, u8 proto, const u8 *ip, const u8 *mac)
{
	struct neighbour n = {
		.tbl = (proto == ASK_FLOW_L3_IPV6) ? &nd_tbl : &arp_tbl,
		.dev = dev,
		.nud_state = NUD_VALID,
	};
	memcpy(n.primary_key, ip, ask_flow_l3_addr_len(proto));
	ether_addr_copy(n.ha, mac);
	ask_neigh_netevent(&ask_neigh_nb, NETEVENT_NEIGH_UPDATE, &n);
}

static void fire_and_drain(struct net_device *dev, u8 proto, const u8 *ip, const u8 *mac)
{
	fire_neigh(dev, proto, ip, mac);
	ask_neigh_work_fn(&ask_neigh_work);
}

int main(void)
{
	struct net_device d1 = { .ifindex = 5, .refs = 1, .net = &init_net };
	struct net_device d2 = { .ifindex = 6, .refs = 1, .net = &init_net };
	struct net_device d1_other = { .ifindex = 5, .refs = 1, .net = &other_net };
	u8 ip4[16] = { 10, 99, 1, 112 };
	u8 ip6[16] = { 0xfd, 0x99, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0x12 };
	u8 mac_a[ETH_ALEN] = { 0x02, 0x00, 0x00, 0x00, 0x00, 0x0a };
	u8 mac_b[ETH_ALEN] = { 0x02, 0x00, 0x00, 0x00, 0x00, 0x0b };
	u8 mac_c[ETH_ALEN] = { 0x02, 0x00, 0x00, 0x00, 0x00, 0x0c };

	/* 1. First IPv4 event: miss -> runs both resolved + mac_changed. */
	fire_and_drain(&d1, ASK_FLOW_L3_IPV4, ip4, mac_a);
	assert(resolved_calls == 1 && mac_changed_calls == 1);
	assert(ether_addr_equal(last_mac, mac_a) && last_ifindex == 5);

	/* 2. Repeat IPv4 same MAC: runs resolved (pending drain), skips mac_changed. */
	fire_and_drain(&d1, ASK_FLOW_L3_IPV4, ip4, mac_a);
	assert(resolved_calls == 2 && mac_changed_calls == 1);
	assert(atomic_read(&ask_neigh_ev_same_mac) == 1);

	/* 3. Cross-netns event with colliding ifindex is ignored (Finding #4). */
	fire_and_drain(&d1_other, ASK_FLOW_L3_IPV4, ip4, mac_c);
	assert(resolved_calls == 2 && mac_changed_calls == 1);
	ask_neigh_netdev_event(NULL, NETDEV_DOWN, &d1_other);
	fire_and_drain(&d1, ASK_FLOW_L3_IPV4, ip4, mac_a);
	assert(resolved_calls == 3 && mac_changed_calls == 1);

	/* 4. A -> B -> A coalesced flap in queue MUST run mac_changed (Finding #1). */
	fire_neigh(&d1, ASK_FLOW_L3_IPV4, ip4, mac_b);
	fire_neigh(&d1, ASK_FLOW_L3_IPV4, ip4, mac_a);
	assert(atomic_read(&ask_neigh_ev_coalesced) == 1);
	ask_neigh_work_fn(&ask_neigh_work);
	assert(mac_changed_calls == 2 && ether_addr_equal(last_mac, mac_a));

	/* 5. A -> B(dropped via OOM) -> A invalidates cache and runs mac_changed (Finding #1). */
	fail_alloc = true;
	fire_neigh(&d1, ASK_FLOW_L3_IPV4, ip4, mac_b);
	fail_alloc = false;
	fire_and_drain(&d1, ASK_FLOW_L3_IPV4, ip4, mac_a);
	assert(mac_changed_calls == 3 && ether_addr_equal(last_mac, mac_a));

	/* 6. A -> B(dropped via queue full) -> A invalidates cache and runs mac_changed (Finding #1). */
	ask_neigh_ev_count = ASK_NEIGH_EV_MAX;
	fire_neigh(&d1, ASK_FLOW_L3_IPV4, ip4, mac_b);
	ask_neigh_ev_count = 0;
	fire_and_drain(&d1, ASK_FLOW_L3_IPV4, ip4, mac_a);
	assert(mac_changed_calls == 4 && ether_addr_equal(last_mac, mac_a));

	/* 7. Pre-DOWN queued event MUST NOT repopulate cache after NETDEV_DOWN (Finding #1). */
	fire_neigh(&d1, ASK_FLOW_L3_IPV4, ip4, mac_a);
	ask_neigh_netdev_event(NULL, NETDEV_DOWN, &d1);
	ask_neigh_work_fn(&ask_neigh_work);
	assert(mac_changed_calls == 5);
	/* Next post-DOWN event for mac_a must still miss (cache was not repopulated). */
	fire_and_drain(&d1, ASK_FLOW_L3_IPV4, ip4, mac_a);
	assert(mac_changed_calls == 6);
	/* Repeat after post-DOWN commit now hits cleanly. */
	fire_and_drain(&d1, ASK_FLOW_L3_IPV4, ip4, mac_a);
	assert(mac_changed_calls == 6);

	/* 8. Incomplete ask_flow_neigh_mac_changed() MUST NOT commit new MAC (Finding #3). */
	mac_changed_ok = false;
	fire_and_drain(&d1, ASK_FLOW_L3_IPV4, ip4, mac_b);
	assert(mac_changed_calls == 7);
	mac_changed_ok = true;
	/* Next refresh for mac_b retries and self-heals instead of hitting cache. */
	fire_and_drain(&d1, ASK_FLOW_L3_IPV4, ip4, mac_b);
	assert(mac_changed_calls == 8 && ether_addr_equal(last_mac, mac_b));
	/* Repeat mac_b now hits cleanly. */
	fire_and_drain(&d1, ASK_FLOW_L3_IPV4, ip4, mac_b);
	assert(mac_changed_calls == 8);

	/* 9. Same IP/MAC on different device is distinct; IPv6 works too. */
	fire_and_drain(&d2, ASK_FLOW_L3_IPV4, ip4, mac_b);
	assert(mac_changed_calls == 9 && last_ifindex == 6);
	fire_and_drain(&d2, ASK_FLOW_L3_IPV4, ip4, mac_b);
	assert(mac_changed_calls == 9);
	fire_and_drain(&d1, ASK_FLOW_L3_IPV6, ip6, mac_a);
	assert(mac_changed_calls == 10 && last_proto == ASK_FLOW_L3_IPV6);
	fire_and_drain(&d1, ASK_FLOW_L3_IPV6, ip6, mac_a);
	assert(mac_changed_calls == 10);

	/* 10. LRU cap: filling beyond ASK_NEIGH_CACHE_MAX bounds count and evicts oldest. */
	ask_neigh_cache_clear();
	assert(live_allocs == 0 && ask_neigh_cache_count == 0);
	u64 gen = READ_ONCE(ask_neigh_cache_gen);
	for (u32 i = 0; i < ASK_NEIGH_CACHE_MAX + 5; i++) {
		u8 ip[16] = { 10, (u8)(i >> 16), (u8)(i >> 8), (u8)i };
		ask_neigh_cache_commit(d1.ifindex, ASK_FLOW_L3_IPV4, ip, mac_a, gen);
	}
	assert(ask_neigh_cache_count == ASK_NEIGH_CACHE_MAX);
	assert(live_allocs == ASK_NEIGH_CACHE_MAX);
	u8 ip_first[16] = { 10, 0, 0, 0 };
	u8 ip_last[16] = { 10, 0, (u8)((ASK_NEIGH_CACHE_MAX + 4) >> 8),
			   (u8)(ASK_NEIGH_CACHE_MAX + 4) };
	assert(ask_neigh_cache_hit(d1.ifindex, ASK_FLOW_L3_IPV4, ip_last, mac_a, gen));
	assert(!ask_neigh_cache_hit(d1.ifindex, ASK_FLOW_L3_IPV4, ip_first, mac_a, gen));

	ask_neigh_cache_clear();
	assert(live_allocs == 0 && ask_neigh_cache_count == 0);
	assert(d1.refs == 1 && d2.refs == 1 && d1_other.refs == 1);
	puts("ask_neigh_cache: OK");
	return 0;
}
