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
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/rcupdate.h>

#include "dpaa_eth.h"
#include "dpaa_flavor.h"

static DEFINE_MUTEX(dpaa_flavor_lock);

static const struct dpaa_pcd_ops   __rcu *dpaa_pcd_ops_global;
static const struct dpaa_qmgmt_ops __rcu *dpaa_qmgmt_ops_global;

int dpaa_register_flavor_ops(const struct dpaa_pcd_ops *pcd_ops,
			     const struct dpaa_qmgmt_ops *qmgmt_ops)
{
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
	mutex_unlock(&dpaa_flavor_lock);
	return 0;
}
EXPORT_SYMBOL_GPL(dpaa_register_flavor_ops);

void dpaa_unregister_flavor_ops(void)
{
	mutex_lock(&dpaa_flavor_lock);
	rcu_assign_pointer(dpaa_pcd_ops_global,   NULL);
	rcu_assign_pointer(dpaa_qmgmt_ops_global, NULL);
	mutex_unlock(&dpaa_flavor_lock);
	synchronize_rcu();
}
EXPORT_SYMBOL_GPL(dpaa_unregister_flavor_ops);

void dpaa_priv_attach_flavor_ops(struct dpaa_priv *priv)
{
	rcu_read_lock();
	rcu_assign_pointer(priv->pcd_ops,
			   rcu_dereference(dpaa_pcd_ops_global));
	rcu_assign_pointer(priv->qmgmt_ops,
			   rcu_dereference(dpaa_qmgmt_ops_global));
	rcu_read_unlock();
}

void dpaa_priv_detach_flavor_ops(struct dpaa_priv *priv)
{
	rcu_assign_pointer(priv->pcd_ops,   NULL);
	rcu_assign_pointer(priv->qmgmt_ops, NULL);
	priv->flavor_priv = NULL;
}
