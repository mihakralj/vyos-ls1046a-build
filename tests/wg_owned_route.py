"""Exercise owned WireGuard test routes in disposable network namespaces."""

import os
from pathlib import Path
import shutil
import subprocess

import pytest

pytestmark = pytest.mark.host
ROUTE = Path(__file__).resolve().parents[1] / "bin/testrig-wg-route.sh"


@pytest.fixture(scope="module", autouse=True)
def namespace_available():
    if not shutil.which("unshare") or not shutil.which("ip"):
        pytest.skip("unshare and ip are required")
    probe = subprocess.run(
        ["unshare", "-Urn", "sh", "-c", "ip link add probe0 type dummy"],
        text=True, capture_output=True, check=False,
    )
    if probe.returncode:
        pytest.skip(f"disposable network namespace unavailable: {probe.stderr.strip()}")


def run_in_namespace(script, env=None):
    return subprocess.run(
        ["unshare", "-Urn", "sh", "-eu", "-c", script, "_", str(ROUTE)],
        text=True, capture_output=True, check=False, env=env or os.environ,
    )


def test_owned_routes_work_and_preserve_unrelated_main_route():
    result = run_in_namespace(
        'ip link add test0 type dummy; ip link set test0 up; '
        'ip addr add 10.99.1.112/24 dev test0; '
        'ip route add 10.99.210.0/24 via 10.99.1.186; '
        'old=$(ip route show exact 10.99.210.0/24); '
        'sh "$1" add plain 18000 2000200 10.99.1.112 10.99.210.0/24 '
        '10.99.1.185 1420 11259375; '
        'ip route get 10.99.210.113 from 10.99.1.112 | grep -q "via 10.99.1.185"; '
        'sh "$1" delete plain 18000 2000200 10.99.1.112 10.99.210.0/24 '
        '10.99.1.185 1420 11259375; '
        'sh "$1" delete plain 18000 2000200 10.99.1.112 10.99.210.0/24 '
        '10.99.1.185 1420 11259375; '
        '[ "$old" = "$(ip route show exact 10.99.210.0/24)" ]'
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("collision", ["rule", "table"])
def test_create_refuses_existing_rule_or_table(collision):
    setup = (
        "ip rule add pref 18000 from 192.0.2.1 lookup 500"
        if collision == "rule" else
        "ip route add table 2000200 192.0.2.0/24 dev test0"
    )
    result = run_in_namespace(
        'ip link add test0 type dummy; ip link set test0 up; '
        f'{setup}; '
        'rules=$(ip rule show); routes=$(ip route show table all); '
        'if sh "$1" add far 18000 2000200 10.99.210.113 10.99.1.0/24 '
        'test0 1420 11259375; then exit 1; fi; '
        '[ "$rules" = "$(ip rule show)" ]; [ "$routes" = "$(ip route show table all)" ]'
    )
    assert result.returncode == 0, result.stderr


def test_cleanup_refuses_replaced_rule_with_different_realm():
    result = run_in_namespace(
        'ip link add test0 type dummy; ip link set test0 up; '
        'sh "$1" add far 18000 2000200 10.99.210.113 10.99.1.0/24 '
        'test0 1420 11259375; '
        'ip rule del priority 18000 from 10.99.210.113 to 10.99.1.0/24 '
        'lookup 2000200 protocol 143 realms 11259375; '
        'ip rule add priority 18000 from 10.99.210.113 to 10.99.1.0/24 '
        'lookup 2000200 protocol 143 realms 11259376; '
        'old=$(ip rule show pref 18000); '
        'if sh "$1" delete far 18000 2000200 10.99.210.113 10.99.1.0/24 '
        'test0 1420 11259375; then exit 1; fi; '
        '[ "$old" = "$(ip rule show pref 18000)" ]; '
        '[ -n "$(ip route show table 2000200)" ]'
    )
    assert result.returncode == 0, result.stderr


def test_cleanup_refuses_replaced_route_with_different_realm():
    result = run_in_namespace(
        'ip link add test0 type dummy; ip link set test0 up; '
        'sh "$1" add far 18000 2000200 10.99.210.113 10.99.1.0/24 '
        'test0 1420 11259375; '
        'ip route replace table 2000200 10.99.1.0/24 dev test0 proto 143 '
        'metric 11259375 realm 11259376; '
        'old=$(ip route show table 2000200); '
        'if sh "$1" delete far 18000 2000200 10.99.210.113 10.99.1.0/24 '
        'test0 1420 11259375; then exit 1; fi; '
        '[ "$old" = "$(ip route show table 2000200)" ]'
    )
    assert result.returncode == 0, result.stderr


def test_failed_route_creation_does_not_add_rule():
    result = run_in_namespace(
        'if sh "$1" add far 18000 2000200 10.99.210.113 10.99.1.0/24 '
        'missing0 1420 11259375; then exit 1; fi; '
        '[ -z "$(ip rule show pref 18000)" ]'
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(("fail_command", "expect_rule"), [("route add", False), ("rule add", True)])
def test_failed_netlink_operation_can_have_committed(tmp_path, fail_command, expect_rule):
    ip = shutil.which("ip")
    wrapper = tmp_path / "ip"
    wrapper.write_text(
        f'#!/bin/sh\nif [ "$2 $3" = "{fail_command}" ]; then "{ip}" "$@" || exit 1; exit 1; fi\n'
        f'exec "{ip}" "$@"\n'
    )
    wrapper.chmod(0o755)
    env = os.environ | {"PATH": f"{tmp_path}:{os.environ['PATH']}"}
    rule_check = '[ -n "$(ip rule show pref 18000)" ]' if expect_rule else '[ -z "$(ip rule show pref 18000)" ]'
    result = run_in_namespace(
        'ip link add test0 type dummy; ip link set test0 up; '
        'if sh "$1" add far 18000 2000200 10.99.210.113 10.99.1.0/24 '
        'test0 1420 11259375; then exit 1; else rc=$?; fi; '
        '[ "$rc" -eq 1 ]; [ -n "$(ip route show table 2000200)" ]; ' + rule_check,
        env=env,
    )
    assert result.returncode == 0, result.stderr


def test_ipv6_owned_route_works_and_preserves_a_replaced_metric():
    result = run_in_namespace(
        'ip link add test0 type dummy; ip link set test0 up; '
        'ip -6 addr add fd99:1::112/64 dev test0 nodad; '
        'sh "$1" add plain 18000 2000200 fd99:1::112 fd99:200::/64 '
        'fd99:1::185 1420 11259375; '
        'ip -6 route get fd99:200::113 from fd99:1::112 | grep -q "via fd99:1::185"; '
        'ip -6 route del table 2000200 fd99:200::/64 via fd99:1::185 '
        'mtu 1420 proto 143 metric 11259375; '
        'ip -6 route add table 2000200 fd99:200::/64 via fd99:1::185 '
        'mtu 1420 proto 143 metric 11259376; '
        'old=$(ip -6 route show table 2000200); '
        'if sh "$1" delete plain 18000 2000200 fd99:1::112 fd99:200::/64 '
        'fd99:1::185 1420 11259375; then exit 1; fi; '
        '[ "$old" = "$(ip -6 route show table 2000200)" ]; '
        '[ -n "$(ip -6 rule show pref 18000)" ]'
    )
    assert result.returncode == 0, result.stderr


def test_ipv6_cleanup_refuses_replaced_rule_protocol():
    result = run_in_namespace(
        'ip link add test0 type dummy; ip link set test0 up; '
        'sh "$1" add far 18000 2000200 fd99:200::113 fd99:1::/64 '
        'test0 1420 11259375; '
        'ip -6 rule del priority 18000 from fd99:200::113 to fd99:1::/64 '
        'lookup 2000200 protocol 143; '
        'ip -6 rule add priority 18000 from fd99:200::113 to fd99:1::/64 '
        'lookup 2000200 protocol 144; '
        'old=$(ip -6 rule show pref 18000); '
        'if sh "$1" delete far 18000 2000200 fd99:200::113 fd99:1::/64 '
        'test0 1420 11259375; then exit 1; fi; '
        '[ "$old" = "$(ip -6 rule show pref 18000)" ]; '
        '[ -n "$(ip -6 route show table 2000200)" ]'
    )
    assert result.returncode == 0, result.stderr
