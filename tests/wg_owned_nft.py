"""Exercise lease-owned NOTRACK tables in disposable network namespaces."""

import os
from pathlib import Path
import shutil
import subprocess

import pytest

pytestmark = pytest.mark.host
NFT_HELPER = Path(__file__).resolve().parents[1] / "bin/testrig-wg-nft.sh"
TOKEN = "0123456789abcdef0123456789abcdef"
NFT_PATH = shutil.which("nft") or ("/usr/sbin/nft" if Path("/usr/sbin/nft").exists() else None)


@pytest.fixture(scope="module", autouse=True)
def namespace_available():
    if not shutil.which("unshare") or not NFT_PATH:
        pytest.skip("unshare and nft are required")
    probe = subprocess.run(
        ["unshare", "-Urn", NFT_PATH, "list", "tables"],
        text=True, capture_output=True, check=False,
    )
    if probe.returncode:
        pytest.skip(f"disposable nft namespace unavailable: {probe.stderr.strip()}")


def run_in_namespace(script):
    env = os.environ | {"PATH": f"{Path(NFT_PATH).parent}:{os.environ['PATH']}"}
    return subprocess.run(
        ["unshare", "-Urn", "sh", "-eu", "-c", script, "_", str(NFT_HELPER), TOKEN],
        text=True, capture_output=True, check=False, env=env,
    )


@pytest.mark.parametrize(("mode", "expected"), [
    ("1", "udp dport 51820 notrack"),
    ("2", "notrack"),
])
def test_owned_notrack_cleanup_preserves_other_tables(mode, expected):
    result = run_in_namespace(
        'nft add table inet foreign; '
        f'sh "$1" add "$2" {mode} 51820; '
        'nft list table inet "wgn$2" | grep -q "' + expected + '"; '
        'sh "$1" delete "$2"; '
        'nft list table inet foreign >/dev/null; '
        'if nft list table inet "wgn$2" >/dev/null 2>&1; then exit 1; fi'
    )
    assert result.returncode == 0, result.stderr


def test_existing_notrack_table_is_not_adopted():
    result = run_in_namespace(
        'nft "add table inet wgn$2 { comment \\"foreign\\"; }"; '
        'old=$(nft -a list table inet "wgn$2"); '
        'if sh "$1" add "$2" 1 51820; then exit 1; fi; '
        '[ "$old" = "$(nft -a list table inet "wgn$2")" ]'
    )
    assert result.returncode == 0, result.stderr


def test_replaced_notrack_table_is_not_deleted():
    result = run_in_namespace(
        'sh "$1" add "$2" 1 51820; nft delete table inet "wgn$2"; '
        'nft "add table inet wgn$2 { comment \\"foreign\\"; }"; '
        'old=$(nft -a list table inet "wgn$2"); '
        'if sh "$1" delete "$2"; then exit 1; fi; '
        '[ "$old" = "$(nft -a list table inet "wgn$2")" ]'
    )
    assert result.returncode == 0, result.stderr
