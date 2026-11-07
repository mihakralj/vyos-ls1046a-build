#!/usr/bin/env python3
"""Characterize DPAA egress identity and queue teardown in a host model."""

import argparse
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
WGFP = ROOT / "kernel/common/files/drivers/net/ethernet/freescale/dpaa/dpaa_wgfp.c"
ASK = ROOT / "kernel/ask/oot-modules/ask/ask_hw.c"
HARNESS = ROOT / "tests/dut"


def function(source, prefix):
    match = re.search(rf"(?m)^{re.escape(prefix)}\([^;]*?\n\{{\n", source)
    if not match:
        raise ValueError(f"{prefix}: function not found")
    return source[match.start():source.index("\n}\n", match.end()) + 3]


def check_qman_model(qman):
    retire = function(qman, "int qman_retire_fq")
    oos = function(qman, "int qman_oos_fq")
    for fragment in ("QM_MCR_RESULT_PENDING", "ret = 1;",
                     "QMAN_FQ_STATE_CHANGING", "QMAN_FQ_STATE_NE",
                     "QMAN_FQ_STATE_ORL"):
        if fragment not in retire:
            raise ValueError(f"QMan retire model needs review: missing {fragment}")
    for fragment in ("fq->state != qman_fq_state_retired",
                     "QMAN_FQ_STATE_BLOCKOOS"):
        if fragment not in oos:
            raise ValueError(f"QMan OOS model needs review: missing {fragment}")


def check_namespace_move(kernel):
    source = (kernel / "net/core/dev.c").read_text()
    move = function(source, "int __dev_change_net_namespace")
    immutable = move.find("if (dev->netns_immutable)")
    unregister = move.find("call_netdevice_notifiers(NETDEV_UNREGISTER, dev)")
    reindex = move.find("dev->ifindex = new_ifindex;")
    if min(immutable, unregister, reindex) < 0 or not immutable < unregister < reindex:
        raise ValueError("namespace-move ordering changed; review identity characterization")
    dpaa = (kernel / "drivers/net/ethernet/freescale/dpaa/dpaa_eth.c").read_text()
    probe = function(dpaa, "static int dpaa_eth_probe")
    immutable = probe.find("net_dev->netns_immutable = true;")
    init_call = probe.find("dpaa_netdev_init(net_dev,")
    netdev_init = function(dpaa, "static int dpaa_netdev_init")
    register = netdev_init.find("register_netdev(net_dev)")
    if min(immutable, init_call, register) < 0 or immutable > init_call:
        raise ValueError("DPAA netns immutability must be set before netdev registration")
    print("DPAA netdev identity: namespace move blocked before registration")


def check_qbman_portal_guard(kernel):
    qbman = kernel / "drivers/soc/fsl/qbman"
    for name in ("qman", "bman"):
        portal = (qbman / f"{name}_portal.c").read_text()
        core = (qbman / f"{name}.c").read_text()
        probe = function(portal, f"static int {name}_portal_probe")
        create = function(core, f"static int {name}_create_portal")
        start = portal.index(f"static struct platform_driver {name}_portal_driver = {{")
        end = portal.index("\n};", start) + 3
        driver = portal[start:end]
        if (driver.count(".suppress_bind_attrs = true,") != 1 or
                ".remove" in driver or
                "pcfg = devm_kmalloc(dev, sizeof(*pcfg), GFP_KERNEL);" not in probe or
                "init_pcfg(pcfg)" not in probe or
                "portal->config = c;" not in create):
            raise ValueError(f"{name} portal ownership or unbind guard changed")

    bus = (kernel / "drivers/base/bus.c").read_text()
    if "if (!drv->suppress_bind_attrs) {\n\t\terror = add_bind_files(drv);" not in bus:
        raise ValueError("driver-core bind/unbind suppression contract changed")
    kconfig = (qbman / "Kconfig").read_text()
    makefile = (qbman / "Makefile").read_text()
    config = (ROOT / "kernel/common/kernel-config/08-dpaa1.config").read_text()
    if ("menuconfig FSL_DPAA\n\tbool " not in kconfig or
            "obj-$(CONFIG_FSL_DPAA)" not in makefile or
            any(f"{name}_portal.o" not in makefile for name in ("qman", "bman")) or
            "CONFIG_FSL_DPAA=y" not in config or
            "# CONFIG_OF_OVERLAY is not set" not in config):
        raise ValueError("QBMan built-in or overlay configuration changed")
    print("QBMan portals: sysfs bind/unbind suppressed for built-in drivers")


def check_dpaa_cleanup_callers(dpaa):
    remove = function(dpaa, "static void dpaa_remove")
    fq_free = remove.find("err = dpaa_fq_free(dev, &priv->dpaa_fq_list);")
    release = remove.find("free_netdev(net_dev);")
    if fq_free < 0 or release < 0 or fq_free >= release:
        raise ValueError("DPAA remove cleanup changed; review FQ owner retention")
    if "if (err)" not in remove[fq_free:release] or "return" in remove[fq_free:release]:
        raise ValueError("DPAA remove error handling changed; review FQ owner retention")

    probe = function(dpaa, "static int dpaa_eth_probe")
    unwind = probe.find("free_dpaa_fqs:")
    fq_free = probe.find("dpaa_fq_free(dev, &priv->dpaa_fq_list);", unwind)
    release = probe.find("free_netdev(net_dev);", fq_free)
    if min(unwind, fq_free, release) < 0 or fq_free >= release:
        raise ValueError("DPAA probe unwind changed; review FQ owner retention")
    print(
        "Parent teardown characterization: probe unwind ignores FQ cleanup "
        "status; remove logs errors but still releases the netdev."
    )


def run(kernel, controls):
    dpaa = (kernel / "drivers/net/ethernet/freescale/dpaa/dpaa_eth.c").read_text()
    qman = (kernel / "drivers/soc/fsl/qbman/qman.c").read_text()
    wgfp = WGFP.read_text()
    ask = ASK.read_text()
    check_qman_model(qman)
    check_namespace_move(kernel)
    check_dpaa_cleanup_callers(dpaa)

    with tempfile.TemporaryDirectory(prefix="dpaa-lifetime-") as directory:
        path = Path(directory)
        # The enqueue transport is mocked; the FQ chosen by the actual
        # egress/xmit functions is the identity oracle.
        enqueue = """\
static int qman_enqueue_fqid(struct qman_fq *tag, u32 fqid,
\t\t\t     const struct qm_fd *fd)
{
\tportal.p.entry.fqid = fqid;
\tportal.p.entry.fd = *fd;
\tcommits++;
\treturn 0;
}
"""
        bodies = [
            enqueue,
            function(wgfp, "static void wgfp_egress_ern"),
            function(wgfp, "int dpaa_wgfp_egress"),
            function(wgfp, "void dpaa_wgfp_xmit"),
        ]
        (path / "qman_wgfp_body.h").write_text("\n".join(bodies))
        (path / "ask_oif_body.h").write_text(
            function(ask, "static int ask_hw_resolve_oif_fqid")
        )
        (path / "qman_destroy_body.h").write_text(
            function(qman, "void qman_destroy_fq")
        )
        (path / "dpaa_fq_free_body.h").write_text(
            function(dpaa, "static int dpaa_fq_free_entry")
        )

        compiler = shlex.split(os.environ.get("HOSTCC", "cc"))
        failed = False
        for name in ("dpaa_egress_lifetime", "dpaa_fq_lifetime"):
            binary = path / name
            command = compiler + [
                "-std=gnu11", "-Wall", "-Wextra", "-Werror",
                "-Wno-unused-parameter", "-Wno-unused-function",
                "-I", str(path), str(HARNESS / f"{name}.c"), "-o", str(binary),
            ]
            if controls:
                command.append("-DLIFETIME_ORACLE_CONTROLS")
            subprocess.run(command, check=True)
            result = subprocess.run([str(binary)], check=False)
            if result.returncode not in (0, 1):
                raise RuntimeError(f"{name} terminated with {result.returncode}")
            failed |= result.returncode != 0
        return failed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kernel_source", type=Path)
    parser.add_argument(
        "--characterize", action="store_true",
        help="report unresolved cases without treating them as a passing gate",
    )
    parser.add_argument(
        "--oracle-controls", action="store_true",
        help="test the oracles with simulated retention and invalidation",
    )
    parser.add_argument(
        "--verify-netns-guard", action="store_true",
        help="verify DPAA netns immutability is set before netdev registration",
    )
    parser.add_argument(
        "--verify-qbman-portal-guard", action="store_true",
        help="verify QMan/BMan sysfs unbind guards and ownership assumptions",
    )
    args = parser.parse_args()
    print(
        "Host model only: namespace transitions, QMan outcomes and enqueue "
        "are simulated; this does not prove hardware or concurrent teardown.",
        flush=True,
    )
    if args.oracle_controls:
        print("Oracle controls simulate protections; they are not production code.",
              flush=True)
    if args.verify_netns_guard:
        try:
            check_namespace_move(args.kernel_source)
        except (OSError, ValueError) as error:
            print(f"DPAA netns guard check failed: {error}", file=sys.stderr)
            return 2
        return 0
    if args.verify_qbman_portal_guard:
        try:
            check_qbman_portal_guard(args.kernel_source)
        except (OSError, ValueError) as error:
            print(f"QBMan portal guard check failed: {error}", file=sys.stderr)
            return 2
        return 0
    try:
        failed = run(args.kernel_source, args.oracle_controls)
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"DPAA lifetime characterization failed: {error}", file=sys.stderr)
        return 2
    if failed and args.characterize and not args.oracle_controls:
        print("Uncontained cases remain; characterization is not a safety pass.")
        return 0
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
