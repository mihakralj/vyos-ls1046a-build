// SPDX-License-Identifier: GPL-2.0
/* Host fault tests for the extracted PPPoE snapshot and publication paths. */
#include <arpa/inet.h>
#include <errno.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

typedef uint8_t u8;
typedef uint16_t u16;
typedef uint32_t u32;
typedef uint64_t u64;

#define ETH_ALEN 6
#define ETH_P_IP 0x0800
#define ETH_P_IPV6 0x86dd
#define ETH_P_8021Q 0x8100
#define ETH_P_8021AD 0x88a8
#define ETH_P_PPP_SES 0x8864
#define VLAN_VID_MASK 0x0fff
#define NF_FLOW_TABLE_ENCAP_MAX 2
#define FLOW_OFFLOAD_DIR_MAX 2
#define FLOW_OFFLOAD_XMIT_DIRECT 1
#define ASK_PPPOE_DECAP 1
#define ASK_PPPOE_ENCAP 2
#define ASK_FE_KEY_SIZE_DUAL 50
#define ASK_FLOW_L3_IPV4 0
#define ASK_FLOW_L3_IPV6 1
#define ASK_FAM_V4 1
#define ASK_FAM_V6 2
#define ASK_VLANF_POP 1
#define ASK_VLANF_PUSH 2
#define ASK_VLANF_PPPOE_STRIP 4
#define ASK_VLANF_PPPOE_INSERT 8
#define ARPHRD_PPP 512
#define NETREG_REGISTERED 1
#define NETREG_UNREGISTERING 2
#define BIT(n) (1U << (n))
#define READ_ONCE(x) (x)
#define __aligned(n) __attribute__((aligned(n)))
#define container_of(ptr, type, member) \
	((type *)((char *)(ptr) - offsetof(type, member)))
#define virt_addr_valid(ptr) ((ptr) != NULL)
#define lockdep_assert_held(lock) ((void)(lock))
#define ask_pr_warn(...) ((void)0)
#define ask_pr_dbg(...) ((void)0)

struct net_device {
	int ifindex, type, reg_state;
	u8 port;
	bool running;
	u8 dev_addr[ETH_ALEN] __aligned(2);
};

enum net_device_path_type {
	DEV_PATH_ETHERNET, DEV_PATH_VLAN, DEV_PATH_BRIDGE, DEV_PATH_PPPOE,
	DEV_PATH_DSA,
};

struct net_device_path {
	enum net_device_path_type type;
	const struct net_device *dev;
	struct {
		u16 id, proto;
		u8 h_dest[ETH_ALEN] __aligned(2);
	} encap;
};

struct net_device_path_stack {
	int num_paths;
	struct net_device_path path[5];
};

#include "ask_pppoe_info.h"

struct ask_flow_key {
	u8 l3_proto, port_id, nat_flags, vlan_edit_flags;
	u8 next_hop_mac[ETH_ALEN], egress_mac[ETH_ALEN];
	u8 nat_src_ip[16], nat_dst_ip[16];
	u16 nat_sport, nat_dport, egress_mtu, vlan_ingress_vid;
	u16 vlan_push_tci, vlan_push_tpid, pppoe_push_sid;
	struct ask_pppoe_info pppoe_session;
	u64 pppoe_session_epoch;
} __attribute__((packed));

struct flow_offload_tuple {
	int dir, iifidx, xmit_type;
	u8 encap_num, in_vlan_ingress;
	struct { u16 proto, id; } encap[NF_FLOW_TABLE_ENCAP_MAX];
	struct {
		int ifidx, hw_ifidx;
		u8 h_dest[ETH_ALEN] __aligned(2);
		u8 h_source[ETH_ALEN] __aligned(2);
	} out;
};

struct flow_offload {
	struct { struct flow_offload_tuple tuple; } tuplehash[2];
};

struct fman {};
struct ask_hw_pcd {};
struct fman_pcd_fe_flow_action {
	u8 key[ASK_FE_KEY_SIZE_DUAL], key_size, table_idx;
	u8 next_hop_mac[ETH_ALEN], egress_mac[ETH_ALEN];
	u8 nat_flags, nat_sip[16], nat_dip[16], vlan_flags;
	u16 eth_type, egress_mtu, nat_sport, nat_dport;
	u16 vlan_ingress_vid, vlan_push_tci, vlan_push_tpid, pppoe_sid;
	u32 enq_off, tx_fqid;
	u64 owner_token;
};

enum ask_hw_dir { ASK_HW_DIR_FWD };
struct ask_flow_table {};
struct ask_flow { u64 fe_token; bool hw_backed; };

static const u8 source_mac[ETH_ALEN] __aligned(2) = { 2, 0, 0, 0, 0, 7 };
static const u8 peer_mac[ETH_ALEN] __aligned(2) = { 2, 0, 0, 0, 0, 9 };
static struct net_device session_dev = {
	.ifindex = 12, .type = ARPHRD_PPP, .reg_state = NETREG_REGISTERED,
	.running = true,
};
static struct net_device physical_dev = {
	.ifindex = 7, .port = 0x11, .reg_state = NETREG_REGISTERED,
	.running = true, .dev_addr = { 2, 0, 0, 0, 0, 7 },
};
static struct net_device vlan_dev = {
	.ifindex = 10, .reg_state = NETREG_REGISTERED, .running = true,
	.dev_addr = { 2, 0, 0, 0, 0, 7 },
};
static struct net_device other_dev = {
	.ifindex = 8, .reg_state = NETREG_REGISTERED, .running = true,
	.dev_addr = { 2, 0, 0, 0, 0, 7 },
};
static struct net_device_path_stack live_path;
static bool path_live = true, pppoe_enabled = true, sessions_usable = true;
static bool session_port_enabled = true, ask_ipv6_hw_frag = true;
static bool ask_vlan_push_only = true, sw_live, model_error;
static u8 session_family = ASK_FAM_V4;
static u64 session_epoch = 4;
static unsigned int publications, withdrawals, dev_refs, rcu_depth;
static int init_net, ask_flow_lifecycle_lock;
static struct fman fm;
static struct ask_hw_pcd hardware;
static struct ask_flow current_flow;
static struct fman_pcd_fe_flow_action last_action;

static void ether_addr_copy(u8 *dst, const u8 *src)
{
	memcpy(dst, src, ETH_ALEN);
}

static bool ether_addr_equal(const u8 *a, const u8 *b)
{
	return memcmp(a, b, ETH_ALEN) == 0;
}

static bool is_zero_ether_addr(const u8 *addr)
{
	const u8 zero[ETH_ALEN] = {};

	return ether_addr_equal(addr, zero);
}

static void rcu_read_lock(void) { rcu_depth++; }
static void rcu_read_unlock(void) { rcu_depth--; }
static bool netif_running(const struct net_device *dev) { return dev->running; }

static struct net_device *dev_get_by_index_rcu(void *net, int ifindex)
{
	if (!rcu_depth)
		model_error = true;
	return ifindex == session_dev.ifindex ? &session_dev : NULL;
}

static struct net_device *dev_get_by_index(void *net, int ifindex)
{
	if (ifindex != physical_dev.ifindex)
		return NULL;
	dev_refs++;
	return &physical_dev;
}

static void dev_put(struct net_device *dev) { dev_refs--; }

static int dev_fill_forward_path(const struct net_device *dev, const u8 *daddr,
				 struct net_device_path_stack *stack)
{
	if (!rcu_depth || dev != &session_dev || !is_zero_ether_addr(daddr))
		model_error = true;
	if (!path_live)
		return -ENODEV;
	*stack = live_path;
	return 0;
}

static bool ask_pppoe_session_is_current(u64 epoch)
{
	return sessions_usable && epoch == session_epoch;
}

static bool ask_pppoe_sessions_usable(void) { return sessions_usable; }
static bool ask_hw_pppoe_offload_armed(void) { return pppoe_enabled; }
static bool ask_hw_pppoe_offload_armed_port(u8 port)
{
	return pppoe_enabled && session_port_enabled && port == physical_dev.port;
}
static u8 ask_hw_port_family_mask(u8 port) { return session_family; }
static int ask_dpaa_get_fman_port_id(struct net_device *dev, u8 *port)
{
	*port = dev->port;
	return 0;
}
static bool ask_hw_vlan_offload_armed_port(u8 port) { return true; }
static bool ask_hw_nat44_offload_armed(void) { return true; }
static bool ask_hw_nat66_offload_armed(void) { return true; }
static int ask_hw_flow_family_ok(u8 port, const struct ask_flow_key *key) { return 0; }
static int ask_hw_flow_publish_begin(const struct ask_flow_key *key, unsigned long enq_off,
				     struct ask_hw_pcd **guard, struct fman **owner)
{
	*guard = &hardware;
	*owner = &fm;
	return 0;
}
static void ask_hw_flow_publish_end(struct ask_hw_pcd *guard) {}
static unsigned long ask_hw_get_enq_fe_off(void) { return 1; }
static int ask_hw_flow_get_sink_fqid(u32 hw_id, u32 *fqid)
{
	*fqid = 2;
	return 0;
}
static void ask_fe_build_key_dual(const struct ask_flow_key *key, u8 *buf)
{
	memset(buf, 0, ASK_FE_KEY_SIZE_DUAL);
}
static int ask_fe_flow_publish(struct fman *owner, u8 port, const u8 *key,
			       struct fman_pcd_fe_flow_action *action)
{
	if (rcu_depth)
		model_error = true;
	publications++;
	last_action = *action;
	return 0;
}

#include "ask_pppoe_port_body.h"
#include "ask_pppoe_cookie_body.h"
#include "ask_pppoe_path_body.h"
#include "ask_fe_insert_body.h"
#include "ask_flow_fe_install_body.h"

static int ask_flow_insert_owned_identified(struct ask_flow_table *table,
		u64 cookie, const struct ask_flow_key *key, u32 oif,
		u32 flags, enum ask_hw_dir dir, u64 generation, u32 *hw_id,
		u64 *fe_token)
{
	current_flow.fe_token = 23;
	current_flow.hw_backed = true;
	sw_live = true;
	*hw_id = 1;
	*fe_token = current_flow.fe_token;
	return 0;
}
static struct ask_flow *ask_flow_lookup(struct ask_flow_table *table, u64 cookie)
{
	return sw_live ? &current_flow : NULL;
}
static bool ask_flow_gen_is_current(struct ask_flow_table *table, u64 cookie,
				    u64 generation)
{
	return true;
}
static bool ask_flow_token_is_current(struct ask_flow_table *table, u64 cookie,
				      u64 token)
{
	return sw_live && current_flow.fe_token == token;
}
static int ask_flow_remove_owned_identified(struct ask_flow_table *table,
					    u64 cookie, u64 generation,
					    u64 token)
{
	sw_live = false;
	return 0;
}
static int ask_flow_withdraw_owned(struct ask_flow_table *table, u64 cookie,
		u64 generation, u64 token, const struct ask_flow_key *key,
		bool hw_backed, const char *what, bool *superseded)
{
	withdrawals++;
	sw_live = false;
	return 0;
}

#include "ask_neigh_republish_body.h"

#define CHECK(condition) do { \
	if (!(condition)) { \
		fprintf(stderr, "%s:%d: failed: %s\n", __func__, __LINE__, #condition); \
		return 1; \
	} \
} while (0)

static void make_path(bool tagged)
{
	struct net_device_path *p;

	memset(&live_path, 0, sizeof(live_path));
	p = &live_path.path[live_path.num_paths++];
	p->type = DEV_PATH_PPPOE;
	p->dev = &session_dev;
	p->encap.proto = htons(ETH_P_PPP_SES);
	p->encap.id = 42;
	ether_addr_copy(p->encap.h_dest, peer_mac);
	if (tagged) {
		p = &live_path.path[live_path.num_paths++];
		p->type = DEV_PATH_VLAN;
		p->dev = &vlan_dev;
		p->encap.proto = htons(ETH_P_8021Q);
		p->encap.id = 100;
	}
	p = &live_path.path[live_path.num_paths++];
	p->type = DEV_PATH_ETHERNET;
	p->dev = &physical_dev;
}

static void make_flow(struct flow_offload *flow, bool tagged)
{
	struct flow_offload_tuple *out, *in;

	memset(flow, 0, sizeof(*flow));
	out = &flow->tuplehash[0].tuple;
	in = &flow->tuplehash[1].tuple;
	out->dir = 0;
	in->dir = 1;
	out->xmit_type = FLOW_OFFLOAD_XMIT_DIRECT;
	out->out.ifidx = session_dev.ifindex;
	out->out.hw_ifidx = physical_dev.ifindex;
	ether_addr_copy(out->out.h_dest, peer_mac);
	ether_addr_copy(out->out.h_source, source_mac);
	in->iifidx = physical_dev.ifindex;
	in->encap_num = tagged ? 2 : 1;
	in->encap[0].proto = htons(ETH_P_PPP_SES);
	in->encap[0].id = 42;
	if (tagged) {
		in->encap[1].proto = htons(ETH_P_8021Q);
		in->encap[1].id = 100;
	}
}

static int test_cookie_and_snapshot(void)
{
	struct flow_offload flow;
	struct ask_pppoe_info encap, decap;
	struct ask_flow_key key = { .l3_proto = ASK_FLOW_L3_IPV4 };

	make_flow(&flow, false);
	make_path(false);
	CHECK(ask_flow_cookie_pppoe((unsigned long)&flow.tuplehash[0].tuple,
				    &encap) == ASK_PPPOE_ENCAP);
	CHECK(ask_flow_cookie_pppoe((unsigned long)&flow.tuplehash[1].tuple,
				    &decap) == ASK_PPPOE_DECAP);
	CHECK(memcmp(&encap, &decap, sizeof(encap)) == 0);
	CHECK(encap.session_ifindex == session_dev.ifindex);
	CHECK(encap.ifindex == physical_dev.ifindex);
	key.pppoe_session = decap;
	key.pppoe_session_epoch = session_epoch;
	CHECK(ask_pppoe_path_current(&key));

	flow.tuplehash[0].tuple.xmit_type = 0;
	CHECK(ask_flow_cookie_pppoe((unsigned long)&flow.tuplehash[1].tuple,
				    &decap) == ASK_PPPOE_DECAP);
	key.pppoe_session = decap;
	CHECK(!ask_pppoe_path_current(&key));
	flow.tuplehash[0].tuple.xmit_type = FLOW_OFFLOAD_XMIT_DIRECT;
	flow.tuplehash[0].tuple.out.hw_ifidx = other_dev.ifindex;
	CHECK(ask_flow_cookie_pppoe((unsigned long)&flow.tuplehash[1].tuple,
				    &decap) == ASK_PPPOE_DECAP);
	key.pppoe_session = decap;
	CHECK(!ask_pppoe_path_current(&key));

	make_flow(&flow, true);
	make_path(true);
	CHECK(ask_flow_cookie_pppoe((unsigned long)&flow.tuplehash[0].tuple,
				    &encap) == ASK_PPPOE_ENCAP);
	CHECK(ask_flow_cookie_pppoe((unsigned long)&flow.tuplehash[1].tuple,
				    &decap) == ASK_PPPOE_DECAP);
	CHECK(memcmp(&encap, &decap, sizeof(encap)) == 0);
	CHECK(encap.vid == 100);
	key.pppoe_session = decap;
	CHECK(ask_pppoe_path_current(&key));
	return 0;
}

static int test_live_path_validation(void)
{
	struct flow_offload flow;
	struct ask_pppoe_info info;
	struct ask_flow_key key = { .l3_proto = ASK_FLOW_L3_IPV4 };

	make_flow(&flow, true);
	make_path(true);
	CHECK(ask_flow_cookie_pppoe((unsigned long)&flow.tuplehash[0].tuple,
				    &info) == ASK_PPPOE_ENCAP);
	key.pppoe_session = info;
	key.pppoe_session_epoch = session_epoch;
	CHECK(ask_pppoe_path_current(&key));

	path_live = false;
	CHECK(!ask_pppoe_path_current(&key));
	path_live = true;
	live_path.path[0].encap.id++;
	CHECK(!ask_pppoe_path_current(&key));
	live_path.path[0].encap.id--;
	live_path.path[0].encap.h_dest[5]++;
	CHECK(!ask_pppoe_path_current(&key));
	live_path.path[0].encap.h_dest[5]--;
	live_path.path[1].encap.id++;
	CHECK(!ask_pppoe_path_current(&key));
	live_path.path[1].encap.id--;
	live_path.path[1].encap.proto = htons(ETH_P_8021AD);
	CHECK(!ask_pppoe_path_current(&key));
	live_path.path[1].encap.proto = htons(ETH_P_8021Q);
	vlan_dev.dev_addr[5]++;
	CHECK(!ask_pppoe_path_current(&key));
	vlan_dev.dev_addr[5]--;
	live_path.path[2].dev = &other_dev;
	CHECK(!ask_pppoe_path_current(&key));
	live_path.path[2].dev = &physical_dev;
	live_path.path[1].type = DEV_PATH_BRIDGE;
	CHECK(!ask_pppoe_path_current(&key));
	live_path.path[1].type = DEV_PATH_VLAN;
	live_path.path[1].dev = NULL;
	CHECK(!ask_pppoe_path_current(&key));
	live_path.path[1].dev = &vlan_dev;

	session_dev.running = false;
	CHECK(!ask_pppoe_path_current(&key));
	session_dev.running = true;
	session_dev.reg_state = NETREG_UNREGISTERING;
	CHECK(!ask_pppoe_path_current(&key));
	session_dev.reg_state = NETREG_REGISTERED;
	session_dev.type = 1;
	CHECK(!ask_pppoe_path_current(&key));
	session_dev.type = ARPHRD_PPP;
	key.pppoe_session.session_ifindex++;
	CHECK(!ask_pppoe_path_current(&key));
	key.pppoe_session.session_ifindex--;
	session_epoch++;
	CHECK(!ask_pppoe_path_current(&key));
	session_epoch--;
	sessions_usable = false;
	CHECK(!ask_pppoe_path_current(&key));
	sessions_usable = true;
	session_family = ASK_FAM_V6;
	CHECK(!ask_pppoe_path_current(&key));
	key.l3_proto = ASK_FLOW_L3_IPV6;
	CHECK(ask_pppoe_path_current(&key));
	key.l3_proto = ASK_FLOW_L3_IPV4;
	session_family = ASK_FAM_V4;
	session_port_enabled = false;
	CHECK(!ask_pppoe_path_current(&key));
	session_port_enabled = true;

	make_path(false);
	CHECK(!ask_pppoe_path_current(&key));
	key.pppoe_session.vid = 0;
	CHECK(ask_pppoe_path_current(&key));
	key.pppoe_session.sid = 0;
	CHECK(!ask_pppoe_path_current(&key));
	CHECK(!rcu_depth && !dev_refs && !model_error);
	return 0;
}

static int test_direct_and_replay_publication(void)
{
	struct ask_flow_table table;
	struct flow_offload flow;
	struct ask_pppoe_info info;
	struct ask_flow_key key = {
		.l3_proto = ASK_FLOW_L3_IPV4, .port_id = 0x10,
		.pppoe_push_sid = 42,
	};
	unsigned int before, previous_withdrawals;
	const u8 flags[] = { ASK_VLANF_PPPOE_STRIP, ASK_VLANF_PPPOE_INSERT };

	make_flow(&flow, false);
	make_path(false);
	CHECK(ask_flow_cookie_pppoe((unsigned long)&flow.tuplehash[0].tuple,
				    &info) == ASK_PPPOE_ENCAP);
	key.pppoe_session = info;
	key.pppoe_session_epoch = session_epoch;
	for (unsigned int i = 0; i < sizeof(flags); i++) {
		struct ask_flow_key replay = key;

		key.vlan_edit_flags = replay.vlan_edit_flags = flags[i];
		CHECK(ask_flow_fe_install(&key, 1, NULL, 11, 12) == 0);
		CHECK(last_action.vlan_flags == flags[i]);
		CHECK(ask_neigh_republish(&table, 11, &replay, 7, 0,
					  ASK_HW_DIR_FWD, 1) == 0);
		before = publications;
		previous_withdrawals = withdrawals;
		path_live = false;
		CHECK(ask_flow_fe_install(&key, 1, NULL, 11, 12) == -EOPNOTSUPP);
		CHECK(ask_neigh_republish(&table, 11, &replay, 7, 0,
					  ASK_HW_DIR_FWD, 1) == -EOPNOTSUPP);
		CHECK(publications == before && withdrawals == previous_withdrawals + 1);
		CHECK(!sw_live);
		path_live = true;
	}
	path_live = false;
	key.vlan_edit_flags = 0;
	CHECK(ask_flow_fe_install(&key, 1, NULL, 11, 12) == 0);
	key.vlan_edit_flags = ASK_VLANF_POP;
	CHECK(ask_flow_fe_install(&key, 1, NULL, 11, 12) == 0);
	CHECK(!rcu_depth && !dev_refs && !model_error);
	return 0;
}

int main(void)
{
	if (test_cookie_and_snapshot() || test_live_path_validation() ||
	    test_direct_and_replay_publication())
		return 1;
	puts("PPPoE live-path snapshot and direct/replay admission: passed");
	return 0;
}
