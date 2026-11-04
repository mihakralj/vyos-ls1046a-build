"""F-275: retry transient XSK detach failures without releasing pool state.

The DPAA flavor returns -EAGAIN when its RX port cannot yet be disabled. XSK
core must retain the pool/netdev association and retry later; otherwise the
UMEM and its DMA mapping can be released while FMan may still access them.
Failed bind rollback also transfers the pool's final reference to deferred
cleanup instead of freeing it while a detach retry is pending.
"""

import sys

HEADER = "include/net/xsk_buff_pool.h"
SOURCE = "net/xdp/xsk_buff_pool.c"
XSK_SOURCE = "net/xdp/xsk.c"


def replace_once(contents, old, new, path):
    count = contents.count(old)
    if count != 1:
        print(
            f"### F-275: FATAL: anchor in {path} found {count} times "
            f"(expected 1): {old[:80]!r}"
        )
        sys.exit(1)
    return contents.replace(old, new)


def patch_xsk_source(contents):
    def replace_one(old, new):
        nonlocal contents
        count = contents.count(old)
        if count != 1:
            print(
                "### F-275: FATAL: xsk.c anchor found "
                f"{count} times (expected 1): {old[:80]!r}"
            )
            sys.exit(1)
        contents = contents.replace(old, new, 1)

    replace_one(
        "\t\t\tif (err) {\n"
        "\t\t\t\txp_destroy(xs->pool);\n",
        "\t\t\tif (err) {\n"
        "\t\t\t\t/* F-275: defer failed-bind cleanup to pool worker. */\n"
        "\t\t\t\tif (err == -EAGAIN)\n"
        "\t\t\t\t\txp_put_pool(xs->pool);\n"
        "\t\t\t\telse\n"
        "\t\t\t\t\txp_destroy(xs->pool);\n",
    )
    replace_one(
        "\t\tif (err) {\n"
        "\t\t\txp_destroy(xs->pool);\n",
        "\t\tif (err) {\n"
        "\t\t\t/* F-275: defer failed-bind cleanup to pool worker. */\n"
        "\t\t\tif (err == -EAGAIN)\n"
        "\t\t\t\txp_put_pool(xs->pool);\n"
        "\t\t\telse\n"
        "\t\t\t\txp_destroy(xs->pool);\n",
    )
    return contents


try:
    with open(HEADER) as stream:
        header = stream.read()
    with open(SOURCE) as stream:
        source = stream.read()
except FileNotFoundError as error:
    print(f"### F-275: FATAL: {error.filename} not found")
    sys.exit(1)

header_marker = "F-275: retry XSK detach while driver quiesces"
source_marker = "F-275: retain the XSK pool while detach is retryable"
xsk_marker = "F-275: defer failed-bind cleanup to pool worker"
header_applied = header_marker in header
source_applied = source_marker in source
try:
    with open(XSK_SOURCE) as stream:
        xsk_source = stream.read()
except FileNotFoundError as error:
    print(f"### F-275: FATAL: {error.filename} not found")
    sys.exit(1)

if header_applied and source_applied and xsk_marker in xsk_source:
    print("### F-275: already applied")
    sys.exit(0)
if header_applied != source_applied:
    print("### F-275: FATAL: partial application detected")
    sys.exit(1)
if header_applied:
    if xsk_marker in xsk_source:
        print("### F-275: FATAL: unexpected XSK caller marker")
        sys.exit(1)
    xsk_source = patch_xsk_source(xsk_source)
    rollback = (
        "\tif (xp_disable_drv_zc(pool) == -EAGAIN) {\n"
        "\t\tschedule_delayed_work(&pool->work, HZ);\n"
        "\t\treturn err;\n"
        "\t}\n"
    )
    rollback_retry = (
        "\tif (xp_disable_drv_zc(pool) == -EAGAIN) {\n"
        "\t\tschedule_delayed_work(&pool->work, HZ);\n"
        "\t\treturn -EAGAIN;\n"
        "\t}\n"
    )
    if rollback in source:
        source = source.replace(rollback, rollback_retry, 1)
        with open(SOURCE, "w") as stream:
            stream.write(source)
    elif rollback_retry not in source:
        print("### F-275: FATAL: failed-bind retry return anchor not found")
        sys.exit(1)
    with open(XSK_SOURCE, "w") as stream:
        stream.write(xsk_source)
    print("### F-275: deferred failed-bind pool cleanup")
    sys.exit(0)
else:
    if xsk_marker in xsk_source:
        print("### F-275: FATAL: partial application detected")
        sys.exit(1)

header = replace_once(
    header,
    "#include <linux/bpf.h>\n",
    "#include <linux/bpf.h>\n#include <linux/workqueue.h>\n",
    HEADER,
)
header = replace_once(
    header,
    "\tstruct work_struct work;\n",
    "\tstruct delayed_work work;\n",
    HEADER,
)
header = replace_once(
    header,
    "void xp_clear_dev(struct xsk_buff_pool *pool);\n",
    "/* F-275: -EAGAIN keeps the pool attached and requests a retry. */\n"
    "int xp_clear_dev(struct xsk_buff_pool *pool);\n",
    HEADER,
)
header = header.replace(
    "struct delayed_work work;\n",
    "struct delayed_work work; /* F-275: retryable ZC detach */\n",
    1,
)
header = header.replace(
    "/* F-275: -EAGAIN keeps the pool attached and requests a retry. */\n",
    f"/* {header_marker}: -EAGAIN keeps the pool attached and requests a retry. */\n",
    1,
)

source = replace_once(
    source,
    "#include <linux/netdevice.h>\n",
    "#include <linux/jiffies.h>\n#include <linux/netdevice.h>\n",
    SOURCE,
)
source = replace_once(
    source,
    "struct xsk_buff_pool *xp_create_and_assign_umem(struct xdp_sock *xs,\n",
    "static void xp_release_deferred(struct work_struct *work);\n\n"
    "struct xsk_buff_pool *xp_create_and_assign_umem(struct xdp_sock *xs,\n",
    SOURCE,
)
source = replace_once(
    source,
    "\trefcount_set(&pool->users, 1);\n",
    "\trefcount_set(&pool->users, 1);\n"
    "\tINIT_DELAYED_WORK(&pool->work, xp_release_deferred);\n",
    SOURCE,
)
source = replace_once(
    source,
    "static void xp_disable_drv_zc(struct xsk_buff_pool *pool)\n"
    "{\n"
    "\tstruct netdev_bpf bpf;\n"
    "\tint err;\n\n"
    "\tASSERT_RTNL();\n\n"
    "\tif (pool->umem->zc) {\n"
    "\t\tbpf.command = XDP_SETUP_XSK_POOL;\n"
    "\t\tbpf.xsk.pool = NULL;\n"
    "\t\tbpf.xsk.queue_id = pool->queue_id;\n\n"
    "\t\terr = pool->netdev->netdev_ops->ndo_bpf(pool->netdev, &bpf);\n\n"
    "\t\tif (err)\n"
    "\t\t\tWARN(1, \"Failed to disable zero-copy!\\n\");\n"
    "\t}\n"
    "}\n",
    "static int xp_disable_drv_zc(struct xsk_buff_pool *pool)\n"
    "{\n"
    "\tstruct netdev_bpf bpf;\n"
    "\tint err;\n\n"
    "\tASSERT_RTNL();\n\n"
    "\tif (!pool->umem->zc)\n"
    "\t\treturn 0;\n\n"
    "\tbpf.command = XDP_SETUP_XSK_POOL;\n"
    "\tbpf.xsk.pool = NULL;\n"
    "\tbpf.xsk.queue_id = pool->queue_id;\n\n"
    "\terr = pool->netdev->netdev_ops->ndo_bpf(pool->netdev, &bpf);\n"
    "\tif (err)\n"
    "\t\tpr_warn_ratelimited(\"Failed to disable zero-copy: %d\\n\", err);\n\n"
    "\treturn err;\n"
    "}\n",
    SOURCE,
)
source = replace_once(
    source,
    "err_unreg_xsk:\n"
    "\txp_disable_drv_zc(pool);\n"
    "err_unreg_pool:\n",
    "err_unreg_xsk:\n"
    "\tif (xp_disable_drv_zc(pool) == -EAGAIN) {\n"
    "\t\tschedule_delayed_work(&pool->work, HZ);\n"
    "\t\treturn -EAGAIN;\n"
    "\t}\n"
    "err_unreg_pool:\n",
    SOURCE,
)
source = replace_once(
    source,
    "void xp_clear_dev(struct xsk_buff_pool *pool)\n"
    "{\n"
    "\tstruct net_device *netdev = pool->netdev;\n\n"
    "\tif (!pool->netdev)\n"
    "\t\treturn;\n\n"
    "\tnetdev_lock_ops(netdev);\n"
    "\txp_disable_drv_zc(pool);\n"
    "\txsk_clear_pool_at_qid(pool->netdev, pool->queue_id);\n"
    "\tpool->netdev = NULL;\n"
    "\tnetdev_unlock_ops(netdev);\n"
    "\tdev_put(netdev);\n"
    "}\n",
    "int xp_clear_dev(struct xsk_buff_pool *pool)\n"
    "{\n"
    "\tstruct net_device *netdev = pool->netdev;\n"
    "\tint err;\n\n"
    "\tif (!netdev)\n"
    "\t\treturn 0;\n\n"
    "\tnetdev_lock_ops(netdev);\n"
    "\terr = xp_disable_drv_zc(pool);\n"
    "\tif (err == -EAGAIN) {\n"
    "\t\tnetdev_unlock_ops(netdev);\n"
    "\t\tschedule_delayed_work(&pool->work, HZ);\n"
    "\t\treturn err;\n"
    "\t}\n"
    "\txsk_clear_pool_at_qid(netdev, pool->queue_id);\n"
    "\tpool->netdev = NULL;\n"
    "\tnetdev_unlock_ops(netdev);\n"
    "\tdev_put(netdev);\n"
    "\treturn 0;\n"
    "}\n",
    SOURCE,
)
source = replace_once(
    source,
    "static void xp_release_deferred(struct work_struct *work)\n"
    "{\n"
    "\tstruct xsk_buff_pool *pool = container_of(work, struct xsk_buff_pool,\n"
    "\t\t\t\t\t\t  work);\n\n"
    "\trtnl_lock();\n"
    "\txp_clear_dev(pool);\n"
    "\trtnl_unlock();\n\n"
    "\tif (pool->fq) {\n",
    "static void xp_release_deferred(struct work_struct *work)\n"
    "{\n"
    "\tstruct xsk_buff_pool *pool = container_of(to_delayed_work(work),\n"
    "\t\t\t\t\t\t\t  struct xsk_buff_pool, work);\n"
    "\tint err;\n\n"
    "\trtnl_lock();\n"
    "\terr = xp_clear_dev(pool);\n"
    "\trtnl_unlock();\n"
    "\tif (err == -EAGAIN || refcount_read(&pool->users))\n"
    "\t\treturn;\n\n"
    "\tif (pool->fq) {\n",
    SOURCE,
)
source = replace_once(
    source,
    "\t\tINIT_WORK(&pool->work, xp_release_deferred);\n"
    "\t\tschedule_work(&pool->work);\n",
    "\t\tschedule_delayed_work(&pool->work, 0);\n",
    SOURCE,
)
source = source.replace(
    "static int xp_disable_drv_zc(struct xsk_buff_pool *pool)\n",
    f"/* {source_marker}: only -EAGAIN is retried; other driver errors keep "
    "the legacy cleanup behavior. */\n"
    "static int xp_disable_drv_zc(struct xsk_buff_pool *pool)\n",
    1,
)

xsk_source = patch_xsk_source(xsk_source)

if not header_applied:
    with open(HEADER, "w") as stream:
        stream.write(header)
    with open(SOURCE, "w") as stream:
        stream.write(source)
with open(XSK_SOURCE, "w") as stream:
    stream.write(xsk_source)

print("### F-275: XSK core retains pool/netdev on retryable detach")
