// SPDX-License-Identifier: GPL-2.0
/* Exercise the extracted pending workers and capability-withdrawal gate. */
#include <assert.h>
#include <errno.h>
#include <netinet/in.h>
#include <pthread.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef uint8_t u8;
typedef uint16_t u16;
typedef uint32_t u32;
typedef uint64_t u64;
typedef uint16_t __be16;
typedef uint32_t __be32;
#define __force
#define U64_MAX UINT64_MAX
#define ETH_ALEN 6
#define VLAN_VID_MASK 0x0fff
#define GFP_ATOMIC 0
#define ASK_FLOW_PENDING_MAX 4096
#define ASK_VLANF_POP 1
#define ASK_VLANF_PUSH 2
#define ASK_VLANF_PPPOE_STRIP 4
#define ASK_VLANF_PPPOE_INSERT 8
#include "ask_flow_protocols.h"
#define ASK_FAM_V4 1
#define ASK_FAM_V6 2
#define ASK_HW_MAX_PORTS 1
#define FMAN_PCD_FE_PROFILE_ROUTED 0
#define THIS_MODULE NULL
#define pppoe_ifindex proto.pppoe.ifindex
#define current get_current()
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
static unsigned int error_logs;
#define READ_ONCE(v) __atomic_load_n(&(v), __ATOMIC_RELAXED)
#define WRITE_ONCE(v, x) __atomic_store_n(&(v), (x), __ATOMIC_RELAXED)
#define pr_info_ratelimited(...) ((void)0)
#define pr_warn_ratelimited(...) ((void)0)
#define pr_err_ratelimited(...) (error_logs++)
#define pr_err(...) (error_logs++)
#define ask_pr_err(...) (error_logs++)

struct list_head { struct list_head *next, *prev; };
#define LIST_HEAD(n) struct list_head n = { &(n), &(n) }
static bool list_empty(const struct list_head *h) { return h->next == h; }
static void list_add_tail(struct list_head *e, struct list_head *h)
{
	e->prev = h->prev;
	e->next = h;
	h->prev->next = e;
	h->prev = e;
}
static void list_del(struct list_head *e)
{
	e->prev->next = e->next;
	e->next->prev = e->prev;
}
#define list_entry(p, t, m) ((t *)((char *)(p) - offsetof(t, m)))
#define list_for_each_entry(pos, head, m)                                  \
	for (pos = list_entry((head)->next, typeof(*pos), m);             \
	     &pos->m != (head); pos = list_entry(pos->m.next, typeof(*pos), m))
#define list_for_each_entry_safe(pos, n, head, m)                           \
	for (pos = list_entry((head)->next, typeof(*pos), m),             \
	     n = list_entry(pos->m.next, typeof(*pos), m);                \
	     &pos->m != (head);                                           \
	     pos = n, n = list_entry(n->m.next, typeof(*n), m))

static pthread_mutex_t event_lock = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t event_cond = PTHREAD_COND_INITIALIZER;
static bool mutex_contended, session_flush_done;
struct mutex { pthread_mutex_t native; pthread_t owner; bool held; };
static struct mutex *watched_mutex;
#define DEFINE_MUTEX(n) struct mutex n = { .native = PTHREAD_MUTEX_INITIALIZER }
#define lockdep_assert_held(m) \
	assert((m)->held && pthread_equal((m)->owner, pthread_self()))
static void mutex_lock(struct mutex *m)
{
	int rc;

	if (m == watched_mutex) {
		rc = pthread_mutex_trylock(&m->native);
		if (rc == EBUSY) {
			assert(!pthread_mutex_lock(&event_lock));
			mutex_contended = true;
			assert(!pthread_cond_broadcast(&event_cond));
			assert(!pthread_mutex_unlock(&event_lock));
			rc = pthread_mutex_lock(&m->native);
		}
	} else {
		rc = pthread_mutex_lock(&m->native);
	}
	assert(!rc);
	m->owner = pthread_self();
	m->held = true;
}
static void mutex_unlock(struct mutex *m)
{
	lockdep_assert_held(m);
	m->held = false;
	assert(!pthread_mutex_unlock(&m->native));
}
#define DEFINE_SPINLOCK(n) pthread_mutex_t n = PTHREAD_MUTEX_INITIALIZER
#define spin_lock_bh(m) assert(!pthread_mutex_lock(m))
static void spin_unlock_mock(pthread_mutex_t *m);
#define spin_unlock_bh(m) spin_unlock_mock(m)

typedef struct { atomic_int value; } atomic_t;
#define ATOMIC_INIT(v) { .value = ATOMIC_VAR_INIT(v) }
static void atomic_inc(atomic_t *v) { atomic_fetch_add(&v->value, 1); }
static void *kzalloc(size_t n, int flags) { return calloc(1, n); }
static void kfree(void *p) { free(p); }

struct ask_flow_key {
	u8 l3_proto, l4_proto, port_id, vlan_edit_flags;
	__be16 vlan_push_tci, vlan_push_tpid;
	u8 next_hop_mac[ETH_ALEN], egress_mac[ETH_ALEN];
	u64 family_policy_seq;
};
enum ask_hw_dir { ASK_DIR_FWD };
struct ask_flow_table { int unused; };
struct ask_flow { int unused; };
static struct ask_flow_table table;
static unsigned long jiffies;
static atomic_bool vlan_armed = ATOMIC_VAR_INIT(true);
static atomic_bool pppoe_armed = ATOMIC_VAR_INIT(true);
static atomic_bool pppoe_port_armed = ATOMIC_VAR_INIT(true);
static u8 ask_hw_port_family[64];
static u64 ask_hw_family_seq = 1;
static u64 ask_hw_family_changed[64][2];
static bool ask_hw_family_seq_exhausted;
static unsigned long ask_hw_port_l2[4];
struct fman { int unused; };
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
static struct fman fm;
static struct ask_hw_pcd hardware = {
	.lock = { .native = PTHREAD_MUTEX_INITIALIZER },
	.fman = &fm,
	.port = { { .in_use = true, .offload_engaged = true, .module_pinned = true,
		    .port_id = 0x10, .engaged_profile = FMAN_PCD_FE_PROFILE_ROUTED } },
};
static struct ask_hw_pcd *ask_hw_pcd_inst = &hardware;
static bool ask_hw_ready = true;
static atomic_int module_refs;
static bool try_module_get(void *module)
{
	atomic_fetch_add(&module_refs, 1);
	return true;
}
static void module_put(void *module)
{
	assert(atomic_fetch_sub(&module_refs, 1) > 0);
}
static bool test_bit(u8 port, const unsigned long *map) { return false; }
static unsigned long ask_hw_get_enq_fe_off(void) { return 1; }
#include "pending_policy.h"

static bool ask_hw_vlan_offload_armed(void) { return atomic_load(&vlan_armed); }
static bool ask_hw_pppoe_offload_armed(void) { return atomic_load(&pppoe_armed); }
static bool ask_pppoe_port_armed(int ifindex, u8 l3_proto)
{
	unsigned int family = l3_proto == ASK_FLOW_L3_IPV4 ? ASK_FAM_V4 :
			      l3_proto == ASK_FLOW_L3_IPV6 ? ASK_FAM_V6 : 0;

	return ifindex == 7 && atomic_load(&pppoe_port_armed) &&
	       (READ_ONCE(ask_hw_port_family[0x10]) & family);
}
static struct ask_flow_table *ask_flow_default_table(void) { return &table; }
static bool ask_flow_gen_is_current(struct ask_flow_table *t, u64 cookie,
				    u64 generation)
{
	return t == &table && cookie && generation;
}

#include "pending_definitions.h"
#include "pending_queue.h"

static _Thread_local bool watching_detach, already_paused;
static bool detached, resume_worker;

static void spin_unlock_mock(pthread_mutex_t *m)
{
	assert(!pthread_mutex_unlock(m));
	if (m != &ask_flow_pending_lock || !watching_detach || already_paused)
		return;
	already_paused = true;
	assert(!pthread_mutex_lock(&event_lock));
	detached = true;
	assert(!pthread_cond_broadcast(&event_cond));
	while (!resume_worker)
		assert(!pthread_cond_wait(&event_cond, &event_lock));
	assert(!pthread_mutex_unlock(&event_lock));
}

static atomic_int publications, resolve_calls;
static atomic_bool live_pppoe;
static bool first_resolution_empty, pause_resolution, resolution_entered;
static bool release_resolution, pause_publication, publication_entered;
static bool release_publication;
static int ask_neigh_republish(struct ask_flow_table *t, u64 cookie,
			       const struct ask_flow_key *key, u32 oif,
			       u32 action_flags, enum ask_hw_dir dir,
			       u64 generation)
{
	lockdep_assert_held(&ask_flow_lifecycle_lock);
	assert(t == &table && cookie && generation);
	if (pause_publication) {
		assert(!pthread_mutex_lock(&event_lock));
		publication_entered = true;
		assert(!pthread_cond_broadcast(&event_cond));
		while (!release_publication)
			assert(!pthread_cond_wait(&event_cond, &event_lock));
		assert(!pthread_mutex_unlock(&event_lock));
	}
	atomic_fetch_add(&publications, 1);
	if (key->vlan_edit_flags & (ASK_VLANF_PPPOE_STRIP | ASK_VLANF_PPPOE_INSERT))
		atomic_store(&live_pppoe, true);
	return 0;
}

struct net_device { int ifindex, type; };
struct neighbour { int lock; unsigned int nud_state; u8 ha[ETH_ALEN]; };
struct work_struct { int unused; };
struct delayed_work { int unused; };
static struct net_device device = { .ifindex = 7 };
static struct neighbour neighbor = { .nud_state = 1, .ha = { 2, 1, 2, 3, 4, 5 } };
static int init_net, arp_tbl;
#define ARPHRD_PPP 512
#define NETDEV_DOWN 1
#define NETDEV_UNREGISTER 2
#define NETDEV_CHANGEMTU 3
#define NETDEV_CHANGEADDR 4
#define NOTIFY_DONE 0
#define DECLARE_WORK(name, fn) struct work_struct name
#define EXPORT_SYMBOL_GPL(symbol)
#define netdev_notifier_info_to_dev(ptr) (ptr)
struct notifier_block { int unused; };
static struct work_struct ask_devchg_flush_work;
static unsigned int scheduled_work;
static void schedule_work(struct work_struct *work) { scheduled_work++; }
static void ask_devchg_queue(int ifindex) {}
#define NUD_VALID 1
#define rcu_read_lock() ((void)0)
#define rcu_read_unlock() ((void)0)
#define read_lock_bh(l) ((void)0)
#define read_unlock_bh(l) ((void)0)
static bool is_vlan_dev(const struct net_device *dev) { return false; }
static struct net_device *vlan_dev_real_dev(struct net_device *dev) { return dev; }
static struct net_device *dev_get_by_index_rcu(void *net, int ifindex)
{
	return ifindex == device.ifindex ? &device : NULL;
}
static struct net_device *__vlan_find_dev_deep_rcu(struct net_device *dev,
						    __be16 tpid, u16 vid)
{
	return dev;
}
static struct neighbour *neigh_lookup(void *table, const void *dst,
				      struct net_device *dev)
{
	return dev == &device ? &neighbor : NULL;
}
static void neigh_release(struct neighbour *n) {}
static bool is_multicast_ether_addr(const u8 *mac) { return mac[0] & 1; }
static bool is_zero_ether_addr(const u8 *mac)
{
	static const u8 zero[ETH_ALEN];
	return !memcmp(mac, zero, sizeof(zero));
}
static void ask_resolve_neigh_v4(struct net_device *dev, __be32 dst,
				 u8 *next, u8 *egress)
{
	int call = atomic_fetch_add(&resolve_calls, 1);

	if (pause_resolution && !call) {
		assert(!pthread_mutex_lock(&event_lock));
		resolution_entered = true;
		assert(!pthread_cond_broadcast(&event_cond));
		while (!release_resolution)
			assert(!pthread_cond_wait(&event_cond, &event_lock));
		assert(!pthread_mutex_unlock(&event_lock));
	}
	if (first_resolution_empty && !call)
		memset(next, 0, ETH_ALEN);
	else
		memcpy(next, neighbor.ha, ETH_ALEN);
	memset(egress, 2, ETH_ALEN);
}
static unsigned int msecs_to_jiffies(unsigned int ms) { return ms; }
static void schedule_delayed_work(struct delayed_work *work, unsigned int delay) {}

#include "pending_workers.h"

static DEFINE_MUTEX(ask_fe_orphan_lock);
static LIST_HEAD(ask_fe_orphans);
static atomic_int withdrawal_calls;
static int withdrawal_rc;
static bool identity_lost;
static bool pause_withdrawal, withdrawal_entered, release_withdrawal;
static int ask_vlan_collect(struct ask_flow *flow, void *arg) { return 0; }
static int ask_pppoe_collect(struct ask_flow *flow, void *arg) { return 0; }
static int __ask_flow_flush_matching(int (*collect)(struct ask_flow *, void *),
				     const char *what, bool held, bool *lost)
{
	lockdep_assert_held(&ask_flow_lifecycle_lock);
	assert(held);
	atomic_fetch_add(&withdrawal_calls, 1);
	if (pause_withdrawal) {
		assert(!pthread_mutex_lock(&event_lock));
		withdrawal_entered = true;
		assert(!pthread_cond_broadcast(&event_cond));
		while (!release_withdrawal)
			assert(!pthread_cond_wait(&event_cond, &event_lock));
		assert(!pthread_mutex_unlock(&event_lock));
	}
		*lost = identity_lost;
		if (!withdrawal_rc)
			atomic_store(&live_pppoe, false);
		return withdrawal_rc;
}
static void ask_flow_flush_matching(int (*collect)(struct ask_flow *, void *),
				    const char *what)
{
	atomic_store(&live_pppoe, false);
}

#include "pending_flush.h"
#include "pending_session.h"
#include "pending_port_family.h"

static void set_family(u8 family)
{
	mutex_lock(&hardware.lock);
	ask_hw_set_family_locked(0x10, family);
	mutex_unlock(&hardware.lock);
}

static void clear_pending(void)
{
	struct ask_flow_pending *p, *next;

	mutex_lock(&ask_flow_lifecycle_lock);
	spin_lock_bh(&ask_flow_pending_lock);
	list_for_each_entry_safe(p, next, &ask_flow_pending_list, node) {
		list_del(&p->node);
		ask_flow_pending_count--;
		kfree(p);
	}
	spin_unlock_bh(&ask_flow_pending_lock);
	mutex_unlock(&ask_flow_lifecycle_lock);
	assert(!ask_flow_pending_count);
}

static void reset_state(void)
{
	clear_pending();
	ask_vlan_pending_epoch = ask_pppoe_pending_epoch = 0;
	ask_pppoe_session_epoch = 0;
	ask_pppoe_session_epoch_exhausted = false;
	ask_pppoe_session_dirty = false;
	ask_pppoe_events_live = true;
	ask_vlan_withdraw_unresolved = ask_pppoe_withdraw_unresolved = false;
	atomic_store(&vlan_armed, true);
	atomic_store(&pppoe_armed, true);
	atomic_store(&pppoe_port_armed, true);
	WRITE_ONCE(ask_hw_port_family[0x10], ASK_FAM_V4 | ASK_FAM_V6);
	ask_hw_family_seq = 1;
	memset(ask_hw_family_changed, 0, sizeof(ask_hw_family_changed));
	ask_hw_family_seq_exhausted = false;
	atomic_store(&ask_flow_pending_deferred.value, 0);
	atomic_store(&publications, 0);
	atomic_store(&resolve_calls, 0);
	atomic_store(&live_pppoe, false);
	atomic_store(&withdrawal_calls, 0);
	withdrawal_rc = 0;
	identity_lost = false;
	pause_withdrawal = withdrawal_entered = release_withdrawal = false;
	first_resolution_empty = false;
	pause_resolution = resolution_entered = release_resolution = false;
	pause_publication = publication_entered = release_publication = false;
	mutex_contended = session_flush_done = false;
	watched_mutex = NULL;
	detached = resume_worker = false;
	scheduled_work = 0;
	error_logs = 0;
}

static void enqueue(u64 cookie, u64 generation, u8 flags)
{
	struct ask_flow_key key = { .l3_proto = ASK_FLOW_L3_IPV4,
				    .l4_proto = IPPROTO_TCP, .port_id = 0x10,
				    .vlan_edit_flags = flags };
	u64 seq;

	mutex_lock(&ask_flow_lifecycle_lock);
	assert(!ask_hw_flow_policy_snapshot(&seq));
	key.family_policy_seq = seq;
	assert(!ask_flow_pending_enqueue(cookie, &key, 1, 0, device.ifindex,
					 device.ifindex, 42, ASK_DIR_FWD, generation,
					 device.ifindex, ask_pppoe_session_snapshot()));
	mutex_unlock(&ask_flow_lifecycle_lock);
}

static void *resolved_worker(void *arg)
{
	watching_detach = true;
	ask_flow_neigh_resolved(&device, 42);
	return NULL;
}

static void *poll_worker(void *arg)
{
	watching_detach = true;
	ask_flow_pending_poll_fn(NULL);
	return NULL;
}

static void *session_flush_worker(void *arg)
{
	ask_pppoe_flush_fn(NULL);
	assert(!pthread_mutex_lock(&event_lock));
	session_flush_done = true;
	assert(!pthread_cond_broadcast(&event_cond));
	assert(!pthread_mutex_unlock(&event_lock));
	return NULL;
}

static void *session_sync_worker(void *arg)
{
	int *result = arg;

	*result = ask_flow_pppoe_flush_sync();
	return NULL;
}

static void notify_pppoe_down(void)
{
	struct net_device ppp = { .ifindex = 99, .type = ARPHRD_PPP };

	assert(ask_pppoe_netdev_event(NULL, NETDEV_DOWN, &ppp) == NOTIFY_DONE);
	assert(scheduled_work);
}

static bool detached_session_replay(bool poll, u8 flags, bool should_publish)
{
	pthread_t worker;

	reset_state();
	enqueue(1, 1, flags);
	assert(!pthread_create(&worker, NULL, poll ? poll_worker : resolved_worker, NULL));
	assert(!pthread_mutex_lock(&event_lock));
	while (!detached)
		assert(!pthread_cond_wait(&event_cond, &event_lock));
	assert(!pthread_mutex_unlock(&event_lock));

	notify_pppoe_down();
	ask_pppoe_flush_fn(NULL);
	assert(!pthread_mutex_lock(&event_lock));
	resume_worker = true;
	assert(!pthread_cond_broadcast(&event_cond));
	assert(!pthread_mutex_unlock(&event_lock));
	assert(!pthread_join(worker, NULL));
	return atomic_load(&publications) == (should_publish ? 1 : 0) &&
	       !ask_flow_pending_count;
}

static bool session_during_publish(void)
{
	pthread_t publisher, flusher;
	bool contended;

	reset_state();
	enqueue(1, 1, ASK_VLANF_PPPOE_STRIP);
	pause_publication = true;
	assert(!pthread_create(&publisher, NULL, resolved_worker, NULL));
	assert(!pthread_mutex_lock(&event_lock));
	while (!detached)
		assert(!pthread_cond_wait(&event_cond, &event_lock));
	resume_worker = true;
	assert(!pthread_cond_broadcast(&event_cond));
	while (!publication_entered)
		assert(!pthread_cond_wait(&event_cond, &event_lock));
	assert(!pthread_mutex_unlock(&event_lock));

	notify_pppoe_down();
	watched_mutex = &ask_flow_lifecycle_lock;
	assert(!pthread_create(&flusher, NULL, session_flush_worker, NULL));
	assert(!pthread_mutex_lock(&event_lock));
	while (!mutex_contended && !session_flush_done)
		assert(!pthread_cond_wait(&event_cond, &event_lock));
	contended = mutex_contended;
	release_publication = true;
	assert(!pthread_cond_broadcast(&event_cond));
	assert(!pthread_mutex_unlock(&event_lock));
	assert(!pthread_join(publisher, NULL));
	assert(!pthread_join(flusher, NULL));
	watched_mutex = NULL;
	return contended && atomic_load(&publications) == 1 &&
	       !atomic_load(&live_pppoe);
}

static bool session_during_repark(void)
{
	pthread_t worker;

	reset_state();
	enqueue(1, 1, ASK_VLANF_PPPOE_STRIP);
	first_resolution_empty = pause_resolution = true;
	assert(!pthread_create(&worker, NULL, resolved_worker, NULL));
	assert(!pthread_mutex_lock(&event_lock));
	while (!detached)
		assert(!pthread_cond_wait(&event_cond, &event_lock));
	resume_worker = true;
	assert(!pthread_cond_broadcast(&event_cond));
	while (!resolution_entered)
		assert(!pthread_cond_wait(&event_cond, &event_lock));
	assert(!pthread_mutex_unlock(&event_lock));

	notify_pppoe_down();
	assert(!pthread_mutex_lock(&event_lock));
	release_resolution = true;
	assert(!pthread_cond_broadcast(&event_cond));
	assert(!pthread_mutex_unlock(&event_lock));
	assert(!pthread_join(worker, NULL));
	return !atomic_load(&publications) && !ask_flow_pending_count;
}

static bool session_withdrawal_admission(void)
{
	reset_state();
	atomic_store(&live_pppoe, true);
	notify_pppoe_down();
	if (ask_pppoe_sessions_usable())
		return false;

	withdrawal_rc = -ENOMEM;
	ask_pppoe_flush_fn(NULL);
	if (ask_pppoe_sessions_usable() || !atomic_load(&live_pppoe))
		return false;
	enqueue(1, 1, ASK_VLANF_PPPOE_STRIP);
	enqueue(2, 1, 0);
	ask_flow_pending_poll_fn(NULL);
	if (atomic_load(&publications) != 1 || ask_flow_pending_count)
		return false;

	atomic_store(&pppoe_armed, false);
	withdrawal_rc = 0;
	if (ask_flow_pppoe_flush_sync() || !ask_pppoe_sessions_usable() ||
	    atomic_load(&live_pppoe))
		return false;
	if (atomic_load(&withdrawal_calls) != 2)
		return false;
	ask_pppoe_flush_fn(NULL);
	if (atomic_load(&withdrawal_calls) != 2)
		return false;
	atomic_store(&pppoe_armed, true);
	enqueue(3, 1, ASK_VLANF_PPPOE_INSERT);
	ask_flow_pending_poll_fn(NULL);
	return atomic_load(&publications) == 2 && !ask_flow_pending_count;
}

static bool session_event_during_withdrawal(bool synchronous)
{
	pthread_t flusher;
	int result = 0;

	reset_state();
	notify_pppoe_down();
	if (synchronous)
		atomic_store(&pppoe_armed, false);
	pause_withdrawal = true;
	assert(!pthread_create(&flusher, NULL,
			       synchronous ? session_sync_worker : session_flush_worker,
			       synchronous ? &result : NULL));
	assert(!pthread_mutex_lock(&event_lock));
	while (!withdrawal_entered)
		assert(!pthread_cond_wait(&event_cond, &event_lock));
	assert(!pthread_mutex_unlock(&event_lock));

	notify_pppoe_down();
	assert(!pthread_mutex_lock(&event_lock));
	release_withdrawal = true;
	assert(!pthread_cond_broadcast(&event_cond));
	assert(!pthread_mutex_unlock(&event_lock));
	assert(!pthread_join(flusher, NULL));
	if (ask_pppoe_sessions_usable() || (synchronous && result != -EAGAIN))
		return false;

	pause_withdrawal = false;
	ask_pppoe_flush_fn(NULL);
	if (!ask_pppoe_sessions_usable())
		return false;
	return !synchronous || !ask_flow_pppoe_flush_sync();
}

static bool session_port_family_disable_reenable(void)
{
	pthread_t worker;
	bool fenced;

	reset_state();
	atomic_store(&live_pppoe, true);
	enqueue(1, 1, ASK_VLANF_PPPOE_STRIP);
	set_family(ASK_FAM_V4 | ASK_FAM_V6);
	assert(!scheduled_work);
	assert(!pthread_create(&worker, NULL, poll_worker, NULL));
	assert(!pthread_mutex_lock(&event_lock));
	while (!detached)
		assert(!pthread_cond_wait(&event_cond, &event_lock));
	assert(!pthread_mutex_unlock(&event_lock));

	set_family(ASK_FAM_V6);
	set_family(ASK_FAM_V4 | ASK_FAM_V6);
	fenced = !ask_pppoe_sessions_usable() &&
		 ask_pppoe_session_epoch == 1 && scheduled_work == 1;
	ask_pppoe_flush_fn(NULL);
	assert(!pthread_mutex_lock(&event_lock));
	resume_worker = true;
	assert(!pthread_cond_broadcast(&event_cond));
	assert(!pthread_mutex_unlock(&event_lock));
	assert(!pthread_join(worker, NULL));
	return fenced && !atomic_load(&live_pppoe) &&
	       !atomic_load(&publications) && !ask_flow_pending_count;
}

static void invalidate_family_policy(void)
{
	set_family(ASK_FAM_V6);
	set_family(ASK_FAM_V4 | ASK_FAM_V6);
}

static bool detached_family_replay(bool poll)
{
	pthread_t worker;

	reset_state();
	enqueue(1, 1, 0);
	assert(!pthread_create(&worker, NULL, poll ? poll_worker : resolved_worker, NULL));
	assert(!pthread_mutex_lock(&event_lock));
	while (!detached)
		assert(!pthread_cond_wait(&event_cond, &event_lock));
	assert(!pthread_mutex_unlock(&event_lock));
	invalidate_family_policy();
	assert(!pthread_mutex_lock(&event_lock));
	resume_worker = true;
	assert(!pthread_cond_broadcast(&event_cond));
	assert(!pthread_mutex_unlock(&event_lock));
	assert(!pthread_join(worker, NULL));
	return !atomic_load(&publications) && !ask_flow_pending_count;
}

static bool family_during_repark(void)
{
	pthread_t worker;

	reset_state();
	enqueue(1, 1, 0);
	first_resolution_empty = pause_resolution = true;
	assert(!pthread_create(&worker, NULL, resolved_worker, NULL));
	assert(!pthread_mutex_lock(&event_lock));
	while (!detached)
		assert(!pthread_cond_wait(&event_cond, &event_lock));
	resume_worker = true;
	assert(!pthread_cond_broadcast(&event_cond));
	while (!resolution_entered)
		assert(!pthread_cond_wait(&event_cond, &event_lock));
	assert(!pthread_mutex_unlock(&event_lock));
	invalidate_family_policy();
	assert(!pthread_mutex_lock(&event_lock));
	release_resolution = true;
	assert(!pthread_cond_broadcast(&event_cond));
	assert(!pthread_mutex_unlock(&event_lock));
	assert(!pthread_join(worker, NULL));
	return !atomic_load(&publications) && !ask_flow_pending_count &&
	       atomic_load(&ask_flow_pending_deferred.value) == 1;
}

static bool detached_replay(bool poll, u8 flags, bool disable_pppoe,
			    bool empty_first, bool should_publish)
{
	pthread_t worker;
	int rc;

	reset_state();
	first_resolution_empty = empty_first;
	enqueue(1, 1, flags);
	assert(!pthread_create(&worker, NULL, poll ? poll_worker : resolved_worker, NULL));
	assert(!pthread_mutex_lock(&event_lock));
	while (!detached)
		assert(!pthread_cond_wait(&event_cond, &event_lock));
	assert(!pthread_mutex_unlock(&event_lock));

	if (disable_pppoe) {
		atomic_store(&pppoe_armed, false);
		rc = ask_flow_pppoe_flush_sync();
		atomic_store(&pppoe_armed, true);
	} else {
		atomic_store(&vlan_armed, false);
		rc = ask_flow_vlan_flush();
		atomic_store(&vlan_armed, true);
	}
	assert(!rc);
	assert(!pthread_mutex_lock(&event_lock));
	resume_worker = true;
	assert(!pthread_cond_broadcast(&event_cond));
	assert(!pthread_mutex_unlock(&event_lock));
	assert(!pthread_join(worker, NULL));
	return atomic_load(&publications) == (should_publish ? 1 : 0) &&
	       !ask_flow_pending_count;
}

static void test_queue_and_failure(void)
{
	struct ask_flow_pending *p;
	struct list_head retained_orphan;
	struct ask_flow_key key = { .l3_proto = ASK_FLOW_L3_IPV4,
				    .l4_proto = IPPROTO_TCP, .port_id = 0x10,
				    .vlan_edit_flags = ASK_VLANF_PPPOE_INSERT };
	struct net_device non_ppp = { .ifindex = 8 };
	struct net_device ppp = { .ifindex = 99, .type = ARPHRD_PPP };
	u64 old_session_epoch, family_seq;

	reset_state();
	enqueue(1, 1, ASK_VLANF_PPPOE_STRIP);
	enqueue(2, 1, ASK_VLANF_PUSH);
	enqueue(3, 1, 0);
	enqueue(4, 1, ASK_VLANF_PUSH | ASK_VLANF_PPPOE_INSERT);
	atomic_store(&pppoe_armed, false);
	withdrawal_rc = -ENOMEM;
	assert(ask_flow_pppoe_flush_sync() == -ENOMEM);
	assert(ask_pppoe_pending_epoch == 1 && ask_flow_pending_count == 2);
	atomic_store(&pppoe_armed, true);
	atomic_store(&vlan_armed, false);
	withdrawal_rc = 0;
	assert(!ask_flow_vlan_flush());
	assert(ask_vlan_pending_epoch == 1 && ask_flow_pending_count == 1);
	p = list_entry(ask_flow_pending_list.next, struct ask_flow_pending, node);
	assert(p->cookie == 3 && !p->key.vlan_edit_flags);
	mutex_lock(&ask_flow_lifecycle_lock);
	assert(ask_flow_pending_eligible(p));
	mutex_unlock(&ask_flow_lifecycle_lock);

	reset_state();
	enqueue(7, 1, ASK_VLANF_PPPOE_STRIP);
	enqueue(7, 2, 0);
	assert(ask_flow_pending_count == 1);
	atomic_store(&pppoe_armed, false);
	assert(!ask_flow_pppoe_flush_sync());
	assert(ask_flow_pending_count == 1);
	p = list_entry(ask_flow_pending_list.next, struct ask_flow_pending, node);
	assert(p->generation == 2 && !p->key.vlan_edit_flags);

	reset_state();
	enqueue(9, 1, 0);
	atomic_store(&pppoe_armed, false);
	assert(!ask_flow_pppoe_flush_sync());
	atomic_store(&pppoe_armed, true);
	enqueue(9, 2, ASK_VLANF_PPPOE_STRIP);
	assert(ask_flow_pending_count == 1);
	p = list_entry(ask_flow_pending_list.next, struct ask_flow_pending, node);
	assert(p->generation == 2 && p->pppoe_epoch == 1);
	ask_flow_pending_poll_fn(NULL);
	assert(atomic_load(&publications) == 1 && !ask_flow_pending_count);

	reset_state();
	ask_vlan_pending_epoch = U64_MAX;
	enqueue(8, 1, ASK_VLANF_POP);
	atomic_store(&vlan_armed, false);
	assert(ask_flow_vlan_flush() == -EOVERFLOW);
	assert(ask_vlan_withdraw_unresolved && ask_vlan_pending_epoch == U64_MAX);
	clear_pending();

	reset_state();
	old_session_epoch = ask_pppoe_session_snapshot();
	assert(!ask_hw_flow_policy_snapshot(&family_seq));
	key.family_policy_seq = family_seq;
	notify_pppoe_down();
	ask_pppoe_flush_fn(NULL);
	mutex_lock(&ask_flow_lifecycle_lock);
	assert(!ask_flow_pending_enqueue(10, &key, 1, 0, device.ifindex,
					 device.ifindex, 42, ASK_DIR_FWD, 1,
					 device.ifindex, old_session_epoch));
	mutex_unlock(&ask_flow_lifecycle_lock);
	ask_flow_pending_poll_fn(NULL);
	assert(!atomic_load(&publications) && !ask_flow_pending_count);

	reset_state();
	assert(!ask_hw_flow_policy_snapshot(&family_seq));
	key.family_policy_seq = family_seq;
	mutex_lock(&ask_flow_lifecycle_lock);
	assert(!ask_flow_pending_enqueue(11, &key, 1, 0, device.ifindex,
					 device.ifindex, 42, ASK_DIR_FWD, 1,
					 8, ask_pppoe_session_snapshot()));
	mutex_unlock(&ask_flow_lifecycle_lock);
	ask_flow_pending_poll_fn(NULL);
	assert(!atomic_load(&publications) && !ask_flow_pending_count);
	enqueue(12, 1, ASK_VLANF_PPPOE_STRIP);
	atomic_store(&pppoe_port_armed, false);
	ask_flow_pending_poll_fn(NULL);
	assert(!atomic_load(&publications) && !ask_flow_pending_count);
	atomic_store(&pppoe_port_armed, true);
	WRITE_ONCE(ask_hw_port_family[0x10], ASK_FAM_V6);
	enqueue(16, 1, ASK_VLANF_PPPOE_INSERT);
	ask_flow_pending_poll_fn(NULL);
	assert(!atomic_load(&publications) && !ask_flow_pending_count);
	atomic_store(&pppoe_port_armed, false);
	WRITE_ONCE(ask_hw_port_family[0x10], ASK_FAM_V4 | ASK_FAM_V6);
	enqueue(13, 1, 0);
	ask_flow_pending_poll_fn(NULL);
	assert(atomic_load(&publications) == 1);

	reset_state();
	assert(ask_pppoe_netdev_event(NULL, NETDEV_DOWN, &non_ppp) == NOTIFY_DONE);
	assert(!scheduled_work && !ask_pppoe_session_epoch);
	ask_pppoe_events_live = false;
	assert(ask_pppoe_netdev_event(NULL, NETDEV_DOWN, &ppp) == NOTIFY_DONE);
	assert(!scheduled_work && !ask_pppoe_session_epoch);

	reset_state();
	ask_pppoe_session_epoch = U64_MAX;
	enqueue(14, 1, ASK_VLANF_PPPOE_STRIP);
	notify_pppoe_down();
	assert(ask_pppoe_session_epoch_exhausted && error_logs);
	atomic_store(&pppoe_armed, false);
	assert(ask_flow_pppoe_flush_sync() == -EOVERFLOW);
	assert(!ask_flow_pending_count && !ask_pppoe_sessions_usable());

	reset_state();
	enqueue(15, 1, ASK_VLANF_PPPOE_STRIP);
	atomic_store(&live_pppoe, true);
	notify_pppoe_down();
	withdrawal_rc = -ETIMEDOUT;
	ask_pppoe_flush_fn(NULL);
	assert(!ask_flow_pending_count && atomic_load(&live_pppoe) && error_logs &&
	       !ask_pppoe_sessions_usable());

	reset_state();
	list_add_tail(&retained_orphan, &ask_fe_orphans);
	notify_pppoe_down();
	ask_pppoe_flush_fn(NULL);
	assert(!ask_pppoe_sessions_usable() && error_logs);
	atomic_store(&pppoe_armed, false);
	assert(ask_flow_pppoe_flush_sync() == -EBUSY);
	assert(!ask_pppoe_sessions_usable());
	list_del(&retained_orphan);

	reset_state();
	notify_pppoe_down();
	identity_lost = true;
	ask_pppoe_flush_fn(NULL);
	assert(ask_pppoe_withdraw_unresolved && error_logs &&
	       !ask_pppoe_sessions_usable());
	atomic_store(&pppoe_armed, false);
	assert(ask_flow_pppoe_flush_sync() == -ESTALE);
}

static bool probe(const char *name)
{
	if (!strcmp(name, "neigh-session"))
		return detached_session_replay(false, ASK_VLANF_PPPOE_STRIP, false);
	if (!strcmp(name, "poll-session"))
		return detached_session_replay(true, ASK_VLANF_PPPOE_INSERT, false);
	if (!strcmp(name, "session-publish"))
		return session_during_publish();
	if (!strcmp(name, "session-repark"))
		return session_during_repark();
	if (!strcmp(name, "session-dirty"))
		return session_withdrawal_admission();
	if (!strcmp(name, "session-requeue"))
		return session_event_during_withdrawal(false);
	if (!strcmp(name, "session-sync-race"))
		return session_event_during_withdrawal(true);
	if (!strcmp(name, "session-family"))
		return session_port_family_disable_reenable();
	if (!strcmp(name, "neigh-family"))
		return detached_family_replay(false);
	if (!strcmp(name, "poll-family"))
		return detached_family_replay(true);
	if (!strcmp(name, "family-repark"))
		return family_during_repark();
	if (!strcmp(name, "neigh-pppoe"))
		return detached_replay(false, ASK_VLANF_PPPOE_STRIP, true, false, false);
	if (!strcmp(name, "neigh-repark"))
		return detached_replay(false, ASK_VLANF_PPPOE_STRIP, true, true, false);
	if (!strcmp(name, "poll-pppoe"))
		return detached_replay(true, ASK_VLANF_PPPOE_INSERT, true, false, false);
	if (!strcmp(name, "poll-vlan"))
		return detached_replay(true, ASK_VLANF_PUSH, false, false, false);
	abort();
}

int main(int argc, char **argv)
{
	if (argc == 2)
		return probe(argv[1]) ? 0 : 1;
	test_queue_and_failure();
	assert(probe("neigh-pppoe"));
	assert(probe("neigh-repark"));
	assert(probe("poll-pppoe"));
	assert(probe("poll-vlan"));
	assert(detached_replay(false, ASK_VLANF_PUSH, false, false, false));
	assert(detached_replay(true, ASK_VLANF_PUSH | ASK_VLANF_PPPOE_INSERT,
			       true, false, false));
	assert(detached_replay(false, 0, true, false, true));
	assert(detached_replay(true, ASK_VLANF_PPPOE_STRIP, false, false, true));
	assert(detached_replay(false, ASK_VLANF_PPPOE_STRIP, false, true, true));
	assert(detached_session_replay(false, 0, true));
	assert(detached_session_replay(true, ASK_VLANF_PUSH, true));
	clear_pending();
	puts("pending capability epochs exclude queued and detached stale replays");
	return 0;
}
