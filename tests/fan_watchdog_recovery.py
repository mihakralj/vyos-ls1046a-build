import configparser
import ctypes
import importlib.machinery
import importlib.util
import io
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest


pytestmark = pytest.mark.host
ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / 'board/scripts/fan-pid-failsafe'
UNIT = ROOT / 'board/systemd/fan-pid.service'


@pytest.fixture
def helper():
    loader = importlib.machinery.SourceFileLoader('fan_pid_failsafe', str(HELPER))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class FakeI2C:
    def __init__(self, compatible, devices, driver, node):
        self.compatible = compatible
        self.devices = devices
        self.driver = driver
        self.node = node
        self.open_calls = []
        self.writes = []
        self.reads = []
        self.open_results = []
        self.write_results = {}
        self.read_results = {}

    @staticmethod
    def result(results, default=None):
        outcome = results.pop(0) if results else default
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def open(self, bus):
        self.open_calls.append(bus)
        self.result(self.open_results)
        return os.open(os.devnull, os.O_RDWR | os.O_CLOEXEC)

    def write(self, fd, register):
        self.writes.append(register)
        self.result(self.write_results.setdefault(register, []))

    def read(self, fd, register):
        self.reads.append(register)
        return self.result(self.read_results.setdefault(register, []), 255)


@pytest.fixture
def device(helper, tmp_path, monkeypatch):
    compatible = tmp_path / 'compatible'
    compatible.write_bytes(b'mono,gateway-dk\0fsl,ls1046a\0')
    devices = tmp_path / 'sys/bus/i2c/devices'
    driver = tmp_path / 'sys/bus/i2c/drivers/emc2305'
    node = devices / '7-002e'
    node.mkdir(parents=True)
    driver.mkdir(parents=True)
    (node / 'driver').symlink_to(driver)
    simulated = FakeI2C(compatible, devices, driver, node)
    monkeypatch.setattr(helper, 'COMPATIBLE', str(compatible))
    monkeypatch.setattr(helper, 'I2C_DEVICES', str(devices))
    monkeypatch.setattr(helper, 'I2C_DRIVER', str(driver))
    monkeypatch.setattr(helper, 'RETRY_DELAY', 0)
    monkeypatch.setattr(helper, 'open_i2c', simulated.open)
    monkeypatch.setattr(helper, 'write_max', simulated.write)
    monkeypatch.setattr(helper, 'read_pwm', simulated.read)
    return simulated


def test_success_requires_both_readbacks(helper, device, capsys):
    assert helper.main() == 0
    assert device.open_calls == [7, 7]
    assert device.writes == [0x30, 0x40]
    assert device.reads == [0x30, 0x40]
    log = capsys.readouterr().err
    assert 'register 0x30 verified at 255' in log
    assert 'register 0x40 verified at 255' in log


@pytest.mark.parametrize('compatible', [
    b'fsl,ls1046a\0',
    b'mono,gateway-dk-other\0fsl,ls1046a\0',
    b'other,mono,gateway-dk\0',
    b'',
])
def test_wrong_board_skips_without_failure_or_i2c(helper, device, capsys, compatible):
    device.compatible.write_bytes(compatible)
    assert helper.main() == 0
    assert device.open_calls == []
    assert device.writes == []
    assert 'skipping' in capsys.readouterr().err


def test_mono_exact_token_is_sufficient(helper, device):
    device.compatible.write_bytes(b'mono,gateway-dk\0')
    assert helper.main() == 0
    assert device.writes == [0x30, 0x40]


def test_unreadable_board_gate_reports_failure(helper, device, capsys):
    device.compatible.unlink()
    assert helper.main() == 1
    assert device.open_calls == []
    assert 'cannot read board compatibility' in capsys.readouterr().err


@pytest.mark.parametrize('case', ['unbound', 'dangling', 'wrong-driver', 'wrong-address',
                                  'ambiguous', 'missing-driver'])
def test_invalid_or_ambiguous_binding_never_opens_i2c(helper, device, tmp_path, capsys, case):
    link = device.node / 'driver'
    if case == 'unbound':
        link.unlink()
    elif case == 'dangling':
        link.unlink()
        link.symlink_to(tmp_path / 'missing/emc2305')
    elif case == 'wrong-driver':
        other = tmp_path / 'other/emc2305'
        other.mkdir(parents=True)
        link.unlink()
        link.symlink_to(other)
    elif case == 'wrong-address':
        device.node.rename(device.devices / '7-002f')
    elif case == 'ambiguous':
        other = device.devices / '8-002e'
        other.mkdir()
        (other / 'driver').symlink_to(device.driver)
    else:
        device.driver.rename(device.driver.parent / 'unavailable')
    assert helper.main() == 1
    assert device.open_calls == []
    assert device.writes == []
    log = capsys.readouterr().err
    assert 'register 0x30 could not be verified' in log
    assert 'register 0x40 could not be verified' in log


def test_late_binding_is_retried_before_pinning_device(helper, device, monkeypatch):
    link = device.node / 'driver'
    link.unlink()
    discover = helper.bound_device
    calls = 0

    def bind_late():
        nonlocal calls
        calls += 1
        if calls == 2:
            link.symlink_to(device.driver)
        return discover()

    monkeypatch.setattr(helper, 'bound_device', bind_late)
    assert helper.main() == 0
    assert device.writes == [0x40, 0x30]


def test_unbind_after_open_prevents_writes(helper, device, monkeypatch):
    def unbind(bus):
        fd = device.open(bus)
        (device.node / 'driver').unlink()
        return fd

    monkeypatch.setattr(helper, 'open_i2c', unbind)
    assert helper.main() == 1
    assert device.open_calls == [7]
    assert device.writes == []


def test_changed_bus_is_rechecked_before_write(helper, device, monkeypatch):
    def rebind(bus):
        fd = device.open(bus)
        if len(device.open_calls) == 1:
            (device.node / 'driver').unlink()
            other = device.devices / '8-002e'
            other.mkdir()
            (other / 'driver').symlink_to(device.driver)
        return fd

    monkeypatch.setattr(helper, 'open_i2c', rebind)
    assert helper.main() == 1
    assert device.open_calls == [7]
    assert device.writes == []


def test_does_not_mix_devices_between_successful_channels(helper, device, monkeypatch, capsys):
    def rebind_on_second_open(bus):
        if len(device.open_calls) == 1:
            (device.node / 'driver').unlink()
            other = device.devices / '8-002e'
            other.mkdir()
            (other / 'driver').symlink_to(device.driver)
        return device.open(bus)

    monkeypatch.setattr(helper, 'open_i2c', rebind_on_second_open)
    assert helper.main() == 1
    assert device.writes == [0x30]
    assert 'register 0x40 could not be verified' in capsys.readouterr().err


def test_device_recreated_at_same_path_is_not_retargeted(helper, device, monkeypatch, capsys):
    def recreate_on_second_open(bus):
        if len(device.open_calls) == 1:
            device.node.rename(device.devices / 'previous-device')
            device.node.mkdir()
            (device.node / 'driver').symlink_to(device.driver)
        return device.open(bus)

    monkeypatch.setattr(helper, 'open_i2c', recreate_on_second_open)
    assert helper.main() == 1
    assert device.writes == [0x30]
    assert 'register 0x40 could not be verified' in capsys.readouterr().err


def test_same_bus_rebind_to_another_device_prevents_stale_write(helper, device, tmp_path, monkeypatch):
    (device.node / 'driver').unlink()
    device.node.rmdir()
    old = tmp_path / 'sys/devices/old'
    new = tmp_path / 'sys/devices/new'
    old.mkdir(parents=True)
    new.mkdir(parents=True)
    (old / 'driver').symlink_to(device.driver)
    (new / 'driver').symlink_to(device.driver)
    device.node.symlink_to(old)

    def rebind(bus):
        fd = device.open(bus)
        if len(device.open_calls) == 1:
            device.node.unlink()
            device.node.symlink_to(new)
        return fd

    monkeypatch.setattr(helper, 'open_i2c', rebind)
    assert helper.main() == 1
    assert device.open_calls == [7]
    assert device.writes == []


def test_unbind_during_readback_prevents_verification(helper, device, monkeypatch, capsys):
    def unbind(fd, register):
        value = device.read(fd, register)
        (device.node / 'driver').unlink()
        return value

    monkeypatch.setattr(helper, 'read_pwm', unbind)
    assert helper.main() == 1
    assert device.writes == [0x30]
    log = capsys.readouterr().err
    assert 'register 0x30 verified at 255' not in log
    assert 'register 0x30 could not be verified' in log


@pytest.mark.parametrize('error', [OSError('temporary open failure'),
                                   subprocess.TimeoutExpired(['/sbin/modprobe', 'i2c-dev'], 1),
                                   subprocess.CalledProcessError(1, ['/sbin/modprobe', 'i2c-dev'])])
def test_transient_open_failure_retries_each_channel(helper, device, capsys, error):
    device.open_results = [error]
    assert helper.main() == 0
    assert device.open_calls == [7, 7, 7]
    assert device.writes == [0x40, 0x30]
    assert 'register 0x30 attempt 1/3 failed' in capsys.readouterr().err


def test_permanent_open_failure_does_not_skip_other_channel(helper, device, capsys):
    device.open_results = [OSError('open failed'), None, OSError('open failed'), OSError('open failed')]
    assert helper.main() == 1
    assert len(device.open_calls) == 4
    assert device.writes == [0x40]
    log = capsys.readouterr().err
    assert 'register 0x30 could not be verified' in log
    assert 'register 0x40 verified at 255' in log


@pytest.mark.parametrize('register', [0x30, 0x40])
def test_transient_write_failure_retries_independently(helper, device, capsys, register):
    device.write_results[register] = [OSError('temporary write failure')]
    assert helper.main() == 0
    assert device.writes.count(register) == 2
    assert device.writes.count(0x70 - register) == 1
    assert len(device.reads) == 2
    assert f'register 0x{register:02x} attempt 1/3 failed' in capsys.readouterr().err


@pytest.mark.parametrize('register', [0x30, 0x40])
def test_permanent_write_failure_does_not_skip_other_channel(helper, device, capsys, register):
    device.write_results[register] = [OSError('write failed') for _ in range(helper.MAX_ATTEMPTS)]
    assert helper.main() == 1
    assert device.writes.count(register) == helper.MAX_ATTEMPTS
    assert device.writes.count(0x70 - register) == 1
    assert device.reads == [0x70 - register]
    log = capsys.readouterr().err
    assert f'register 0x{register:02x} could not be verified' in log


@pytest.mark.parametrize('read_failure', [OSError('SMBus read failed'), 0])
def test_readback_failure_is_retried(helper, device, capsys, read_failure):
    device.read_results[0x30] = [read_failure, 255]
    assert helper.main() == 0
    assert device.writes == [0x30, 0x40, 0x30]
    assert device.reads == [0x30, 0x40, 0x30]
    assert 'register 0x30 attempt 1/3 failed' in capsys.readouterr().err


def test_unverifiable_readback_reports_failure(helper, device, capsys):
    device.read_results[0x40] = [OSError('SMBus unsupported') for _ in range(helper.MAX_ATTEMPTS)]
    assert helper.main() == 1
    assert device.writes == [0x30, 0x40, 0x40, 0x40]
    assert device.reads == [0x30, 0x40, 0x40, 0x40]
    log = capsys.readouterr().err
    assert 'SMBus unsupported' in log
    assert 'register 0x40 could not be verified' in log
    assert 'register 0x30 verified at 255' in log


def test_low_level_write_is_allowlisted_and_detects_short_write(helper, monkeypatch):
    writes = []
    monkeypatch.setattr(helper.os, 'write', lambda fd, data: writes.append(data) or len(data))
    helper.write_max(42, 0x30)
    helper.write_max(42, 0x40)
    assert writes == [b'\x30\xff', b'\x40\xff']
    with pytest.raises(ValueError):
        helper.write_max(42, 0x31)
    assert len(writes) == 2
    monkeypatch.setattr(helper.os, 'write', lambda fd, data: 1)
    with pytest.raises(OSError, match='short I2C write'):
        helper.write_max(42, 0x30)


def test_smbus_readback_uses_native_byte_data_abi(helper, monkeypatch):
    class SMBusData(ctypes.Union):
        _fields_ = [('byte', ctypes.c_uint8), ('word', ctypes.c_uint16),
                    ('block', ctypes.c_uint8 * 34)]

    class SMBusRequest(ctypes.Structure):
        _fields_ = [('direction', ctypes.c_uint8), ('register', ctypes.c_uint8),
                    ('size', ctypes.c_uint32), ('data', ctypes.POINTER(SMBusData))]

    calls = []

    def ioctl(fd, operation, request, mutate):
        assert len(request) == ctypes.sizeof(SMBusRequest)
        payload = SMBusRequest.from_buffer(request)
        calls.append((fd, operation, payload.direction, payload.register, payload.size, mutate))
        payload.data.contents.byte = 255
        return 0

    monkeypatch.setattr(helper.fcntl, 'ioctl', ioctl)
    assert helper.read_pwm(42, 0x30) == 255
    assert helper.read_pwm(42, 0x40) == 255
    assert calls == [(42, 0x0720, 1, 0x30, 2, True),
                     (42, 0x0720, 1, 0x40, 2, True)]


def test_open_claims_address_and_closes_after_ioctl_failure(helper, tmp_path, monkeypatch):
    monkeypatch.setattr(helper, 'DEV_DIR', str(tmp_path))
    (tmp_path / 'i2c-7').touch()
    claims = []

    def claim(fd, operation, address):
        claims.append((fd, operation, address))
        raise OSError('claim failed')

    monkeypatch.setattr(helper.fcntl, 'ioctl', claim)
    with pytest.raises(OSError, match='claim failed'):
        helper.open_i2c(7)
    assert claims[0][1:] == (0x0706, 0x2e)
    with pytest.raises(OSError):
        os.fstat(claims[0][0])


def test_missing_i2c_dev_is_loaded_with_timeout(helper, tmp_path, monkeypatch):
    monkeypatch.setattr(helper, 'DEV_DIR', str(tmp_path))
    calls = []

    def modprobe(command, **kwargs):
        calls.append((command, kwargs))
        (tmp_path / 'i2c-7').touch()

    monkeypatch.setattr(helper.subprocess, 'run', modprobe)
    monkeypatch.setattr(helper.fcntl, 'ioctl', lambda *args: 0)
    fd = helper.open_i2c(7)
    os.close(fd)
    assert calls[0][0] == ['/sbin/modprobe', 'i2c-dev']
    assert calls[0][1]['timeout'] == 1
    assert calls[0][1]['check'] is True


def test_unit_orders_recovery_and_bounds_termination():
    parser = configparser.ConfigParser(interpolation=None, strict=True)
    parser.read_file(io.StringIO(UNIT.read_text()))
    service = parser['Service']
    assert service['ExecStart'] == '/usr/local/bin/fan-pid'
    assert service['ExecStopPost'] == '/usr/bin/python3 /usr/local/bin/fan-pid-failsafe'
    assert service['Restart'] == 'on-failure'
    assert service['KillMode'] == 'control-group'
    assert service['TimeoutAbortSec'] == '10s'
    assert service['TimeoutStopSec'] == '10s'
    assert service['WatchdogSec'] == '30s'
    assert service['PrivateDevices'] == 'no'
    assert service.get('DevicePolicy', 'auto') == 'auto'
    assert service.get('User', 'root') == 'root'
    assert os.access(HELPER, os.X_OK)
    setup = (ROOT / 'bin/ci-setup-vyos-build.sh').read_text()
    assert 'cp board/scripts/fan-pid-failsafe "$CHROOT/usr/local/bin/fan-pid-failsafe"' in setup
    assert 'chmod +x "$CHROOT/usr/local/bin/fan-pid-failsafe"' in setup


def test_systemd_unit_verifies_on_host(tmp_path):
    if not shutil.which('systemd-analyze'):
        pytest.skip('systemd-analyze is unavailable')
    unit = tmp_path / 'fan-pid.service'
    text = UNIT.read_text().replace('/usr/local/bin/fan-pid-failsafe', '/bin/true')
    text = text.replace('/usr/local/bin/fan-pid', '/bin/true')
    unit.write_text(text)
    subprocess.run(['systemd-analyze', 'verify', str(unit)], check=True,
                   capture_output=True, text=True, timeout=10)


@pytest.fixture(scope='module')
def user_systemd():
    if not shutil.which('systemd-run') or not shutil.which('systemctl'):
        pytest.skip('systemd user manager is unavailable')
    result = subprocess.run(['systemctl', '--user', 'show-environment'],
                            capture_output=True, timeout=5, check=False)
    if result.returncode:
        pytest.skip('systemd user manager is unavailable')
    unit = f'fan-pid-recovery-probe-{uuid.uuid4().hex}.service'
    probe = subprocess.run(['systemd-run', '--user', '--wait', '--collect', f'--unit={unit}',
                            '--property=Type=oneshot', '/bin/true'],
                           capture_output=True, timeout=10, check=False)
    if probe.returncode:
        pytest.skip('systemd user manager cannot run transient units')


def systemd_events(path, predicate, timeout=8):
    deadline = time.monotonic() + timeout
    lines = []
    while time.monotonic() < deadline:
        if path.exists():
            lines = path.read_text().splitlines()
            if predicate(lines):
                return lines
        time.sleep(0.03)
    pytest.fail(f'timed out waiting for transient unit events: {lines}')


def run_transient(unit, start, stop, watchdog):
    command = [
        'systemd-run', '--user', '--collect', f'--unit={unit}',
        '--property=Type=simple', '--property=Restart=on-failure',
        '--property=RestartSec=100ms', '--property=StartLimitIntervalSec=0',
        '--property=KillMode=control-group', '--property=TimeoutStopSec=1s',
        '--property=TimeoutAbortSec=1s', '--property=LimitCORE=0',
        f'--property=WatchdogSec={watchdog}', f'--property=ExecStopPost={sys.executable} {stop}',
        sys.executable, str(start),
    ]
    subprocess.run(command, check=True, capture_output=True, text=True, timeout=10)


@pytest.mark.parametrize('cause', ['startup', 'watchdog', 'cleanup-failed'])
def test_real_systemd_orders_cleanup_before_restart(user_systemd, tmp_path, cause):
    events = tmp_path / 'events'
    marker = tmp_path / 'first-start'
    start = tmp_path / 'start.py'
    stop = tmp_path / 'stop.py'
    start.write_text(
        'import sys, time\n'
        'from pathlib import Path\n'
        f'events = Path({str(events)!r})\n'
        f'marker = Path({str(marker)!r})\n'
        "with events.open('a') as output: output.write('start\\n')\n"
        f'if {cause!r} in ("startup", "cleanup-failed") and not marker.exists():\n'
        '    marker.touch()\n'
        '    sys.exit(1)\n'
        'time.sleep(10)\n'
    )
    stop.write_text(
        'import time\n'
        'from pathlib import Path\n'
        f'events = Path({str(events)!r})\n'
        "with events.open('a') as output: output.write('post-start\\n')\n"
        'time.sleep(0.15)\n'
        "with events.open('a') as output: output.write('post-end\\n')\n"
        f'raise SystemExit(1 if {cause!r} == "cleanup-failed" else 0)\n'
    )
    unit = f'fan-pid-recovery-{uuid.uuid4().hex}.service'
    watchdog = '400ms' if cause == 'watchdog' else '0'
    try:
        run_transient(unit, start, stop, watchdog)
        lines = systemd_events(events, lambda entries: entries.count('start') >= 2)
        assert lines[:4] == ['start', 'post-start', 'post-end', 'start']
    finally:
        subprocess.run(['systemctl', '--user', 'stop', unit],
                       capture_output=True, check=False, timeout=5)


def test_real_systemd_startup_failure_runs_stop_post(user_systemd, tmp_path):
    result_file = tmp_path / 'service-result'
    stop = tmp_path / 'stop.py'
    stop.write_text(
        'import os\n'
        'from pathlib import Path\n'
        f'Path({str(result_file)!r}).write_text(os.environ.get("SERVICE_RESULT", "missing"))\n'
    )
    unit = f'fan-pid-recovery-{uuid.uuid4().hex}.service'
    result = subprocess.run([
        'systemd-run', '--user', '--wait', '--collect', f'--unit={unit}',
        '--property=Type=simple', '--property=Restart=no', '--property=ExecStartPre=/bin/false',
        f'--property=ExecStopPost={sys.executable} {stop}', '/bin/sleep', '10',
    ], capture_output=True, text=True, timeout=10, check=False)
    assert result.returncode != 0
    assert result_file.read_text() == 'exit-code'


def test_real_systemd_bounds_slow_cleanup_before_restart(user_systemd, tmp_path):
    events = tmp_path / 'events'
    marker = tmp_path / 'first-start'
    start = tmp_path / 'start.py'
    stop = tmp_path / 'stop.py'
    start.write_text(
        'import sys, time\n'
        'from pathlib import Path\n'
        f'events = Path({str(events)!r})\n'
        f'marker = Path({str(marker)!r})\n'
        "with events.open('a') as output: output.write('start\\n')\n"
        'if not marker.exists():\n'
        '    marker.touch()\n'
        '    sys.exit(1)\n'
        'time.sleep(10)\n'
    )
    stop.write_text(
        'import signal, sys, time\n'
        'from pathlib import Path\n'
        f'events = Path({str(events)!r})\n'
        'def terminate(signum, frame):\n'
        "    with events.open('a') as output: output.write('post-terminated\\n')\n"
        '    sys.exit(1)\n'
        'signal.signal(signal.SIGTERM, terminate)\n'
        "with events.open('a') as output: output.write('post-start\\n')\n"
        'time.sleep(10)\n'
    )
    unit = f'fan-pid-recovery-{uuid.uuid4().hex}.service'
    try:
        run_transient(unit, start, stop, '0')
        lines = systemd_events(events, lambda entries: entries.count('start') >= 2)
        assert lines[:4] == ['start', 'post-start', 'post-terminated', 'start']
    finally:
        subprocess.run(['systemctl', '--user', 'stop', unit],
                       capture_output=True, check=False, timeout=5)


def test_real_systemd_stops_children_before_recovery(user_systemd, tmp_path):
    events = tmp_path / 'events'
    marker = tmp_path / 'first-start'
    ready = tmp_path / 'child-ready'
    start = tmp_path / 'start.py'
    child = tmp_path / 'child.py'
    stop = tmp_path / 'stop.py'
    child.write_text(
        'import signal, sys, time\n'
        'from pathlib import Path\n'
        f'events = Path({str(events)!r})\n'
        f'ready = Path({str(ready)!r})\n'
        "with events.open('a') as output: output.write('child-start\\n')\n"
        'def terminate(signum, frame):\n'
        "    with events.open('a') as output: output.write('child-stop\\n')\n"
        '    sys.exit(0)\n'
        'signal.signal(signal.SIGTERM, terminate)\n'
        'ready.touch()\n'
        'while True: time.sleep(1)\n'
    )
    start.write_text(
        'import subprocess, sys, time\n'
        'from pathlib import Path\n'
        f'events = Path({str(events)!r})\n'
        f'marker = Path({str(marker)!r})\n'
        f'ready = Path({str(ready)!r})\n'
        "with events.open('a') as output: output.write('start\\n')\n"
        'if not marker.exists():\n'
        '    marker.touch()\n'
        f'    subprocess.Popen([sys.executable, {str(child)!r}])\n'
        '    deadline = time.monotonic() + 3\n'
        '    while not ready.exists() and time.monotonic() < deadline: time.sleep(0.01)\n'
        '    sys.exit(1 if ready.exists() else 2)\n'
        'time.sleep(10)\n'
    )
    stop.write_text(
        'import time\n'
        'from pathlib import Path\n'
        f'events = Path({str(events)!r})\n'
        "with events.open('a') as output: output.write('post-start\\n')\n"
        'time.sleep(0.15)\n'
        "with events.open('a') as output: output.write('post-end\\n')\n"
    )
    unit = f'fan-pid-recovery-{uuid.uuid4().hex}.service'
    try:
        run_transient(unit, start, stop, '0')
        lines = systemd_events(events, lambda entries: entries.count('start') >= 2)
        assert lines[:6] == ['start', 'child-start', 'child-stop', 'post-start', 'post-end', 'start']
    finally:
        subprocess.run(['systemctl', '--user', 'stop', unit],
                       capture_output=True, check=False, timeout=5)


def test_real_systemd_explicit_stop_exposes_recovery_failure(user_systemd):
    unit = f'fan-pid-recovery-{uuid.uuid4().hex}.service'
    try:
        subprocess.run([
            'systemd-run', '--user', f'--unit={unit}', '--property=Type=simple',
            '--property=Restart=on-failure', '--property=ExecStopPost=/bin/false',
            '/bin/sleep', '10',
        ], capture_output=True, text=True, timeout=10, check=True)
        subprocess.run(['systemctl', '--user', 'stop', unit],
                       capture_output=True, text=True, timeout=5, check=False)
        status = subprocess.run(['systemctl', '--user', 'show', unit,
                                 '--property=ActiveState', '--property=Result', '--property=NRestarts'],
                                capture_output=True, text=True, timeout=5, check=True).stdout
        assert 'ActiveState=failed' in status
        assert 'Result=exit-code' in status
        assert 'NRestarts=0' in status
    finally:
        subprocess.run(['systemctl', '--user', 'stop', unit],
                       capture_output=True, check=False, timeout=5)
        subprocess.run(['systemctl', '--user', 'reset-failed', unit],
                       capture_output=True, check=False, timeout=5)


def test_real_systemd_reports_stop_post_failure(user_systemd):
    unit = f'fan-pid-recovery-{uuid.uuid4().hex}.service'
    result = subprocess.run([
        'systemd-run', '--user', '--wait', '--collect', f'--unit={unit}',
        '--property=Type=oneshot', '--property=ExecStopPost=/bin/false', '/bin/true',
    ], capture_output=True, text=True, timeout=10, check=False)
    assert result.returncode != 0
    assert 'failed' in result.stderr.lower()
