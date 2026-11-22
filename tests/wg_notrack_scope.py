"""Host tests for peer-scoped, lease-owned WireGuard NOTRACK rules."""

import os
from pathlib import Path
import subprocess

import pytest

pytestmark = pytest.mark.host
HELPER = Path(__file__).resolve().parents[1] / "bin/testrig-wg-nft.sh"
TOKEN = "0123456789abcdef0123456789abcdef"


def run_add(tmp_path, family, peer, dut):
    nft = tmp_path / "nft"
    nft.write_text("#!/bin/sh\ncat > \"$NFT_INPUT\"\n")
    nft.chmod(0o755)
    output = tmp_path / "rules"
    env = os.environ | {"PATH": f"{tmp_path}:{os.environ['PATH']}", "NFT_INPUT": str(output)}
    result = subprocess.run(
        ["sh", str(HELPER), "add", TOKEN, "3", "51820", family, peer, dut],
        env=env, capture_output=True, text=True, timeout=5,
    )
    return result, output.read_text() if output.exists() else ""


@pytest.mark.parametrize(("family", "peer", "dut", "prefix"), [
    ("4", "192.0.2.10", "192.0.2.1", "ip"),
    ("6", "2001:db8::10", "2001:db8::1", "ip6"),
])
def test_notrack_matches_both_endpoints_and_udp_ports(tmp_path, family, peer, dut, prefix):
    result, rules = run_add(tmp_path, family, peer, dut)
    assert result.returncode == 0, result.stderr
    assert f"{prefix} saddr {peer} {prefix} daddr {dut} udp sport 51820 udp dport 51820 notrack" in rules
    assert f"{prefix} saddr {dut} {prefix} daddr {peer} udp sport 51820 udp dport 51820 notrack" in rules
    assert f'comment "wgrig-{TOKEN}"' in rules


@pytest.mark.parametrize(("family", "peer", "dut"), [
    ("5", "192.0.2.10", "192.0.2.1"),
    ("4", "192.0.2.10;drop", "192.0.2.1"),
    ("6", "2001:db8::10", "2001:db8::1}"),
])
def test_notrack_rejects_unsafe_endpoint_input_before_nft(tmp_path, family, peer, dut):
    result, rules = run_add(tmp_path, family, peer, dut)
    assert result.returncode != 0
    assert not rules
