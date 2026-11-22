"""Host tests for the benchmark generators' tuning preflight."""

import json
import os
from pathlib import Path
import subprocess

import pytest

pytestmark = pytest.mark.host
ROOT = Path(__file__).resolve().parents[1]
TUNER = ROOT / "bin/testrig-gen-tune.sh"

FAKE_SSH = r"""#!/usr/bin/env python3
import os
from pathlib import Path
import shlex
import subprocess
import sys

args = sys.argv[1:]
remote = next(i for i, arg in enumerate(args) if "@" in arg)
command = shlex.split(args[remote + 1])
os.environ["FAKE_SYSTEMCTL_STATE"] += "-" + args[remote].split("@", 1)[1]
script = sys.stdin.read()
for source, target in (
    ("/usr/sbin/ethtool", os.environ["FAKE_ETHTOOL"]),
    ("/sys/", os.environ["FAKE_SYS"] + "/"),
    ("/proc/interrupts", os.environ["FAKE_INTERRUPTS"]),
    ("/proc/irq", os.environ["FAKE_IRQ"]),
):
    script = script.replace(source, target)
sys.exit(subprocess.run(command[1:], input=script, text=True).returncode)
"""

FAKE_ETHTOOL = r"""#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys

args = sys.argv[1:]
with Path(os.environ["FAKE_LOG"]).open("a") as log:
    log.write(json.dumps(["ethtool", *args]) + "\n")
state_path = Path(os.environ["FAKE_STATE"])
state = json.loads(state_path.read_text())
op, interface = args[:2]
if op == "-i":
    sys.exit(1 if interface == os.environ.get("FAKE_MISSING_INTERFACE") else 0)
if op == "-g":
    print("Current hardware settings:\nRX:\t" + str(state["ring"]) + "\nTX:\t4096")
elif op == "-l":
    channels = os.environ.get("FAKE_CHANNELS_" + interface.upper(), "1")
    print("Current hardware settings:\nCombined:\t" + channels)
elif op == "-G":
    if os.environ.get("FAKE_RING_FAILURE"):
        print("ring change failed", file=sys.stderr)
        sys.exit(1)
    state["ring"] = int(args[args.index("rx") + 1])
elif op == "-c":
    print("Adaptive RX: " + state["adaptive"] + "  TX: " + state["adaptive"])
elif op == "-C":
    state["adaptive"] = "on"
else:
    sys.exit(1)
state_path.write_text(json.dumps(state))
"""

FAKE_IP = r"""#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys

args = sys.argv[1:]
with Path(os.environ["FAKE_LOG"]).open("a") as log:
    log.write(json.dumps(["ip", *args]) + "\n")
if args[:2] != ["netns", "exec"]:
    sys.exit(1)
os.execvp(args[3], args[3:])
"""


def executable(path, content):
    path.write_text(content)
    path.chmod(0o755)


@pytest.fixture
def rig(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    executable(bindir / "ssh", FAKE_SSH)
    executable(bindir / "ethtool", FAKE_ETHTOOL)
    executable(bindir / "ip", FAKE_IP)
    executable(bindir / "nproc", "#!/bin/sh\necho 2\n")
    executable(bindir / "systemctl", """#!/bin/sh
if [ "${FAKE_SYSTEMCTL_MODE:-}" = fail-verify ]; then
    case "$1" in
        is-active) [ -e "$FAKE_SYSTEMCTL_STATE" ] && exit 1; exit 0 ;;
        stop) touch "$FAKE_SYSTEMCTL_STATE"; exit 0 ;;
    esac
fi
exit 3
""")

    sysfs = tmp_path / "sys"
    for cpu in range(2):
        cpu_dir = sysfs / f"devices/system/cpu/cpu{cpu}"
        (cpu_dir / "cpufreq").mkdir(parents=True)
        (cpu_dir / "cpufreq/scaling_governor").write_text("performance\n")
        (cpu_dir / "cpuidle/state2").mkdir(parents=True)
        (cpu_dir / "cpuidle/state2/disable").write_text("1\n")
    for interface in ("port0", "port1"):
        net_dir = sysfs / "class/net" / interface
        net_dir.mkdir(parents=True)
        (net_dir / "carrier").write_text("1\n")

    interrupts = tmp_path / "interrupts"
    interrupts.write_text(
        "16: 0 i40e-port0-TxRx-0\n"
        "17: 0 i40e-port1-TxRx-0\n"
    )
    irq = tmp_path / "irq"
    for number in (16, 17):
        (irq / str(number)).mkdir(parents=True)
        (irq / str(number) / "smp_affinity_list").write_text("0\n")
        (irq / str(number) / "effective_affinity_list").write_text("0\n")

    state = tmp_path / "state.json"
    state.write_text(json.dumps({"ring": 4096, "adaptive": "on"}))
    log = tmp_path / "commands.jsonl"
    env = os.environ | {
        "PATH": f"{bindir}:{os.environ['PATH']}",
        "TESTRIG_ENV": str(tmp_path / "no-rig-config"),
        "GEN1": "test@gen1",
        "GEN2": "test@gen2",
        "GEN_IF": "port0",
        "GEN_IF_NS": "port1",
        "VENDOR_NS": "custom-ns",
        "RIG_KEY": "",
        "FAKE_ETHTOOL": str(bindir / "ethtool"),
        "FAKE_SYS": str(sysfs),
        "FAKE_INTERRUPTS": str(interrupts),
        "FAKE_IRQ": str(irq),
        "FAKE_STATE": str(state),
        "FAKE_LOG": str(log),
        "FAKE_SYSTEMCTL_STATE": str(tmp_path / "systemctl-state"),
    }

    def run(mode, **extra_env):
        result = subprocess.run(
            ["bash", str(TUNER), mode],
            env=env | extra_env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        commands = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
        return result, commands

    return run, state, interrupts, irq, sysfs


def test_check_is_read_only_and_honors_namespace(rig):
    run, _, _, _, _ = rig
    result, commands = run("check")
    assert result.returncode == 0, result.stderr + result.stdout
    assert not any(command[:2] in (["ethtool", "-G"], ["ethtool", "-C"]) for command in commands)
    assert any(command[:4] == ["ip", "netns", "exec", "custom-ns"] for command in commands)


def test_check_rejects_missing_interface(rig):
    run, _, _, _, _ = rig
    result, _ = run("check", FAKE_MISSING_INTERFACE="port1")
    assert result.returncode != 0
    assert "port1" in result.stderr + result.stdout


def test_check_reports_adaptive_coalescing_drift(rig):
    run, state, _, _, _ = rig
    state.write_text(json.dumps({"ring": 4096, "adaptive": "off"}))
    result, commands = run("check")
    assert result.returncode != 0
    assert "coalesc" in (result.stderr + result.stdout).lower()
    assert not any(command[:2] == ["ethtool", "-C"] for command in commands)


def test_check_rejects_missing_queue_interrupts(rig):
    run, _, interrupts, _, _ = rig
    interrupts.write_text("16: 0 i40e-port0-TxRx-0\n")
    result, _ = run("check")
    assert result.returncode != 0
    assert "irq" in (result.stderr + result.stdout).lower()


def test_check_rejects_an_incomplete_set_of_queue_interrupts(rig):
    run, _, _, _, _ = rig
    result, _ = run("check", FAKE_CHANNELS_PORT0="2")
    assert result.returncode != 0
    assert "irq" in (result.stderr + result.stdout).lower()


def test_check_maps_queue_numbers_not_interrupt_order(rig):
    run, _, interrupts, irq, _ = rig
    interrupts.write_text(
        "16: 0 i40e-port0-TxRx-1\n"
        "18: 0 i40e-port0-TxRx-0\n"
        "17: 0 i40e-port1-TxRx-0\n"
    )
    (irq / "16/effective_affinity_list").write_text("1\n")
    (irq / "18").mkdir()
    (irq / "18/smp_affinity_list").write_text("0\n")
    (irq / "18/effective_affinity_list").write_text("0\n")
    result, _ = run("check", FAKE_CHANNELS_PORT0="2")
    assert result.returncode == 0, result.stderr + result.stdout


def test_apply_propagates_ring_change_failure(rig):
    run, state, _, _, _ = rig
    state.write_text(json.dumps({"ring": 1024, "adaptive": "on"}))
    result, _ = run("apply", FAKE_RING_FAILURE="1")
    assert result.returncode != 0
    assert "ring" in (result.stderr + result.stdout).lower()


def test_apply_restores_and_verifies_adaptive_coalescing(rig):
    run, state, _, _, _ = rig
    state.write_text(json.dumps({"ring": 4096, "adaptive": "off"}))
    result, commands = run("apply")
    assert result.returncode == 0, result.stderr + result.stdout
    assert any(command[:2] == ["ethtool", "-C"] for command in commands)
    assert json.loads(state.read_text())["adaptive"] == "on"


def test_apply_rejects_an_irqbalance_verification_error(rig):
    run, _, _, _, _ = rig
    result, _ = run("apply", FAKE_SYSTEMCTL_MODE="fail-verify")
    assert result.returncode != 0
    assert "irqbalance" in (result.stdout + result.stderr).lower()


def test_check_detects_enabled_double_digit_idle_states(rig):
    run, _, _, _, sysfs = rig
    state = sysfs / "devices/system/cpu/cpu0/cpuidle/state10"
    state.mkdir()
    (state / "disable").write_text("0\n")
    result, _ = run("check")
    assert result.returncode != 0
    assert "idle" in (result.stdout + result.stderr).lower()
