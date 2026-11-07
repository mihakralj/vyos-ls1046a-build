#!/usr/bin/env python3
"""Exercise source-extracted QMan/DPAA CGR containment in a host model."""

import argparse
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "tests/dut/qman_cgr_containment.c"


def function(source, prefix):
    match = re.search(rf"(?m)^{re.escape(prefix)}\([^;]*?\n\{{\n", source)
    if not match:
        raise ValueError(f"{prefix}: function not found")
    return source[match.start():source.index("\n}\n", match.end()) + 3]


def ordered(source, *fragments):
    position = 0
    for fragment in fragments:
        position = source.find(fragment, position)
        if position < 0:
            raise ValueError(f"source contract changed: {fragment}")
        position += len(fragment)


def check_contract(qman, portal, dpaa, dpaa_header, qman_header):
    create = function(qman, "int qman_create_cgr")
    congestion = function(qman, "static void qm_congestion_task")
    for body in (create, congestion):
        ordered(body, "raw_spin_lock_irq(&p->cgr_lock);", "cgr->cb(",
                "raw_spin_unlock_irq(&p->cgr_lock);")
    if len(re.findall(r"\bcgr->cb\s*\(", qman)) != 2:
        raise ValueError("QMan has another callback invocation to audit")
    linked = create[create.index("list_add(&cgr->node, &p->cgr_cbs);"):]
    ordered(linked, "ret = qman_query_cgr(", "if (ret) {", "ret = 0;",
            "goto out;", "return ret;")
    if (len(re.findall(r"\bret\s*=", linked)) != 2
            or linked.count("goto out;") != 1
            or len(re.findall(r"(?m)^\s*return\b", linked)) != 1):
        raise ValueError("failed CGR creation may leave a linked callback")

    offline = function(portal, "static int qman_offline_cpu")
    if ("affine_portals[cpu]" not in offline or "irq_set_affinity(" not in offline
            or "destroy" in offline):
        raise ValueError("CPU-offline portal lifetime contract changed")
    driver = portal[portal.index("static struct platform_driver qman_portal_driver = {"):]
    driver = driver[:driver.index("\n};")]
    if ".suppress_bind_attrs = true," not in driver:
        raise ValueError("QMan portal sysfs unbind guard missing")
    if ("static DEFINE_PER_CPU(struct qman_portal, qman_affine_portal);" not in qman
            or "int portal_cpu;" not in qman_header
            or "void qman_cgr_disable_callback(struct qman_cgr *cgr);" not in qman_header
            or "int qman_delete_cgr_safe(struct qman_cgr *cgr);" not in qman_header
            or "struct dpaa_cgr_context {" not in dpaa_header):
        raise ValueError("CGR owner or checked API declaration changed")

    for prefix, member in (
        ("static int dpaa_eth_cgr_init", "cgr_data.ctx"),
        ("static int dpaa_ingress_cgr_init", "ingress_cgr"),
    ):
        body = function(dpaa, prefix)
        ordered(body, "dpaa_cgr_alloc(priv)", "qman_alloc_cgrid(",
                "qman_create_cgr(", "if (err < 0) {",
                "dpaa_cgr_create_failed(", f"&priv->{member}, err);")
    probe = function(dpaa, "static int dpaa_eth_probe")
    ordered(probe, "free_dpaa_fqs:", "dpaa_cgr_release(dev, &priv->ingress_cgr);",
            "delete_egress_cgr:", "dpaa_cgr_release(dev, &priv->cgr_data.ctx);",
            "free_netdev(net_dev);")
    remove = function(dpaa, "static void dpaa_remove")
    ordered(remove, "dpaa_cgr_deactivate(priv->cgr_data.ctx);",
            "unregister_netdev(net_dev);",
            "dpaa_cgr_release(dev, &priv->ingress_cgr);",
            "dpaa_cgr_release(dev, &priv->cgr_data.ctx);",
            "free_netdev(net_dev);")


def run(kernel, sanitize):
    qman = (kernel / "drivers/soc/fsl/qbman/qman.c").read_text()
    portal = (kernel / "drivers/soc/fsl/qbman/qman_portal.c").read_text()
    dpaa = (kernel / "drivers/net/ethernet/freescale/dpaa/dpaa_eth.c").read_text()
    dpaa_header = (kernel / "drivers/net/ethernet/freescale/dpaa/dpaa_eth.h").read_text()
    qman_header = (kernel / "include/soc/fsl/qman.h").read_text()
    check_contract(qman, portal, dpaa, dpaa_header, qman_header)
    params = re.search(r"(?ms)^struct delete_cgr_params \{\n.*?^\};\n", qman)
    if not params:
        raise ValueError("CGR delete parameter structure not found")
    body = "\n".join((
        function(qman, "int qman_create_cgr"),
        function(qman, "static struct qman_portal *qman_cgr_get_affine_portal"),
        function(qman, "int qman_delete_cgr"),
        function(qman, "void qman_cgr_disable_callback"),
        params.group(),
        function(qman, "static void qman_delete_cgr_smp_call"),
        function(qman, "int qman_delete_cgr_safe"),
        function(dpaa, "static struct dpaa_cgr_context *dpaa_cgr_alloc"),
        function(dpaa, "static void dpaa_cgr_deactivate"),
        function(dpaa, "static void dpaa_cgr_create_failed"),
        function(dpaa, "static void dpaa_cgr_quarantine"),
        function(dpaa, "static void dpaa_cgr_release"),
        function(dpaa, "static void dpaa_eth_cgscn"),
        function(dpaa, "static int dpaa_eth_cgr_init"),
        function(dpaa, "static int dpaa_ingress_cgr_init"),
    ))
    with tempfile.TemporaryDirectory(prefix="qman-cgr-containment-") as directory:
        path = Path(directory)
        (path / "qman_cgr_containment_body.h").write_text(body)
        binary = path / "qman_cgr_containment"
        flags = ["-fsanitize=address,undefined", "-fno-omit-frame-pointer"] if sanitize else []
        subprocess.run(
            shlex.split(os.environ.get("HOSTCC", "cc")) + [
                "-std=gnu11", "-Wall", "-Wextra", "-Werror",
                "-Wno-unused-parameter", "-pthread", *flags,
                "-I", str(path), str(HARNESS), "-o", str(binary),
            ],
            check=True,
        )
        subprocess.run([str(binary)], check=True, timeout=30)
    print("Host model only: no QMan hardware, CPU hotplug or portal teardown was tested.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kernel_source", type=Path)
    parser.add_argument("--sanitize", action="store_true")
    args = parser.parse_args()
    try:
        run(args.kernel_source, args.sanitize)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"QMan CGR containment test failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
