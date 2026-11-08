/* Host harness for WGFP tuple uniqueness and sink-side ambiguity rejection. */
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>

#define pr_warn_ratelimited(...) ((void)0)

typedef uint32_t u32;
typedef uint8_t u8;

struct dpaa_wgfp_tuple {
	uint32_t src, dst;
	uint16_t sport, dport;
	uint8_t proto, family, pad[2];
};
struct hlist_node { int unused; };
struct wgfp_flow {
	struct dpaa_wgfp_tuple t;
	int ifindex;
	struct hlist_node node;
};

static struct wgfp_flow *flow_list[4];
static size_t flow_count;
static int wgfp_flows;
enum { AF_INET = 2, AF_INET6 = 10 };

static bool wgfp_tuple_eq(const struct dpaa_wgfp_tuple *a,
			  const struct dpaa_wgfp_tuple *b)
{
	return !memcmp(a, b, sizeof(*a));
}

static u32 wgfp_tuple_hash(const struct dpaa_wgfp_tuple *tuple)
{
	(void)tuple;
	return 0;
}

#define hash_for_each_possible(table, obj, member, hash) \
	for (size_t __i = ((void)(table), (void)(hash), 0); __i < flow_count && \
	     (((obj) = flow_list[__i]), true); __i++)
#define hash_for_each_possible_rcu(table, obj, member, hash) \
	hash_for_each_possible(table, obj, member, hash)

#include "wgfp_flow_identity_body.h"

int main(void)
{
	struct dpaa_wgfp_tuple tuple = {
		.src = 1, .dst = 2, .sport = 3, .dport = 4,
		.proto = 17, .family = 2,
	};
	struct wgfp_flow first = { .t = tuple, .ifindex = 5 };
	struct wgfp_flow second = { .t = tuple, .ifindex = 6 };

	/* the TX path handles both families; the per-port offload policy
	 * (dpaa_wgfp_family_ok) decides which a port admits */
	assert(wgfp_tx_family_allowed(AF_INET));
	assert(wgfp_tx_family_allowed(AF_INET6));
	assert(!wgfp_tx_family_allowed(0));
	flow_list[flow_count++] = &first;
	assert(wgfp_flow_find(&tuple, first.ifindex,
			      wgfp_tuple_hash(&tuple)) == &first);
	assert(wgfp_flow_find_tuple(&tuple, wgfp_tuple_hash(&tuple)) == &first);
	assert(wgfp_flow_lookup(&tuple) == &first);

	flow_list[flow_count++] = &second;
	assert(wgfp_flow_find(&tuple, second.ifindex,
			      wgfp_tuple_hash(&tuple)) == &second);
	assert(wgfp_flow_find_tuple(&tuple, wgfp_tuple_hash(&tuple)) == &first);
	assert(wgfp_flow_lookup(&tuple) == NULL);
	return 0;
}
