"""Common pytest reporting and hardware ownership for every ASK suite."""

import importlib.metadata
import os
import random
import tempfile
import time
from contextlib import ExitStack
from pathlib import Path

import pytest

from ask_orch.artifacts import artifact_dir, prune_runs, record
from ask_orch.lab import Bench, temporary_wan_vlan
from ask_orch.lifecycle import bench_lock
from ask_orch.provenance import checkout


def backend_requirements(filename):
    if filename == "smoke.py":
        return ("kasan",)
    if filename == "mwifiex_rx_dut.py":
        return ("legacy_cdx", "kasan", "wifi")
    if filename.startswith(("flowtable_", "mcast_", "mroute_", "reassembly_",
                            "ipv6_reassembly_", "profile_")):
        return ("native_flowtable", "kasan")
    return ("legacy_cdx", "kasan")


def lab_preflight(config):
    if not hasattr(config, "_ask_lab_preflight"):
        config._ask_lab_preflight = config._ask_bench.preflight()
        record("lab-preflight", config._ask_lab_preflight, nodeid="session")
    return config._ask_lab_preflight


def pytest_addoption(parser):
    parser.addoption("--lab-run", action="store_true", help="execute through the dell2 rig worker")
    parser.addoption("--allow-disruptive", action="store_true",
                     help="allow fault injection, resource exhaustion and module lifecycle tests")
    parser.addoption("--include-slow", action="store_true", help="include sustained traffic and profiles")
    parser.addoption(
        "--release",
        action="store_true",
        help="require hardware capabilities and reject unexpected skips",
    )
    parser.addoption(
        "--module-order-seed", type=int,
        help="shuffle modules reproducibly while preserving each module's test order",
    )


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config):
    config._ask_broken = None
    if config.getoption("--lab-run"):
        config._ask_bench = Bench.load(os.environ.get("ASK_LAB_DUT", "dut1"))
        os.environ.update(config._ask_bench.environment(os.environ))
    if hasattr(config, "workerinput"):
        config._ask_run_dir = Path(config.workerinput["ask_run_dir"])
        os.environ["ASK_TEST_RUN_DIR"] = str(config._ask_run_dir)
        return
    root = Path(
        os.environ.get(
            "ASK_TEST_ARTIFACTS",
            os.environ.get("ASK_FLOWTABLE_ARTIFACTS", f"/tmp/ask-tests-{os.getuid()}"),
        )
    )
    root.mkdir(parents=True, exist_ok=True)
    prune_runs(root, keep_days=float(os.environ.get("ASK_TEST_ARTIFACT_DAYS", "3")),
               keep_runs=10, min_free=4 << 30)
    config._ask_run_dir = Path(
        tempfile.mkdtemp(prefix=time.strftime("%Y%m%d-%H%M%S-"), dir=root)
    )
    os.environ["ASK_TEST_RUN_DIR"] = str(config._ask_run_dir)
    if not config.option.xmlpath:
        config.option.xmlpath = str(config._ask_run_dir / "junit.xml")
    versions = {
        d.metadata["Name"]: d.version for d in importlib.metadata.distributions()
    }
    source = checkout(Path(__file__).resolve().parents[2])
    # Only bench settings: credentials and unrelated environment variables
    # never belong in a distributable test report.
    settings = {
        key: os.environ[key]
        for key in (
            "ASK_TARGET_IP",
            "ASK_LAB_DUT",
            "ASK_DUT_KIND",
            "ASK_RUNNER_ROLE",
            "ASK_RUNNER_NETNS",
            "ASK_SUITE_SHA256",
            "ASK_DUT_SSH_HOST",
            "ASK_LAN_SSH_HOST",
            "ASK_WAN_SSH_HOST",
            "ASK_LAN_NETNS",
            "ASK_WAN_NETNS",
            "ASK_TARGET_DEV",
            "ASK_TARGET_AGENT_DEV",
            "ASK_TARGET_LAN_IF",
            "ASK_TARGET_WAN_IF",
            "ASK_LAN_VM",
            "ASK_LAN_NIC",
            "ASK_WAN_IP",
            "ASK_WAN_IPERF_IP",
            "ASK_WAN_INJECT_IF",
            "ASK_KERNEL_SOURCE",
            "ASK_FLOWTABLE_CHURN",
            "ASK_FLOWTABLE_CHURN_SECONDS",
            "ASK_FLOWTABLE_BASELINE",
            "ASK_FLOWTABLE_MIN_GBPS",
            "ASK_IPSEC_IPERF",
            "ASK_IPSEC_IPERF_BPS",
        )
        if os.environ.get(key)
    }
    record(
        "environment",
        {
            "revision": source["revision"],
            "checkout": source,
            "packages": versions,
            "bench": settings,
            "release": config.getoption("--release"),
        },
        nodeid="session",
    )


@pytest.hookimpl(optionalhook=True)
def pytest_configure_node(node):
    node.workerinput["ask_run_dir"] = str(node.config._ask_run_dir)


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_collection_modifyitems(config, items):
    for item in items:
        suite = Path(item.path).parent.name
        item.add_marker("host" if suite == "host_tests" or item.get_closest_marker("host") else "hardware")
        if item.get_closest_marker("hardware") and not item.get_closest_marker("portable"):
            item.add_marker("upstream")
            item.add_marker(pytest.mark.backend(*backend_requirements(item.path.name)))
            if ("pppoe_rig" in item.fixturenames or item.path.name in {
                "profile_isp.py", "mroute_capacity.py", "flowtable_service_multicast_edges.py",
                "flowtable_dhcp.py"}):
                item.add_marker(pytest.mark.usefixtures("tagged_wan"))
        original = getattr(item, "originalname", "") or ""
        if any(token in item.path.name for token in
               ("failslab", "capacity", "pressure", "churn", "restart", "unregister",
                "module", "startup", "reassembly")) or any(token in original for token in
               ("terminal", "recover", "quarantine", "provider_lifetime")):
            item.add_marker("destructive")
        if suite == "startup_tests":
            item.add_marker("destructive")
        if item.path.name == "smoke.py":
            item.add_marker("smoke")
        if "pppoe_rig" in item.fixturenames:
            item.add_marker(pytest.mark.requires("pppd"))
        if "smcrouted" in item.fixturenames:
            item.add_marker(pytest.mark.requires("smcrouted"))
        if item.path.name.startswith("profile_") or item.path.name in {
            "flowtable_churn.py",
            "ipsec_vlan_iperf_probe.py",
            "reassembly_storm.py",
            "ipv6_reassembly_storm.py",
        }:
            item.add_marker("slow")
        if (
            (item.path.name == "flowtable_offload.py"
             and getattr(item, "originalname", None) == "test_terminal")
            or item.path.name == "flowtable_unregister.py"
        ):
            item.add_marker("destructive")
        if item.path.name == "flowtable_churn.py":
            # Churn requires both a minimum soak and a complete tuple rotation.
            # UART round trips can make the latter longer than the soak. Leave
            # an hour for that coverage, including setup, drain and leak scans.
            item.add_marker(
                pytest.mark.timeout(
                    max(3600, int(os.environ.get("ASK_FLOWTABLE_CHURN_SECONDS", "900")) + 420),
                    func_only=True,
                )
            )
    # pytest applies -k/-m inside this hook. Mark first; validate only the
    # remaining selection so `-m host -n auto` never reserves the bench.
    result = yield
    seed = config.getoption("--module-order-seed")
    if seed is not None:
        modules = {}
        for item in items:
            modules.setdefault(item.path, []).append(item)
        groups = list(modules.values())
        random.Random(seed).shuffle(groups)
        items[:] = [item for group in groups for item in group]
    record("selection", {"module_order_seed": seed,
                         "nodeids": [item.nodeid for item in items]}, nodeid="session")
    if any(item.get_closest_marker("hardware") for item in items):
        if getattr(config.option, "numprocesses", None) or hasattr(
            config, "workerinput"
        ):
            raise pytest.UsageError(
                "hardware tests require one runner; use test-host for parallel host tests"
            )
    return result


@pytest.fixture(scope="session", autouse=True)
def hardware_bench(request):
    if not any(item.get_closest_marker("hardware") for item in request.session.items):
        yield
        return
    bench = request.config._ask_bench
    resources = ["dut:" + bench.dut["host"]]
    resources.extend(f"{role}:{bench.endpoint(role).host}:{bench.dut['nic']}:{bench.dut['namespace']}"
                     for role in ("lan", "wan"))
    with bench_lock(resources):
        yield


@pytest.fixture(scope="session")
def lab_report(request, hardware_bench):
    return lab_preflight(request.config)


@pytest.fixture(scope="session", autouse=True)
def tagged_wan(request, hardware_bench):
    if not request.config.getoption("--lab-run"):
        yield
        return
    candidates = [item for item in request.session.items if any(
        "tagged_wan" in marker.args for marker in item.iter_markers("usefixtures"))]
    if not candidates:
        yield
        return
    capabilities = lab_preflight(request.config)["nodes"]["dut"]["capabilities"]
    candidates = [item for item in candidates
                  if (not item.get_closest_marker("destructive") or request.config.getoption("--allow-disruptive"))
                  and (not item.get_closest_marker("slow") or request.config.getoption("--include-slow"))
                  and all(capabilities.get(name) for marker in item.iter_markers("backend") for name in marker.args)]
    if not candidates:
        yield
        return
    interfaces = set()
    for item in candidates:
        if "pppoe_rig" in item.fixturenames or item.path.name == "profile_isp.py":
            interfaces.add((os.environ["ASK_PPPOE_SERVER_IF"], int(os.environ.get("ASK_FLOWTABLE_PPPOE_VID", "3900"))))
        if item.path.name in {"mroute_capacity.py", "flowtable_service_multicast_edges.py"}:
            interfaces.add((os.environ["ASK_MROUTE_WAN_IF"], int(os.environ.get("ASK_MROUTE_WAN_VID", "3900"))))
        if item.path.name == "flowtable_dhcp.py":
            interfaces.add((os.environ["ASK_DHCP_WAN_IF"], 3900))
    with ExitStack() as stack:
        for name, vid in sorted(interfaces):
            stack.enter_context(temporary_wan_vlan(request.config._ask_bench, name, vid))
        yield


@pytest.fixture(autouse=True)
def upstream_fixture_setup(request):
    if request.node.get_closest_marker("upstream"):
        request.getfixturevalue("_target_reachable")
        request.getfixturevalue("splat_window")


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item):
    if item.get_closest_marker("hardware"):
        if item.config._ask_broken:
            pytest.skip("bench requires recovery: " + item.config._ask_broken)
        if not item.config.getoption("--lab-run"):
            pytest.skip("hardware execution requires tests/run_test.py --run")
        if os.environ.get("ASK_RUNNER_ROLE") != "wan":
            pytest.skip("hardware tests must run on dell2 via tests/run_test.py --run")
        if item.get_closest_marker("destructive") and not item.config.getoption("--allow-disruptive"):
            pytest.skip("requires --allow-disruptive and an operator-controlled recovery path")
        if item.get_closest_marker("slow") and not item.config.getoption("--include-slow"):
            pytest.skip("requires --include-slow")
        report = lab_preflight(item.config)
        if not report["ok"]:
            pytest.fail("lab preflight failed: " + str(report["nodes"]))
        capabilities = report["nodes"]["dut"]["capabilities"]
        missing = sorted({name for marker in item.iter_markers("backend") for name in marker.args
                          if not capabilities.get(name)})
        if missing:
            pytest.skip(f"{report['dut']} lacks upstream test capabilities: {', '.join(missing)}")


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item, call):
    report = yield
    if (
        report.skipped
        and item.config.getoption("--release")
        and not item.config._ask_broken
        and not getattr(report, "wasxfail", None)
    ):
        report.outcome = "failed"
        report.longrepr = (
            f"release run cannot skip required coverage: {report.longrepr}"
        )
    item._ask_failed = getattr(item, "_ask_failed", False) or report.failed
    if (
        report.failed
        and report.when in {"setup", "teardown"}
        and item.get_closest_marker("hardware")
    ):
        item.config._ask_broken = item.nodeid
    path = artifact_dir(item.nodeid)
    report.user_properties.append(("artifacts", str(path)))
    record(
        report.when,
        {
            "nodeid": item.nodeid,
            "phase": report.when,
            "outcome": report.outcome,
            "duration_s": report.duration,
            "failure": str(report.longrepr) if report.longrepr else None,
            "sections": report.sections,
        },
        nodeid=item.nodeid,
    )
    return report


def pytest_terminal_summary(terminalreporter):
    terminalreporter.write_line(f"Artifacts: {terminalreporter.config._ask_run_dir}")


def _duration(seconds):
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, seconds = divmod(int(seconds), 60)
    if minutes < 60:
        return f"{minutes}m{seconds:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m"


_elapsed = {}
_latest = None


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_logreport(report):
    """Time each test across its phases, ahead of the terminal reporter
    printing its line, so that line can carry it."""
    global _latest
    _elapsed[report.nodeid] = _elapsed.get(report.nodeid, 0.0) + report.duration
    _latest = report.nodeid


@pytest.hookimpl(trylast=True)
def pytest_sessionstart(session):
    """With console_output_style = count, a verbose line ends in the test's
    time as well as its place in the run: `PASSED  41.2s [ 27/514]`. pytest
    offers the time or the count but not both, so its count is extended
    here; the column keeps the reporter's colour, green while every test
    passes. It reaches into the reporter's private progress API, which a
    pytest upgrade has to keep in view."""
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    if reporter is None or reporter._show_progress_info != "count" or reporter.verbosity <= 0:
        return
    count = reporter._get_progress_information_message

    def message():
        return f" {_duration(_elapsed.get(_latest, 0.0))}{count()}"

    reporter._get_progress_information_message = message
