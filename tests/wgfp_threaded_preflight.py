"""Host tests for the WGFP threaded-mode preflight and reboot detection in testrig-wgfp-counters.sh."""

from pathlib import Path
import re
import subprocess

import pytest

pytestmark = pytest.mark.host
HELPER = Path(__file__).resolve().parents[1] / "bin/testrig-wgfp-counters.sh"

# dut() stub: state lives in files under $S so writes made by the helper persist between calls.
STUB = r"""
dut() {
  printf '%s\n' "$1" >> "$S/calls"
  case $1 in
    *wgfp_peer_lease*) echo unowned;;
    *wgfp_peer*) cat "$S/peer";;
    *"head -n1 /sys/kernel/debug/dpaa_wgfp/stats_sw"*)
      [ ! -e "$S/sw-missing" ] || return 1
      echo "touch=0 sinks=0 threaded=$(cat "$S/threaded") tx_batch=32 frm_cnt_encoding=none";;
    *"cat /sys/kernel/debug/dpaa_wgfp/stats_sw"*)
      [ ! -e "$S/sw-missing" ] || return 1
      printf '%s\n' 'touch=0 sinks=1 xmit_ok=10 xmit_fail=0 xmit_ern=0 frm_cnt_encoding=none' \
        'sink0 port=0 kind=global cpu=0 fqid=0x2be frames=10 bytes=100 errs=0 sg=0 irqs=0';;
    *"dpaa_wgfp/ctl"*)
      [ "$(cat "$S/napis")" = 0 ] || return 1
      echo "$1" | grep -o 'threaded [01]' | cut -d' ' -f2 > "$S/threaded";;
    *RuntimeWatchdogUSec*) cat "$S/watchdog";;
    *boot_id*) cat "$S/boot";;
    *) return 1;;
  esac
}
"""


def run(tmp_path, body, threaded="0", napis="0", watchdog="10s", env=None):
    for name, value in {"peer": "off", "threaded": threaded, "napis": napis,
                        "watchdog": watchdog,
                        "boot": "11111111-1111-1111-1111-111111111111"}.items():
        (tmp_path / name).write_text(value + "\n")
    script = f'S={tmp_path}; . "{HELPER}"; {STUB}\n{body}'
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                            env={"PATH": "/usr/bin:/bin", "WGFP": "1", **(env or {})})
    return result, (tmp_path / "threaded").read_text().strip()


def test_preflight_enables_threaded_before_first_bind(tmp_path):
    result, threaded = run(tmp_path, "wgfp_require_unbound")
    assert result.returncode == 0, result.stderr
    assert threaded == "1"


def test_preflight_refuses_when_napis_already_exist(tmp_path):
    result, threaded = run(tmp_path, "wgfp_require_unbound", napis="1")
    assert result.returncode == 1
    assert "reboot the DUT" in result.stderr
    assert threaded == "0"


@pytest.mark.parametrize("watchdog, ok", [("10s", False), ("2min", True), ("120s", True)])
def test_explicit_unthreaded_needs_raised_watchdog(tmp_path, watchdog, ok):
    result, threaded = run(tmp_path, "wgfp_require_unbound", watchdog=watchdog,
                           env={"WGFP_THREADED": "0"})
    assert (result.returncode == 0) is ok, result.stderr
    assert threaded == "0"


def test_software_rig_leaves_threaded_alone(tmp_path):
    result, threaded = run(tmp_path, "wgfp_require_unbound", env={"WGFP": "0"})
    assert result.returncode == 0, result.stderr
    assert threaded == "0"


def test_sample_reports_dut_reboot_instead_of_unbound(tmp_path):
    body = ("WGFP_COUNTER_LOG=$S/log; WGFP_QMAN_BOOT_BEFORE=22222222-2222-2222-2222-222222222222; "
            "wgfp_counter_sample wg-v4-eth3 up 1 after")
    result, _ = run(tmp_path, body)
    assert result.returncode == 1
    assert "DUT rebooted" in result.stderr
    assert "not bound" not in result.stderr


def test_missing_software_stats_refuses_setup_without_legacy_query(tmp_path):
    result, threaded = run(tmp_path, 'touch "$S/sw-missing"; wgfp_require_unbound')
    assert result.returncode != 0
    assert threaded == "0"
    calls = (tmp_path / "calls").read_text().splitlines()
    assert not any("dpaa_wgfp/ctl" in call for call in calls)
    assert not any(re.search(r"dpaa_wgfp/stats(?!_sw)", call) for call in calls)


def test_counter_sample_uses_software_sink_stats_without_legacy_query(tmp_path):
    body = ('echo "on dev=wg0" > "$S/peer"; WGFP_COUNTER_LOG=$S/log; '
            'wgfp_counter_sample wg-v4 up 1 before')
    result, _ = run(tmp_path, body)
    assert result.returncode == 0, result.stderr
    assert "sink0 port=0 kind=global" in (tmp_path / "log").read_text()
    calls = (tmp_path / "calls").read_text().splitlines()
    assert not any(re.search(r"dpaa_wgfp/stats(?!_sw)", call) for call in calls)


def test_missing_software_stats_refuses_counter_sample(tmp_path):
    body = ('echo "on dev=wg0" > "$S/peer"; touch "$S/sw-missing"; '
            'WGFP_COUNTER_LOG=$S/log; wgfp_counter_sample wg-v4 up 1 before')
    result, _ = run(tmp_path, body)
    assert result.returncode != 0
    assert "cannot read WGFP QMan counters" in result.stderr
    assert not (tmp_path / "log").exists()
    calls = (tmp_path / "calls").read_text().splitlines()
    assert not any(re.search(r"dpaa_wgfp/stats(?!_sw)", call) for call in calls)
