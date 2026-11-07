// SPDX-License-Identifier: GPL-2.0
/* Host fault test for the extracted FE publication path. */
#include <arpa/inet.h>
#include <assert.h>
#include <errno.h>
#include <pthread.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdarg.h>
#include <stdatomic.h>
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
#define ASK_FE_KEY_SIZE_DUAL 50
#include "ask_flow_protocols.h"
#define U64_MAX UINT64_MAX
#define ASK_FAM_V4 1
#define ASK_FAM_V6 2
#define ASK_VLANF_POP 1
#define ASK_VLANF_PUSH 2
#define ASK_VLANF_PPPOE_STRIP 4
#define ASK_VLANF_PPPOE_INSERT 8
#define ASK_HW_MAX_PORTS 2
#define FMAN_PCD_FE_PROFILE_ROUTED 0
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
#define READ_ONCE(x) (x)
#define WRITE_ONCE(x, v) ((x) = (v))
#define ask_pr_warn(...) ((void)0)
#define ask_pr_err(...) ((void)0)
static void check_debug(const char *fmt, ...);
#define ask_pr_dbg(...) check_debug(__VA_ARGS__)
#define THIS_MODULE NULL

struct net_device { int ifindex; u8 port; };
struct fman {};
struct mutex { pthread_mutex_t native; };
struct ask_hw_port {
	bool in_use, offload_engaged, module_pinned, fe_retained;
	bool teardown_in_progress;
	u8 port_id, engaged_profile;
};
struct ask_hw_pcd {
	struct mutex lock;
	struct fman *fman;
	struct ask_hw_port port[ASK_HW_MAX_PORTS];
};
struct ask_flow_key {
	u8 l3_proto, l4_proto, port_id, nat_flags, vlan_edit_flags;
	u8 next_hop_mac[ETH_ALEN], egress_mac[ETH_ALEN];
	u8 nat_src_ip[16], nat_dst_ip[16];
	u16 nat_sport, nat_dport, egress_mtu, vlan_ingress_vid;
	u16 vlan_push_tci, vlan_push_tpid, pppoe_push_sid;
	u64 family_policy_seq;
};
struct fman_pcd_fe_flow_action {
	u8 key[ASK_FE_KEY_SIZE_DUAL], key_size, table_idx;
	u8 next_hop_mac[ETH_ALEN], egress_mac[ETH_ALEN];
	u8 nat_flags, nat_sip[16], nat_dip[16], vlan_flags;
	u16 eth_type, egress_mtu, nat_sport, nat_dport;
	u16 vlan_ingress_vid, vlan_push_tci, vlan_push_tpid, pppoe_sid;
	u32 enq_off, tx_fqid;
	u64 owner_token;
};

static bool pppoe_enabled, ask_ipv6_hw_frag = true, ask_vlan_push_only = true;
static bool sessions_usable = true, session_port_enabled = true;
static bool session_path_live = true;
static bool ingress_is_l2;
static bool clear_family_during_key_build;
static bool cycle_family_during_key_build;
static u8 session_family = ASK_FAM_V4;
static u8 ask_hw_port_family[64] = { [0x10] = ASK_FAM_V4 | ASK_FAM_V6 };
static u64 ask_hw_family_seq = 1;
static u64 ask_hw_family_changed[64][2];
static bool ask_hw_family_seq_exhausted;
#define ingress_family (ask_hw_port_family[0x10])
static unsigned long ask_hw_port_l2[4];
static unsigned int publications;
static unsigned int dev_refs;
static struct fman fm;
static struct ask_hw_pcd hardware = {
	.lock = { .native = PTHREAD_MUTEX_INITIALIZER },
	.fman = &fm,
	.port = { { .in_use = true, .offload_engaged = true, .module_pinned = true,
		    .port_id = 0x10, .engaged_profile = FMAN_PCD_FE_PROFILE_ROUTED } },
};
static struct ask_hw_pcd *ask_hw_pcd_inst = &hardware;
static bool ask_hw_ready = true, module_get_allowed = true;
static atomic_int module_refs;
static atomic_uint debug_before_guard, debug_bad_owner, debug_publications;
static unsigned long current_enq_off = 1;
static struct fman_pcd_fe_flow_action last_action;
static pthread_mutex_t stage_lock = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t stage_changed = PTHREAD_COND_INITIALIZER;
static bool block_publish, publisher_inside, release_publisher;
static bool family_waiting, family_done;
static _Thread_local bool family_changer;
static struct net_device pppoe_dev = { .ifindex = 7, .port = 0x11 };
static int init_net;

static void check_debug(const char *fmt, ...)
{
	struct fman *owner;
	va_list ap;
	int rc;

	if (strncmp(fmt, "F-195 flow-add call", 19))
		return;
	atomic_fetch_add(&debug_publications, 1);
	rc = pthread_mutex_trylock(&hardware.lock.native);
	if (!rc) {
		assert(!pthread_mutex_unlock(&hardware.lock.native));
		atomic_fetch_add(&debug_before_guard, 1);
		return;
	}
	assert(rc == EBUSY);
	va_start(ap, fmt);
	owner = va_arg(ap, struct fman *);
	va_end(ap);
	if (owner != &fm)
		atomic_fetch_add(&debug_bad_owner, 1);
}

static bool try_module_get(void *module)
{
	if (!module_get_allowed)
		return false;
	atomic_fetch_add(&module_refs, 1);
	return true;
}
static void module_put(void *module)
{
	assert(atomic_fetch_sub(&module_refs, 1) > 0);
}
static void mutex_lock(struct mutex *lock)
{
	int rc = family_changer ? pthread_mutex_trylock(&lock->native) :
				  pthread_mutex_lock(&lock->native);

	if (family_changer && rc == EBUSY) {
		assert(!pthread_mutex_lock(&stage_lock));
		family_waiting = true;
		assert(!pthread_cond_broadcast(&stage_changed));
		assert(!pthread_mutex_unlock(&stage_lock));
		rc = pthread_mutex_lock(&lock->native);
	}
	assert(!rc);
}
static void mutex_unlock(struct mutex *lock) { assert(!pthread_mutex_unlock(&lock->native)); }
static unsigned long ask_hw_get_enq_fe_off(void) { return current_enq_off; }
static void ask_flow_pppoe_flush(void) {}
void ask_hw_offload_set_family(u8 port, u8 family);
static bool test_bit(u8 port, const unsigned long *map) { return ingress_is_l2; }
static bool ask_hw_pppoe_offload_armed(void) { return pppoe_enabled; }
static bool ask_pppoe_sessions_usable(void) { return sessions_usable; }
static bool ask_pppoe_path_current(const struct ask_flow_key *key)
{
	return session_path_live;
}
static bool ask_hw_pppoe_offload_armed_port(u8 port)
{
	return pppoe_enabled && session_port_enabled && port == pppoe_dev.port;
}
static u8 ask_hw_port_family_mask(u8 port) { return session_family; }
static struct net_device *dev_get_by_index(void *net, int ifindex)
{
	if (ifindex != pppoe_dev.ifindex)
		return NULL;
	dev_refs++;
	return &pppoe_dev;
}
static void dev_put(struct net_device *dev) { dev_refs--; }
static int ask_dpaa_get_fman_port_id(struct net_device *dev, u8 *port)
{
	*port = dev->port;
	return 0;
}
static bool ask_hw_vlan_offload_armed_port(u8 port) { return true; }
static bool ask_hw_nat44_offload_armed(void) { return true; }
static bool ask_hw_nat66_offload_armed(void) { return true; }
static void ether_addr_copy(u8 *dst, const u8 *src) { memcpy(dst, src, ETH_ALEN); }
static void ask_fe_build_key_dual(const struct ask_flow_key *key, u8 *buf)
{
	memset(buf, 0, ASK_FE_KEY_SIZE_DUAL);
	if (clear_family_during_key_build)
		ingress_family = 0;
	if (cycle_family_during_key_build) {
		ask_hw_offload_set_family(0x10, ASK_FAM_V6);
		ask_hw_offload_set_family(0x10, ASK_FAM_V4 | ASK_FAM_V6);
	}
}
static int ask_fe_flow_publish(struct fman *owner, u8 port, const u8 *key,
			       struct fman_pcd_fe_flow_action *action)
{
	if (block_publish) {
		assert(!pthread_mutex_lock(&stage_lock));
		publisher_inside = true;
		assert(!pthread_cond_broadcast(&stage_changed));
		while (!release_publisher)
			assert(!pthread_cond_wait(&stage_changed, &stage_lock));
		assert(!pthread_mutex_unlock(&stage_lock));
	}
	publications++;
	last_action = *action;
	return 0;
}

#include "ask_hw_flow_family_body.h"
#include "ask_hw_publish_body.h"
#include "ask_fe_insert_body.h"
#include "ask_pppoe_port_body.h"

struct publication_race {
	struct ask_flow_key key;
	int rc;
};

static void *publish_in_flight(void *arg)
{
	struct publication_race *race = arg;

	race->rc = ask_fe_flow_insert(&race->key, 1, 2, NULL, 3);
	return NULL;
}

static void *change_family_in_flight(void *arg)
{
	family_changer = true;
	ask_hw_offload_set_family(0x10, 0);
	assert(!pthread_mutex_lock(&stage_lock));
	family_done = true;
	assert(!pthread_cond_broadcast(&stage_changed));
	assert(!pthread_mutex_unlock(&stage_lock));
	return NULL;
}

#define CHECK(condition) do { \
	if (!(condition)) { \
		fprintf(stderr, "failed: %s\n", #condition); \
		return 1; \
	} \
} while (0)

int main(void)
{
	struct ask_flow_key key = {
		.l3_proto = ASK_FLOW_L3_IPV4,
		.l4_proto = IPPROTO_TCP,
		.port_id = 0x10,
		.pppoe_push_sid = 42,
	};
	const u8 pppoe_flags[] = {
		ASK_VLANF_PPPOE_STRIP,
		ASK_VLANF_PPPOE_INSERT,
		ASK_VLANF_PPPOE_INSERT | ASK_VLANF_PUSH,
	};
	u64 snapshot;

	CHECK(!ask_hw_flow_policy_snapshot(&snapshot));
	key.family_policy_seq = snapshot;

	for (unsigned int i = 0; i < sizeof(pppoe_flags); i++) {
		key.vlan_edit_flags = pppoe_flags[i];
		CHECK(ask_fe_flow_insert(&key, 1, 2, NULL, 3) == -EOPNOTSUPP);
		CHECK(publications == 0);
	}

	/* A family removed after preflight must not reach FE publication. */
	key.vlan_edit_flags = 0;
	ingress_family = ASK_FAM_V6;
	CHECK(ask_fe_flow_insert(&key, 1, 2, NULL, 3) == -EOPNOTSUPP);
	CHECK(publications == 0);
	ingress_family = ASK_FAM_V4;
	key.l3_proto = ASK_FLOW_L3_IPV6;
	CHECK(ask_fe_flow_insert(&key, 1, 2, NULL, 3) == -EOPNOTSUPP);
	CHECK(publications == 0);
	key.l3_proto = ASK_FLOW_L3_IPV4;
	ingress_is_l2 = true;
	CHECK(ask_fe_flow_insert(&key, 1, 2, NULL, 3) == -EOPNOTSUPP);
	CHECK(publications == 0);
	ingress_is_l2 = false;

	/* The ingress mask can change after the first check, before publish. */
	clear_family_during_key_build = true;
	CHECK(ask_fe_flow_insert(&key, 1, 2, NULL, 3) == -EOPNOTSUPP);
	CHECK(publications == 0);
	clear_family_during_key_build = false;
	ingress_family = ASK_FAM_V4;

	/* A disable/re-enable must not resurrect a pre-transition publisher. */
	ingress_family = ASK_FAM_V4 | ASK_FAM_V6;
	cycle_family_during_key_build = true;
	CHECK(ask_fe_flow_insert(&key, 1, 2, NULL, 3) == -ESTALE);
	CHECK(publications == 0);
	cycle_family_during_key_build = false;
	CHECK(!ask_hw_flow_policy_current(&key));
	CHECK(!ask_hw_flow_policy_snapshot(&snapshot));
	key.family_policy_seq = snapshot;
	CHECK(ask_hw_flow_policy_current(&key));

	/* Other families and ports do not invalidate this IPv4 candidate. */
	ask_hw_offload_set_family(0x10, ASK_FAM_V4);
	CHECK(ask_hw_flow_policy_current(&key));
	hardware.port[1] = (struct ask_hw_port) {
		.in_use = true, .offload_engaged = true, .module_pinned = true,
		.port_id = 0x11, .engaged_profile = FMAN_PCD_FE_PROFILE_ROUTED,
	};
	ask_hw_offload_set_family(0x11, ASK_FAM_V6);
	CHECK(ask_hw_flow_policy_current(&key));
	key.family_policy_seq = 0;
	CHECK(ask_fe_flow_insert(&key, 1, 2, NULL, 3) == -ESTALE);
	key.family_policy_seq = ask_hw_family_seq + 1;
	CHECK(ask_fe_flow_insert(&key, 1, 2, NULL, 3) == -ESTALE);
	key.family_policy_seq = snapshot;

	module_get_allowed = false;
	CHECK(ask_fe_flow_insert(&key, 1, 2, NULL, 3) == -ESHUTDOWN);
	module_get_allowed = true;
	hardware.port[0].offload_engaged = false;
	CHECK(ask_fe_flow_insert(&key, 1, 2, NULL, 3) == -ENODEV);
	hardware.port[0].offload_engaged = true;
	hardware.port[0].fe_retained = true;
	CHECK(ask_fe_flow_insert(&key, 1, 2, NULL, 3) == -ENODEV);
	hardware.port[0].fe_retained = false;
	hardware.port[0].teardown_in_progress = true;
	CHECK(ask_fe_flow_insert(&key, 1, 2, NULL, 3) == -ENODEV);
	hardware.port[0].teardown_in_progress = false;
	hardware.port[0].module_pinned = false;
	CHECK(ask_fe_flow_insert(&key, 1, 2, NULL, 3) == -ENODEV);
	hardware.port[0].module_pinned = true;
	hardware.port[0].engaged_profile = 1;
	CHECK(ask_fe_flow_insert(&key, 1, 2, NULL, 3) == -ENODEV);
	hardware.port[0].engaged_profile = FMAN_PCD_FE_PROFILE_ROUTED;
	ask_hw_ready = false;
	CHECK(ask_fe_flow_insert(&key, 1, 2, NULL, 3) == -ENODEV);
	ask_hw_ready = true;
	hardware.fman = NULL;
	CHECK(ask_fe_flow_insert(&key, 1, 2, NULL, 3) == -ENODEV);
	hardware.fman = &fm;
	ask_hw_pcd_inst = NULL;
	CHECK(ask_fe_flow_insert(&key, 1, 2, NULL, 3) == -ENODEV);
	ask_hw_pcd_inst = &hardware;
	current_enq_off = 2;
	CHECK(ask_fe_flow_insert(&key, 1, 2, NULL, 3) == -ESTALE);
	current_enq_off = 1;
	CHECK(!publications && !module_refs);

	key.vlan_edit_flags = 0;
	CHECK(ask_fe_flow_insert(&key, 1, 2, NULL, 3) == 0);
	key.vlan_edit_flags = ASK_VLANF_POP;
	CHECK(ask_fe_flow_insert(&key, 1, 2, NULL, 3) == 0);
	CHECK(publications == 2);

	pppoe_enabled = true;
	for (unsigned int i = 0; i < sizeof(pppoe_flags); i++) {
		key.vlan_edit_flags = pppoe_flags[i];
		CHECK(ask_fe_flow_insert(&key, 1, 2, NULL, 3) == 0);
		CHECK(last_action.vlan_flags == pppoe_flags[i]);
		CHECK(last_action.pppoe_sid == 42);
	}
	CHECK(publications == 5);
	session_path_live = false;
	key.vlan_edit_flags = ASK_VLANF_PPPOE_STRIP;
	CHECK(ask_fe_flow_insert(&key, 1, 2, NULL, 3) == -EOPNOTSUPP);
	key.vlan_edit_flags = ASK_VLANF_PPPOE_INSERT;
	CHECK(ask_fe_flow_insert(&key, 1, 2, NULL, 3) == -EOPNOTSUPP);
	CHECK(publications == 5);
	session_path_live = true;
	sessions_usable = false;
	key.vlan_edit_flags = ASK_VLANF_PPPOE_INSERT;
	CHECK(ask_fe_flow_insert(&key, 1, 2, NULL, 3) == -EOPNOTSUPP);
	CHECK(publications == 5);

	CHECK(ask_pppoe_port_armed(7, ASK_FLOW_L3_IPV4));
	CHECK(!ask_pppoe_port_armed(7, ASK_FLOW_L3_IPV6));
	session_family = ASK_FAM_V6;
	CHECK(!ask_pppoe_port_armed(7, ASK_FLOW_L3_IPV4));
	CHECK(ask_pppoe_port_armed(7, ASK_FLOW_L3_IPV6));
	CHECK(!ask_pppoe_port_armed(7, 0xff));
	CHECK(!ask_pppoe_port_armed(8, ASK_FLOW_L3_IPV6));
	session_port_enabled = false;
	CHECK(!ask_pppoe_port_armed(7, ASK_FLOW_L3_IPV6));
	CHECK(!dev_refs);

	/* A family change cannot pass an in-flight FE publication. */
	struct publication_race race = { .key = key };
	pthread_t publisher, changer;
	bool waited;

	race.key.vlan_edit_flags = 0;
	ingress_family = ASK_FAM_V4;
	block_publish = true;
	CHECK(!pthread_create(&publisher, NULL, publish_in_flight, &race));
	CHECK(!pthread_mutex_lock(&stage_lock));
	while (!publisher_inside)
		CHECK(!pthread_cond_wait(&stage_changed, &stage_lock));
	CHECK(!pthread_mutex_unlock(&stage_lock));
	CHECK(!pthread_create(&changer, NULL, change_family_in_flight, NULL));
	CHECK(!pthread_mutex_lock(&stage_lock));
	while (!family_waiting && !family_done)
		CHECK(!pthread_cond_wait(&stage_changed, &stage_lock));
	waited = family_waiting;
	release_publisher = true;
	CHECK(!pthread_cond_broadcast(&stage_changed));
	CHECK(!pthread_mutex_unlock(&stage_lock));
	CHECK(!pthread_join(publisher, NULL));
	CHECK(!pthread_join(changer, NULL));
	CHECK(waited && !race.rc && !ingress_family && !module_refs);
	CHECK(publications == 6);

	/* Exhaustion never wraps into an older candidate's sequence. */
	ask_hw_offload_set_family(0x10, ASK_FAM_V4);
	ask_hw_family_seq = U64_MAX;
	CHECK(!ask_hw_flow_policy_snapshot(&snapshot));
	key.family_policy_seq = snapshot;
	key.vlan_edit_flags = 0;
	CHECK(ask_hw_flow_policy_current(&key));
	ask_hw_offload_set_family(0x10, ASK_FAM_V4 | ASK_FAM_V6);
	CHECK(ask_hw_family_seq_exhausted && !ask_hw_flow_policy_current(&key));
	CHECK(ask_hw_flow_policy_snapshot(&snapshot) == -EOVERFLOW && !snapshot);
	CHECK(ask_fe_flow_insert(&key, 1, 2, NULL, 3) == -EOVERFLOW);
	CHECK(!module_refs);
	CHECK(debug_publications && !debug_before_guard && !debug_bad_owner);

	puts("PPPoE replay gate: disabled records refused; other flows unchanged");
	return 0;
}
