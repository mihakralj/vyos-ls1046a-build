import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.host
SCRIPT = Path(__file__).resolve().parents[1] / 'data/vyos-1x-files/src/op_mode/show_hardware_fan.py'


def clock_now():
    return time.clock_gettime(time.CLOCK_BOOTTIME)


def put(root, path, value):
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(value)
    return target


def sensor(root, index, node, name='tmp431', values=None):
    hwmon = root / f'sys/class/hwmon/hwmon{index}'
    put(hwmon, 'name', name)
    device = hwmon / 'device'
    device.mkdir()
    of_node = root / 'sys/firmware/devicetree/base' / node
    of_node.mkdir(parents=True, exist_ok=True)
    (device / 'of_node').symlink_to(of_node)
    for attr, value in (values or {}).items():
        put(hwmon, attr, value)
    if name == 'tmp431' and 'temp2_input' in (values or {}) and 'temp2_fault' not in (values or {}):
        put(hwmon, 'temp2_fault', '0')


def put_snapshot(root, values, generation='0' * 32):
    snapshot = {'zones': values, 'generation': generation}
    payload = json.dumps(snapshot, sort_keys=True, separators=(',', ':')).encode('ascii')
    return put(root, 'run/vyos-fan-zones.json', json.dumps({
        **snapshot, 'sha256': hashlib.sha256(payload).hexdigest()}))


def verify_snapshot(root, values):
    generation = json.loads((root / 'run/vyos-fan-zones.json').read_text())['generation']
    payload = json.dumps({'zones': values, 'generation': generation},
                         sort_keys=True, separators=(',', ':')).encode('ascii')
    put(root, 'run/vyos-fan-boot-ready', '')
    put(root, 'run/vyos-fan-verified.json', json.dumps({
        'generation': generation, 'sha256': hashlib.sha256(payload).hexdigest(),
        'updated': clock_now()}))


def acknowledge(root, values=None, mode='normal', updated=None, verify=True):
    if values is None:
        values = {
            'memory': {'setpoint': 65, 'critical': 80},
            'serdes': {'setpoint': 70, 'critical': 85},
            'fman': {'setpoint': 70, 'critical': 87},
            'cpu-int': {'setpoint': 65, 'critical': 85},
            'crypto': {'setpoint': 65, 'critical': 85},
            'pcb': {'setpoint': 60, 'critical': 85},
            'cpu-ext': {'setpoint': 70, 'critical': 85},
            'sfp-plus': {'setpoint': 60, 'critical': 85},
        }
    if verify:
        if not (root / 'run/vyos-fan-zones.json').exists():
            put_snapshot(root, values)
        verify_snapshot(root, values)
    generation = json.loads((root / 'run/vyos-fan-zones.json').read_text())['generation']
    pid = os.getpid()
    start = int(Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[-1].split()[19])
    sampled = clock_now()
    put(root, 'run/fan-pid/applied.json', json.dumps({
        'zones': values, 'generation': generation, 'updated': sampled if updated is None else updated,
        'mode': mode, 'pid': pid, 'start': start,
        'sensors': {name: sampled for name in values},
        'chassis': {'rpm': 1000, 'sampled': sampled}}))


def ensure_bound_fixture(root):
    bus = root / 'sys/bus/i2c'
    if bus.exists():
        return
    hwmon = root / 'sys/class/hwmon'
    if not hwmon.exists():
        return
    controllers = [entry for entry in hwmon.iterdir()
                   if (entry / 'name').is_file() and (entry / 'name').read_text().strip() == 'emc2305']
    if not controllers:
        return
    driver = bus / 'drivers/emc2305'
    driver.mkdir(parents=True)
    device = bus / 'devices/7-002e'
    device.mkdir(parents=True)
    (device / 'driver').symlink_to(driver)
    for entry in controllers:
        (entry / 'device').symlink_to(device)


def ensure_fresh_floor_fixture(root):
    path = root / 'run/fan-pid/floors'
    if path.exists():
        contents = path.read_text()
        if 'export_version=' not in contents:
            floor_export_metadata(root, contents)


def run_show(root, raw=False):
    ensure_bound_fixture(root)
    ensure_fresh_floor_fixture(root)
    ack = root / 'run/fan-pid/applied.json'
    if not ack.exists():
        snapshot = root / 'run/vyos-fan-zones.json'
        acknowledge(root, json.loads(snapshot.read_text())['zones'] if snapshot.exists() else None)
    pythonpath = str(SCRIPT.parents[2] / 'python')
    env = dict(os.environ, VYOS_FAN_SYS_ROOT=str(root), PYTHONPATH=pythonpath)
    return subprocess.run([sys.executable, str(SCRIPT)] + (['raw'] if raw else []),
                          env=env, text=True, capture_output=True, check=False).stdout


def run_check(root, prepare_floor=True):
    ensure_bound_fixture(root)
    if prepare_floor:
        ensure_fresh_floor_fixture(root)
    script = (SCRIPT.parents[4] / 'board/scripts/fan-check').read_text()
    script = script.replace('/proc/device-tree', f'{root}/proc/device-tree')
    script = script.replace('/sys/class/', f'{root}/sys/class/')
    script = script.replace('/sys/bus/i2c/', f'{root}/sys/bus/i2c/')
    script = script.replace('/run/fan-pid/', f'{root}/run/fan-pid/')
    command = (f"python3 -c \"from pathlib import Path; import vyos.fan_control as f; "
               f"f.STATE=Path('{root}/run/vyos-fan-zones.json'); "
               f"f.PENDING=Path('{root}/run/vyos-fan-pending'); "
               f"f.APPLIED=Path('{root}/run/fan-pid/applied.json'); "
               f"f.FLOOR_EXPORT=Path('{root}/run/fan-pid/floors'); exit(f.main())\"")
    script = script.replace('python3 -m vyos.fan_control', command)
    return subprocess.run(['bash', '-c', script], text=True, capture_output=True,
                          env=dict(os.environ, PYTHONPATH=str(SCRIPT.parents[2] / 'python')))


def fake_emc_controller(root, index, bus, bound=True, driver_name='emc2305', pwm='170'):
    driver = root / f'sys/bus/i2c/drivers/{driver_name}'
    driver.mkdir(parents=True, exist_ok=True)
    device = root / f'sys/bus/i2c/devices/{bus}-002e'
    device.mkdir(parents=True, exist_ok=True)
    if bound:
        (device / 'driver').symlink_to(driver)
    hwmon = root / f'sys/class/hwmon/hwmon{index}'
    put(hwmon, 'name', 'emc2305')
    put(hwmon, 'pwm1', pwm)
    put(hwmon, 'fan1_input', '1200')
    put(hwmon, 'fan1_fault', '0')
    (hwmon / 'device').symlink_to(device)
    return device, hwmon


def test_consumers_select_only_bound_matching_controller(tmp_path):
    put(tmp_path, 'proc/device-tree/compatible', 'mono,gateway-dk\0fsl,ls1046a\0')
    fake_emc_controller(tmp_path, 0, 3, bound=False, pwm='13')
    selected, hwmon = fake_emc_controller(tmp_path, 4, 7, pwm='170')
    put(tmp_path, 'run/fan-pid/floors', f'controller_device={selected.resolve()}\n')
    raw = json.loads(run_show(tmp_path, raw=True))
    assert raw['fans'][0]['duty'] == 170
    assert f'hwmon path : {hwmon}' in run_show(tmp_path)
    check = run_check(tmp_path)
    assert f'hwmon path : {hwmon}' in check.stdout


@pytest.mark.parametrize('scenario', ['two-bound', 'unbound', 'wrong-driver', 'duplicate-hwmon'])
def test_consumers_report_unavailable_for_ambiguous_or_unbound_controller(tmp_path, scenario):
    put(tmp_path, 'proc/device-tree/compatible', 'mono,gateway-dk\0fsl,ls1046a\0')
    if scenario == 'unbound':
        fake_emc_controller(tmp_path, 0, 7, bound=False)
    elif scenario == 'wrong-driver':
        device, _ = fake_emc_controller(tmp_path, 0, 7, driver_name='other')
        (tmp_path / 'sys/bus/i2c/drivers/emc2305').mkdir(parents=True)
        wrong = tmp_path / 'unrelated/emc2305'
        wrong.mkdir(parents=True)
        (device / 'driver').unlink()
        (device / 'driver').symlink_to(wrong)
    else:
        selected, _ = fake_emc_controller(tmp_path, 0, 7)
        if scenario == 'two-bound':
            fake_emc_controller(tmp_path, 1, 8)
        else:
            hwmon = tmp_path / 'sys/class/hwmon/hwmon1'
            put(hwmon, 'name', 'emc2305')
            put(hwmon, 'pwm1', '170')
            put(hwmon, 'fan1_input', '1200')
            (hwmon / 'device').symlink_to(selected)
    raw = json.loads(run_show(tmp_path, raw=True))
    assert all(not item['exists'] for item in raw['fans'])
    assert 'unavailable' in raw['configuration']['message'].lower()
    assert 'NOT FOUND' in run_show(tmp_path)
    check = run_check(tmp_path)
    assert check.returncode == 1 and 'NOT FOUND' in check.stdout


def test_ambiguous_controller_overrides_healthy_configuration(tmp_path):
    put(tmp_path, 'proc/device-tree/compatible', 'mono,gateway-dk\0fsl,ls1046a\0')
    for index, zone in enumerate(('ddr-thermal', 'serdes-thermal', 'fman-thermal',
                                  'cluster-thermal', 'sec-thermal')):
        put(tmp_path, f'sys/class/thermal/thermal_zone{index}/type', zone)
        put(tmp_path, f'sys/class/thermal/thermal_zone{index}/temp', '40000')
    sensor(tmp_path, 9, 'i2c-mux@70/i2c@1/temp-sensor@4c',
           values={'temp1_input': '40000', 'temp2_input': '40000'})
    sensor(tmp_path, 10, 'i2c-mux@70/i2c@2/temp-sensor@4c', values={'temp1_input': '40000'})
    fake_emc_controller(tmp_path, 0, 7)
    fake_emc_controller(tmp_path, 1, 8)
    raw = json.loads(run_show(tmp_path, raw=True))
    assert raw['configuration']['state'] == 'error'
    assert 'unavailable' in raw['configuration']['message'].lower()


@pytest.mark.parametrize('identity', ['/sys/devices/old/7-002e', '', 'relative/path'])
def test_consumers_reject_controller_identity_changed_since_export(tmp_path, identity):
    put(tmp_path, 'proc/device-tree/compatible', 'mono,gateway-dk\0fsl,ls1046a\0')
    fake_emc_controller(tmp_path, 0, 7)
    put(tmp_path, 'run/fan-pid/floors', f'controller_device={identity}\n')
    raw = json.loads(run_show(tmp_path, raw=True))
    assert all(not item['exists'] for item in raw['fans'])
    assert 'unavailable' in raw['configuration']['message'].lower()
    assert 'NOT FOUND' in run_show(tmp_path)
    assert 'NOT FOUND' in run_check(tmp_path).stdout


def healthy_display_fixture(root):
    put(root, 'proc/device-tree/compatible', 'mono,gateway-dk\0fsl,ls1046a\0')
    for index, zone in enumerate(('ddr-thermal', 'serdes-thermal', 'fman-thermal',
                                  'cluster-thermal', 'sec-thermal')):
        put(root, f'sys/class/thermal/thermal_zone{index}/type', zone)
        put(root, f'sys/class/thermal/thermal_zone{index}/temp', '40000')
    sensor(root, 9, 'i2c-mux@70/i2c@1/temp-sensor@4c',
           values={'temp1_input': '40000', 'temp2_input': '40000'})
    sensor(root, 10, 'i2c-mux@70/i2c@2/temp-sensor@4c', values={'temp1_input': '40000'})
    device, hwmon = fake_emc_controller(root, 0, 7)
    put(hwmon, 'pwm2', '90')
    put(hwmon, 'fan2_input', '1100')
    put(hwmon, 'fan2_fault', '0')
    put(root, 'run/fan-pid/floors', f'controller_device={device.resolve()}\n'
        '0x30=60\n0x40=60\npresent:0x30=1\npresent:0x40=1\ntach_source=sysfs-m2\n')
    ensure_fresh_floor_fixture(root)
    acknowledge(root)
    return hwmon


def show_result(root, raw=False):
    env = dict(os.environ, VYOS_FAN_SYS_ROOT=str(root),
               PYTHONPATH=str(SCRIPT.parents[2] / 'python'))
    return subprocess.run([sys.executable, str(SCRIPT)] + (['raw'] if raw else []),
                          env=env, text=True, capture_output=True)


@pytest.mark.parametrize('channel,field,value,state,absent', [
    (1, 'fan1_fault', '1', 'fault', False),
    (1, 'fan1_fault', '1', 'fault', True),
    (1, 'fan1_input', '0', 'stalled', False),
    (1, 'fan1_fault', 'invalid', 'read-error', False),
    (1, 'fan1_fault', '2', 'read-error', False),
    (1, 'pwm1', '256', 'read-error', False),
    (1, 'fan1_input', '-1', 'read-error', False),
    (2, 'fan2_fault', '1', 'fault', False),
    (2, 'fan2_input', '0', 'stalled', False),
    (2, 'fan2_fault', 'invalid', 'read-error', False),
    (2, 'fan2_fault', '1', 'fault', True),
])
def test_fan_fault_overrides_fresh_normal_ack(tmp_path, channel, field, value, state, absent):
    hwmon = healthy_display_fixture(tmp_path)
    baseline = show_result(tmp_path, raw=True)
    assert baseline.returncode == 0
    assert json.loads(baseline.stdout)['configuration']['state'] == 'ok'
    put(hwmon, field, value)
    if absent:
        floors = tmp_path / 'run/fan-pid/floors'
        register = '0x30' if channel == 1 else '0x40'
        floors.write_text(floors.read_text().replace(f'present:{register}=1', f'present:{register}=0'))
    raw = show_result(tmp_path, raw=True)
    assert raw.returncode == 1
    output = json.loads(raw.stdout)
    assert output['configuration']['state'] == 'error'
    assert f'fan{channel}' in output['configuration']['message']
    assert output['fans'][channel - 1]['state'] == state
    text = show_result(tmp_path)
    assert text.returncode == 1 and 'configuration: error' in text.stdout


@pytest.mark.parametrize('channel,value,state', [(1, '0', 'stalled'), (2, '0', 'stalled'),
                                                        (1, 'invalid', 'read-error')])
def test_published_raw_tach_fault_overrides_fresh_ack(tmp_path, channel, value, state):
    healthy_display_fixture(tmp_path)
    floors = tmp_path / 'run/fan-pid/floors'
    contents = floors.read_text().replace('tach_source=sysfs-m2', 'tach_source=raw-m1')
    contents += 'rpm:0x30=1200\nrpm:0x40=1100\n'
    floors.write_text(contents)
    assert show_result(tmp_path, raw=True).returncode == 0
    register = '0x30' if channel == 1 else '0x40'
    old = '1200' if channel == 1 else '1100'
    floors.write_text(contents.replace(f'rpm:{register}={old}', f'rpm:{register}={value}'))
    result = show_result(tmp_path, raw=True)
    assert result.returncode == 1
    output = json.loads(result.stdout)
    assert output['configuration']['state'] == 'error'
    assert output['fans'][channel - 1]['state'] == state


def floor_export_metadata(root, contents, updated=None, samples=None, pid=None, start=None):
    pid = os.getpid() if pid is None else pid
    if start is None:
        start = int(Path(f'/proc/{os.getpid()}/stat').read_text().rsplit(')', 1)[-1].split()[19])
    updated = clock_now() if updated is None else updated
    samples = {0x30: updated, 0x40: updated} if samples is None else samples
    contents = ''.join(line + '\n' for line in contents.splitlines()
                       if not line.startswith(('export_', 'rpm_sample:')))
    metadata = (f'export_version=1\nexport_updated={updated}\n'
                f'export_pid={pid}\nexport_start={start}\n')
    return put(root, 'run/fan-pid/floors', contents + metadata + ''.join(
        f'rpm_sample:0x{reg:02x}={stamp}\n' for reg, stamp in samples.items()))


@pytest.mark.parametrize('problem', ['legacy', 'future', 'dead-pid', 'wrong-start', 'duplicate', 'oversize'])
def test_unverified_floor_export_is_unavailable_with_fresh_ack(tmp_path, problem):
    healthy_display_fixture(tmp_path)
    floors = tmp_path / 'run/fan-pid/floors'
    contents = ''.join(line + '\n' for line in floors.read_text().splitlines()
                       if not line.startswith(('export_', 'rpm_sample:')))
    contents += 'rpm:0x30=1234\n'
    if problem == 'legacy':
        floors.write_text(contents)
    elif problem == 'future':
        floor_export_metadata(tmp_path, contents, updated=clock_now() + 60)
    elif problem == 'dead-pid':
        floor_export_metadata(tmp_path, contents, pid=999999999)
    elif problem == 'wrong-start':
        floor_export_metadata(tmp_path, contents, start=0)
    elif problem == 'duplicate':
        floor_export_metadata(tmp_path, contents + 'present:0x30=1\n')
    else:
        floor_export_metadata(tmp_path, contents + 'x' * 17000 + '\n')
    result = show_result(tmp_path, raw=True)
    assert result.returncode == 1
    output = json.loads(result.stdout)
    assert output['configuration']['state'] == 'error'
    assert 'telemetry' in output['configuration']['message'].lower()
    assert output['fans'][0]['rpm'] is None
    assert output['fans'][0]['presence'] == 'unknown'
    check = run_check(tmp_path, prepare_floor=False)
    assert check.returncode == 1 and 'telemetry' in check.stdout.lower()


def test_stale_floor_export_is_not_reported_with_fresh_ack(tmp_path):
    healthy_display_fixture(tmp_path)
    floors = tmp_path / 'run/fan-pid/floors'
    contents = floors.read_text() + 'rpm:0x30=1234\n'
    floor_export_metadata(tmp_path, contents, updated=clock_now() - 60)
    result = show_result(tmp_path, raw=True)
    assert result.returncode == 1
    output = json.loads(result.stdout)
    assert output['configuration']['state'] == 'error'
    assert 'telemetry' in output['configuration']['message'].lower()
    assert output['fans'][0]['rpm'] is None
    assert output['fans'][0]['presence'] == 'unknown'
    check = run_check(tmp_path)
    assert check.returncode == 1 and 'telemetry' in check.stdout.lower()


@pytest.mark.parametrize('value', ['', 'invalid', 'missing'])
def test_unknown_chassis_presence_never_looks_healthy_with_ack(tmp_path, value):
    healthy_display_fixture(tmp_path)
    floors = tmp_path / 'run/fan-pid/floors'
    contents = floors.read_text()
    replacement = '' if value == 'missing' else f'present:0x30={value}'
    contents = contents.replace('present:0x30=1', replacement)
    floor_export_metadata(tmp_path, contents)
    result = show_result(tmp_path, raw=True)
    assert result.returncode == 1
    output = json.loads(result.stdout)
    assert output['configuration']['state'] == 'error'
    assert output['fans'][0]['presence'] == 'unknown'
    check = run_check(tmp_path)
    assert check.returncode == 1 and 'fan health: ERROR' in check.stdout


def test_required_fan_missing_fault_is_not_healthy(tmp_path):
    hwmon = healthy_display_fixture(tmp_path)
    (hwmon / 'fan1_fault').unlink()
    floors = tmp_path / 'run/fan-pid/floors'
    contents = floors.read_text().replace('present:0x30=1', 'present:0x30=0')
    floor_export_metadata(tmp_path, contents)
    result = show_result(tmp_path, raw=True)
    assert result.returncode == 1
    assert json.loads(result.stdout)['configuration']['state'] == 'error'


@pytest.mark.parametrize('value', ['missing', 'invalid'])
def test_unconfirmed_optional_fan_does_not_make_chassis_unhealthy(tmp_path, value):
    healthy_display_fixture(tmp_path)
    floors = tmp_path / 'run/fan-pid/floors'
    replacement = '' if value == 'missing' else 'present:0x40=invalid'
    contents = floors.read_text().replace('present:0x40=1', replacement)
    floor_export_metadata(tmp_path, contents)
    result = show_result(tmp_path, raw=True)
    assert result.returncode == 0
    output = json.loads(result.stdout)
    assert output['configuration']['state'] == 'ok'
    assert output['fans'][0]['presence'] == 'present'
    assert output['fans'][1]['presence'] == 'unknown'


def test_absent_optional_fan_missing_fault_does_not_block_chassis(tmp_path):
    hwmon = healthy_display_fixture(tmp_path)
    (hwmon / 'fan2_fault').unlink()
    floors = tmp_path / 'run/fan-pid/floors'
    contents = floors.read_text().replace('present:0x40=1', 'present:0x40=0')
    floor_export_metadata(tmp_path, contents)
    result = show_result(tmp_path, raw=True)
    assert result.returncode == 0
    output = json.loads(result.stdout)
    assert output['configuration']['state'] == 'ok'
    assert output['fans'][1]['state'] == 'not present'


@pytest.mark.parametrize('source', ['', 'tach_source=invalid\n'])
def test_missing_tach_source_never_trusts_sysfs_with_fresh_ack(tmp_path, source):
    healthy_display_fixture(tmp_path)
    floors = tmp_path / 'run/fan-pid/floors'
    contents = floors.read_text().replace('tach_source=sysfs-m2\n', source)
    floor_export_metadata(tmp_path, contents)
    result = show_result(tmp_path, raw=True)
    assert result.returncode == 1
    output = json.loads(result.stdout)
    assert output['configuration']['state'] == 'error'
    assert output['fans'][0]['rpm'] is None
    check = run_check(tmp_path)
    assert 'fan health: ERROR' in check.stdout


def test_stale_absent_auxiliary_tach_does_not_block_primary(tmp_path):
    healthy_display_fixture(tmp_path)
    floors = tmp_path / 'run/fan-pid/floors'
    contents = floors.read_text().replace('tach_source=sysfs-m2', 'tach_source=raw-m1')
    contents = contents.replace('present:0x40=1', 'present:0x40=0')
    contents += 'rpm:0x30=1200\nrpm:0x40=1100\n'
    floor_export_metadata(tmp_path, contents,
                          samples={0x30: clock_now(), 0x40: clock_now() - 60})
    result = show_result(tmp_path, raw=True)
    assert result.returncode == 0
    output = json.loads(result.stdout)
    assert output['configuration']['state'] == 'ok'
    assert output['fans'][0]['rpm'] == 1200
    assert output['fans'][1]['state'] == 'not present'
    check = run_check(tmp_path)
    assert 'fan health: OK' in check.stdout


def test_stale_per_fan_rpm_is_not_published_as_current(tmp_path):
    healthy_display_fixture(tmp_path)
    floors = tmp_path / 'run/fan-pid/floors'
    contents = floors.read_text().replace('tach_source=sysfs-m2', 'tach_source=raw-m1')
    contents += 'rpm:0x30=1234\nrpm:0x40=1100\n'
    floor_export_metadata(tmp_path, contents,
                          samples={0x30: clock_now() - 60, 0x40: clock_now()})
    result = show_result(tmp_path, raw=True)
    assert result.returncode == 1
    output = json.loads(result.stdout)
    assert output['fans'][0]['rpm'] is None
    assert output['configuration']['state'] == 'error'
    check = run_check(tmp_path)
    assert check.returncode == 1
    assert any('Fan 1' in line and 'READ-FAIL' in line for line in check.stdout.splitlines())


def test_explicit_fault_overrides_pending_configuration_status(tmp_path):
    hwmon = healthy_display_fixture(tmp_path)
    put(tmp_path, 'run/vyos-fan-pending', str(clock_now()))
    put(hwmon, 'fan1_fault', '1')
    result = show_result(tmp_path, raw=True)
    assert result.returncode == 1
    output = json.loads(result.stdout)
    assert output['configuration']['state'] == 'error'
    assert 'fan1' in output['configuration']['message']
    assert output['fans'][0]['state'] == 'fault'


def test_confirmed_fan_missing_attributes_is_read_error(tmp_path):
    hwmon = healthy_display_fixture(tmp_path)
    (hwmon / 'pwm2').unlink()
    result = show_result(tmp_path, raw=True)
    assert result.returncode == 1
    output = json.loads(result.stdout)
    assert output['configuration']['state'] == 'error'
    assert output['fans'][1]['state'] == 'read-error'


def test_confirmed_fan_zero_tach_at_max_floor_is_stalled(tmp_path):
    hwmon = healthy_display_fixture(tmp_path)
    put(hwmon, 'pwm1', '255')
    put(hwmon, 'fan1_input', '0')
    floors = tmp_path / 'run/fan-pid/floors'
    floors.write_text(floors.read_text().replace('0x30=60', '0x30=255'))
    result = show_result(tmp_path, raw=True)
    assert result.returncode == 1
    output = json.loads(result.stdout)
    assert output['configuration']['state'] == 'error'
    assert output['fans'][0]['state'] == 'stalled'


def test_absent_optional_fan_without_fault_remains_healthy(tmp_path):
    hwmon = healthy_display_fixture(tmp_path)
    put(hwmon, 'fan2_input', '0')
    floors = tmp_path / 'run/fan-pid/floors'
    floors.write_text(floors.read_text().replace('present:0x40=1', 'present:0x40=0'))
    result = show_result(tmp_path, raw=True)
    assert result.returncode == 0
    output = json.loads(result.stdout)
    assert output['configuration']['state'] == 'ok'
    assert output['fans'][1]['state'] == 'not present'


def test_failed_boot_marker_overrides_fresh_normal_ack(tmp_path):
    healthy_display_fixture(tmp_path)
    put(tmp_path, 'run/vyos-fan-boot-failed', '')
    result = show_result(tmp_path, raw=True)
    assert result.returncode == 1
    output = json.loads(result.stdout)
    assert output['configuration']['state'] == 'error'
    assert 'boot' in output['configuration']['message'].lower()


def test_tmp431_discovery_and_output(tmp_path):
    put(tmp_path, 'proc/device-tree/compatible', 'fsl,ls1046a')
    sensor(tmp_path, 9, 'i2c-mux@70/i2c@1/temp-sensor@4c', values={
        'temp1_input': '55000', 'temp2_input': '70000'})
    sensor(tmp_path, 2, 'i2c-mux@70/i2c@2/temp-sensor@4c', values={
        'temp1_input': '85000', 'temp2_input': '99999'})
    sensor(tmp_path, 0, 'other/i2c@1/temp-sensor@4c', values={'temp1_input': '100000'})
    sensor(tmp_path, 1, 'i2c-mux@70/i2c@1/temp-sensor@4c-extra', values={'temp1_input': '100000'})
    sensor(tmp_path, 3, 'i2c-mux@70/i2c@1/temp-sensor@4c', name='other', values={'temp1_input': '100000'})
    zones = {z['zone']: z for z in json.loads(run_show(tmp_path, raw=True))['zones']}
    for name, temp, state, setpoint in [('PCB', 55.0, 'warm', 60.0),
                                        ('CPU-ext', 70.0, 'hot', 70.0),
                                        ('SFP+', 85.0, 'crit', 60.0)]:
        assert zones[name] == {'zone': name, 'temp_c': temp, 'state': state,
                               'setpoint_c': setpoint, 'critical_c': 85.0}
    text = run_show(tmp_path)
    for name, temp, state in [('PCB', '55.0', '[WARM]'), ('CPU-ext', '70.0', '[HOT]'),
                              ('SFP+', '85.0', '[CRIT]')]:
        assert any(name in line and temp in line and state in line for line in text.splitlines())


def test_missing_and_unreadable_board_sensors(tmp_path):
    put(tmp_path, 'proc/device-tree/compatible', 'fsl,ls1046a')
    sensor(tmp_path, 4, 'i2c-mux@70/i2c@1/temp-sensor@4c', values={'temp1_input': 'invalid'})
    zones = {z['zone']: z for z in json.loads(run_show(tmp_path, raw=True))['zones']}
    for name in ('PCB', 'CPU-ext', 'SFP+'):
        assert zones[name]['temp_c'] is None
        assert zones[name]['state'] in ('missing', 'unreadable')
    assert zones['PCB']['state'] == 'unreadable'
    assert zones['CPU-ext']['state'] == 'missing'
    assert zones['SFP+']['state'] == 'missing'
    text = run_show(tmp_path)
    assert any('PCB' in line and '(read failed)' in line for line in text.splitlines())
    assert any('CPU-ext' in line and '(not present)' in line for line in text.splitlines())
    assert any('SFP+' in line and '(not present)' in line for line in text.splitlines())


def test_full_display_and_fan_fault(tmp_path):
    put(tmp_path, 'proc/device-tree/compatible', 'fsl,ls1046a')
    for index, (zone, temp) in enumerate([
        ('ddr_thermal', '42000'), ('serdes-thermal', '66000'),
        ('fman-thermal', '70000'), ('core_cluster', '85000'),
        ('sec-thermal', '51000'),
    ]):
        base = f'sys/class/thermal/thermal_zone{index}'
        put(tmp_path, f'{base}/type', zone)
        put(tmp_path, f'{base}/temp', temp)
    sensor(tmp_path, 9, 'i2c-mux@70/i2c@1/temp-sensor@4c', values={
        'temp1_input': '33000', 'temp2_input': '54000'})
    sensor(tmp_path, 2, 'i2c-mux@70/i2c@2/temp-sensor@4c', values={'temp1_input': '41000'})
    hwmon = tmp_path / 'sys/class/hwmon/hwmon4'
    for path, value in {'name': 'emc2305', 'pwm1': '128', 'fan1_input': '4000',
                        'fan1_fault': '1', 'pwm2': '0', 'fan2_input': '0',
                        'fan2_fault': '0'}.items():
        put(hwmon, path, value)
    put(tmp_path, 'run/fan-pid/floors',
        '0x30=51\nrpm:0x30=2000\npresent:0x30=1\npresent:0x40=0\ntach_source=raw-m1\n')
    text = run_show(tmp_path)
    for label, temp, sp, crit, state in [
        ('Memory', '42.0', '65', '80', 'COOL'), ('SerDes', '66.0', '70', '85', 'WARM'),
        ('FMan', '70.0', '70', '87', 'HOT'), ('CPU-int', '85.0', '65', '85', 'CRIT'),
        ('Crypto', '51.0', '65', '85', 'COOL'), ('PCB', '33.0', '60', '85', 'COOL'),
        ('CPU-ext', '54.0', '70', '85', 'COOL'), ('SFP+', '41.0', '60', '85', 'COOL'),
    ]:
        assert any(line.split() == [label, temp, 'C', sp, 'C', crit, 'C', f'[{state}]']
                   for line in text.splitlines())
    assert 'EMC2305 PWM fan controller' in text
    assert f'hwmon path : {hwmon}' in text
    assert any(line.split() == ['Fan', '1', '51', '128', '255', '50%', '2000', 'yes', 'FAULT']
               for line in text.splitlines())
    assert any('Fan 2' in line and 'not present' in line for line in text.splitlines())
    raw = json.loads(run_show(tmp_path, raw=True))
    assert raw['fans'][0]['rpm'] == 2000
    assert raw['fans'][0]['state'] == 'fault'
    assert raw['fans'][0]['fault'] == 1
    assert raw['fans'][1]['present'] is False


def test_board_zone_names_are_not_thermal_zone_types(tmp_path):
    put(tmp_path, 'proc/device-tree/compatible', 'fsl,ls1046a')
    for index, name in enumerate(('PCB', 'CPU-ext', 'SFP+')):
        put(tmp_path, f'sys/class/thermal/thermal_zone{index}/type', name)
        put(tmp_path, f'sys/class/thermal/thermal_zone{index}/temp', '99000')
    zones = {z['zone']: z for z in json.loads(run_show(tmp_path, raw=True))['zones']}
    for name in ('PCB', 'CPU-ext', 'SFP+'):
        assert zones[name]['state'] == 'missing'
        assert zones[name]['temp_c'] is None


@pytest.mark.parametrize('pwm,rpm,fault,floor,tach_source,expected', [
    ('25', '0', '0', '51', 'sysfs-m2', 'blind'),
    ('51', '0', '0', '51', 'sysfs-m2', 'stalled'),
    ('128', '4000', '0', '51', 'raw-m1', 'read-error'),
    ('128', '4000', 'invalid', '51', 'sysfs-m2', 'read-error'),
    ('128', '0', '0', '0', 'sysfs-m2', 'not present'),
])
def test_fan_states(tmp_path, pwm, rpm, fault, floor, tach_source, expected):
    put(tmp_path, 'proc/device-tree/compatible', 'fsl,ls1046a')
    hwmon = tmp_path / 'sys/class/hwmon/hwmon0'
    for path, value in {'name': 'emc2305', 'pwm1': pwm, 'fan1_input': rpm,
                        'fan1_fault': fault}.items():
        put(hwmon, path, value)
    present = 0 if expected == 'not present' else 1
    put(tmp_path, 'run/fan-pid/floors',
        f'0x30={floor}\npresent:0x30={present}\ntach_source={tach_source}\n')
    fan = json.loads(run_show(tmp_path, raw=True))['fans'][0]
    assert fan['state'] == expected
    text = run_show(tmp_path)
    display = {'blind': 'BLIND', 'stalled': 'NO-TACH', 'read-error': 'READ-FAIL',
               'not present': 'not present'}[expected]
    assert any('Fan 1' in line and display in line for line in text.splitlines())


def test_zone_overrides_and_corrupt_snapshot(tmp_path):
    put(tmp_path, 'proc/device-tree/compatible', 'fsl,ls1046a')
    put(tmp_path, 'sys/class/thermal/thermal_zone0/type', 'ddr-thermal')
    put(tmp_path, 'sys/class/thermal/thermal_zone0/temp', '60000')
    defaults = {
        'memory': {'setpoint': 55, 'critical': 75},
        'serdes': {'setpoint': 70, 'critical': 85},
        'fman': {'setpoint': 70, 'critical': 87},
        'cpu-int': {'setpoint': 65, 'critical': 85},
        'crypto': {'setpoint': 65, 'critical': 85},
        'pcb': {'setpoint': 60, 'critical': 85},
        'cpu-ext': {'setpoint': 70, 'critical': 85},
        'sfp-plus': {'setpoint': 60, 'critical': 85},
    }
    state = put_snapshot(tmp_path, defaults)
    zones = json.loads(run_show(tmp_path, raw=True))['zones']
    assert zones[0]['setpoint_c'] == 55
    assert zones[0]['critical_c'] == 75
    assert zones[0]['state'] == 'hot'
    assert any(line.split() == ['Memory', '60.0', 'C', '55', 'C', '75', 'C', '[HOT]']
               for line in run_show(tmp_path).splitlines())
    state.unlink()
    acknowledge(tmp_path)
    assert json.loads(run_show(tmp_path, raw=True))['zones'][0]['setpoint_c'] == 65
    state.write_text('{')
    env = dict(os.environ, VYOS_FAN_SYS_ROOT=str(tmp_path),
               PYTHONPATH=str(SCRIPT.parents[2] / 'python'))
    result = subprocess.run([sys.executable, str(SCRIPT)], env=env, text=True,
                            capture_output=True)
    assert result.returncode == 1
    assert 'configuration: error' in result.stdout
    assert 'snapshot unavailable' in result.stdout


def test_fan_check_uses_shared_snapshot(tmp_path):
    put(tmp_path, 'proc/device-tree/compatible', 'fsl,ls1046a')
    put(tmp_path, 'sys/class/thermal/thermal_zone0/type', 'ddr-thermal')
    put(tmp_path, 'sys/class/thermal/thermal_zone0/temp', '60000')
    state = {
        'memory': {'setpoint': 55, 'critical': 75},
        'serdes': {'setpoint': 70, 'critical': 85},
        'fman': {'setpoint': 70, 'critical': 87},
        'cpu-int': {'setpoint': 65, 'critical': 85},
        'crypto': {'setpoint': 65, 'critical': 85},
        'pcb': {'setpoint': 60, 'critical': 85},
        'cpu-ext': {'setpoint': 70, 'critical': 85},
        'sfp-plus': {'setpoint': 60, 'critical': 85},
    }
    put_snapshot(tmp_path, state)
    acknowledge(tmp_path, state)
    result = run_check(tmp_path)
    assert any(line.split() == ['Memory', '60.0', 'C', '55', 'C', '75', 'C', '[HOT]']
               for line in result.stdout.splitlines())
    put(tmp_path, 'run/vyos-fan-zones.json', '{')
    result = run_check(tmp_path)
    assert result.returncode == 1
    assert 'configuration: error' in result.stdout
    assert 'snapshot unavailable' in result.stdout


def test_unacknowledged_config_is_not_reported_as_applied(tmp_path):
    put(tmp_path, 'proc/device-tree/compatible', 'fsl,ls1046a')
    put(tmp_path, 'sys/class/thermal/thermal_zone0/type', 'ddr-thermal')
    put(tmp_path, 'sys/class/thermal/thermal_zone0/temp', '60000')
    desired = {
        'memory': {'setpoint': 55, 'critical': 75},
        'serdes': {'setpoint': 70, 'critical': 85},
        'fman': {'setpoint': 70, 'critical': 87},
        'cpu-int': {'setpoint': 65, 'critical': 85},
        'crypto': {'setpoint': 65, 'critical': 85},
        'pcb': {'setpoint': 60, 'critical': 85},
        'cpu-ext': {'setpoint': 70, 'critical': 85},
        'sfp-plus': {'setpoint': 60, 'critical': 85},
    }
    put_snapshot(tmp_path, desired)
    floor_export_metadata(tmp_path, 'present:0x30=1\npresent:0x40=0\n')
    env = dict(os.environ, VYOS_FAN_SYS_ROOT=str(tmp_path),
               PYTHONPATH=str(SCRIPT.parents[2] / 'python'))
    result = subprocess.run([sys.executable, str(SCRIPT), 'raw'], env=env,
                            text=True, capture_output=True)
    assert result.returncode == 1
    raw = json.loads(result.stdout)
    assert raw['configuration']['state'] == 'unknown'
    assert raw['zones'][0]['setpoint_c'] is None
    previous = dict(desired)
    previous['memory'] = {'setpoint': 65, 'critical': 80}
    acknowledge(tmp_path, previous, verify=False)
    verify_snapshot(tmp_path, desired)
    result = subprocess.run([sys.executable, str(SCRIPT), 'raw'], env=env,
                            text=True, capture_output=True)
    assert result.returncode == 1
    raw = json.loads(result.stdout)
    assert raw['configuration']['state'] == 'pending'
    assert raw['zones'][0]['setpoint_c'] == 65
    assert raw['zones'][0]['critical_c'] == 80


@pytest.mark.parametrize('floor,present,rpm', [('0', '1', '0'), ('255', '1', '0'), ('255', '0', '0')])
def test_explicit_fault_is_not_masked_by_absence(tmp_path, floor, present, rpm):
    put(tmp_path, 'proc/device-tree/compatible', 'fsl,ls1046a')
    hwmon = tmp_path / 'sys/class/hwmon/hwmon0'
    for path, value in {'name': 'emc2305', 'pwm1': '255', 'fan1_input': rpm,
                        'fan1_fault': '1'}.items():
        put(hwmon, path, value)
    put(tmp_path, 'run/fan-pid/floors', f'0x30={floor}\npresent:0x30={present}\n')
    fan = json.loads(run_show(tmp_path, raw=True))['fans'][0]
    assert fan['fault'] == 1
    assert fan['state'] == 'fault'
    assert any('Fan 1' in line and 'yes' in line and 'FAULT' in line
               for line in run_show(tmp_path).splitlines())


def test_remote_sensor_fault_is_reported_and_unhealthy(tmp_path):
    put(tmp_path, 'proc/device-tree/compatible', 'fsl,ls1046a')
    sensor(tmp_path, 9, 'i2c-mux@70/i2c@1/temp-sensor@4c',
           values={'temp1_input': '33000', 'temp2_input': '54000', 'temp2_fault': '1'})
    acknowledge(tmp_path)
    env = dict(os.environ, VYOS_FAN_SYS_ROOT=str(tmp_path),
               PYTHONPATH=str(SCRIPT.parents[2] / 'python'))
    result = subprocess.run([sys.executable, str(SCRIPT), 'raw'], env=env,
                            text=True, capture_output=True)
    assert result.returncode == 1
    data = json.loads(result.stdout)
    assert data['configuration']['state'] == 'error'
    cpu = next(zone for zone in data['zones'] if zone['zone'] == 'CPU-ext')
    assert cpu['state'] == 'fault'
    check = run_check(tmp_path)
    assert check.returncode == 1
    assert any('CPU-ext' in line and 'sensor fault' in line for line in check.stdout.splitlines())


def test_stale_sensor_ack_is_not_reported_as_effective(tmp_path):
    put(tmp_path, 'proc/device-tree/compatible', 'fsl,ls1046a')
    put(tmp_path, 'sys/class/thermal/thermal_zone0/type', 'ddr-thermal')
    put(tmp_path, 'sys/class/thermal/thermal_zone0/temp', '60000')
    acknowledge(tmp_path)
    floor_export_metadata(tmp_path, 'present:0x30=1\npresent:0x40=0\n')
    ack = tmp_path / 'run/fan-pid/applied.json'
    data = json.loads(ack.read_text())
    data['sensors']['memory'] = clock_now() - 30
    ack.write_text(json.dumps(data))
    env = dict(os.environ, VYOS_FAN_SYS_ROOT=str(tmp_path),
               PYTHONPATH=str(SCRIPT.parents[2] / 'python'))
    result = subprocess.run([sys.executable, str(SCRIPT), 'raw'], env=env,
                            text=True, capture_output=True)
    assert result.returncode == 1
    raw = json.loads(result.stdout)
    assert raw['configuration']['state'] == 'unknown'
    assert raw['zones'][0]['setpoint_c'] is None
    check = run_check(tmp_path)
    assert check.returncode == 1
    assert 'configuration: unknown' in check.stdout


def test_fan_check_reports_fault_even_when_channel_is_absent(tmp_path):
    put(tmp_path, 'proc/device-tree/compatible', 'fsl,ls1046a')
    hwmon = tmp_path / 'sys/class/hwmon/hwmon0'
    for path, value in {'name': 'emc2305', 'pwm1': '255', 'fan1_input': '0',
                        'fan1_fault': '1'}.items():
        put(hwmon, path, value)
    put(tmp_path, 'run/fan-pid/floors', '0x30=255\npresent:0x30=0\n')
    acknowledge(tmp_path)
    result = run_check(tmp_path)
    assert result.returncode == 1
    assert any('Fan 1' in line and 'yes' in line and 'FAULT' in line
               for line in result.stdout.splitlines())


def test_unacknowledged_fan_check_still_reports_fault(tmp_path):
    put(tmp_path, 'proc/device-tree/compatible', 'fsl,ls1046a')
    put(tmp_path, 'sys/class/thermal/thermal_zone0/type', 'ddr-thermal')
    put(tmp_path, 'sys/class/thermal/thermal_zone0/temp', '60000')
    hwmon = tmp_path / 'sys/class/hwmon/hwmon0'
    put(hwmon, 'name', 'emc2305')
    put(hwmon, 'fan1_fault', '1')
    result = run_check(tmp_path)
    assert result.returncode == 1
    assert 'configuration: error' in result.stdout
    assert 'fan telemetry unavailable' in result.stdout
    assert any('Memory' in line and '[UNKNOWN]' in line for line in result.stdout.splitlines())
    assert any('Fan 1' in line and 'FAULT' in line for line in result.stdout.splitlines())


def test_expired_and_malformed_pending_are_visible(tmp_path):
    put(tmp_path, 'proc/device-tree/compatible', 'fsl,ls1046a')
    put(tmp_path, 'sys/class/thermal/thermal_zone0/type', 'ddr-thermal')
    put(tmp_path, 'sys/class/thermal/thermal_zone0/temp', '60000')
    acknowledge(tmp_path)
    pending = put(tmp_path, 'run/vyos-fan-pending', str(clock_now() - 60))
    env = dict(os.environ, VYOS_FAN_SYS_ROOT=str(tmp_path),
               PYTHONPATH=str(SCRIPT.parents[2] / 'python'))
    for value in (None, 'nan', 'bad'):
        if value is not None:
            pending.write_text(value)
        result = subprocess.run([sys.executable, str(SCRIPT), 'raw'], env=env,
                                text=True, capture_output=True)
        assert result.returncode == 1
        assert json.loads(result.stdout)['configuration']['state'] == 'error'
        check = run_check(tmp_path)
        assert check.returncode == 1
        assert 'configuration: error' in check.stdout


@pytest.mark.parametrize('marker', [
    'fan_state:0x30=recovering\ntach_recovery:0x30=1\n',
    'tach_recovery:0x30=1\n',
])
def test_recovering_chassis_is_not_reported_absent(tmp_path, marker):
    put(tmp_path, 'proc/device-tree/compatible', 'fsl,ls1046a')
    hwmon = tmp_path / 'sys/class/hwmon/hwmon0'
    for path, value in {'name': 'emc2305', 'pwm1': '255', 'fan1_input': '0',
                        'fan1_fault': '0'}.items():
        put(hwmon, path, value)
    put(tmp_path, 'run/fan-pid/floors', '0x30=255\npresent:0x30=0\n'
        'rpm:0x30=0\ntach_source=raw-m1\n' + marker)
    raw = json.loads(run_show(tmp_path, raw=True))
    assert raw['fans'][0]['state'] == 'recovering'
    assert raw['fans'][0]['present'] is False
    assert raw['fans'][0]['presence'] == 'unknown'
    text = run_show(tmp_path)
    assert any('Fan 1' in line and 'RECOVERING' in line and 'not present' not in line
               for line in text.splitlines())
    check = run_check(tmp_path)
    assert check.returncode == 1
    assert any('Fan 1' in line and 'RECOVERING' in line and 'not present' not in line
               for line in check.stdout.splitlines())


def test_unconfirmed_optional_fan_is_unknown_but_legacy_absence_is_preserved(tmp_path):
    put(tmp_path, 'proc/device-tree/compatible', 'fsl,ls1046a')
    hwmon = tmp_path / 'sys/class/hwmon/hwmon0'
    for path, value in {'name': 'emc2305', 'pwm2': '255', 'fan2_input': '0',
                        'fan2_fault': '0'}.items():
        put(hwmon, path, value)
    state = put(tmp_path, 'run/fan-pid/floors', '0x40=255\npresent:0x40=0\n'
                'rpm:0x40=0\ntach_source=raw-m1\nfan_state:0x40=unknown\n')
    fan = json.loads(run_show(tmp_path, raw=True))['fans'][1]
    assert fan['state'] == 'unknown' and fan['present'] is False and fan['presence'] == 'unknown'
    assert any('Fan 2' in line and 'UNKNOWN' in line for line in run_check(tmp_path).stdout.splitlines())
    state.write_text('0x40=255\npresent:0x40=0\nrpm:0x40=0\ntach_source=raw-m1\n')
    fan = json.loads(run_show(tmp_path, raw=True))['fans'][1]
    assert fan['state'] == 'not present' and fan['present'] is False


@pytest.mark.parametrize('marker,expected', [
    ('tach_recovery:0x40=1\n', 'unknown'),
    ('fan_state:0x30=garbage\n', 'unknown'),
    ('fan_state:0x30=present\ntach_recovery:0x30=1\n', 'recovering'),
])
def test_recovery_metadata_is_fail_safe_for_legacy_and_malformed_values(tmp_path, marker, expected):
    put(tmp_path, 'proc/device-tree/compatible', 'fsl,ls1046a')
    hwmon = tmp_path / 'sys/class/hwmon/hwmon0'
    for path, value in {'name': 'emc2305', 'pwm1': '255', 'fan1_input': '0', 'fan1_fault': '0',
                        'pwm2': '255', 'fan2_input': '0', 'fan2_fault': '0'}.items():
        put(hwmon, path, value)
    put(tmp_path, 'run/fan-pid/floors', '0x30=255\n0x40=255\npresent:0x30=0\npresent:0x40=0\n'
        'rpm:0x30=0\nrpm:0x40=0\ntach_source=raw-m1\n' + marker)
    index = 1 if '0x40' in marker else 0
    fan = json.loads(run_show(tmp_path, raw=True))['fans'][index]
    assert fan['state'] == expected and fan['present'] is False and fan['presence'] == 'unknown'
    assert any(f'Fan {index + 1}' in line and expected.upper() in line
               for line in run_check(tmp_path).stdout.splitlines())


def test_previously_confirmed_auxiliary_fan_reports_recovering(tmp_path):
    put(tmp_path, 'proc/device-tree/compatible', 'fsl,ls1046a')
    hwmon = tmp_path / 'sys/class/hwmon/hwmon0'
    for path, value in {'name': 'emc2305', 'pwm2': '255', 'fan2_input': '0',
                        'fan2_fault': '0'}.items():
        put(hwmon, path, value)
    put(tmp_path, 'run/fan-pid/floors', '0x40=60\npresent:0x40=1\nrpm:0x40=0\n'
        'tach_source=raw-m1\nfan_state:0x40=recovering\ntach_recovery:0x40=1\n')
    fan = json.loads(run_show(tmp_path, raw=True))['fans'][1]
    assert fan['state'] == 'recovering'
    assert fan['present'] is True and fan['presence'] == 'unknown'
    assert any('Fan 2' in line and 'RECOVERING' in line and 'not present' not in line
               for line in run_check(tmp_path).stdout.splitlines())


def test_recovering_fan_is_visible_with_missing_hwmon_attributes(tmp_path):
    put(tmp_path, 'proc/device-tree/compatible', 'fsl,ls1046a')
    hwmon = tmp_path / 'sys/class/hwmon/hwmon0'
    put(hwmon, 'name', 'emc2305')
    put(tmp_path, 'run/fan-pid/floors', '0x30=255\npresent:0x30=0\n'
        'tach_source=raw-m1\nfan_state:0x30=recovering\n')
    fan = json.loads(run_show(tmp_path, raw=True))['fans'][0]
    assert fan['state'] == 'recovering' and fan['present'] is False and fan['presence'] == 'unknown'
    assert any('Fan 1' in line and 'RECOVERING' in line for line in run_check(tmp_path).stdout.splitlines())


def test_fan_fault_remains_visible_while_recovering(tmp_path):
    put(tmp_path, 'proc/device-tree/compatible', 'fsl,ls1046a')
    hwmon = tmp_path / 'sys/class/hwmon/hwmon0'
    for path, value in {'name': 'emc2305', 'pwm1': '255', 'fan1_input': '0',
                        'fan1_fault': '1'}.items():
        put(hwmon, path, value)
    put(tmp_path, 'run/fan-pid/floors', '0x30=255\npresent:0x30=0\n'
        'rpm:0x30=0\ntach_source=raw-m1\nfan_state:0x30=recovering\n')
    fan = json.loads(run_show(tmp_path, raw=True))['fans'][0]
    assert fan['state'] == 'fault' and fan['fault'] == 1
    assert any('Fan 1' in line and 'FAULT' in line for line in run_check(tmp_path).stdout.splitlines())


def test_missing_emc_controller(tmp_path):
    put(tmp_path, 'proc/device-tree/compatible', 'fsl,ls1046a')
    text = run_show(tmp_path)
    assert 'EMC2305 PWM fan controller' in text
    assert 'NOT FOUND' in text
    assert all(not fan['exists'] for fan in json.loads(run_show(tmp_path, raw=True))['fans'])
