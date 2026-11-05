/* Host mocks for the actual WGFP flow flush state machine. */
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

typedef int atomic64_t;

struct hlist_node {
	struct hlist_node *next;
};

struct dpaa_wgfp_tuple {
	uint32_t value;
};

struct net_device {
	int unused;
};

struct wgfp_pst {
	struct hlist_node node;
	struct delayed_work {
		int unused;
	} refresh;
	bool stopping;
};

struct wgfp_flow {
	struct hlist_node node;
	struct dpaa_wgfp_tuple t;
	struct wgfp_pst *pst;
	int ifindex;
	unsigned char state;
	bool del, gone;
};

enum {
	WGFP_LEARNING, WGFP_PENDING, WGFP_INSTALLING, WGFP_INSTALLED,
	WGFP_FAILED, WGFP_STUCK
};

static struct wgfp_flow *wgfp_flows;
static struct wgfp_pst *wgfp_psts;
static int delete_result, delete_calls, frees, queued;
static int wgfp_withdraw_fail, wgfp_withdraws, wgfp_decline_withdraws;

#define hash_for_each(head, bkt, obj, member) \
	for ((bkt) = 0, (void)(bkt), (obj) = (head); (obj); \
	     (obj) = (void *)(obj)->member.next)
#define hash_for_each_safe(head, bkt, tmp, obj, member) \
	for ((bkt) = 0, (void)(bkt), (obj) = (head); \
	     (obj) && (((tmp) = (obj)->member.next), true); \
	     (obj) = (void *)(tmp))
#define spin_lock_bh(lock) ((void)(lock))
#define spin_unlock_bh(lock) ((void)(lock))
#define cancel_delayed_work_sync(work) ((void)(work))
#define cancel_work_sync(work) ((void)(work))
#define atomic64_inc(counter) (++*(counter))
#define pr_warn_ratelimited(...) ((void)0)
#define READ_ONCE(x) (x)
#define WRITE_ONCE(x, v) ((x) = (v))

static int wgfp_age_work, wgfp_flow_work, wgfp_flows_lock;
static int *system_wq;

static void wgfp_flow_free_locked(struct wgfp_flow *fl)
{
	struct wgfp_flow **link = &wgfp_flows;

	while (*link && *link != fl)
		link = (struct wgfp_flow **)&(*link)->node.next;
	assert(*link == fl);
	*link = (void *)fl->node.next;
	frees++;
	free(fl);
}

static struct net_device *dev_get_by_index(void *net, int ifindex)
{
	return ifindex == 7 ? (struct net_device *)1 : NULL;
}

static void dev_put(struct net_device *dev) {}
static bool queue_work(int *wq, int *work)
{
	queued++;
	return true;
}

static int dpaa_wgfp_flow_del(struct net_device *dev,
			      const struct dpaa_wgfp_tuple *tuple)
{
	delete_calls++;
	return delete_result;
}

static int init_net;

#include "wgfp_flow_teardown_body.h"

static struct wgfp_flow *add_flow(unsigned char state, int ifindex)
{
	struct wgfp_flow *fl = calloc(1, sizeof(*fl));

	assert(fl);
	fl->state = state;
	fl->ifindex = ifindex;
	fl->node.next = (struct hlist_node *)wgfp_flows;
	wgfp_flows = fl;
	return fl;
}

static void stopping_peer_does_not_queue_new_withdrawal(void)
{
	struct wgfp_pst pst = { .stopping = true };
	struct wgfp_flow fl = { .pst = &pst };

	queued = wgfp_decline_withdraws = 0;
	wgfp_flow_withdraw(&fl, &wgfp_decline_withdraws);
	assert(!fl.del && !queued && !wgfp_decline_withdraws);
	pst.stopping = false;
	wgfp_flow_withdraw(&fl, &wgfp_decline_withdraws);
	assert(fl.del && queued == 1 && wgfp_decline_withdraws == 1);
}

static void failed_delete_is_retained_and_not_retried(void)
{
	struct wgfp_flow *installed;
	struct wgfp_pst pst = {};

	wgfp_flows = NULL;
	wgfp_psts = &pst;
	delete_calls = frees = wgfp_withdraw_fail = wgfp_withdraws = 0;
	delete_result = -ETIMEDOUT;
	installed = add_flow(WGFP_INSTALLED, 7);
	add_flow(WGFP_PENDING, 7);
	assert(wgfp_flows_flush() == -ETIMEDOUT);
	assert(delete_calls == 1 && wgfp_withdraw_fail == 1 && !wgfp_withdraws);
	assert(wgfp_flows == installed && installed->state == WGFP_STUCK);
	assert(frees == 1 && pst.stopping);
	assert(wgfp_flows_flush() == -EBUSY);
	assert(delete_calls == 1 && wgfp_flows == installed && frees == 1);
	free(wgfp_flows);
	wgfp_flows = NULL;
}

static void successful_delete_releases_only_after_success(void)
{
	struct wgfp_flow *installed;
	struct wgfp_pst pst = {};

	wgfp_flows = NULL;
	wgfp_psts = &pst;
	delete_calls = frees = wgfp_withdraw_fail = wgfp_withdraws = 0;
	delete_result = 0;
	installed = add_flow(WGFP_INSTALLED, 7);
	assert(wgfp_flows_flush() == 0);
	assert(delete_calls == 1 && wgfp_withdraws == 1 && !wgfp_withdraw_fail);
	assert(!wgfp_flows && frees == 1);
	(void)installed;
}

static void missing_interface_keeps_record_owner(void)
{
	struct wgfp_flow *installed;
	struct wgfp_pst pst = {};

	wgfp_flows = NULL;
	wgfp_psts = &pst;
	delete_calls = frees = wgfp_withdraw_fail = wgfp_withdraws = 0;
	delete_result = 0;
	installed = add_flow(WGFP_INSTALLED, 99);
	assert(wgfp_flows_flush() == -ENODEV);
	assert(!delete_calls && wgfp_withdraw_fail == 1 && !wgfp_withdraws);
	assert(wgfp_flows == installed && installed->state == WGFP_STUCK && !frees);
	free(installed);
	wgfp_flows = NULL;
}

int main(void)
{
	stopping_peer_does_not_queue_new_withdrawal();
	failed_delete_is_retained_and_not_retried();
	successful_delete_releases_only_after_success();
	missing_interface_keeps_record_owner();
	puts("WGFP teardown ownership: OK");
	return 0;
}
