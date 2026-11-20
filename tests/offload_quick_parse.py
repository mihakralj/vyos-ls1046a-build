"""Host checks for quick benchmark TCP packet-count estimates."""
import json
import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.host
SCRIPT = Path(__file__).resolve().parents[1] / "bin/testrig-offload-quick.sh"


def run_parse(tmp_path, mss, data=None, *, raw=False, reverse=False):
    from testrig_iperf_measurement import tcp_result
    data = tcp_result() if data is None else data
    data["start"]["tcp_mss_default"] = mss
    source = tmp_path / "iperf.json"
    source.write_text(json.dumps(data))
    script = SCRIPT.read_text()
    parse = script[script.index("parse(){"):script.index('\necho "ts,cell,')]
    estimate = next(line for line in script.splitlines() if line.strip().startswith("est=$(awk "))
    return subprocess.run(
        ["bash", "-c",
         f'{parse}\nHERE="{SCRIPT.parent}"\nSTEADY_FROM=2 DUR=8 STREAMS=2 BIDIR=0\n'
         + ('REV=-R\n' if reverse else '')
         + 'read gbps gen rt bytes mss < <(parse "$1") || exit 1\n'
         + ('printf "%s %s\\n" "$gbps" "$bytes"\n' if raw else
          f'win=10\n{estimate}\nprintf "%s %s\\n" "$mss" "$est"\n'),
         "test", str(source)],
        capture_output=True, text=True,
    )


@pytest.mark.parametrize("mss", [1448, 8948, 8928])
def test_packet_estimate_uses_observed_mss(tmp_path, mss):
    result = run_parse(tmp_path, mss)
    assert result.returncode == 0, result.stderr
    observed, estimate = result.stdout.split()
    assert int(observed) == mss
    assert int(estimate) == int(8e9 / 8 / mss * 10)


@pytest.mark.parametrize("mss", [None, 0, -1])
def test_missing_mss_rejects_estimate(tmp_path, mss):
    result = run_parse(tmp_path, mss)
    assert result.returncode != 0
    assert "MSS" in result.stderr


def test_quick_excludes_extended_terminal_interval(tmp_path):
    from testrig_iperf_measurement import tcp_result
    data = tcp_result()
    data["intervals"][-1]["sum"].update(end=17, seconds=10, bits_per_second=0, bytes=0)
    result = run_parse(tmp_path, 1448, data, raw=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["8.000", "5000000000"]
    report = json.loads((tmp_path / "iperf.measurement.json").read_text())
    assert report["directions"]["forward"]["terminal_overrun_seconds"] == 9
    assert report["directions"]["forward"]["measured_seconds"] == 5


def test_quick_reverse_client_is_scored_as_receiver(tmp_path):
    from testrig_iperf_measurement import tcp_result
    result = run_parse(tmp_path, 1448, tcp_result(reverse=True), raw=True, reverse=True)
    assert result.returncode == 0, result.stderr
    report = json.loads((tmp_path / "iperf.measurement.json").read_text())
    assert report["directions"]["forward"]["role"] == "receiver"


@pytest.mark.parametrize("mode,family,address", [
    ("dnat4", "ip", "10.99.1.185"), ("dnat6", "ip6", "fd99:1::185"),
])
def test_owned_battery_port_is_used_in_dnat_rules(mode, family, address):
    script = SCRIPT.read_text()
    nat = script[script.index("nat_clear(){"):script.index("# dell1's mlx4 NIC")]
    result = subprocess.run(
        ["bash", "-c",
         'S=echo DUT_KIND=ask2 RIG_EXTERNAL_NAT_TOKEN= IPERF_PORT=5278\n'
         f'{nat}\nnat_set {mode}\n'],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert f"{family} daddr {address} tcp dport 5278 dnat" in result.stdout


def run_nat(tmp_path, token, listing):
    script = SCRIPT.read_text()
    nat = script[script.index("nat_clear(){"):script.index("# dell1's mlx4 NIC")]
    calls = tmp_path / "nat-calls"
    result = subprocess.run(
        ["bash", "-c",
         'mock_dut(){ case "$1" in '
         '*"nft -a list table ip"*) printf "%s\\n" "$LISTING";; '
         '*) printf "%s\\n" "$1" >> "$CALLS";; esac; }\n'
         'S=mock_dut DUT_KIND=vendor\n'
         f'{nat}\nnat_set 4w 192.0.2.2 198.51.100.1 && '
         'nat_set 4wg 192.0.2.2 203.0.113.3 && nat_clear\n'],
        env={"PATH": "/usr/bin:/bin", "CALLS": str(calls), "LISTING": listing,
             "RIG_EXTERNAL_NAT_TOKEN": token},
        capture_output=True, text=True, check=False,
    )
    return result, calls.read_text() if calls.exists() else ""


def test_external_nat_does_not_touch_shared_tables(tmp_path):
    token = "0" * 32
    listing = (
        f'comment "dut2-wan-{token}"\n'
        f'oifname "pppoe7" ip saddr 192.0.2.2 ip daddr 198.51.100.1 masquerade '
        f'comment "dut2-wan-{token}-ppp"\n'
        f'oifname "wgw0" ip saddr 192.0.2.2 ip daddr 203.0.113.3 masquerade '
        f'comment "dut2-wan-{token}-wg"\n'
    )
    result, calls = run_nat(tmp_path, token, listing)
    assert result.returncode == 0, result.stderr
    assert calls == ""


def test_external_nat_refuses_missing_or_foreign_owner(tmp_path):
    result, calls = run_nat(tmp_path, "0" * 32, 'comment "someone-else"')
    assert result.returncode != 0
    assert calls == ""


@pytest.mark.parametrize(
    ("mode", "family", "destination", "target"),
    [
        ("dnat4", "ip", "10.99.1.185", "10.99.2.113"),
        ("dnat6", "ip6", "fd99:1::185", "fd99:2::113"),
    ],
)
def test_external_nat_accepts_owned_dnat(
    tmp_path, mode, family, destination, target
):
    token = "a" * 32
    suffix = mode
    listing = (
        f'comment "ask2-wan-{token}"\n'
        f'iifname "eth3" {family} daddr {destination} tcp dport 5201 '
        f'dnat to {target} comment "ask2-wan-{token}-{suffix}"\n'
    )
    script = SCRIPT.read_text()
    nat = script[script.index("nat_clear(){"):script.index("# dell1's mlx4 NIC")]
    result = subprocess.run(
        ["bash", "-c",
         'mock_dut(){ printf "%s\\n" "$LISTING"; }\n'
         'S=mock_dut DUT_KIND=ask2\n'
         f'{nat}\nnat_set "$MODE" 192.0.2.1 "$DEST"\n'],
        env={
            "PATH": "/usr/bin:/bin", "LISTING": listing,
            "RIG_EXTERNAL_NAT_TOKEN": token,
            "RIG_EXTERNAL_NAT_OWNER": f"ask2-wan-{token}",
            "MODE": mode, "DEST": destination,
        },
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr


def test_external_nat_rejects_prefix_only_address_match(tmp_path):
    token = "0" * 32
    listing = (
        f'comment "dut2-wan-{token}"\n'
        f'oifname "pppoe7" ip saddr 192.0.2.20 ip daddr 198.51.100.1 masquerade '
        f'comment "dut2-wan-{token}-ppp"\n'
    )
    result, calls = run_nat(tmp_path, token, listing)
    assert result.returncode != 0
    assert calls == ""


def test_default_nat_behavior_is_unchanged(tmp_path):
    result, calls = run_nat(tmp_path, "", "")
    assert result.returncode == 0, result.stderr
    assert "nft add table ip ask2q" in calls
    assert "oifname pppoe7" in calls
    assert "oifname wgw0" in calls


@pytest.mark.parametrize(
    ("mode", "family", "iface", "source", "dest", "suffix"),
    [
        ("4", "ip", "eth4", "192.0.2.1", "198.51.100.2", "nat4up"),
        ("4r", "ip", "eth3", "198.51.100.2", "192.0.2.1", "nat4down"),
        ("6", "ip6", "eth4", "2001:db8:1::1", "2001:db8:2::2", "nat6up"),
        ("6r", "ip6", "eth3", "2001:db8:2::2", "2001:db8:1::1", "nat6down"),
    ],
)
def test_external_nat_accepts_owned_routed_rule(tmp_path, mode, family, iface, source, dest, suffix):
    token = "1" * 32
    listing = (
        f'comment "dut2-wan-{token}"\n'
        f'oifname "{iface}" {family} saddr {source} {family} daddr {dest} masquerade '
        f'comment "dut2-wan-{token}-{suffix}"\n'
    )
    script = SCRIPT.read_text()
    nat = script[script.index("nat_clear(){"):script.index("# dell1's mlx4 NIC")]
    result = subprocess.run(
        ["bash", "-c",
         'mock_dut(){ printf "%s\\n" "$1" >> "$CALLS"; printf "%s\\n" "$LISTING"; }\n'
         'S=mock_dut DUT_KIND=vendor\n'
         f'{nat}\nnat_set "$MODE" "$SOURCE" "$DEST"\n'],
        env={"PATH": "/usr/bin:/bin", "CALLS": str(tmp_path / "calls"), "LISTING": listing,
             "RIG_EXTERNAL_NAT_TOKEN": token, "MODE": mode, "SOURCE": source, "DEST": dest},
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "calls").read_text().splitlines() == [
        f"nft -a list table {family} vwn{token}"
    ]


def run_builtin_nat(tmp_path, dut_kind, mode, source, dest):
    script = SCRIPT.read_text()
    nat = script[script.index("nat_clear(){"):script.index("# dell1's mlx4 NIC")]
    calls = tmp_path / "calls"
    result = subprocess.run(
        ["bash", "-c",
         'mock_dut(){ printf "%s\\n" "$1" >> "$CALLS"; }\n'
         'pf_nat(){ mock_dut "$1"; }\n'
         'S=mock_dut\n'
         f'{nat}\nnat_set "$MODE" "$SOURCE" "$DEST"\n'],
        env={"PATH": "/usr/bin:/bin", "CALLS": str(calls), "DUT_KIND": dut_kind,
             "RIG_EXTERNAL_NAT_TOKEN": "", "MODE": mode, "SOURCE": source, "DEST": dest},
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    return calls.read_text()


@pytest.mark.parametrize("dut_kind", ["vendor", "opnsense"])
@pytest.mark.parametrize(
    ("mode", "source", "dest"),
    [
        ("4", "192.0.2.1", "198.51.100.2"),
        ("4r", "198.51.100.2", "192.0.2.1"),
        ("6", "2001:db8:1::1", "2001:db8:2::2"),
        ("6r", "2001:db8:2::2", "2001:db8:1::1"),
    ],
)
def test_builtin_routed_nat_matches_endpoint_override(tmp_path, dut_kind, mode, source, dest):
    calls = run_builtin_nat(tmp_path, dut_kind, mode, source, dest)
    expected = f"from {source} " if dut_kind == "opnsense" else f"saddr {source} "
    assert expected in calls


@pytest.mark.parametrize("dut_kind", ["vendor", "opnsense"])
@pytest.mark.parametrize(
    ("mode", "source", "dest", "network"),
    [
        ("4", "10.99.1.112", "10.99.2.113", "10.99.1.0/24"),
        ("4r", "10.99.2.113", "10.99.1.112", "10.99.2.0/24"),
        ("6", "fd99:1::112", "fd99:2::113", "fd99:1::/64"),
        ("6r", "fd99:2::113", "fd99:1::112", "fd99:2::/64"),
    ],
)
def test_builtin_routed_nat_preserves_default_network(tmp_path, dut_kind, mode, source, dest, network):
    calls = run_builtin_nat(tmp_path, dut_kind, mode, source, dest)
    expected = f"from {network} " if dut_kind == "opnsense" else f"saddr {network} "
    assert expected in calls


def test_rps_can_be_preserved_for_an_externally_owned_rig(tmp_path):
    script = SCRIPT.read_text()
    rps = script[script.index("rps_set(){"):script.index("\ncleanup(){")]
    calls = tmp_path / "calls"
    result = subprocess.run(
        ["bash", "-c",
         'mock_gen(){ printf "%s\\n" "$*" >> "$CALLS"; }\n'
         'D1=mock_gen D1_IF=test0 RIG_NS=fixture RIG_PRESERVE_RPS=1\n'
         f'{rps}\nrps_set on; rps_set off\n'],
        env={"PATH": "/usr/bin:/bin", "CALLS": str(calls)},
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert not calls.exists()


@pytest.mark.parametrize("port", ["0", "65536", "-1", "5201;false"])
def test_invalid_iperf_port_is_rejected_before_bench_access(tmp_path, port):
    env = os.environ | {"TESTRIG_ENV": str(tmp_path / "missing"), "OUTDIR": str(tmp_path),
                        "IPERF_PORT": port, "DUT_KIND": "ask2"}
    env.pop("ASK2_DUT", None)
    result = subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True)
    assert result.returncode != 0
    assert "IPERF_PORT" in result.stderr


def test_routed_cells_use_explicit_generator_endpoint_overrides():
    script = SCRIPT.read_text()
    cells = script[script.index("QUICK_G1_IPV4="):script.index("\n# opt-in combo cells")]
    env = os.environ | {
        "QUICK_G1_IPV4": "192.0.2.1", "QUICK_G2_IPV4": "198.51.100.2",
        "QUICK_G1_IPV6": "2001:db8:1::1", "QUICK_G2_IPV6": "2001:db8:2::2",
        "QUICK_G1_VLAN4": "192.0.2.10", "QUICK_G2_VLAN4": "198.51.100.20",
        "QUICK_G1_VLAN6": "2001:db8:10::1", "QUICK_G2_VLAN6": "2001:db8:20::2",
        "PPPOE_SERVER4": "192.0.2.50", "PPPOE_SERVER6": "2001:db8:50::1",
    }
    result = subprocess.run(
        ["bash", "-c", f'{cells}\nprintf "%s\\n" "$CELLS_ALL"\n'],
        env=env, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "unicast-v4|none|d1|192.0.2.1|198.51.100.2" in result.stdout
    assert "nat-v6-rev|6r|d2|2001:db8:2::2|2001:db8:1::1" in result.stdout
    assert "vlan-v4|none|d1|192.0.2.10|198.51.100.20" in result.stdout
    assert "vlanport-v6|none|d1|2001:db8:10::1|2001:db8:2::2" in result.stdout


@pytest.mark.parametrize("endpoint", ["pppoe", "routed"])
def test_scoped_ipv6_endpoint_is_rejected_before_bench_access(endpoint):
    script = SCRIPT.read_text()
    if endpoint == "pppoe":
        snippet = script[script.index("PPPOE_SERVER4="):script.index("\nDUT=${DUT:-")]
        env = os.environ | {"PPPOE_SERVER4": "192.0.2.1", "PPPOE_SERVER6": "fe80::1%zone"}
    else:
        snippet = script[script.index("QUICK_G1_IPV4="):script.index("\n# opt-in combo cells")]
        env = os.environ | {
            "QUICK_G1_IPV6": "fe80::1%zone",
            "PPPOE_SERVER4": "192.0.2.1", "PPPOE_SERVER6": "2001:db8::1",
        }
    result = subprocess.run(
        ["bash", "-c", snippet], env=env, capture_output=True, text=True, check=False,
    )
    assert result.returncode != 0


@pytest.mark.parametrize(
    ("second_eth3", "second_eth4", "expected"),
    [(11, 21, 0), (10, 21, 1), (11, 20, 1)],
)
def test_optional_port_check_requires_activity_on_both_dut_ports(
    tmp_path, second_eth3, second_eth4, expected
):
    script = SCRIPT.read_text()
    check = script[script.index("verify_vendor_ports(){"):script.index('\necho "ts,cell,')]
    sample = tmp_path / "sample"
    sample.write_text(
        "cpu 1 1 1 1\nP eth3 10\nP eth4 20\n"
        f"cpu 2 2 2 2\nP eth3 {second_eth3}\nP eth4 {second_eth4}\n"
    )
    result = subprocess.run(
        ["bash", "-c", f'{check}\nDUT_KIND=vendor RIG_VERIFY_PORTS=1\nverify_vendor_ports "$1"\n',
         "test", str(sample)],
        capture_output=True, text=True, check=False,
    )
    assert (result.returncode == 0) == (expected == 0)
