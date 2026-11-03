"""Exercise WireGuard link ownership in disposable network namespaces."""

import os
from pathlib import Path
import shutil
import subprocess

import pytest

pytestmark = pytest.mark.host
LINK = Path(__file__).resolve().parents[1] / "bin/testrig-wg-link.sh"
OWNER = "wgo-0123456789abcdef0123456789abcdef-dut"


@pytest.fixture(scope="module", autouse=True)
def namespace_available():
    if not shutil.which("unshare") or not shutil.which("ip"):
        pytest.skip("unshare and ip are required")
    probe = subprocess.run(
        ["unshare", "-Urn", "sh", "-c",
         "ip link add wgprobe0 type wireguard && "
         "ip link property add wgprobe0 altname wgo-probe"],
        text=True, capture_output=True, check=False,
    )
    if probe.returncode:
        pytest.skip(f"disposable WireGuard namespace unavailable: {probe.stderr.strip()}")


def run_in_namespace(script):
    return subprocess.run(
        ["unshare", "-Urn", "sh", "-eu", "-c", script, "_", str(LINK), OWNER],
        text=True, capture_output=True, check=False,
        env=os.environ,
    )


def test_owned_link_cleanup_preserves_other_links():
    result = run_in_namespace(
        'ip link add other0 type wireguard; '
        'sh "$1" create wg0 "$2"; ip link show "$2" >/dev/null; '
        'sh "$1" delete wg0 "$2"; '
        '! ip link show wg0 >/dev/null 2>&1; ip link show other0 >/dev/null'
    )
    assert result.returncode == 0, result.stderr


def test_create_refuses_existing_interface_without_deleting_it():
    result = run_in_namespace(
        'ip link add wg0 type wireguard; old=$(ip -o link show wg0); '
        'if sh "$1" create wg0 "$2"; then exit 1; fi; '
        '[ "$old" = "$(ip -o link show wg0)" ]; '
        '! ip link show "$2" >/dev/null 2>&1; '
        '[ "$(ip -o link show | wc -l)" -eq 2 ]'
    )
    assert result.returncode == 0, result.stderr


def test_cleanup_does_not_delete_a_replacement_with_the_same_name():
    result = run_in_namespace(
        'sh "$1" create wg0 "$2"; ip link del wg0; '
        'ip link add wg0 type wireguard; replacement=$(ip -o link show wg0); '
        'sh "$1" delete wg0 "$2"; '
        '[ "$replacement" = "$(ip -o link show wg0)" ]'
    )
    assert result.returncode == 0, result.stderr


def test_duplicate_ownership_name_is_not_adopted():
    result = run_in_namespace(
        'ip link add other0 type wireguard; ip link property add other0 altname "$2"; '
        'if sh "$1" create wg0 "$2"; then exit 1; fi; '
        'ip link show other0 >/dev/null; ! ip link show wg0 >/dev/null 2>&1'
    )
    assert result.returncode == 0, result.stderr
