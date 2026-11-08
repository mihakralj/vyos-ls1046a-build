"""Host tests for the production ASK disengage script."""

import os
from pathlib import Path
import shlex
import subprocess
from types import SimpleNamespace

import ask2_offload
import pytest


pytestmark = pytest.mark.host
ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "board/scripts/vyos-offload-ask"


def run_disengage(tmp_path, *, armed=None, faulted=None, fault_state=None,
                  f291_marker=False, fail="", module_loaded=True, port="0x11"):
    pcd = tmp_path / "pcd"
    pcd.mkdir()
    if armed is not None:
        status = f"Armed ports: {armed}\n"
        if faulted is not None:
            status += f"Faulted ports: {faulted}\n"
        if fault_state is not None:
            status += f"FE fault/staging: {fault_state}\n"
        if f291_marker:
            status += "debugfs FE arm changes disabled: ownership unresolved\n"
        (pcd / "fe_arm").write_text(status)
    module = tmp_path / "ask-module"
    if module_loaded:
        module.mkdir()
    calls = tmp_path / "calls"
    calls.touch()
    bindir = tmp_path / "bin"
    bindir.mkdir()
    conntrack = bindir / "conntrack"
    conntrack.write_text(
        '#!/bin/sh\nprintf "conntrack:%s\\n" "$*" >> "$CALLS"\n'
        '[ "$FAIL" != conntrack ]\n'
    )
    conntrack.chmod(0o755)

    source = SCRIPT.read_text()
    start = source.index("\ndisengage() {")
    end = source.index("\n# ---------------------------------------------------------------------------\n"
                       "# hit_engage", start)
    body = source[start:end]
    port_start = source.index("\nport_id_dec() {")
    port_end = source.index("\n# ---------------------------------------------------------------------------",
                            port_start)
    port_body = source[port_start:port_end]
    original = "[ ! -d /sys/module/ask ]"
    assert body.count(original) == 1
    body = body.replace(original, '[ ! -d "$ASK_MODULE" ]')
    harness = """
die()  { echo "ERROR: $*" >&2; exit 1; }
warn() { echo "WARNING: $*" >&2; }
info() { echo "$*"; }
ynl_do() {
    printf 'ynl:%s\\n' "$1" >> "$CALLS"
    if [ "$FAIL" = disengage-cleared ] && [ "$1" = disengage ]; then
        printf 'Armed ports: (none)\\n' > "$PCD_BASE/fe_arm"
        return 1
    fi
    [ "$FAIL" != "$1" ] || return 1
    if [ "$FAIL" = stays-armed ] && [ "$1" = disengage ]; then
        return 0
    fi
    if [ "$FAIL" = malformed-arm ] && [ "$1" = disengage ]; then
        printf 'invalid arm state\\n' > "$PCD_BASE/fe_arm"
        return 0
    fi
    if [ "$1" = disengage ] && [ -f "$PCD_BASE/fe_arm" ]; then
        printf 'Armed ports: (none)\\n' > "$PCD_BASE/fe_arm"
    fi
}
""" + port_body + body + "\ndisengage\n"
    env = {
        **os.environ,
        "PATH": f"{bindir}:/usr/bin:/bin",
        "PCD_BASE": str(pcd),
        "ASK_MODULE": str(module),
        "CALLS": str(calls),
        "PORT": port,
        "FAIL": fail,
    }
    result = subprocess.run(
        ["bash", "-c", harness], env=env, text=True, capture_output=True,
        check=False,
    )
    return result, calls.read_text().splitlines()


def test_unarmed_port_still_propagates_retained_state(tmp_path):
    result, calls = run_disengage(tmp_path, armed="(none)", fail="disengage")

    assert result.returncode != 0
    assert "disengage of 0x11 failed" in result.stderr
    assert calls == ["ynl:disengage"]


def test_busy_disengage_does_not_flush_unrelated_flows(tmp_path):
    result, calls = run_disengage(tmp_path, armed="0x11", fail="disengage")

    assert result.returncode != 0
    assert "disengage of 0x11 failed" in result.stderr
    assert calls == ["ynl:disengage"]


def test_successful_disengage_does_not_globally_flush(tmp_path):
    result, calls = run_disengage(tmp_path, armed="0x11")

    assert result.returncode == 0, result.stderr
    assert calls == ["ynl:disengage"]
    assert "DISENGAGED" in result.stdout


def test_successful_response_with_armed_port_is_rejected(tmp_path):
    result, calls = run_disengage(tmp_path, armed="0x11", fail="stays-armed")

    assert result.returncode != 0
    assert "still armed" in result.stderr
    assert "DISENGAGED" not in result.stdout
    assert calls == ["ynl:disengage"]


@pytest.mark.parametrize(("port", "armed"), [
    ("17", "0x10 0x11"),
    ("0x011", "0x10 0x11"),
    ("0X11", "0x10 0x11"),
    ("13", "0x09 0x0d 0x10"),
    ("0xD", "0x09 0x0d 0x10"),
    ("0x20", "0x1f 0x20"),
    ("0x27", "0x20 0x27"),
])
def test_readback_compares_numeric_port_id(tmp_path, port, armed):
    result, calls = run_disengage(tmp_path, armed=armed,
                                  fail="stays-armed", port=port)

    assert result.returncode != 0
    assert "still armed" in result.stderr
    assert "DISENGAGED" not in result.stdout
    assert calls == ["ynl:disengage"]


@pytest.mark.parametrize("armed", [
    "", "unknown", "0x10,0x11", "0x1g", "0x00", "0x07", "0x28", "(none) 0x10",
    "0x10\nArmed ports: 0x11",
])
def test_invalid_arm_list_does_not_confirm_disengage(tmp_path, armed):
    result, calls = run_disengage(tmp_path, armed=armed, fail="stays-armed")

    assert result.returncode != 0
    assert "FE arm state is unreadable" in result.stderr
    assert "DISENGAGED" not in result.stdout
    assert calls == ["ynl:disengage"]


@pytest.mark.parametrize("armed", ["0x10", "0x20", "0x27"])
def test_other_armed_port_does_not_block_numeric_id(tmp_path, armed):
    result, calls = run_disengage(tmp_path, armed=armed,
                                  fail="stays-armed", port="17")

    assert result.returncode == 0, result.stderr
    assert "DISENGAGED" in result.stdout
    assert calls == ["ynl:disengage"]


@pytest.mark.parametrize(("faulted", "fault_state"), [
    ("0x11", "yes"),
    ("0x20", "yes"),
    ("(none)", "yes"),
    ("0x10", "no"),
])
def test_retained_fault_or_staging_does_not_confirm_disengage(
        tmp_path, faulted, fault_state):
    result, calls = run_disengage(
        tmp_path, armed="(none)", faulted=faulted, fault_state=fault_state,
        fail="stays-armed",
    )

    assert result.returncode != 0
    assert "fault" in result.stderr.lower()
    assert "DISENGAGED" not in result.stdout
    assert calls == ["ynl:disengage"]


@pytest.mark.parametrize(("faulted", "fault_state"), [
    ("", "no"),
    ("0x28", "no"),
    ("(none) 0x10", "no"),
    ("(none)\nFaulted ports: 0x10", "no"),
    ("(none)", "unknown"),
    ("(none)", None),
    (None, "no"),
])
def test_malformed_fault_readback_does_not_confirm_disengage(
        tmp_path, faulted, fault_state):
    result, calls = run_disengage(
        tmp_path, armed="(none)", faulted=faulted, fault_state=fault_state,
        fail="stays-armed",
    )

    assert result.returncode != 0
    assert "fault" in result.stderr.lower()
    assert "DISENGAGED" not in result.stdout
    assert calls == ["ynl:disengage"]


def test_clean_fault_readback_confirms_disengage(tmp_path):
    result, calls = run_disengage(
        tmp_path, armed="(none)", faulted="(none)", fault_state="no",
        fail="stays-armed",
    )

    assert result.returncode == 0, result.stderr
    assert "DISENGAGED" in result.stdout
    assert "unverified" not in result.stdout
    assert calls == ["ynl:disengage"]


def test_legacy_readback_reports_fault_state_unverified(tmp_path):
    result, calls = run_disengage(
        tmp_path, armed="(none)", fail="stays-armed",
    )

    assert result.returncode == 0, result.stderr
    assert "fault state unverified" in result.stdout
    assert "fault state" in result.stderr
    assert calls == ["ynl:disengage"]


def test_f291_readback_without_fault_fields_is_rejected(tmp_path):
    result, calls = run_disengage(
        tmp_path, armed="(none)", f291_marker=True, fail="stays-armed",
    )

    assert result.returncode != 0
    assert "fault state is unreadable" in result.stderr
    assert "DISENGAGED" not in result.stdout
    assert calls == ["ynl:disengage"]


def assert_ask2_armed(status):
    dut = SimpleNamespace(
        execute=lambda *args, **kwargs: SimpleNamespace(rc=0, stdout=status),
    )
    bench = SimpleNamespace(
        dut={"lan_if": "eth3", "wan_if": "eth4"},
        endpoint=lambda role: dut,
    )
    ask2_offload._assert_armed(bench)


@pytest.mark.parametrize("status", [
    "Faulted ports: 0x10\nFE fault/staging: yes\n",
    "Faulted ports: (none)\nFE fault/staging: yes\n",
    "Faulted ports: (none)\n",
    "Faulted ports: (none)\nFE fault/staging: no\nFE fault/staging: yes\n",
    "debugfs FE arm changes disabled: ownership unresolved\n",
])
def test_ask2_armed_check_rejects_faulted_or_incomplete_state(status):
    with pytest.raises(AssertionError):
        assert_ask2_armed(
            "fe_pool engaged: YES\nArmed ports: 0x10 0x11\n" + status
        )


@pytest.mark.parametrize("status", [
    "",
    "Faulted ports: (none)\nFE fault/staging: no\n",
])
def test_ask2_armed_check_accepts_clean_and_legacy_state(status):
    assert_ask2_armed(
        "fe_pool engaged: YES\nArmed ports: 0x10 0x11\n" + status
    )


def run_compare_offload(*, mode, status, read_ok=True):
    source = (ROOT / "bin/testrig-compare.sh").read_text()
    start = source.index("\noffload(){")
    end = source.index("\ndut_snap(){", start)
    harness = """
say() { echo "$*"; }
cli() { :; }
sleep() { :; }
mock_dut() {
    [ "$READ_OK" = yes ] || return 1
    printf '%s\\n' "$FE_STATE"
}
DUT=mock_dut
off=0
""" + source[start:end] + '\noffload "$MODE"\nprintf "off=%s\\n" "$off"\n'
    return subprocess.run(
        ["bash", "-c", harness],
        env={**os.environ, "MODE": mode, "FE_STATE": status,
             "READ_OK": "yes" if read_ok else "no"},
        text=True, capture_output=True, check=False,
    )


@pytest.mark.parametrize("mode", ["on", "off"])
def test_compare_rig_rejects_retained_fault(mode):
    armed = "0x10 0x11" if mode == "on" else "(none)"
    result = run_compare_offload(
        mode=mode,
        status=f"Armed ports: {armed}\nFaulted ports: 0x10\n"
               "FE fault/staging: yes\n",
    )

    assert result.returncode != 0
    assert "fault" in result.stdout.lower()


def test_compare_rig_does_not_confirm_unreadable_disengage():
    result = run_compare_offload(mode="off", status="", read_ok=False)

    assert result.returncode != 0
    assert "read" in result.stdout.lower()


def test_compare_rig_does_not_confirm_malformed_disengage():
    result = run_compare_offload(mode="off", status="Armed ports: unknown\n")

    assert result.returncode != 0
    assert "read" in result.stdout.lower()


@pytest.mark.parametrize("mode", ["on", "off"])
def test_compare_rig_accepts_clean_readback(mode):
    armed = "0x10 0x11" if mode == "on" else "(none)"
    result = run_compare_offload(
        mode=mode,
        status=f"Armed ports: {armed}\nFaulted ports: (none)\n"
               "FE fault/staging: no\n",
    )

    assert result.returncode == 0, result.stdout
    assert f"off={0 if mode == 'on' else 1}" in result.stdout


def run_matrix_detect_mode(status):
    source = (ROOT / "bin/testrig-combo-matrix.sh").read_text()
    start = source.index("\ndetect_mode() {")
    end = source.index("\n# ------------------------------------------------------------ check", start)
    harness = """
SSHD() {
    case "$1" in
        *nft*) printf '1\\n' ;;
        *"grep -c"*) printf '%s\\n' "$FE_STATE" | grep -c 'Armed ports: 0x' ;;
        *) printf '%s\\n' "$FE_STATE" ;;
    esac
}
""" + source[start:end] + "\ndetect_mode\n"
    return subprocess.run(
        ["bash", "-c", harness], env={**os.environ, "FE_STATE": status},
        text=True, capture_output=True, check=False,
    )


@pytest.mark.parametrize("status", [
    "Faulted ports: 0x10\nFE fault/staging: yes\n",
    "Faulted ports: (none)\nFE fault/staging: yes\n",
    "debugfs FE arm changes disabled: ownership unresolved\n",
])
def test_matrix_does_not_label_uncertain_fe_as_hardware(status):
    result = run_matrix_detect_mode("Armed ports: 0x10 0x11\n" + status)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "UNKNOWN"


@pytest.mark.parametrize("status", [
    "",
    "Faulted ports: (none)\nFE fault/staging: no\n",
])
def test_matrix_labels_clean_or_legacy_fe_as_hardware(status):
    result = run_matrix_detect_mode("Armed ports: 0x10 0x11\n" + status)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "HW"


def test_malformed_arm_readback_is_not_treated_as_unarmed(tmp_path):
    result, calls = run_disengage(tmp_path, armed="0x11", fail="malformed-arm")

    assert result.returncode != 0
    assert "FE arm state is unreadable" in result.stderr
    assert "DISENGAGED" not in result.stdout
    assert calls == ["ynl:disengage"]


def test_failed_disengage_is_not_masked_by_unarmed_readback(tmp_path):
    result, calls = run_disengage(tmp_path, armed="0x11", fail="disengage-cleared")

    assert result.returncode != 0
    assert "disengage of 0x11 failed" in result.stderr
    assert "DISENGAGED" not in result.stdout
    assert calls == ["ynl:disengage"]


def test_absent_module_and_unreadable_arm_state(tmp_path):
    absent = tmp_path / "absent"
    absent.mkdir()
    result, calls = run_disengage(absent, module_loaded=False)
    assert result.returncode == 0
    assert calls == []

    unreadable = tmp_path / "unreadable"
    unreadable.mkdir()
    result, calls = run_disengage(unreadable, fail="disengage")
    assert result.returncode != 0
    assert "disengage of 0x11 failed" in result.stderr
    assert calls == ["ynl:disengage"]

    success = tmp_path / "unreadable-success"
    success.mkdir()
    result, calls = run_disengage(success)
    assert result.returncode == 0
    assert "state unverified" in result.stdout
    assert calls == ["ynl:disengage"]


@pytest.mark.parametrize("function", ["hit_engage", "hit_disengage"])
def test_unsafe_hit_lifecycle_is_refused_before_any_write(tmp_path, function):
    nodes = (
        "fe_port", "fe_pool", "fe_singletons", "fe_ehash", "fe_hashfe",
        "fe_enq", "fe_enter", "fe_flow", "fe_kg_ekfc", "fe_arm",
    )
    for node in nodes:
        (tmp_path / node).write_text(
            "Armed ports: 0x11\n" if node == "fe_arm" else "unchanged\n"
        )
    before = {node: (tmp_path / node).read_text() for node in nodes}
    source = SCRIPT.read_text()
    start = source.index("\nhit_engage() {")
    end = source.index("\n# ---------------------------------------------------------------------------\n"
                       "# status", start)
    harness = """
die() { echo "ERROR: $*" >&2; exit 1; }
info() { :; }
resolve_fqid() { echo 292; }
read_fe_offset() { echo 0x100; }
""" + source[start:end] + f"\n{function}\n"
    result = subprocess.run(
        ["bash", "-c", harness],
        env={**os.environ, "PCD_BASE": str(tmp_path), "PORT": "0x11"},
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "HIT diagnostic disabled" in result.stderr
    assert {node: (tmp_path / node).read_text() for node in nodes} == before


@pytest.mark.parametrize("verb", ["flow-del", "flow-clear"])
@pytest.mark.parametrize("write_fails", [False, True])
def test_flow_clear_reports_debugfs_result(tmp_path, verb, write_fails):
    pcd = tmp_path / "pcd"
    pcd.mkdir()
    if write_fails:
        (pcd / "fe_flow").mkdir()
    else:
        (pcd / "fe_flow").write_text("unchanged\n")
    source = SCRIPT.read_text()
    board_guard = 'if ! grep -q "fsl,ls1046a" /proc/device-tree/compatible 2>/dev/null; then'
    assert source.count(board_guard) == 1
    source = source.replace(board_guard, "if ! true; then")
    pcd_assignment = 'PCD_BASE="/sys/kernel/debug/fman_pcd/0"'
    assert source.count(pcd_assignment) == 1
    source = source.replace(pcd_assignment, f"PCD_BASE={shlex.quote(str(pcd))}")
    script = tmp_path / "vyos-offload-ask"
    script.write_text(source)

    result = subprocess.run(
        ["bash", str(script), verb], text=True, capture_output=True, check=False,
    )

    if write_fails:
        assert result.returncode != 0
        assert "flow clear failed" in result.stderr
        assert "flows CLEARED" not in result.stdout
    else:
        assert result.returncode == 0
        assert "flows CLEARED" in result.stdout
        assert (pcd / "fe_flow").read_text() == "clear\n"
