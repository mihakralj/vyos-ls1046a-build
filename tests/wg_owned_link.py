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


def run_in_namespace(script, env=None):
    return subprocess.run(
        ["unshare", "-Urn", "sh", "-eu", "-c", script, "_", str(LINK), OWNER],
        text=True, capture_output=True, check=False,
        env=env or os.environ,
    )


def test_owned_link_cleanup_preserves_other_links():
    result = run_in_namespace(
        'ip link add other0 type wireguard; '
        'sh "$1" create wg0 "$2"; ip link show "$2" >/dev/null; '
        'sh "$1" delete wg0 "$2"; '
        '! ip link show wg0 >/dev/null 2>&1; ip link show other0 >/dev/null'
    )
    assert result.returncode == 0, result.stderr


def fake_tc(tmp_path, failure="", output="qdisc fq 8001: root refcnt 2"):
    tc = tmp_path / "tc"
    tc.write_text(
        '#!/bin/sh\n'
        'echo "$*" >> "$TC_LOG"\n'
        'if [ "$2" = "$TC_FAILURE" ]; then exit 1; fi\n'
        'if [ "$2" = show ]; then printf "%s\\n" "$TC_OUTPUT"; fi\n'
    )
    tc.chmod(0o755)
    return os.environ | {
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "TC_LOG": str(tmp_path / "tc.log"),
        "TC_FAILURE": failure,
        "TC_OUTPUT": output,
    }


def test_default_does_not_change_the_link_qdisc(tmp_path):
    env = fake_tc(tmp_path, "add")
    result = run_in_namespace(
        'sh "$1" create wg0 "$2"; sh "$1" delete wg0 "$2"',
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "tc.log").exists()


def test_fq_uses_the_real_kernel_qdisc():
    if not shutil.which("tc"):
        pytest.skip("tc is required")
    result = run_in_namespace(
        'sh "$1" create wg0 "$2" fq; '
        'tc qdisc show dev wg0 | grep -Eq "^qdisc fq [^ ]+ root( |$)"; '
        'sh "$1" delete wg0 "$2"; ! ip link show wg0 >/dev/null 2>&1'
    )
    assert result.returncode == 0, result.stderr


def test_fq_is_added_to_the_owned_temporary_link(tmp_path):
    env = fake_tc(tmp_path)
    result = run_in_namespace(
        'ip link add other0 type wireguard; '
        'sh "$1" create wg0 "$2" fq; ip link show "$2" >/dev/null; '
        'sh "$1" delete wg0 "$2"; '
        '! ip link show wg0 >/dev/null 2>&1; ip link show other0 >/dev/null',
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "tc.log").read_text().splitlines() == [
        "qdisc add dev wt0123456789 root fq",
        "qdisc show dev wt0123456789",
    ]


@pytest.mark.parametrize("failure", ["add", "show"])
def test_fq_failure_removes_only_the_owned_link(tmp_path, failure):
    env = fake_tc(tmp_path, failure)
    result = run_in_namespace(
        'ip link add other0 type wireguard; '
        'if sh "$1" create wg0 "$2" fq; then exit 1; else rc=$?; fi; '
        '[ "$rc" -eq 12 ]; ! ip link show "$2" >/dev/null 2>&1; '
        '! ip link show wg0 >/dev/null 2>&1; ip link show other0 >/dev/null',
        env=env,
    )
    assert result.returncode == 0, result.stderr


def test_fq_readback_mismatch_removes_only_the_owned_link(tmp_path):
    env = fake_tc(tmp_path, output="qdisc noqueue 0: root")
    result = run_in_namespace(
        'ip link add other0 type wireguard; '
        'if sh "$1" create wg0 "$2" fq; then exit 1; else rc=$?; fi; '
        '[ "$rc" -eq 12 ]; ! ip link show "$2" >/dev/null 2>&1; '
        'ip link show other0 >/dev/null',
        env=env,
    )
    assert result.returncode == 0, result.stderr


def test_fq_failure_retains_the_lease_when_owned_link_cannot_be_removed(tmp_path):
    env = fake_tc(tmp_path, "add")
    ip = shutil.which("ip")
    wrapper = tmp_path / "ip"
    wrapper.write_text(
        f'#!/bin/sh\nif [ "$1 $2" = "link del" ]; then exit 1; fi\n'
        f'exec "{ip}" "$@"\n'
    )
    wrapper.chmod(0o755)
    result = run_in_namespace(
        'if sh "$1" create wg0 "$2" fq; then exit 1; else rc=$?; fi; '
        '[ "$rc" -eq 1 ]; ip link show "$2" >/dev/null; '
        '! ip link show wg0 >/dev/null 2>&1',
        env=env,
    )
    assert result.returncode == 0, result.stderr


def test_create_refuses_existing_interface_without_deleting_it():
    result = run_in_namespace(
        'ip link add wg0 type wireguard; old=$(ip -o link show wg0); '
        'if sh "$1" create wg0 "$2"; then exit 1; else rc=$?; fi; [ "$rc" -eq 12 ]; '
        '[ "$old" = "$(ip -o link show wg0)" ]; '
        '! ip link show "$2" >/dev/null 2>&1; '
        '[ "$(ip -o link show | wc -l)" -eq 2 ]'
    )
    assert result.returncode == 0, result.stderr


def test_cleanup_does_not_delete_a_replacement_with_the_same_name():
    result = run_in_namespace(
        'sh "$1" create wg0 "$2"; ip link del wg0; '
        'ip link add wg0 type wireguard; replacement=$(ip -o link show wg0); '
        'if sh "$1" delete wg0 "$2"; then exit 1; fi; '
        '[ "$replacement" = "$(ip -o link show wg0)" ]'
    )
    assert result.returncode == 0, result.stderr


def test_cleanup_refuses_a_link_whose_ownership_name_was_removed():
    result = run_in_namespace(
        'sh "$1" create wg0 "$2"; '
        'ip link property del dev wg0 altname "$2"; old=$(ip -o link show wg0); '
        'if sh "$1" delete wg0 "$2"; then exit 1; fi; '
        '[ "$old" = "$(ip -o link show wg0)" ]'
    )
    assert result.returncode == 0, result.stderr


def test_duplicate_ownership_name_is_not_adopted():
    result = run_in_namespace(
        'ip link add other0 type wireguard; ip link property add other0 altname "$2"; '
        'if sh "$1" create wg0 "$2"; then exit 1; else rc=$?; fi; [ "$rc" -eq 1 ]; '
        'ip link show other0 >/dev/null; ip link show wt0123456789 >/dev/null; '
        '! ip link show wg0 >/dev/null 2>&1'
    )
    assert result.returncode == 0, result.stderr


def test_failed_owner_attachment_preserves_a_replaced_temporary_link(tmp_path):
    ip = shutil.which("ip")
    wrapper = tmp_path / "ip"
    wrapper.write_text(
        f'#!/bin/sh\nif [ "$1 $2 $3" = "link property add" ]; then\n'
        f'  "{ip}" link del "$5" && "{ip}" link add "$5" type wireguard\n'
        f'  exit 1\nfi\nexec "{ip}" "$@"\n'
    )
    wrapper.chmod(0o755)
    env = os.environ | {"PATH": f"{tmp_path}:{os.environ['PATH']}"}
    result = run_in_namespace(
        'if sh "$1" create wg0 "$2"; then exit 1; else rc=$?; fi; '
        '[ "$rc" -eq 1 ]; ip link show wt0123456789 >/dev/null; '
        '! ip link show wg0 >/dev/null 2>&1',
        env=env,
    )
    assert result.returncode == 0, result.stderr


def test_temporary_name_collision_is_known_not_to_create_a_link():
    result = run_in_namespace(
        'ip link add wt0123456789 type wireguard; old=$(ip -o link show wt0123456789); '
        'if sh "$1" create wg0 "$2"; then exit 1; else rc=$?; fi; [ "$rc" -eq 10 ]; '
        '[ "$old" = "$(ip -o link show wt0123456789)" ]; '
        '! ip link show wg0 >/dev/null 2>&1'
    )
    assert result.returncode == 0, result.stderr


def test_failed_create_can_have_installed_a_temporary_link(tmp_path):
    ip = shutil.which("ip")
    wrapper = tmp_path / "ip"
    wrapper.write_text(
        f'#!/bin/sh\nif [ "$1 $2" = "link add" ]; then "{ip}" "$@" || exit 1; exit 1; fi\n'
        f'exec "{ip}" "$@"\n'
    )
    wrapper.chmod(0o755)
    env = os.environ | {"PATH": f"{tmp_path}:{os.environ['PATH']}"}
    result = run_in_namespace(
        'if sh "$1" create wg0 "$2"; then exit 1; else rc=$?; fi; '
        '[ "$rc" -eq 1 ]; ip link show wt0123456789 >/dev/null',
        env=env,
    )
    assert result.returncode == 0, result.stderr
