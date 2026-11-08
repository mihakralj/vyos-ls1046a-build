#!/usr/bin/env python3
"""Fault-test extracted DPAA offload FQ creation against a host QMan model."""

import argparse
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "tests/dut/dpaa_offload_fq.c"


def function(source, prefix):
    match = re.search(rf"(?m)^{re.escape(prefix)}\([^;]*?\n\{{\n", source)
    if not match:
        raise ValueError(f"{prefix}: function not found")
    return source[match.start():source.index("\n}\n", match.end()) + 3]


def check_source_contract(dpaa, qman, net, netdev, rtnl, rtnl_header):
    init = function(qman, "int qman_init_fq")
    commit = init.find("qm_mc_commit(")
    timeout = init.find("qm_mc_result_timeout(", commit)
    error = init.find("ret = -ETIMEDOUT;", timeout)
    if min(commit, timeout, error) < 0 or not commit < timeout < error:
        raise ValueError("QMan INITFQ timeout ordering changed; review the model")
    create = function(qman, "int qman_create_fq")
    destroy = function(qman, "void qman_destroy_fq")
    if ("fq->state = qman_fq_state_oos;" not in create or
            "fq->idx = fqid * 2;" not in create or
            "fq_table[fq->idx] = fq;" not in create or
            "fq_table[fq->idx] = NULL;" not in destroy):
        raise ValueError("QMan FQ lookup ownership changed; review the model")

    setup = function(dpaa, "static inline void dpaa_setup_egress")
    if ("if (port)" not in setup or "QMAN_FQ_FLAG_TO_DCPORTAL" not in setup or
            "QMAN_FQ_FLAG_NO_MODIFY" not in setup):
        raise ValueError("DPAA egress port setup changed; review the model")
    allocator = function(dpaa, "int dpaa_alloc_offload_tx_fq")
    lock = allocator.find("rtnl_lock();")
    create_fq = allocator.find("dpaa_fq_init(")
    unlock = allocator.find("rtnl_unlock();")
    if min(lock, create_fq, unlock) < 0 or not lock < create_fq < unlock:
        raise ValueError("DPAA offload allocation locking changed; review the model")
    remove = function(dpaa, "static void dpaa_remove")
    unregister = remove.find("unregister_netdev(net_dev);")
    release = remove.find("dpaa_fq_free(dev,")
    if min(unregister, release) < 0 or unregister >= release:
        raise ValueError("DPAA remove ordering changed; review the owner model")
    unregister = function(net, "void unregister_netdev")
    lock = unregister.find("rtnl_net_dev_lock(dev);")
    unregister_call = unregister.find("unregister_netdevice(dev);")
    unlock = unregister.find("rtnl_net_dev_unlock(dev);")
    if min(lock, unregister_call, unlock) < 0 or not lock < unregister_call < unlock:
        raise ValueError("unregister_netdev ordering changed; review the owner model")
    if ("rtnl_net_lock(net);" not in function(net, "static void rtnl_net_dev_lock") or
            "rtnl_net_unlock(net);" not in function(net, "static void rtnl_net_dev_unlock") or
            "rtnl_lock();" not in function(rtnl, "void rtnl_net_lock") or
            "rtnl_unlock();" not in function(rtnl, "void rtnl_net_unlock") or
            "rtnl_lock();" not in function(rtnl_header, "static inline void rtnl_net_lock") or
            "rtnl_unlock();" not in function(rtnl_header, "static inline void rtnl_net_unlock") or
            "netdev_run_todo();" not in function(rtnl, "void rtnl_unlock") or
            "__dev_hold(dev);" not in function(netdev, "static inline void netdev_hold")):
        raise ValueError("netdev locking or reference chain changed; review the model")
    wait = function(net, "static struct net_device *netdev_wait_allrefs_any")
    todo = function(net, "void netdev_run_todo")
    if ("while (true)" not in wait or "netdev_refcnt_read(dev) == 1" not in wait or
            "netdev_wait_allrefs_any(&list)" not in todo):
        raise ValueError("netdev unregister reference waiting changed; review the model")


def run(kernel):
    dpaa = (kernel / "drivers/net/ethernet/freescale/dpaa/dpaa_eth.c").read_text()
    qman = (kernel / "drivers/soc/fsl/qbman/qman.c").read_text()
    net = (kernel / "net/core/dev.c").read_text()
    netdev = (kernel / "include/linux/netdevice.h").read_text()
    rtnl = (kernel / "net/core/rtnetlink.c").read_text()
    rtnl_header = (kernel / "include/linux/rtnetlink.h").read_text()
    check_source_contract(dpaa, qman, net, netdev, rtnl, rtnl_header)
    body = "\n".join((
        function(dpaa, "static int dpaa_fq_init"),
        function(dpaa, "int dpaa_alloc_offload_tx_fq"),
        function(dpaa, "static void dpaa_remove"),
    ))
    with tempfile.TemporaryDirectory(prefix="dpaa-offload-fq-") as directory:
        path = Path(directory)
        (path / "dpaa_offload_fq_body.h").write_text(body)
        binary = path / "dpaa_offload_fq"
        subprocess.run(
            shlex.split(os.environ.get("HOSTCC", "cc")) + [
                "-std=gnu11", "-Wall", "-Wextra", "-Werror",
                "-Wno-unused-parameter", "-Wno-unused-function",
                *(["-DCGR_CONTAINMENT"] if "F-300: keep unsettled CGR owners alive" in dpaa else []),
                "-I", str(path), str(HARNESS), "-o", str(binary),
            ],
            check=True,
        )
        for case in ("success", "create-error", "timeout", "init-error",
                     "unregistered", "probe-error"):
            subprocess.run([str(binary), case], check=True, timeout=30)
    print("Host model only: no QMan hardware or concurrent parent teardown tested.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kernel_source", type=Path)
    args = parser.parse_args()
    try:
        run(args.kernel_source)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"DPAA offload FQ test failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
