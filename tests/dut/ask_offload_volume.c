/* Host harness for the S6 static volume gate, ask_offload_too_small()
 * (kernel/ask/oot-modules/ask/ask_flow_offload.c, plans/ASK2-FLOW-SCALING-PLAN.md S6 / §3.3).
 * The real function is included from ask_offload_volume_body.h, extracted by
 * tests/ask_offload_volume.py; the flowtable and conntrack-accounting types are mocked. */
#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>

typedef uint64_t u64;
typedef struct { long long counter; } atomic64_t;
static long long atomic64_read(const atomic64_t *a) { return a->counter; }
#define READ_ONCE(x) (x)
#define container_of(ptr, type, member) ((type *)((char *)(ptr) - offsetof(type, member)))
#define virt_addr_valid(p) ((p) != NULL)
#define FLOW_OFFLOAD_DIR_MAX 2

struct flow_offload_tuple { int dir; };
struct flow_offload_tuple_rhash { struct flow_offload_tuple tuple; };
struct nf_conn { int id; };
struct flow_offload { struct flow_offload_tuple_rhash tuplehash[FLOW_OFFLOAD_DIR_MAX]; struct nf_conn *ct; };
struct nf_conn_counter { atomic64_t packets, bytes; };
struct nf_conn_acct { struct nf_conn_counter counter[2]; };

static struct nf_conn_acct acct;
static bool have_acct = true;
static int warnings;
static struct nf_conn_acct *nf_conn_acct_find(const struct nf_conn *ct) { (void)ct; return have_acct ? &acct : NULL; }
static bool __ratelimit(void *rs) { (void)rs; return true; }
static int ask_offload_no_acct_rs;
#define pr_warn(...) (warnings++)

static unsigned int ask_offload_min_packets, ask_offload_min_bytes;

#include "ask_offload_volume_body.h"

static void set(long long op, long long ob, long long rp, long long rb)
{
	acct.counter[0].packets.counter = op; acct.counter[0].bytes.counter = ob;
	acct.counter[1].packets.counter = rp; acct.counter[1].bytes.counter = rb;
}

int main(void)
{
	struct nf_conn ct;
	struct flow_offload fl = { .tuplehash = { { { 0 } }, { { 1 } } }, .ct = &ct };
	unsigned long orig = (unsigned long)&fl.tuplehash[0].tuple, reply = (unsigned long)&fl.tuplehash[1].tuple;

	/* off by default, whatever the volume */
	set(0, 0, 0, 0);
	assert(!ask_offload_too_small(orig) && !ask_offload_too_small(reply));

	/* packets threshold: counted over both directions, same answer for both cookies */
	ask_offload_min_packets = 16;
	set(7, 700, 8, 800);
	assert(ask_offload_too_small(orig) && ask_offload_too_small(reply));
	set(8, 800, 8, 800);
	assert(!ask_offload_too_small(orig) && !ask_offload_too_small(reply));

	/* either packets or bytes admits: a few large packets pass on bytes */
	ask_offload_min_bytes = 16384;
	set(2, 9000, 1, 9000);
	assert(!ask_offload_too_small(orig));
	set(5, 600, 5, 600);
	assert(ask_offload_too_small(reply));

	/* bytes only */
	ask_offload_min_packets = 0;
	set(100, 6000, 100, 6000);
	assert(ask_offload_too_small(orig));

	/* no accounting extension: fail open, with a warning */
	have_acct = false;
	assert(!ask_offload_too_small(orig) && warnings == 1);
	have_acct = true;

	/* bad cookies fail open */
	assert(!ask_offload_too_small(0));
	fl.tuplehash[0].tuple.dir = 5;
	assert(!ask_offload_too_small(orig));

	puts("ask_offload_volume: OK");
	return 0;
}
