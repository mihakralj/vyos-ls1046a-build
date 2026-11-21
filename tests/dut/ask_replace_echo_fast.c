// SPDX-License-Identifier: GPL-2.0
/* Host regression for S8(3) early egress-side echo skip in ask_flow_offload.c. */
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>

typedef uint32_t u32;
typedef uint64_t u64;

#define FLOW_DISSECTOR_KEY_META 1
#define ask_pr_dbg(...) ((void)0)

struct net { int dummy; };
static struct net init_net;

struct net_device {
	int ifindex;
	const char *name;
	bool is_vlan;
	struct net_device *real_dev;
	int refs;
};

static const char *netdev_name(const struct net_device *d) { return d ? d->name : "?"; }
static bool is_vlan_dev(const struct net_device *d) { return d && d->is_vlan; }
static struct net_device *vlan_dev_real_dev(struct net_device *d) { return d->real_dev; }

static struct net_device *devs[16];
static int dev_lookups;
static struct net_device *dev_get_by_index(struct net *net, int ifindex)
{
	(void)net;
	dev_lookups++;
	for (unsigned i = 0; i < 16; i++) {
		if (devs[i] && devs[i]->ifindex == ifindex) {
			devs[i]->refs++;
			return devs[i];
		}
	}
	return NULL;
}
static void dev_put(struct net_device *d) { assert(d->refs > 0); d->refs--; }

struct flow_dissector_key_meta { int ingress_ifindex; };
struct flow_match_meta {
	struct flow_dissector_key_meta *key;
	struct flow_dissector_key_meta *mask;
};
struct flow_rule {
	bool has_meta;
	struct flow_dissector_key_meta key;
	struct flow_dissector_key_meta mask;
};
struct flow_cls_offload {
	unsigned long cookie;
	struct flow_rule *rule;
};
static struct flow_rule *flow_cls_offload_flow_rule(struct flow_cls_offload *f) { return f->rule; }
static bool flow_rule_match_key(struct flow_rule *r, int k) { return k == FLOW_DISSECTOR_KEY_META && r->has_meta; }
static void flow_rule_match_meta(struct flow_rule *r, struct flow_match_meta *m)
{
	m->key = &r->key;
	m->mask = &r->mask;
}

struct ask_flow_table { int dummy; };
struct ask_flow { u64 generation; };
static struct ask_flow *stub_flow;
static bool stub_gen_current = true;
static void rcu_read_lock(void) {}
static void rcu_read_unlock(void) {}
static struct ask_flow *ask_flow_lookup(struct ask_flow_table *t, u64 cookie)
{
	(void)t; (void)cookie;
	return stub_flow;
}
static bool ask_flow_gen_is_current(struct ask_flow_table *t, u64 cookie, u64 gen)
{
	(void)t; (void)cookie; (void)gen;
	return stub_gen_current;
}

#include "replace_echo_body.h"

int main(void)
{
	struct net_device eth3 = { .ifindex = 5, .name = "eth3", .refs = 1 };
	struct net_device eth4 = { .ifindex = 6, .name = "eth4", .refs = 1 };
	struct net_device eth3_100 = {
		.ifindex = 12, .name = "eth3.100", .is_vlan = true,
		.real_dev = &eth3, .refs = 1
	};
	devs[0] = &eth3;
	devs[1] = &eth4;
	devs[2] = &eth3_100;

	struct flow_rule rule = {
		.has_meta = true,
		.key = { .ingress_ifindex = 5 },
		.mask = { .ingress_ifindex = ~0u },
	};
	struct flow_cls_offload f = { .cookie = 0x100, .rule = &rule };

	/* Plain routed flow on eth3: eth3 proceeds (no dev_get_by_index), eth4 skipped. */
	assert(!ask_flow_replace_echo_unlocked(&eth3, &f));
	assert(dev_lookups == 0);
	assert(ask_flow_replace_echo_unlocked(&eth4, &f));
	assert(eth3.refs == 1 && eth4.refs == 1);

	/* VLAN vif ingress (eth3.100 -> lower eth3): eth3 proceeds, eth4 skipped. */
	rule.key.ingress_ifindex = 12;
	assert(!ask_flow_replace_echo_unlocked(&eth3, &f));
	assert(ask_flow_replace_echo_unlocked(&eth4, &f));
	assert(eth3_100.refs == 1);

	/* Missing dev or missing META (KUnit): never skipped. */
	assert(!ask_flow_replace_echo_unlocked(NULL, &f));
	rule.has_meta = false;
	assert(!ask_flow_replace_echo_unlocked(&eth4, &f));

	/* Unsettled owner check preserves F-291 -EINPROGRESS path. */
	struct ask_flow_table tbl = {};
	struct ask_flow fl = { .generation = 7 };
	assert(!ask_flow_cookie_unsettled(&tbl, 0x100));
	stub_flow = &fl;
	stub_gen_current = true;
	assert(!ask_flow_cookie_unsettled(&tbl, 0x100));
	stub_gen_current = false;
	assert(ask_flow_cookie_unsettled(&tbl, 0x100));

	puts("ask_replace_echo_fast: OK");
	return 0;
}
