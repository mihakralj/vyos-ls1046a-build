"""Host checks for OpenWrt WAN test resources."""

import os
from pathlib import Path
import shutil
import subprocess

import pytest

pytestmark = pytest.mark.host
ROOT = Path(__file__).resolve().parents[1]
NFT = ROOT / "bin/testrig-vendor-wan-nft.sh"
TOKEN = "0123456789abcdef0123456789abcdef"
SOURCE = "192.0.2.2"
PPP_DEST = "198.51.100.1"
WG_DEST = "203.0.113.3"


def mock_nft(tmp_path, owner):
    log = tmp_path / "calls"
    nft = tmp_path / "nft"
    nft.write_text(
        '#!/bin/sh\n'
        'printf "%s\\n" "$*" >> "$CALLS"\n'
        'case "$*" in\n'
        f'  "list tables") echo "table ip vwn{TOKEN}";;\n'
        f'  "-a list table ip vwn{TOKEN}")\n'
        f'    printf \'table ip vwn{TOKEN} # handle 42 {{\\n'
        f'    comment "%s"\\n}}\\n\' "$OWNER";;\n'
        'esac\n'
    )
    nft.chmod(0o755)
    env = os.environ | {"PATH": f"{tmp_path}:{os.environ['PATH']}",
                        "CALLS": str(log), "OWNER": owner}
    result = subprocess.run(["sh", str(NFT), "delete", TOKEN], env=env,
                            capture_output=True, text=True, check=False)
    return result, log.read_text().splitlines() if log.exists() else []


def test_delete_refuses_foreign_table(tmp_path):
    result, calls = mock_nft(tmp_path, "someone-else")
    assert result.returncode != 0
    assert "ownership" in result.stderr.lower()
    assert not any(call.startswith("delete") for call in calls)


def test_delete_owned_table_uses_verified_handle(tmp_path):
    result, calls = mock_nft(tmp_path, f"dut2-wan-{TOKEN}")
    assert result.returncode == 0, result.stderr
    assert calls[-1] == "delete table ip handle 42"


def test_add_uses_explicit_addresses(tmp_path):
    rules = tmp_path / "rules"
    nft = tmp_path / "nft"
    nft.write_text('#!/bin/sh\n[ "$*" = "-f -" ] && cat > "$RULES"\n')
    nft.chmod(0o755)
    env = os.environ | {"PATH": f"{tmp_path}:{os.environ['PATH']}", "RULES": str(rules)}
    result = subprocess.run(
        ["sh", str(NFT), "add", TOKEN, SOURCE, PPP_DEST, WG_DEST],
        env=env, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    content = rules.read_text()
    assert f'ip saddr {SOURCE} ip daddr {PPP_DEST} masquerade' in content
    assert f'ip saddr {SOURCE} ip daddr {WG_DEST} masquerade' in content


@pytest.mark.parametrize("bad", ["", "192.0.2.256", "192.0.2.1;delete", "1.2.3.4\nx"])
def test_add_rejects_invalid_addresses(tmp_path, bad):
    nft = tmp_path / "nft"
    nft.write_text('#!/bin/sh\ntouch "$CALLED"\n')
    nft.chmod(0o755)
    called = tmp_path / "called"
    env = os.environ | {"PATH": f"{tmp_path}:{os.environ['PATH']}", "CALLED": str(called)}
    result = subprocess.run(
        ["sh", str(NFT), "add", TOKEN, SOURCE, bad, WG_DEST],
        env=env, capture_output=True, text=True, check=False,
    )
    assert result.returncode != 0
    assert not called.exists()


def test_token_cannot_contain_newlines(tmp_path):
    nft = tmp_path / "nft"
    nft.write_text('#!/bin/sh\ntouch "$CALLED"\n')
    nft.chmod(0o755)
    called = tmp_path / "called"
    env = os.environ | {"PATH": f"{tmp_path}:{os.environ['PATH']}", "CALLED": str(called)}
    result = subprocess.run(
        ["sh", str(NFT), "delete", f"{TOKEN}\nforeign"],
        env=env, capture_output=True, text=True, check=False,
    )
    assert result.returncode != 0
    assert not called.exists()


def test_nat_table_round_trip_in_disposable_namespace():
    if not shutil.which("unshare") or not shutil.which("nft"):
        pytest.skip("unshare and nft are required")
    probe = subprocess.run(["unshare", "-Urn", "nft", "list", "tables"],
                           capture_output=True, check=False)
    if probe.returncode:
        pytest.skip("disposable network namespace unavailable")
    script = (
        'sh "$1" add "$2" "$3" "$4" "$5"; '
        'nft list table ip "vwn$2" | grep -q "oifname \\"pppoe7\\""; '
        'nft list table ip "vwn$2" | grep -q "oifname \\"wgw0\\""; '
        '! sh "$1" add "$2" "$3" "$4" "$5"; '
        'nft add table ip unrelated; '
        'sh "$1" delete "$2"; '
        '! nft list table ip "vwn$2" 2>/dev/null; '
        'nft list table ip unrelated >/dev/null'
    )
    result = subprocess.run(["unshare", "-Urn", "sh", "-eu", "-c",
                             script, "_", str(NFT), TOKEN, SOURCE, PPP_DEST, WG_DEST],
                            capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
