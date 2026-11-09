/* Host mocks for the actual WGFP binding and reservation callbacks. */
#include <assert.h>
#include <ctype.h>
#include <errno.h>
#include <pthread.h>
#include <stdbool.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/types.h>

#define WGFP_OWNER_TOKEN_LEN 32
#define WGFP_OWNER_ALIAS_MAX 63
#define WGFP_MAX_BIND 8
#define WGFP_OWNER_ALIASES_SIZE (WGFP_MAX_BIND * (WGFP_OWNER_ALIAS_MAX + 1))
#define IFNAMSIZ 16
#define PAGE_SIZE 4096
#define GFP_KERNEL 0
#define NETREG_REGISTERED 1
#define NETDEV_UNREGISTER 2
#define NOTIFY_DONE 0
#define WGFP_AGE_PERIOD 1
#define pr_warn_ratelimited(...) ((void)0)
#define pr_err(...) ((void)0)
#define rcu_access_pointer(p) (p)
#define rcu_dereference(p) (p)
#define rcu_dereference_protected(p, c) (p)
#define rcu_assign_pointer(p, v) ((p) = (v))
#define RCU_INIT_POINTER(p, v) ((p) = (v))
#define READ_ONCE(x) (x)
#define WRITE_ONCE(x, v) ((x) = (v))
#define lockdep_is_held(x) 1
#define rcu_read_lock() ((void)0)
#define rcu_read_unlock() ((void)0)
#define set_bit(n, p) __atomic_fetch_or((p), 1UL << (n), __ATOMIC_SEQ_CST)
#define test_bit(n, p) ((__atomic_load_n((p), __ATOMIC_SEQ_CST) >> (n)) & 1UL)
#define pr_info(...) ((void)0)
#define mutex_lock(lock) pthread_mutex_lock(lock)
#define mutex_unlock(lock) pthread_mutex_unlock(lock)

struct kernel_param { int unused; };
struct work_struct { int unused; };
struct notifier_block { int unused; };
struct rtnl_link_ops { const char *kind; };
struct net_device {
	char name[IFNAMSIZ];
	int reg_state, references, netns;
	struct rtnl_link_ops *rtnl_link_ops;
};
struct wgfp_binds {
	unsigned int n;
	struct net_device *wgdev[WGFP_MAX_BIND];
	unsigned long invalidated;
	bool stopping;
};
struct netdev_notifier_info { struct net_device *dev; };

static pthread_mutex_t wgfp_lab_lock = PTHREAD_MUTEX_INITIALIZER;
static char wgfp_peer_owner[WGFP_OWNER_TOKEN_LEN + 1];
static char wgfp_owner_aliases[WGFP_OWNER_ALIASES_SIZE];
static char wgfp_peer_cfg[WGFP_MAX_BIND * (IFNAMSIZ + 1) + 8] = "off";
static char wgfp_lab_cfg[16] = "off";
static bool wgfp_lab_on;
static struct wgfp_binds *wgfp_bs;
static int init_net, other_net, wgfp_peer_ops, wgfp_netdev_nb, wgfp_age_work;
static struct work_struct wgfp_stop_work;
static int starts, stops, lab_starts, register_error, unregister_during_lookup;
static int flow_flush_error, module_refs;
static bool alias_two_is_first, move_during_lookup, event_during_registration;
static bool notifier_active, stop_work_pending, event_during_cancel;
static bool alias_reassign_in_register, alias_reassigned, published_during_registration;
static int notifier_register_error;
static struct rtnl_link_ops wireguard_ops = { .kind = "wireguard" };
static struct net_device devices[2];
static char aliases[2][WGFP_OWNER_ALIAS_MAX + 1];

static int wgfp_netdev_event(struct notifier_block *nb, unsigned long event,
			     void *ptr);

static void *kzalloc(size_t n, int flags) { return calloc(1, n); }
static char *kstrdup(const char *s, int flags) { return strdup(s); }
static void kfree(void *p) { free(p); }
static ssize_t strscpy(char *dst, const char *src, size_t size)
{
	size_t n = strlen(src);
	if (n >= size) {
		if (size) {
			memcpy(dst, src, size - 1);
			dst[size - 1] = '\0';
		}
		return -E2BIG;
	}
	memcpy(dst, src, n + 1);
	return n;
}
static size_t strlcat(char *dst, const char *src, size_t size)
{
	size_t d = strlen(dst), s = strlen(src);
	if (d < size) {
		size_t n = s < size - d - 1 ? s : size - d - 1;
		memcpy(dst + d, src, n);
		dst[d + n] = '\0';
	}
	return d + s;
}
static char *strim(char *s)
{
	char *end;
	while (isspace((unsigned char)*s))
		s++;
	end = s + strlen(s);
	while (end > s && isspace((unsigned char)end[-1]))
		*--end = '\0';
	return s;
}
static int scnprintf(char *buf, size_t size, const char *format, ...)
{
	va_list ap;
	int n;
	va_start(ap, format);
	n = vsnprintf(buf, size, format, ap);
	va_end(ap);
	return n >= (int)size ? (int)size - 1 : n;
}
static struct net_device *dev_get_by_name(int *net, const char *name)
{
	unsigned int i;
	for (i = 0; i < 2; i++) {
		if (!strcmp(name, devices[i].name) || !strcmp(name, aliases[i])) {
			struct net_device *dev = &devices[i];
			if (i == 1 && alias_two_is_first && !strcmp(name, aliases[i]))
				dev = &devices[0];
			dev->references++;
			if (unregister_during_lookup)
				dev->reg_state = 2;
			if (move_during_lookup)
				dev->netns = 1;
			return dev;
		}
	}
	return NULL;
}
static struct net_device *__dev_get_by_name(int *net, const char *name)
{
	unsigned int i;

	for (i = 0; i < 2; i++) {
		if (!strcmp(name, devices[i].name) || !strcmp(name, aliases[i])) {
			if (i == 0 && alias_reassigned && !strcmp(name, aliases[i]))
				return &devices[1];
			if (i == 1 && alias_two_is_first && !strcmp(name, aliases[i]))
				return &devices[0];
			return &devices[i];
		}
	}
	return NULL;
}
static void rtnl_lock(void) {}
static void rtnl_unlock(void) {}
static void dev_put(struct net_device *dev) { dev->references--; }
static int dpaa_wgfp_register(void *ops, void *priv)
{
	if (register_error)
		return register_error;
	starts++;
	return 0;
}
static void dpaa_wgfp_unregister(void) { stops++; }
static int register_netdevice_notifier(int *nb)
{
	struct netdev_notifier_info event = { .dev = &devices[0] };

	published_during_registration = wgfp_bs != NULL;
	if (notifier_register_error)
		return notifier_register_error;
	notifier_active = true;
	if (event_during_registration) {
		wgfp_netdev_event(NULL, NETDEV_UNREGISTER, &event);
		devices[0].netns = 1;
	}
	if (alias_reassign_in_register)
		alias_reassigned = true;
	return 0;
}
static void unregister_netdevice_notifier(int *nb)
{
	assert(notifier_active);
	notifier_active = false;
}
static void synchronize_rcu(void) {}
static int wgfp_flows_flush(void) { return flow_flush_error; }
static void wgfp_psts_reap(bool all) {}
static void wgfp_rflows_reap(bool all) {}
static void wgfp_nf_register(void) {}
static void wgfp_nf_unregister(void) {}
static struct work_struct wgfp_eg_work;
static void schedule_delayed_work(int *work, int delay) {}
static void schedule_work(struct work_struct *work) { stop_work_pending = true; }
static void cancel_work_sync(struct work_struct *work)
{
	struct netdev_notifier_info event = { .dev = &devices[0] };

	if (work == &wgfp_eg_work)
		return;
	stop_work_pending = false;
	if (notifier_active && event_during_cancel)
		wgfp_netdev_event(NULL, NETDEV_UNREGISTER, &event);
}
static int *dev_net(struct net_device *dev) { return dev->netns ? &other_net : &init_net; }
static struct net_device *netdev_notifier_info_to_dev(void *ptr)
{
	return ((struct netdev_notifier_info *)ptr)->dev;
}
static void wgfp_lab_stop(void) { wgfp_lab_on = false; }
static int wgfp_lab_start(const char *cfg) { lab_starts++; wgfp_lab_on = true; return 0; }
static bool try_module_get(void *module) { module_refs++; return true; }
static void module_put(void *module) { assert(module_refs > 0); module_refs--; }
#define THIS_MODULE NULL
static int wgfp_peer_stop(void);
static int wgfp_peer_start(char *names, bool distinct);

#include "wgfp_peer_lease_body.h"

static const char token_a[] = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
static const char token_b[] = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";

static int command(const char *action, const char *token, const char *extra)
{
	char input[1024];
	snprintf(input, sizeof(input), "%s %s%s%s", action, token,
		 extra ? " " : "", extra ? extra : "");
	return wgfp_peer_lease_set(input, NULL);
}

static void reset(void)
{
	pthread_mutex_lock(&wgfp_lab_lock);
	assert(wgfp_peer_stop() == 0);
	pthread_mutex_unlock(&wgfp_lab_lock);
	assert(module_refs == 0);
	memset(devices, 0, sizeof(devices));
	strcpy(devices[0].name, "wg0");
	strcpy(devices[1].name, "wg1");
	devices[0].reg_state = devices[1].reg_state = NETREG_REGISTERED;
	devices[0].rtnl_link_ops = devices[1].rtnl_link_ops = &wireguard_ops;
	snprintf(aliases[0], sizeof(aliases[0]), "wgo-%s-dut", token_a);
	snprintf(aliases[1], sizeof(aliases[1]), "wgo-%s-d1", token_a);
	wgfp_peer_owner[0] = wgfp_owner_aliases[0] = '\0';
	strcpy(wgfp_peer_cfg, "off");
	strcpy(wgfp_lab_cfg, "off");
	wgfp_lab_on = false;
	starts = stops = lab_starts = register_error = unregister_during_lookup = 0;
	alias_two_is_first = move_during_lookup = event_during_registration = false;
	notifier_active = stop_work_pending = event_during_cancel = false;
	alias_reassign_in_register = alias_reassigned = published_during_registration = false;
	notifier_register_error = 0;
	flow_flush_error = 0;
	module_refs = 0;
}

static void reservation_and_idempotent_cleanup(void)
{
	char output[PAGE_SIZE], names[128];

	reset();
	assert(wgfp_peer_lease_set("reserve xyz", NULL) == -EINVAL);
	assert(wgfp_peer_lease_set("reserve AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA", NULL) == -EINVAL);
	assert(command("reserve", token_a, "extra") == -EINVAL);
	assert(command("reserve", token_a, NULL) == 0);
	assert(command("reserve", token_a, NULL) == 0);
	assert(command("reserve", token_b, NULL) == -EBUSY);
	assert(wgfp_peer_cfg_set("wg0", NULL) == -EBUSY);
	assert(wgfp_lab_cfg_set("start", NULL) == -EBUSY && lab_starts == 0);
	assert(command("bind", token_b, aliases[0]) == -EINVAL);
	assert(command("bind", token_a, "wg0") == -EINVAL);
	snprintf(names, sizeof(names), "wgo-%s-missing", token_a);
	assert(command("bind", token_a, names) == -ENODEV);
	assert(!strcmp(wgfp_peer_owner, token_a) && !wgfp_bs);
	snprintf(names, sizeof(names), "%s %s", aliases[0], aliases[0]);
	assert(command("bind", token_a, names) == -EINVAL);
	snprintf(names, sizeof(names), "%s %s", aliases[0], aliases[1]);
	alias_two_is_first = true;
	assert(command("bind", token_a, names) == -EINVAL);
	assert(!devices[0].references && !wgfp_bs);
	alias_two_is_first = false;
	assert(command("bind", token_a, aliases[0]) == 0);
	assert(command("bind", token_a, aliases[0]) == 0 && starts == 1);
	assert(command("bind", token_a, aliases[1]) == -EBUSY);
	assert(command("release", token_a, NULL) == -EBUSY);
	wgfp_peer_lease_get(output, NULL);
	assert(strstr(output, "owned=aaaaaaaa") && strstr(output, "dev=wg0"));
	assert(command("stop", token_b, NULL) == -EPERM);
	assert(command("stop", token_a, NULL) == 0);
	assert(command("stop", token_a, NULL) == 0 && stops == 1);
	assert(module_refs == 0);
	assert(wgfp_peer_cfg_set("wg1", NULL) == -EBUSY);
	assert(command("bind", token_a, aliases[0]) == 0 && starts == 2);
	assert(command("stop", token_a, NULL) == 0);
	assert(command("release", token_a, NULL) == 0);
	wgfp_peer_lease_get(output, NULL);
	assert(!strcmp(output, "unowned\n"));
	assert(!devices[0].references && !devices[1].references);
}

static void failed_flow_flush_retains_binding_consumer_and_module(void)
{
	struct netdev_notifier_info event = { .dev = &devices[0] };

	reset();
	assert(command("reserve", token_a, NULL) == 0);
	assert(command("bind", token_a, aliases[0]) == 0);
	assert(module_refs == 1);
	flow_flush_error = -ETIMEDOUT;
	assert(command("stop", token_a, NULL) == -ETIMEDOUT);
	assert(wgfp_bs && wgfp_bs->stopping && notifier_active);
	assert(stops == 0 && devices[0].references == 1 && module_refs == 1);
	assert(!strcmp(wgfp_peer_cfg, "wg0"));
	assert(!strcmp(wgfp_peer_owner, token_a));
	assert(!strcmp(wgfp_owner_aliases, aliases[0]));
	assert(wgfp_peer_cfg_set("wg1", NULL) == -EBUSY);
	assert(wgfp_bs && stops == 0 && module_refs == 1);
	flow_flush_error = 0;
	assert(command("stop", token_a, NULL) == 0);
	assert(!wgfp_bs && !notifier_active && stops == 1);
	assert(!devices[0].references && !module_refs);
	assert(command("release", token_a, NULL) == 0);

	assert(command("reserve", token_a, NULL) == 0);
	assert(command("bind", token_a, aliases[0]) == 0);
	devices[0].reg_state = 2;
	flow_flush_error = -ETIMEDOUT;
	assert(wgfp_netdev_event(NULL, NETDEV_UNREGISTER, &event) == NOTIFY_DONE);
	wgfp_stop_work_fn(NULL);
	assert(wgfp_bs && wgfp_bs->stopping && notifier_active);
	assert(stops == 1 && devices[0].references == 1 && module_refs == 1);
	assert(!strcmp(wgfp_peer_cfg, "wg0"));
	flow_flush_error = 0;
	assert(command("stop", token_a, NULL) == 0);
	assert(!wgfp_bs && !notifier_active && stops == 2);
	assert(!devices[0].references && !module_refs);
	assert(command("release", token_a, NULL) == 0);
}

static void preserves_ownership_on_failures_and_unregister(void)
{
	char names[128];
	struct netdev_notifier_info event = { .dev = &devices[0] };

	reset();
	assert(wgfp_lab_cfg_set("start", NULL) == 0);
	assert(command("reserve", token_a, NULL) == -EBUSY);
	assert(wgfp_lab_cfg_set("off", NULL) == 0);
	assert(wgfp_peer_cfg_set("wg0", NULL) == 0);
	assert(command("reserve", token_a, NULL) == -EBUSY);
	assert(wgfp_peer_cfg_set("off", NULL) == 0);
	assert(command("reserve", token_a, NULL) == 0);
	register_error = -EIO;
	assert(command("bind", token_a, aliases[0]) == -EIO);
	assert(!strcmp(wgfp_peer_owner, token_a) && !wgfp_bs);
	register_error = 0;
	notifier_register_error = -EIO;
	assert(command("bind", token_a, aliases[0]) == -EIO);
	assert(!wgfp_bs && !devices[0].references && !notifier_active);
	notifier_register_error = 0;
	unregister_during_lookup = 1;
	assert(command("bind", token_a, aliases[0]) == -ENODEV);
	assert(!wgfp_bs && !devices[0].references);
	unregister_during_lookup = 0;
	devices[0].reg_state = NETREG_REGISTERED;
	move_during_lookup = true;
	assert(command("bind", token_a, aliases[0]) == -ENODEV);
	assert(!wgfp_bs && !devices[0].references);
	move_during_lookup = false;
	devices[0].netns = 0;
	event_during_registration = true;
	assert(command("bind", token_a, aliases[0]) == -ENODEV);
	assert(!wgfp_bs && !devices[0].references && !published_during_registration);
	event_during_registration = false;
	devices[0].netns = 0;
	alias_reassign_in_register = true;
	assert(command("bind", token_a, aliases[0]) == -ENODEV);
	assert(!wgfp_bs && !devices[0].references && !published_during_registration);
	alias_reassign_in_register = alias_reassigned = false;
	snprintf(names, sizeof(names), "%s %s", aliases[0], aliases[1]);
	assert(command("bind", token_a, names) == 0);
	assert(!published_during_registration);
	assert(wgfp_netdev_event(NULL, NETDEV_UNREGISTER, &event) == NOTIFY_DONE);
	wgfp_stop_work_fn(NULL);
	assert(!wgfp_bs && !strcmp(wgfp_peer_owner, token_a));
	assert(!strcmp(wgfp_peer_cfg, "off") && !devices[0].references &&
	       !devices[1].references);
	assert(wgfp_peer_cfg_set("wg1", NULL) == -EBUSY);
	assert(command("stop", token_a, NULL) == 0);
	assert(command("release", token_a, NULL) == 0);
}

static void stale_stop_work_preserves_new_binding(void)
{
	struct netdev_notifier_info event = { .dev = &devices[0] };

	reset();
	assert(command("reserve", token_a, NULL) == 0);
	assert(command("bind", token_a, aliases[0]) == 0);
	assert(wgfp_netdev_event(NULL, NETDEV_UNREGISTER, &event) == NOTIFY_DONE);
	assert(stop_work_pending);
	assert(command("stop", token_a, NULL) == 0);
	assert(command("bind", token_a, aliases[1]) == 0);
	wgfp_stop_work_fn(NULL);
	assert(wgfp_bs && wgfp_bs->wgdev[0] == &devices[1]);
	assert(!devices[0].references && devices[1].references == 1);
	assert(command("stop", token_a, NULL) == 0);
	assert(command("release", token_a, NULL) == 0);
	assert(!devices[1].references);
}

static void unregister_ignores_unrelated_devices(void)
{
	struct netdev_notifier_info event = { .dev = &devices[1] };

	reset();
	assert(command("reserve", token_a, NULL) == 0);
	assert(command("bind", token_a, aliases[0]) == 0);
	assert(wgfp_netdev_event(NULL, NETDEV_UNREGISTER, &event) == NOTIFY_DONE);
	assert(!stop_work_pending && wgfp_bs);
	event.dev = &devices[0];
	devices[0].reg_state = 2;
	assert(wgfp_netdev_event(NULL, NETDEV_UNREGISTER, &event) == NOTIFY_DONE);
	assert(stop_work_pending);
	wgfp_stop_work_fn(NULL);
	assert(!wgfp_bs && !devices[0].references);
	assert(!strcmp(wgfp_peer_owner, token_a));
	assert(command("release", token_a, NULL) == 0);
}

static void uninit_cannot_leave_late_notifier_work(void)
{
	reset();
	assert(command("reserve", token_a, NULL) == 0);
	assert(command("bind", token_a, aliases[0]) == 0);
	event_during_cancel = true;
	wg_fastpath_uninit();
	assert(!notifier_active && !stop_work_pending && !devices[0].references);
}

struct contender {
	pthread_barrier_t *barrier;
	char token[33];
	int result;
};

static void *reserve_worker(void *arg)
{
	struct contender *c = arg;
	pthread_barrier_wait(c->barrier);
	c->result = command("reserve", c->token, NULL);
	return NULL;
}

static void concurrent_reservations_have_one_winner(void)
{
	enum { N = 16 };
	struct contender contenders[N];
	pthread_t threads[N];
	pthread_barrier_t barrier;
	int i, winners = 0;

	reset();
	assert(!pthread_barrier_init(&barrier, NULL, N));
	for (i = 0; i < N; i++) {
		contenders[i].barrier = &barrier;
		snprintf(contenders[i].token, sizeof(contenders[i].token), "%032x", i + 1);
		assert(!pthread_create(&threads[i], NULL, reserve_worker, &contenders[i]));
	}
	for (i = 0; i < N; i++) {
		assert(!pthread_join(threads[i], NULL));
		if (!contenders[i].result) {
			winners++;
			assert(!strcmp(wgfp_peer_owner, contenders[i].token));
		} else {
			assert(contenders[i].result == -EBUSY);
		}
	}
	assert(winners == 1);
	assert(command("release", wgfp_peer_owner, NULL) == 0);
	pthread_barrier_destroy(&barrier);
}

static void *legacy_worker(void *arg)
{
	struct contender *c = arg;
	pthread_barrier_wait(c->barrier);
	c->result = wgfp_peer_cfg_set("wg0", NULL);
	return NULL;
}

static void legacy_binding_and_reservation_cannot_both_win(void)
{
	struct contender reserve, legacy;
	pthread_t first, second;
	pthread_barrier_t barrier;
	int i;

	for (i = 0; i < 100; i++) {
		reset();
		assert(!pthread_barrier_init(&barrier, NULL, 2));
		reserve.barrier = legacy.barrier = &barrier;
		strcpy(reserve.token, token_a);
		assert(!pthread_create(&first, NULL, reserve_worker, &reserve));
		assert(!pthread_create(&second, NULL, legacy_worker, &legacy));
		assert(!pthread_join(first, NULL));
		assert(!pthread_join(second, NULL));
		assert((reserve.result == 0 && legacy.result == -EBUSY) ||
		       (reserve.result == -EBUSY && legacy.result == 0));
		assert(!(wgfp_peer_owner[0] && wgfp_bs));
		if (!reserve.result)
			assert(command("release", token_a, NULL) == 0);
		else
			assert(wgfp_peer_cfg_set("off", NULL) == 0);
		pthread_barrier_destroy(&barrier);
	}
}

int main(void)
{
	reservation_and_idempotent_cleanup();
	failed_flow_flush_retains_binding_consumer_and_module();
	preserves_ownership_on_failures_and_unregister();
	stale_stop_work_preserves_new_binding();
	unregister_ignores_unrelated_devices();
	uninit_cannot_leave_late_notifier_work();
	concurrent_reservations_have_one_winner();
	legacy_binding_and_reservation_cannot_both_win();
	return 0;
}
