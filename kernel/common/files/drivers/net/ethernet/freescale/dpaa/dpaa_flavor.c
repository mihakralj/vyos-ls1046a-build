// SPDX-License-Identifier: BSD-3-Clause OR GPL-2.0-or-later
/*
 * dpaa_flavor.c - flavor ops registration for fsl_dpa
 *
 * Two RCU-protected singletons (dpaa_pcd_ops, dpaa_qmgmt_ops) plus a single
 * registration mutex. At most one flavor module may be registered at a
 * time; second registrant gets -EBUSY. Unregister is synchronous and
 * waits for in-flight RCU readers via synchronize_rcu().
 *
 * Spec: specs/dpaa1-afxdp-modernization-spec.md sec 3 + sec 5.1 (M0).
 */

#include <linux/export.h>
#include <linux/list.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/rcupdate.h>
#include <linux/slab.h>
#include <linux/spinlock.h>

#include "dpaa_eth.h"
#include "dpaa_flavor.h"

static DEFINE_MUTEX(dpaa_flavor_lock);

/* Tracker node: one per probed dpaa_priv. Allocated in
 * dpaa_priv_attach_flavor_ops(), freed in dpaa_priv_detach_flavor_ops().
 * Stored as a private list in dpaa_flavor.c so we do not need to add a
 * struct list_head field to struct dpaa_priv (which would shift line
 * numbers in dpaa_eth.h and break follow-on patches that add their own
 * fields to struct dpaa_priv).
 */
struct dpaa_priv_tracker {
struct list_head node;
struct dpaa_priv *priv;
};

static LIST_HEAD(dpaa_priv_trackers);
static DEFINE_SPINLOCK(dpaa_priv_list_lock);

static const struct dpaa_pcd_ops   __rcu *dpaa_pcd_ops_global;
static const struct dpaa_qmgmt_ops __rcu *dpaa_qmgmt_ops_global;

int dpaa_register_flavor_ops(const struct dpaa_pcd_ops *pcd_ops,
     const struct dpaa_qmgmt_ops *qmgmt_ops)
{
struct dpaa_priv_tracker *t;
unsigned long flags;

if (!pcd_ops && !qmgmt_ops)
return -EINVAL;

mutex_lock(&dpaa_flavor_lock);
if (rcu_access_pointer(dpaa_pcd_ops_global) ||
    rcu_access_pointer(dpaa_qmgmt_ops_global)) {
mutex_unlock(&dpaa_flavor_lock);
return -EBUSY;
}
rcu_assign_pointer(dpaa_pcd_ops_global,   pcd_ops);
rcu_assign_pointer(dpaa_qmgmt_ops_global, qmgmt_ops);

/* Retro-attach to every priv that probed before us. */
spin_lock_irqsave(&dpaa_priv_list_lock, flags);
list_for_each_entry(t, &dpaa_priv_trackers, node) {
rcu_assign_pointer(t->priv->pcd_ops,   pcd_ops);
rcu_assign_pointer(t->priv->qmgmt_ops, qmgmt_ops);
}
spin_unlock_irqrestore(&dpaa_priv_list_lock, flags);
mutex_unlock(&dpaa_flavor_lock);
return 0;
}
EXPORT_SYMBOL_GPL(dpaa_register_flavor_ops);

void dpaa_unregister_flavor_ops(void)
{
struct dpaa_priv_tracker *t;
unsigned long flags;

mutex_lock(&dpaa_flavor_lock);
rcu_assign_pointer(dpaa_pcd_ops_global,   NULL);
rcu_assign_pointer(dpaa_qmgmt_ops_global, NULL);

/* Clear every per-priv pointer that referenced the
 * about-to-be-freed flavor-module text.
 */
spin_lock_irqsave(&dpaa_priv_list_lock, flags);
list_for_each_entry(t, &dpaa_priv_trackers, node) {
rcu_assign_pointer(t->priv->pcd_ops,   NULL);
rcu_assign_pointer(t->priv->qmgmt_ops, NULL);
t->priv->flavor_priv = NULL;
}
spin_unlock_irqrestore(&dpaa_priv_list_lock, flags);
mutex_unlock(&dpaa_flavor_lock);
synchronize_rcu();
}
EXPORT_SYMBOL_GPL(dpaa_unregister_flavor_ops);

void dpaa_priv_attach_flavor_ops(struct dpaa_priv *priv)
{
struct dpaa_priv_tracker *t;
unsigned long flags;

/* Allocate the tracker BEFORE taking the spinlock; kzalloc
 * may sleep. GFP_KERNEL is safe at probe time.
 */
t = kzalloc(sizeof(*t), GFP_KERNEL);
if (t) {
t->priv = priv;
spin_lock_irqsave(&dpaa_priv_list_lock, flags);
list_add(&t->node, &dpaa_priv_trackers);
spin_unlock_irqrestore(&dpaa_priv_list_lock, flags);
}

rcu_read_lock();
rcu_assign_pointer(priv->pcd_ops,
   rcu_dereference(dpaa_pcd_ops_global));
rcu_assign_pointer(priv->qmgmt_ops,
   rcu_dereference(dpaa_qmgmt_ops_global));
rcu_read_unlock();
}

void dpaa_priv_detach_flavor_ops(struct dpaa_priv *priv)
{
struct dpaa_priv_tracker *t, *tmp;
unsigned long flags;

/* Walk-and-prune the tracker list under the spinlock. */
spin_lock_irqsave(&dpaa_priv_list_lock, flags);
list_for_each_entry_safe(t, tmp, &dpaa_priv_trackers, node) {
if (t->priv == priv) {
list_del(&t->node);
kfree(t);
break;
}
}
spin_unlock_irqrestore(&dpaa_priv_list_lock, flags);

rcu_assign_pointer(priv->pcd_ops,   NULL);
rcu_assign_pointer(priv->qmgmt_ops, NULL);
priv->flavor_priv = NULL;
}