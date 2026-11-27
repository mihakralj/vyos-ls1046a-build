import fcntl
import importlib.machinery
import importlib.util
import io
import json
import multiprocessing
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import types
from pathlib import Path

import pytest

pytestmark = pytest.mark.host
ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / 'data/vyos-1x-files/python/vyos/fan_control.py'


def load_module():
    spec = importlib.util.spec_from_file_location('fan_control_test', MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_all_zones_and_numeric_boundaries():
    fan = load_module()
    for name, defaults in fan.DEFAULTS.items():
        result = fan.effective({'zone': {name: {'setpoint': '0', 'critical': '1'}}})
        assert result[name] == {'setpoint': 0, 'critical': 1}
        result = fan.effective({'zone': {name: {'setpoint': str(defaults['critical'] - 1)}}})
        assert result[name]['critical'] == defaults['critical']
        result = fan.effective({'zone': {name: {'critical': str(defaults['critical'])}}})
        assert result[name] == defaults


def test_schema_and_service_installation():
    from xml.etree import ElementTree
    schema = ROOT / 'data/vyos-1x-files/interface-definitions/system_hardware_fan.xml.in'
    root = ElementTree.parse(schema).getroot()
    fan = root.find(".//node[@name='fan']")
    assert fan is not None
    assert fan.get('owner') == '${vyos_conf_scripts_dir}/system_hardware_fan.py'
    zone = fan.find(".//tagNode[@name='zone']")
    assert set(zone.find('properties/completionHelp/list').text.split()) == set(load_module().DEFAULTS)
    assert {node.get('name') for node in zone.findall('children/leafNode')} == {'setpoint', 'critical'}
    assert 'Wants=vyos-fan-reconcile.service' in (ROOT / 'board/systemd/fan-pid.service').read_text()
    tmpfiles = (ROOT / 'board/systemd/fan-pid.tmpfiles').read_text()
    assert 'vyos-fan-reconcile.service' in tmpfiles
    assert 'vyos-fan-reconcile.timer' not in tmpfiles
    reconcile_unit = (ROOT / 'board/systemd/vyos-fan-reconcile.service').read_text()
    assert 'Type=simple' in reconcile_unit
    assert '--reconcile-daemon' in reconcile_unit
    assert 'Restart=always' in reconcile_unit
    assert 'WantedBy=multi-user.target' in reconcile_unit
    assert not (ROOT / 'board/systemd/vyos-fan-reconcile.timer').exists()
    build = (ROOT / 'bin/ci-setup-vyos-build.sh').read_text()
    assert 'board/systemd/vyos-fan-reconcile.service' in build
    assert 'board/systemd/vyos-fan-reconcile.timer' not in build
    assert load_module().VERIFICATION_MAX_AGE >= 30.0


def test_partial_overrides_and_delete():
    fan = load_module()
    values = fan.effective({'zone': {'memory': {'setpoint': '55'},
                                      'cpu-ext': {'critical': '78'}}})
    assert values['memory'] == {'setpoint': 55, 'critical': 80}
    assert values['cpu-ext'] == {'setpoint': 70, 'critical': 78}
    assert values['sfp-plus'] == fan.DEFAULTS['sfp-plus']
    assert fan.effective({'zone': {'memory': {'critical': '75'}}})['memory'] == {
        'setpoint': 65, 'critical': 75}
    assert fan.effective({}) == fan.DEFAULTS


@pytest.mark.parametrize('config', [
    {'zone': {'bad': {'setpoint': '1'}}},
    {'zone': {'memory': {'unknown': '1'}}},
    {'zone': {'memory': {'setpoint': '-1'}}},
    {'zone': {'memory': {'setpoint': '1.5'}}},
    {'zone': {'memory': {'setpoint': '101'}}},
    {'zone': {'memory': {'critical': '101'}}},
    {'zone': {'memory': {'setpoint': '80'}}},
    {'zone': {'memory': {'critical': '65'}}},
    {'zone': {'memory': {'critical': '81'}}},
    {'zone': {'memory': {'setpoint': '60', 'critical': '60'}}},
    {'zone': {'memory': {'setpoint': True}}},
])
def test_invalid_values(config):
    with pytest.raises(ValueError):
        load_module().effective(config)


def test_snapshot_atomic_and_corruption(tmp_path, monkeypatch):
    fan = load_module()
    path = tmp_path / 'state.json'
    values = fan.effective({'zone': {'fman': {'setpoint': '50'}}})
    fan.write_snapshot(values, path)
    assert fan.read_snapshot(path) == values
    assert path.stat().st_mode & 0o777 == 0o644
    previous = path.read_text()
    monkeypatch.setattr(fan.os, 'replace', lambda *_: (_ for _ in ()).throw(OSError('failure')))
    with pytest.raises(OSError):
        fan.write_snapshot(fan.effective({}), path)
    assert path.read_text() == previous
    assert sorted(p.name for p in tmp_path.iterdir()) == ['state.json', 'state.json.established']
    path.write_text('{')
    with pytest.raises(ValueError):
        fan.read_snapshot(path)
    path.write_text(json.dumps({'memory': {'setpoint': 1, 'critical': 2}}))
    with pytest.raises(ValueError):
        fan.read_snapshot(path)
    path.unlink()
    with pytest.raises(ValueError, match='missing after initialization'):
        fan.read_snapshot(path)
    fan.snapshot_marker(path).unlink()
    assert fan.read_snapshot(path) == fan.DEFAULTS


def publish_test_snapshot(fan, values):
    fan.mark_boot_ready()
    fan.write_snapshot(values)
    fan.write_verification(values)


def publish_test_ack(fan, values, mode='normal', path=None):
    now = fan.clock_now()
    _, persisted, generation = fan.load_snapshot_record()
    fan.write_applied(values, mode=mode, path=path,
                      sensors={name: now for name in fan.DEFAULTS},
                      generation=generation if persisted else '0' * 32,
                      chassis={'rpm': 1000, 'sampled': now})


def modules(monkeypatch):
    fan = load_module()
    vyos = types.ModuleType('vyos')
    vyos.__path__ = []
    vyos.ConfigError = type('ConfigError', (Exception,), {})
    config = types.ModuleType('vyos.config')
    config.Config = None
    base = types.ModuleType('vyos.base')
    base.Warning = lambda message: None
    utils = types.ModuleType('vyos.utils')
    utils.__path__ = []
    boot = types.ModuleType('vyos.utils.boot')
    boot.boot_configuration_complete = lambda: True
    boot.boot_configuration_success = lambda: True
    commit = types.ModuleType('vyos.utils.commit')
    commit.acquire_commit_lock_file = lambda: (object(), '')
    commit.release_commit_lock_file = lambda lock: None
    commit.commit_in_progress2 = lambda: False
    for name, module in {'vyos': vyos, 'vyos.base': base, 'vyos.fan_control': fan, 'vyos.config': config,
                         'vyos.utils': utils, 'vyos.utils.boot': boot,
                         'vyos.utils.commit': commit}.items():
        monkeypatch.setitem(sys.modules, name, module)
    return fan, config


def load_script(monkeypatch, path, name):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    return module


def mock_tach_modes(monkeypatch, daemon):
    daemon.TACH_MODES.update({0x30: 1, 0x40: 1})
    monkeypatch.setattr(daemon, 'current_tach_mode', lambda *_: 1)
    monkeypatch.setattr(daemon, 'configure_tach_channel', lambda *_: 1)
    monkeypatch.setattr(daemon, 'i2c_read_byte', lambda fd, reg: 255 if reg in (0x30, 0x40) else 0)


def test_daemon_reload_and_corruption_force_max(tmp_path, monkeypatch):
    fan, _ = modules(monkeypatch)
    fan.STATE = tmp_path / 'state'
    fan.PENDING = tmp_path / 'pending'
    fan.APPLIED = tmp_path / 'applied'
    publish_test_snapshot(fan, fan.effective({}))
    daemon = load_script(monkeypatch, ROOT / 'board/scripts/fan-pid', 'fan_pid_test')
    mock_tach_modes(monkeypatch, daemon)
    monkeypatch.setattr(daemon.Controller, 'export_floors', lambda self: True)
    monkeypatch.setattr(daemon, 'fan_rpm', lambda *args: 1000)
    monkeypatch.setattr(daemon, 'read_temp', lambda path: 76000 / 1000)
    monkeypatch.setattr(daemon, 'read_sensor', lambda zone, path: daemon.read_temp(path))
    writes = []
    monkeypatch.setattr(daemon, 'i2c_write_pwm', lambda fd, value, reg: writes.append(value) or True)
    ctrl = daemon.Controller(1, [{'name': 'fan1', 'duty_reg': 0x30,
                                  'tach_file': 'fan1_input'}], '',
                             {zone.name: f'/fake/{zone.name}' for zone in daemon.ZONES}, {0x30: 60})
    ctrl.next_rescan_t = float('inf')
    assert ctrl.step()[0]['fan1'] < 255
    publish_test_snapshot(fan, fan.effective({'zone': {'memory': {'setpoint': '50', 'critical': '75'}}}))
    assert ctrl.step()[0]['fan1'] == 255
    assert daemon.ZONES[0].setpoint == 50
    fan.STATE.write_text('{')
    assert ctrl.step()[0]['fan1'] == 255
    assert writes[-1] == 255
    fan.STATE.unlink()
    assert not ctrl.reload_zone_settings()
    fan.write_snapshot(fan.effective({}))
    assert ctrl.reload_zone_settings()
    assert daemon.ZONES[0].setpoint == 65
    assert daemon.ZONES[0].crit_temp == 80


@pytest.mark.parametrize('running', [{}, {'zone': {'memory': {'setpoint': '55'}}}])
def test_failed_boot_never_verifies_empty_or_partial_fan_config(tmp_path, monkeypatch, running):
    fan, _ = modules(monkeypatch)
    fan.STATE = tmp_path / 'state'
    fan.APPLIED = tmp_path / 'applied'
    script = load_script(monkeypatch, ROOT / 'data/vyos-1x-files/src/conf_mode/system_hardware_fan.py',
                         'fan_conf_failed_boot_test')
    script.PENDING = tmp_path / 'pending'
    monkeypatch.setattr(script, 'boot_configuration_success', lambda: False, raising=False)
    monkeypatch.setattr(script, 'explicit_boot_failure', lambda: True)
    class FakeConfig:
        def get_config_tree(self, effective=False):
            return {}

        def get_config_dict(self, path, effective=False, get_first_key=False):
            return running

    script.Config = FakeConfig
    script.reconcile()
    assert script.PENDING.exists()
    marker = tmp_path / 'vyos-fan-boot-failed'
    assert marker.exists() and marker.stat().st_mode & 0o077 == 0
    assert not fan.STATE.exists()
    assert not fan.verification_path().exists()


def test_failed_boot_revokes_existing_verification_and_manual_commit_stays_safe(tmp_path, monkeypatch):
    fan, _, ctrl, writes = controller_fixture(tmp_path, monkeypatch, 'fan_pid_failed_boot_test')
    ctrl.step()
    assert fan.APPLIED.exists()
    old = fan.read_snapshot()
    script = load_script(monkeypatch, ROOT / 'data/vyos-1x-files/src/conf_mode/system_hardware_fan.py',
                         'fan_conf_failed_boot_latch_test')
    script.PENDING = fan.PENDING
    successful = [False]
    monkeypatch.setattr(script, 'boot_configuration_success', lambda: successful[0], raising=False)
    monkeypatch.setattr(script, 'explicit_boot_failure', lambda: not successful[0])
    class FakeConfig:
        def get_config_tree(self, effective=False):
            return {}

        def get_config_dict(self, path, effective=False, get_first_key=False):
            return {'zone': {'memory': {'setpoint': '55'}}}

    script.Config = FakeConfig
    script.reconcile()
    assert not fan.verification_path().exists()
    assert fan.PENDING.exists()
    assert ctrl.step()[0]['fan1'] == 255
    assert writes[-1] == 255 and not fan.APPLIED.exists()
    successful[0] = True
    script.apply({'zone': {'memory': {'setpoint': '55'}}})
    restarted = load_script(monkeypatch, ROOT / 'data/vyos-1x-files/src/conf_mode/system_hardware_fan.py',
                            'fan_conf_failed_boot_restart_test')
    restarted.PENDING = fan.PENDING
    restarted.Config = FakeConfig
    monkeypatch.setattr(restarted, 'boot_configuration_success', lambda: True)
    restarted.reconcile()
    assert fan.read_snapshot() == old
    assert not fan.verification_path().exists()
    assert fan.PENDING.exists()
    assert ctrl.step()[0]['fan1'] == 255 and not fan.APPLIED.exists()


def test_failed_boot_marker_invalidates_even_a_fresh_verification(tmp_path, monkeypatch):
    fan, _, ctrl, _ = controller_fixture(tmp_path, monkeypatch, 'fan_pid_boot_latch_test')
    ctrl.step()
    assert fan.read_applied()['mode'] == 'normal'
    (tmp_path / 'vyos-fan-boot-failed').touch()
    with pytest.raises(ValueError, match='boot|Boot'):
        fan.read_verification(fan.read_snapshot())
    with pytest.raises(ValueError, match='boot|Boot'):
        fan.write_verification(fan.read_snapshot())
    assert fan.configuration_status()['state'] == 'error'
    assert ctrl.step()[0]['fan1'] == 255
    assert not fan.APPLIED.exists()


def test_dangling_failed_boot_marker_remains_fail_safe(tmp_path, monkeypatch):
    fan, _, ctrl, _ = controller_fixture(tmp_path, monkeypatch, 'fan_pid_dangling_boot_latch_test')
    ctrl.step()
    (tmp_path / 'vyos-fan-boot-failed').symlink_to(tmp_path / 'missing-target')
    with pytest.raises(ValueError, match='boot|Boot'):
        fan.read_verification(fan.read_snapshot())
    assert fan.configuration_status()['state'] == 'error'
    script = load_script(monkeypatch, ROOT / 'data/vyos-1x-files/src/conf_mode/system_hardware_fan.py',
                         'fan_conf_dangling_boot_latch_test')
    script.PENDING = fan.PENDING
    monkeypatch.setattr(script, 'boot_configuration_success', lambda: True)
    script.reconcile()
    assert fan.PENDING.exists() and not fan.verification_path().exists()
    assert ctrl.step()[0]['fan1'] == 255
    assert not fan.APPLIED.exists()


def test_manual_commit_warns_when_failed_boot_requires_reboot(tmp_path, monkeypatch):
    fan, _ = modules(monkeypatch)
    fan.STATE = tmp_path / 'state'
    script = load_script(monkeypatch, ROOT / 'data/vyos-1x-files/src/conf_mode/system_hardware_fan.py',
                         'fan_conf_failed_boot_warning_test')
    script.PENDING = tmp_path / 'pending'
    warnings = []
    monkeypatch.setattr(script, 'Warning', warnings.append)
    monkeypatch.setattr(script, 'boot_configuration_success', lambda: False)
    monkeypatch.setattr(script, 'explicit_boot_failure', lambda: True)
    script.apply({})
    assert (tmp_path / 'vyos-fan-boot-failed').exists()
    assert warnings and 'reboot' in warnings[0].lower()
    monkeypatch.setattr(script, 'boot_configuration_success', lambda: True)
    warnings.clear()
    script.apply({})
    assert warnings and 'reboot' in warnings[0].lower()
    assert script.PENDING.exists()


def test_failed_boot_marker_write_error_still_revokes_verification(tmp_path, monkeypatch):
    fan, _, ctrl, _ = controller_fixture(tmp_path, monkeypatch, 'fan_pid_failed_marker_write_test')
    ctrl.step()
    assert fan.verification_path().exists()
    script = load_script(monkeypatch, ROOT / 'data/vyos-1x-files/src/conf_mode/system_hardware_fan.py',
                         'fan_conf_failed_marker_write_test')
    script.PENDING = fan.PENDING
    monkeypatch.setattr(script, 'boot_configuration_success', lambda: False)
    monkeypatch.setattr(script, 'explicit_boot_failure', lambda: True)
    def cannot_latch():
        raise PermissionError('read-only marker')
    monkeypatch.setattr(script, 'mark_boot_failed', cannot_latch)
    with pytest.raises(PermissionError, match='read-only marker'):
        script.reconcile()
    assert not fan.verification_path().exists()
    assert fan.PENDING.exists()
    assert ctrl.step()[0]['fan1'] == 255
    assert not fan.APPLIED.exists()


def test_boot_failure_during_config_lookup_never_publishes_verification(tmp_path, monkeypatch):
    fan, _ = modules(monkeypatch)
    fan.STATE = tmp_path / 'state'
    fan.APPLIED = tmp_path / 'applied'
    script = load_script(monkeypatch, ROOT / 'data/vyos-1x-files/src/conf_mode/system_hardware_fan.py',
                         'fan_conf_boot_failure_race_test')
    script.PENDING = tmp_path / 'pending'
    successful = [True]
    monkeypatch.setattr(script, 'boot_configuration_success', lambda: successful[0], raising=False)
    monkeypatch.setattr(script, 'explicit_boot_failure', lambda: not successful[0])
    class FakeConfig:
        def get_config_tree(self, effective=False):
            return {}

        def get_config_dict(self, path, effective=False, get_first_key=False):
            successful[0] = False
            return {}

    script.Config = FakeConfig
    script.reconcile()
    assert (tmp_path / 'vyos-fan-boot-failed').exists()
    assert script.PENDING.exists() and not fan.verification_path().exists()


@pytest.mark.parametrize('phase', ['snapshot', 'verification'])
def test_boot_failure_during_publication_revokes_lease(tmp_path, monkeypatch, phase):
    fan, _ = modules(monkeypatch)
    fan.STATE = tmp_path / 'state'
    fan.APPLIED = tmp_path / 'applied'
    script = load_script(monkeypatch, ROOT / 'data/vyos-1x-files/src/conf_mode/system_hardware_fan.py',
                         'fan_conf_boot_publish_failure_test')
    script.PENDING = tmp_path / 'pending'
    successful = [True]
    monkeypatch.setattr(script, 'boot_configuration_success', lambda: successful[0])
    monkeypatch.setattr(script, 'explicit_boot_failure', lambda: not successful[0])
    class FakeConfig:
        def get_config_tree(self, effective=False):
            return {}

        def get_config_dict(self, path, effective=False, get_first_key=False):
            return {'zone': {'memory': {'setpoint': '55'}}}

    script.Config = FakeConfig
    name = 'write_snapshot' if phase == 'snapshot' else 'write_verification'
    original = getattr(script, name)
    def flip_after_write(*args):
        result = original(*args)
        successful[0] = False
        return result
    monkeypatch.setattr(script, name, flip_after_write)
    script.reconcile()
    assert (tmp_path / 'vyos-fan-boot-failed').exists()
    assert script.PENDING.exists()
    assert not fan.verification_path().exists()


@pytest.mark.parametrize('error', [ValueError('invalid status'), PermissionError('status unreadable')])
def test_unreadable_boot_result_fails_closed_without_latching(tmp_path, monkeypatch, error):
    fan, _ = modules(monkeypatch)
    fan.STATE = tmp_path / 'state'
    script = load_script(monkeypatch, ROOT / 'data/vyos-1x-files/src/conf_mode/system_hardware_fan.py',
                         'fan_conf_bad_boot_status_test')
    script.PENDING = tmp_path / 'pending'
    def failed_status():
        raise error
    monkeypatch.setattr(script, 'boot_configuration_success', failed_status, raising=False)
    script.reconcile()
    assert script.PENDING.exists()
    assert not (tmp_path / 'vyos-fan-boot-failed').exists()
    assert not fan.verification_path().exists()


def test_incomplete_boot_does_not_latch_failure_or_verify_defaults(tmp_path, monkeypatch):
    fan, _ = modules(monkeypatch)
    fan.STATE = tmp_path / 'state'
    fan.APPLIED = tmp_path / 'applied'
    script = load_script(monkeypatch, ROOT / 'data/vyos-1x-files/src/conf_mode/system_hardware_fan.py',
                         'fan_conf_incomplete_boot_test')
    script.PENDING = tmp_path / 'pending'
    monkeypatch.setattr(script, 'boot_configuration_complete', lambda: False)
    monkeypatch.setattr(script, 'boot_configuration_success', lambda: True, raising=False)
    script.reconcile()
    assert script.PENDING.exists()
    assert not (tmp_path / 'vyos-fan-boot-failed').exists()
    assert not fan.STATE.exists() and not fan.verification_path().exists()
    monkeypatch.setattr(script, 'boot_configuration_complete', lambda: True)
    class FakeConfig:
        def get_config_tree(self, effective=False):
            return {}

        def get_config_dict(self, path, effective=False, get_first_key=False):
            return {}

    script.Config = FakeConfig
    script.reconcile()
    assert fan.read_snapshot() == fan.DEFAULTS
    assert fan.read_verification(fan.DEFAULTS)
    assert script.PENDING.exists()


def test_floor_export_failure_clears_ack_and_requests_max_before_pwm(tmp_path, monkeypatch):
    fan, daemon, ctrl, writes = controller_fixture(tmp_path, monkeypatch, 'fan_pid_export_preflight_test')
    ctrl.step()
    assert fan.APPLIED.exists()
    ctrl.fans[0]['last_written_pwm'] = -1
    writes.clear()
    monkeypatch.setattr(ctrl, 'export_floors', lambda: False)
    ctrl.step()
    assert writes and all(value == 255 for value in writes)
    assert not fan.APPLIED.exists()


def test_floor_export_failure_after_pwm_invalidates_ack_and_requests_max(tmp_path, monkeypatch):
    fan, daemon, ctrl, writes = controller_fixture(tmp_path, monkeypatch, 'fan_pid_export_after_write_test')
    ctrl.step()
    assert fan.APPLIED.exists()
    ctrl.fans[0]['last_written_pwm'] = -1
    calls = iter([True, False])
    monkeypatch.setattr(ctrl, 'export_floors', lambda: next(calls))
    writes.clear()
    result, _, _ = ctrl.step()
    assert writes[-2:] == [255, 255]
    assert result['fan1'] == 255
    assert not fan.APPLIED.exists()


def test_fan_verification_revoked_immediately_before_pwm_uses_max(tmp_path, monkeypatch):
    fan, daemon, ctrl, writes = controller_fixture(tmp_path, monkeypatch, 'fan_pid_prewrite_revoke_test')
    ctrl.step()
    assert fan.APPLIED.exists()
    ctrl.fans[0]['last_written_pwm'] = -1
    original = ctrl.precheck_fans
    def revoke_after_precheck(allow_seek):
        result = original(allow_seek)
        fan.mark_boot_failed()
        return result
    monkeypatch.setattr(ctrl, 'precheck_fans', revoke_after_precheck)
    writes.clear()
    ctrl.step()
    assert writes and all(value == 255 for value in writes)
    assert not fan.APPLIED.exists()


def test_configuration_failure_between_fan_writes_forces_max(tmp_path, monkeypatch):
    fan, daemon, template, _ = controller_fixture(tmp_path, monkeypatch, 'fan_pid_between_writes_test')
    ctrl = daemon.Controller(1, [
        {'name': 'fan1', 'duty_reg': 0x30, 'tach_file': 'fan1_input', 'present': True},
        {'name': 'fan2', 'duty_reg': 0x40, 'tach_file': 'fan2_input', 'present': True},
    ], '', dict(template.zone_paths), {0x30: 60, 0x40: 60})
    ctrl.next_rescan_t = float('inf')
    ctrl.step()
    assert fan.APPLIED.exists()
    for item in ctrl.fans:
        item['last_written_pwm'] = -1
    writes = []
    def write(fd, value, reg):
        writes.append((reg, value))
        if reg == 0x30 and value < 255:
            fan.mark_boot_failed()
        return True
    monkeypatch.setattr(daemon, 'i2c_write_pwm', write)
    result, _, _ = ctrl.step()
    first = next(index for index, (reg, value) in enumerate(writes) if reg == 0x30 and value < 255)
    assert all(value == 255 for _, value in writes[first + 1:])
    assert result == {'fan1': 255, 'fan2': 255}
    assert not fan.APPLIED.exists()


def test_export_contains_process_and_per_rpm_freshness(tmp_path, monkeypatch):
    fan, _ = modules(monkeypatch)
    daemon = load_script(monkeypatch, ROOT / 'board/scripts/fan-pid', 'fan_pid_export_freshness_test')
    daemon.TACH_MODES.update({0x30: 1, 0x40: 1})
    monkeypatch.setattr(daemon, 'FLOOR_STATE_DIR', str(tmp_path / 'fan-pid'))
    monkeypatch.setattr(daemon, 'FLOOR_STATE_FILE', str(tmp_path / 'fan-pid' / 'floors'))
    ctrl = daemon.Controller(1, [
        {'name': 'fan1', 'duty_reg': 0x30, 'tach_file': 'fan1_input', 'present': True},
    ], '', {}, {0x30: 96})
    ctrl.fans[0]['rpm'] = 1200
    ctrl.fans[0]['rpm_at'] = fan.clock_now()
    assert ctrl.export_floors()
    path = Path(daemon.FLOOR_STATE_FILE)
    fresh = fan.read_floor_export(path)
    assert 'export_version=1\n' in fresh
    assert 'rpm:0x30=1200\n' in fresh and 'rpm_sample:0x30=' in fresh
    ctrl.fans[0]['rpm_at'] = fan.clock_now() - fan.SENSOR_MAX_AGE - 1
    assert ctrl.export_floors()
    stale = fan.read_floor_export(path)
    assert 'rpm:0x30=' not in stale
    assert 'rpm_unavailable:0x30=1\n' in stale


def test_floor_export_failure_removes_previous_snapshot(tmp_path, monkeypatch):
    modules(monkeypatch)
    daemon = load_script(monkeypatch, ROOT / 'board/scripts/fan-pid', 'fan_pid_export_failure_test')
    daemon.TACH_MODES.update({0x30: 1, 0x40: 1})
    monkeypatch.setattr(daemon, 'FLOOR_STATE_DIR', str(tmp_path / 'fan-pid'))
    monkeypatch.setattr(daemon, 'FLOOR_STATE_FILE', str(tmp_path / 'fan-pid' / 'floors'))
    ctrl = daemon.Controller(1, [
        {'name': 'fan1', 'duty_reg': 0x30, 'tach_file': 'fan1_input', 'present': True},
    ], '', {}, {0x30: 96})
    assert Path(daemon.FLOOR_STATE_FILE).exists()
    monkeypatch.setattr(daemon.os, 'replace', lambda *_: (_ for _ in ()).throw(OSError('export failed')))
    assert ctrl.export_floors() is False
    assert not Path(daemon.FLOOR_STATE_FILE).exists()


def test_truncated_boot_status_is_retried_without_failure_latch(tmp_path, monkeypatch):
    fan, _ = modules(monkeypatch)
    fan.STATE = tmp_path / 'state'
    fan.APPLIED = tmp_path / 'applied'
    script = load_script(monkeypatch, ROOT / 'data/vyos-1x-files/src/conf_mode/system_hardware_fan.py',
                         'fan_conf_truncated_status_test')
    script.PENDING = tmp_path / 'pending'
    status = tmp_path / 'config-status'
    monkeypatch.setattr(script, 'boot_configuration_complete', lambda: status.is_file())
    monkeypatch.setattr(script, 'boot_configuration_success', lambda: int(status.read_text().strip()) == 0)
    class FakeConfig:
        def get_config_tree(self, effective=False):
            return {}

        def get_config_dict(self, path, effective=False, get_first_key=False):
            return {}

    script.Config = FakeConfig
    with status.open('w') as loader:
        script.reconcile()
        assert script.PENDING.exists()
        assert not fan.boot_failure_marker().exists()
        assert not fan.verification_path().exists()
        loader.write('0\n')
        loader.flush()
    script.reconcile()
    assert not fan.boot_failure_marker().exists()
    assert fan.read_snapshot() == fan.DEFAULTS
    assert fan.read_verification(fan.DEFAULTS)


@pytest.mark.skipif(not os.environ.get('VYOS_1X_SOURCE'), reason='Pinned VyOS source unavailable')
def test_pinned_boot_status_truncate_then_success_is_retryable(tmp_path):
    source = Path(os.environ['VYOS_1X_SOURCE'])
    code = (
        'import importlib.util, sys\n'
        'from pathlib import Path\n'
        'import vyos.defaults as defaults\n'
        'root = Path(sys.argv[3]); status = root / "config-status"; defaults.config_status = str(status)\n'
        'spec = importlib.util.spec_from_file_location("vyos.fan_control", sys.argv[1])\n'
        'fan = importlib.util.module_from_spec(spec); sys.modules[spec.name] = fan; spec.loader.exec_module(fan)\n'
        'spec = importlib.util.spec_from_file_location("fan_owner_truncate", sys.argv[2])\n'
        'owner = importlib.util.module_from_spec(spec); spec.loader.exec_module(owner)\n'
        'fan.STATE = root / "state"; fan.APPLIED = root / "applied"; owner.PENDING = root / "pending"\n'
        'owner.acquire_commit_lock_file = lambda: (object(), ""); owner.release_commit_lock_file = lambda lock: None\n'
        'owner.Warning = lambda message: (_ for _ in ()).throw(AssertionError("premature warning"))\n'
        'class Config:\n'
        '    def get_config_tree(self, effective=False): return {}\n'
        '    def get_config_dict(self, path, effective=False, get_first_key=False): return {}\n'
        'owner.Config = Config\n'
        'with status.open("w") as loader:\n'
        '    owner.apply({}); owner.reconcile()\n'
        '    assert owner.PENDING.exists() and not fan.boot_failure_marker().exists()\n'
        '    assert not fan.verification_path().exists()\n'
        '    loader.write("0\\n"); loader.flush()\n'
        'owner.reconcile(); assert not fan.boot_failure_marker().exists()\n'
        'assert fan.read_snapshot() == fan.DEFAULTS and fan.read_verification(fan.DEFAULTS)\n'
        'print("ok")\n')
    env = dict(os.environ, PYTHONPATH=str(source / 'python'))
    result = subprocess.run([sys.executable, '-c', code, str(MODULE),
                             str(ROOT / 'data/vyos-1x-files/src/conf_mode/system_hardware_fan.py'),
                             str(tmp_path)], env=env, text=True, capture_output=True, check=True)
    assert result.stdout.strip() == 'ok'


def test_unreadable_boot_status_retries_without_failure_latch(tmp_path, monkeypatch):
    fan, _ = modules(monkeypatch)
    fan.STATE = tmp_path / 'state'
    script = load_script(monkeypatch, ROOT / 'data/vyos-1x-files/src/conf_mode/system_hardware_fan.py',
                         'fan_conf_unreadable_status_retry_test')
    script.PENDING = tmp_path / 'pending'
    def unreadable():
        raise PermissionError('temporarily unreadable')
    monkeypatch.setattr(script, 'boot_configuration_success', unreadable)
    script.reconcile()
    assert script.PENDING.exists()
    assert not fan.boot_failure_marker().exists()
    assert not fan.verification_path().exists()


@pytest.mark.parametrize('content,should_latch', [
    ('1\n', True), ('-2\n', True), ('+3\n', True), ('', False), ('garbage', False),
])
def test_only_explicit_nonzero_boot_status_latches(tmp_path, monkeypatch, content, should_latch):
    fan, _ = modules(monkeypatch)
    fan.STATE = tmp_path / 'state'
    script = load_script(monkeypatch, ROOT / 'data/vyos-1x-files/src/conf_mode/system_hardware_fan.py',
                         'fan_conf_explicit_boot_status_test')
    script.PENDING = tmp_path / 'pending'
    status = tmp_path / 'config-status'
    status.write_text(content)
    defaults = types.ModuleType('vyos.defaults')
    defaults.config_status = str(status)
    monkeypatch.setitem(sys.modules, 'vyos.defaults', defaults)
    monkeypatch.setattr(script, 'boot_configuration_complete', lambda: True)
    monkeypatch.setattr(script, 'boot_configuration_success', lambda: int(status.read_text().strip()) == 0)
    script.reconcile()
    assert fan.boot_failure_marker().exists() is should_latch
    assert script.PENDING.exists() and not fan.verification_path().exists()


def test_missing_status_between_boot_checks_is_not_explicit_failure(tmp_path, monkeypatch):
    fan, _ = modules(monkeypatch)
    fan.STATE = tmp_path / 'state'
    script = load_script(monkeypatch, ROOT / 'data/vyos-1x-files/src/conf_mode/system_hardware_fan.py',
                         'fan_conf_missing_status_race_test')
    script.PENDING = tmp_path / 'pending'
    defaults = types.ModuleType('vyos.defaults')
    defaults.config_status = str(tmp_path / 'missing-status')
    monkeypatch.setitem(sys.modules, 'vyos.defaults', defaults)
    monkeypatch.setattr(script, 'boot_configuration_complete', lambda: True)
    monkeypatch.setattr(script, 'boot_configuration_success', lambda: False)
    script.reconcile()
    assert script.PENDING.exists()
    assert not fan.boot_failure_marker().exists()
    assert not fan.verification_path().exists()


def test_reconcile_success_delete_and_failed_commit(tmp_path, monkeypatch):
    fan, _ = modules(monkeypatch)
    fan.STATE = tmp_path / 'state'
    fan.APPLIED = tmp_path / 'applied'
    script = load_script(monkeypatch, ROOT / 'data/vyos-1x-files/src/conf_mode/system_hardware_fan.py', 'fan_conf_test')
    script.PENDING = tmp_path / 'pending'
    class FakeConfig:
        running = {'zone': {'memory': {'setpoint': '55'}}}

        def get_config_tree(self, effective=False):
            return {}

        def get_config_dict(self, path, effective=False, get_first_key=False):
            return self.running

    script.Config = FakeConfig
    script.apply({'zone': {'memory': {'setpoint': '55'}}})
    assert fan.read_snapshot() == fan.DEFAULTS
    script.reconcile()
    assert fan.read_snapshot()['memory']['setpoint'] == 55
    assert script.PENDING.exists()
    publish_test_ack(fan, fan.read_snapshot())
    script.reconcile()
    assert not script.PENDING.exists()
    script.apply({'zone': {'memory': {'setpoint': '60'}}})
    script.reconcile()
    assert fan.read_snapshot()['memory']['setpoint'] == 55
    script.apply({})
    FakeConfig.running = {}
    script.reconcile()
    assert fan.read_snapshot() == fan.DEFAULTS
    assert script.PENDING.exists()
    publish_test_ack(fan, fan.read_snapshot())
    script.reconcile()
    assert not script.PENDING.exists()


def test_steady_state_reconcile_never_takes_commit_lock(tmp_path, monkeypatch):
    fan, _ = modules(monkeypatch)
    fan.STATE = tmp_path / 'state'
    fan.APPLIED = tmp_path / 'applied'
    script = load_script(monkeypatch, ROOT / 'data/vyos-1x-files/src/conf_mode/system_hardware_fan.py',
                         'fan_conf_steady_test')
    script.PENDING = tmp_path / 'pending'

    class FakeConfig:
        def get_config_tree(self, effective=False):
            return {}

        def get_config_dict(self, path, effective=False, get_first_key=False):
            return {'zone': {'memory': {'setpoint': '55'}}}

    script.Config = FakeConfig
    script.apply({})
    script.reconcile()
    publish_test_ack(fan, fan.read_snapshot())
    script.reconcile()
    assert not script.PENDING.exists()
    # Steady state: an operator commit must never find the lock taken by fan reconcile.
    monkeypatch.setattr(script, 'acquire_commit_lock_file',
                        lambda: (_ for _ in ()).throw(AssertionError('steady state took the commit lock')))
    fan.invalidate_verification()
    fan.write_verification(fan.read_snapshot(), fan.load_snapshot_record()[2])
    before = json.loads(fan.verification_path().read_text())['updated']
    monkeypatch.setattr(fan, 'clock_now', lambda: before + 1.0)
    script.reconcile()
    assert json.loads(fan.verification_path().read_text())['updated'] == before + 1.0
    # A commit racing the lock-free read defers to the locked path, which yields to it.
    monkeypatch.setattr(script, 'commit_in_progress2', lambda: True)
    monkeypatch.setattr(script, 'acquire_commit_lock_file', lambda: (None, script.COMMIT_LOCK_BUSY))
    script.reconcile()
    assert fan.verification_path().exists() and not script.PENDING.exists()

    # Fast fan commit creating PENDING between Config() read and write_verification()
    # must defer to the locked path instead of extending the stale verification lease.
    locked_calls = []
    class RacingConfig(FakeConfig):
        def get_config_dict(self, path, effective=False, get_first_key=False):
            script.PENDING.write_text(str(fan.clock_now()))
            return super().get_config_dict(path, effective=effective, get_first_key=get_first_key)

    monkeypatch.setattr(script, 'commit_in_progress2', lambda: False)
    script.Config = RacingConfig
    monkeypatch.setattr(script, 'acquire_commit_lock_file',
                        lambda: (locked_calls.append(True), (None, script.COMMIT_LOCK_BUSY))[1])
    script.reconcile()
    assert locked_calls == [True]
    script.PENDING.unlink(missing_ok=True)

    # Second commit_in_progress2() raising OSError must defer to locked path (not bypass fail_closed).
    script.Config = FakeConfig
    calls = iter([False, OSError('lock probe failed')])
    def flaky_probe():
        val = next(calls)
        if isinstance(val, Exception):
            raise val
        return val

    monkeypatch.setattr(script, 'commit_in_progress2', flaky_probe)
    monkeypatch.setattr(script, 'acquire_commit_lock_file',
                        lambda: (_ for _ in ()).throw(OSError('lock broken')))
    with pytest.raises(OSError, match='lock broken'):
        script.reconcile()
    assert not fan.verification_path().exists() and script.PENDING.exists()


def test_reconcile_waits_for_boot_lock_and_retries_failure(tmp_path, monkeypatch):
    fan, _ = modules(monkeypatch)
    fan.STATE = tmp_path / 'state'
    script = load_script(monkeypatch, ROOT / 'data/vyos-1x-files/src/conf_mode/system_hardware_fan.py', 'fan_conf_retry_test')
    script.PENDING = tmp_path / 'pending'
    script.apply({})
    monkeypatch.setattr(script, 'boot_configuration_complete', lambda: False)
    script.reconcile()
    assert script.PENDING.exists()
    monkeypatch.setattr(script, 'boot_configuration_complete', lambda: True)
    monkeypatch.setattr(script, 'acquire_commit_lock_file', lambda: (None, script.COMMIT_LOCK_BUSY))
    script.reconcile()
    assert script.PENDING.exists()
    released = []
    monkeypatch.setattr(script, 'acquire_commit_lock_file', lambda: ('lock', ''))
    monkeypatch.setattr(script, 'release_commit_lock_file', released.append)
    class BrokenConfig:
        def get_config_tree(self, effective=False):
            return None
    script.Config = BrokenConfig
    with pytest.raises(Exception, match='running configuration is unavailable'):
        script.reconcile()
    assert released == ['lock']
    assert script.PENDING.exists()


def test_reconcile_write_failure_preserves_state_and_pending(tmp_path, monkeypatch):
    fan, _ = modules(monkeypatch)
    fan.STATE = tmp_path / 'state'
    fan.APPLIED = tmp_path / 'applied'
    fan.write_snapshot(fan.effective({'zone': {'pcb': {'setpoint': '50'}}}))
    script = load_script(monkeypatch, ROOT / 'data/vyos-1x-files/src/conf_mode/system_hardware_fan.py',
                         'fan_conf_write_failure_test')
    script.PENDING = tmp_path / 'pending'
    script.apply({})
    class FakeConfig:
        def get_config_tree(self, effective=False):
            return {}

        def get_config_dict(self, path, effective=False, get_first_key=False):
            return {}

    script.Config = FakeConfig
    def fail(_values):
        raise OSError('disk full')
    monkeypatch.setattr(script, 'write_snapshot', fail)
    with pytest.raises(OSError, match='disk full'):
        script.reconcile()
    assert script.PENDING.exists()
    assert fan.read_snapshot()['pcb']['setpoint'] == 50
    monkeypatch.setattr(script, 'write_snapshot', fan.write_snapshot)
    script.reconcile()
    assert fan.read_snapshot() == fan.DEFAULTS
    assert script.PENDING.exists()
    publish_test_ack(fan, fan.read_snapshot())
    script.reconcile()
    assert not script.PENDING.exists()


def controller_fixture(tmp_path, monkeypatch, name, ready=True):
    fan, _ = modules(monkeypatch)
    fan.STATE = tmp_path / 'state'
    fan.PENDING = tmp_path / 'pending'
    fan.APPLIED = tmp_path / 'fan-pid' / 'applied.json'
    fan.APPLIED.parent.mkdir()
    if ready:
        publish_test_snapshot(fan, fan.effective({}))
    daemon = load_script(monkeypatch, ROOT / 'board/scripts/fan-pid', name)
    mock_tach_modes(monkeypatch, daemon)
    monkeypatch.setattr(daemon.Controller, 'export_floors', lambda self: True)
    monkeypatch.setattr(daemon, 'fan_rpm', lambda *args: 1000)
    monkeypatch.setattr(daemon, 'read_temp', lambda path: 50.0)
    monkeypatch.setattr(daemon, 'read_sensor', lambda zone, path: daemon.read_temp(path))
    paths = {zone.name: f'/fake/{zone.name}' for zone in daemon.ZONES}
    monkeypatch.setattr(daemon, 'find_thermal_zones', lambda: dict(paths))
    writes = []
    monkeypatch.setattr(daemon, 'i2c_write_pwm', lambda fd, value, reg: writes.append(value) or True)
    ctrl = daemon.Controller(1, [{'name': 'fan1', 'duty_reg': 0x30,
                                  'tach_file': 'fan1_input'}], '', dict(paths), {0x30: 60})
    ctrl.next_rescan_t = float('inf')
    return fan, daemon, ctrl, writes


def test_persistent_sensor_failure_logs_once_and_holds_max(tmp_path, monkeypatch):
    _, daemon, ctrl, writes = controller_fixture(tmp_path, monkeypatch, 'fan_pid_sensor_log_test')
    logs = []
    monkeypatch.setattr(daemon.syslog, 'syslog', lambda level, msg: logs.append(msg))
    monkeypatch.setattr(daemon, 'find_thermal_zones', lambda: {})
    ctrl.zone_paths.pop(daemon.ZONES[-1].name)
    for _ in range(5):
        assert ctrl.step()[0]['fan1'] == 255
    assert sum('sensor readings unavailable' in m for m in logs) == 1


@pytest.mark.parametrize('fault', ['missing', 'expired'])
def test_startup_does_not_chirp_below_max_on_bad_configuration(tmp_path, monkeypatch, fault):
    fan, daemon, ctrl, writes = controller_fixture(tmp_path, monkeypatch, 'fan_pid_startup_test')
    if fault == 'missing':
        fan.write_snapshot(fan.effective({'zone': {'memory': {'setpoint': '55'}}}))
        fan.STATE.unlink()
    else:
        fan.PENDING.write_text(str(fan.clock_now() - 60))
    monkeypatch.setattr(daemon.time, 'sleep', lambda _: None)
    daemon.play_startup_whistle(1, ctrl.zone_paths, ctrl.fans)
    assert writes and all(value == 255 for value in writes)


def test_startup_whistle_is_suppressed_before_reconciliation(tmp_path, monkeypatch):
    _, daemon, ctrl, writes = controller_fixture(tmp_path, monkeypatch, 'fan_pid_early_whistle_test', ready=False)
    monkeypatch.setattr(daemon.time, 'sleep', lambda _: None)
    daemon.play_startup_whistle(1, ctrl.zone_paths, ctrl.fans)
    assert writes and all(value == 255 for value in writes)


def test_startup_whistle_runs_after_verified_configuration(tmp_path, monkeypatch):
    _, daemon, ctrl, writes = controller_fixture(tmp_path, monkeypatch, 'fan_pid_verified_whistle_test')
    monkeypatch.setattr(daemon.time, 'sleep', lambda _: None)
    daemon.play_startup_whistle(1, ctrl.zone_paths, ctrl.fans)
    assert writes[:4] == [130, 255, 255, 255]
    assert len(writes) == daemon.WHISTLE_PULSES * 4


def test_startup_whistle_detects_snapshot_loss_mid_sequence(tmp_path, monkeypatch):
    fan, daemon, ctrl, writes = controller_fixture(tmp_path, monkeypatch, 'fan_pid_whistle_loss_test')
    publish_test_snapshot(fan, fan.effective({'zone': {'memory': {'setpoint': '55'}}}))
    def remove_snapshot(_):
        fan.STATE.unlink(missing_ok=True)
        fan.snapshot_marker(fan.STATE).unlink(missing_ok=True)
    monkeypatch.setattr(daemon.time, 'sleep', remove_snapshot)
    daemon.play_startup_whistle(1, ctrl.zone_paths, ctrl.fans)
    assert writes[:2] == [130, 255]
    assert all(value == 255 for value in writes[2:])


def test_missing_established_snapshot_forces_max_including_restart(tmp_path, monkeypatch):
    fan, daemon, ctrl, writes = controller_fixture(tmp_path, monkeypatch, 'fan_pid_missing_test')
    assert ctrl.step()[0]['fan1'] < 255
    publish_test_snapshot(fan, fan.effective({'zone': {'memory': {'setpoint': '40', 'critical': '70'}}}))
    ctrl.step()
    fan.STATE.unlink()
    assert ctrl.step()[0]['fan1'] == 255
    assert writes[-1] == 255
    restarted = daemon.Controller(1, [{'name': 'fan1', 'duty_reg': 0x30,
                                        'tach_file': 'fan1_input'}], '',
                                  {'ddr-thermal': '/fake'}, {0x30: 60})
    restarted.next_rescan_t = float('inf')
    assert restarted.step()[0]['fan1'] == 255


def test_deeply_nested_snapshot_forces_max(tmp_path, monkeypatch):
    fan, _, ctrl, writes = controller_fixture(tmp_path, monkeypatch, 'fan_pid_recursion_test')
    assert ctrl.step()[0]['fan1'] < 255
    fan.STATE.write_text('[' * 2000 + '0' + ']' * 2000)
    assert ctrl.step()[0]['fan1'] == 255
    assert writes[-1] == 255


def test_valid_json_snapshot_corruption_forces_max(tmp_path, monkeypatch):
    fan, _, ctrl, writes = controller_fixture(tmp_path, monkeypatch, 'fan_pid_integrity_test')
    publish_test_snapshot(fan, fan.effective({'zone': {'memory': {'setpoint': '55'}}}))
    ctrl.step()
    data = json.loads(fan.STATE.read_text())
    data.get('zones', data)['memory']['setpoint'] = 56
    fan.STATE.write_text(json.dumps(data))
    assert ctrl.step()[0]['fan1'] == 255
    assert writes[-1] == 255
    assert not fan.APPLIED.exists()


def test_expired_pending_forces_max_and_stays_visible(tmp_path, monkeypatch):
    fan, _, ctrl, writes = controller_fixture(tmp_path, monkeypatch, 'fan_pid_pending_test')
    assert ctrl.step()[0]['fan1'] < 255
    fan.PENDING.write_text(str(fan.clock_now() - 60))
    assert ctrl.step()[0]['fan1'] == 255
    assert writes[-1] == 255


def test_reconcile_keeps_pending_until_daemon_acknowledges(tmp_path, monkeypatch):
    fan, _ = modules(monkeypatch)
    fan.STATE = tmp_path / 'state'
    fan.PENDING = tmp_path / 'pending'
    fan.APPLIED = tmp_path / 'applied'
    script = load_script(monkeypatch, ROOT / 'data/vyos-1x-files/src/conf_mode/system_hardware_fan.py',
                         'fan_conf_ack_test')
    script.PENDING = fan.PENDING
    class FakeConfig:
        def get_config_tree(self, effective=False):
            return {}

        def get_config_dict(self, path, effective=False, get_first_key=False):
            return {'zone': {'pcb': {'setpoint': '50'}}}

    script.Config = FakeConfig
    script.apply({})
    script.reconcile()
    assert fan.read_snapshot()['pcb']['setpoint'] == 50
    assert script.PENDING.exists()
    publish_test_ack(fan, fan.read_snapshot())
    script.reconcile()
    assert not script.PENDING.exists()


def test_early_boot_defaults_and_post_boot_snapshot_guard(tmp_path, monkeypatch):
    fan, daemon, ctrl, writes = controller_fixture(tmp_path, monkeypatch, 'fan_pid_boot_guard_test', ready=False)
    assert ctrl.step()[0]['fan1'] == 255
    assert not fan.APPLIED.exists()
    fan.mark_boot_ready()
    assert ctrl.step()[0]['fan1'] == 255
    fan.write_snapshot(fan.effective({}))
    fan.write_verification(fan.effective({}))
    assert ctrl.step()[0]['fan1'] < 255
    fan.STATE.unlink()
    fan.snapshot_marker(fan.STATE).unlink()
    restarted = daemon.Controller(1, [{'name': 'fan1', 'duty_reg': 0x30,
                                        'tach_file': 'fan1_input'}], '',
                                  {'ddr-thermal': '/fake'}, {0x30: 60})
    restarted.next_rescan_t = float('inf')
    assert restarted.step()[0]['fan1'] == 255
    assert writes[-1] == 255


def test_post_boot_default_snapshot_is_reconciled_without_cli_node(tmp_path, monkeypatch):
    fan, _ = modules(monkeypatch)
    fan.STATE = tmp_path / 'state'
    fan.APPLIED = tmp_path / 'applied'
    script = load_script(monkeypatch, ROOT / 'data/vyos-1x-files/src/conf_mode/system_hardware_fan.py',
                         'fan_conf_default_boot_test')
    script.PENDING = tmp_path / 'pending'
    class FakeConfig:
        def get_config_tree(self, effective=False):
            return {}

        def get_config_dict(self, path, effective=False, get_first_key=False):
            return {}

    script.Config = FakeConfig
    script.reconcile()
    assert fan.boot_marker().exists()
    assert fan.read_snapshot() == fan.DEFAULTS
    assert script.PENDING.exists()
    publish_test_ack(fan, fan.read_snapshot())
    script.reconcile()
    assert not script.PENDING.exists()


def test_missing_snapshot_and_marker_after_load_still_forces_max(tmp_path, monkeypatch):
    fan, _, ctrl, writes = controller_fixture(tmp_path, monkeypatch, 'fan_pid_lost_guard_test')
    publish_test_snapshot(fan, fan.effective({'zone': {'memory': {'setpoint': '40'}}}))
    ctrl.step()
    fan.STATE.unlink()
    fan.snapshot_marker(fan.STATE).unlink()
    assert ctrl.step()[0]['fan1'] == 255
    assert writes[-1] == 255
    assert not fan.APPLIED.exists()


def test_failed_first_snapshot_publication_leaves_guard(tmp_path, monkeypatch):
    fan = load_module()
    path = tmp_path / 'state'
    monkeypatch.setattr(fan.os, 'replace', lambda *_: (_ for _ in ()).throw(OSError('write failure')))
    with pytest.raises(OSError):
        fan.write_snapshot(fan.effective({}), path)
    assert fan.snapshot_marker(path).exists()
    with pytest.raises(ValueError, match='missing after initialization'):
        fan.read_snapshot(path)


def test_pending_deadline_is_not_reset_by_later_commit(tmp_path, monkeypatch):
    fan = load_module()
    path = tmp_path / 'pending'
    now = fan.clock_now()
    monkeypatch.setattr(fan, 'clock_now', lambda: now)
    fan.mark_pending(path)
    monkeypatch.setattr(fan, 'clock_now', lambda: now + 35)
    fan.mark_pending(path)
    assert fan.pending_age(path) == 35
    assert path.stat().st_mode & 0o777 == 0o644


def test_i2c_write_result_detects_short_and_failed_writes(monkeypatch):
    modules(monkeypatch)
    daemon = load_script(monkeypatch, ROOT / 'board/scripts/fan-pid', 'fan_pid_i2c_real_test')
    monkeypatch.setattr(daemon.os, 'write', lambda *_: 1)
    assert daemon.i2c_write_pwm(1, 255) is False
    monkeypatch.setattr(daemon.os, 'write', lambda *_: (_ for _ in ()).throw(OSError('NACK')))
    assert daemon.i2c_write_pwm(1, 255) is False
    monkeypatch.setattr(daemon.os, 'write', lambda *_: 2)
    assert daemon.i2c_write_pwm(1, 255) is True


def test_bad_pending_encoding_forces_max(tmp_path, monkeypatch):
    fan, _, ctrl, writes = controller_fixture(tmp_path, monkeypatch, 'fan_pid_pending_encoding_test')
    ctrl.step()
    fan.PENDING.write_bytes(b'\xff')
    assert ctrl.step()[0]['fan1'] == 255
    assert writes[-1] == 255


def test_i2c_failure_invalidates_acknowledgement_and_retries(tmp_path, monkeypatch):
    fan, daemon, ctrl, writes = controller_fixture(tmp_path, monkeypatch, 'fan_pid_write_failure_test')
    clock = [fan.clock_now()]
    monkeypatch.setattr(fan, 'clock_now', lambda: clock[0])
    monkeypatch.setattr(daemon, 'clock_now', lambda: clock[0])
    monkeypatch.setattr(daemon.time, 'monotonic', lambda: clock[0])
    ctrl.step()
    assert fan.read_applied()['mode'] == 'normal'
    ctrl.fans[0]['last_written_pwm'] = -1
    monkeypatch.setattr(daemon, 'i2c_write_pwm', lambda *args: False)
    ctrl.step()
    assert not fan.APPLIED.exists()
    assert ctrl.fans[0]['last_written_pwm'] == -1
    monkeypatch.setattr(daemon, 'i2c_write_pwm', lambda fd, value, reg: writes.append(value) or True)
    for _ in range(3):
        ctrl.step()
        assert not fan.APPLIED.exists()
        clock[0] += 2
    ctrl.step()
    assert fan.read_applied()['mode'] == 'failsafe'
    assert writes[-1] == 255
    clock[0] += 2
    ctrl.step()
    assert fan.read_applied()['mode'] == 'normal'


def test_acknowledgement_failure_requests_max(tmp_path, monkeypatch):
    fan, daemon, ctrl, writes = controller_fixture(tmp_path, monkeypatch, 'fan_pid_ack_failure_test')
    ctrl.step()
    assert fan.APPLIED.exists()
    monkeypatch.setattr(daemon, 'write_applied',
                        lambda *_, **__: (_ for _ in ()).throw(OSError('read-only state directory')))
    ctrl.step()
    assert not fan.APPLIED.exists()
    assert writes[-1] == 255
    assert ctrl.fans[0]['last_written_pwm'] == -1
    writes.clear()
    ctrl.step()
    assert writes and all(value == 255 for value in writes)
    assert not fan.APPLIED.exists()
    monkeypatch.setattr(daemon, 'write_applied', fan.write_applied)
    writes.clear()
    ctrl.step()
    assert writes and all(value == 255 for value in writes)
    assert fan.read_applied()['mode'] == 'failsafe'
    ctrl.step()
    assert fan.read_applied()['mode'] == 'normal'


def test_timer_repairs_missing_established_snapshot(tmp_path, monkeypatch):
    fan, _ = modules(monkeypatch)
    fan.STATE = tmp_path / 'state'
    fan.APPLIED = tmp_path / 'applied'
    script = load_script(monkeypatch, ROOT / 'data/vyos-1x-files/src/conf_mode/system_hardware_fan.py',
                         'fan_conf_repair_test')
    script.PENDING = tmp_path / 'pending'
    values = fan.effective({'zone': {'memory': {'setpoint': '55'}}})
    fan.write_snapshot(values)
    fan.STATE.unlink()
    class FakeConfig:
        def get_config_tree(self, effective=False):
            return {}

        def get_config_dict(self, path, effective=False, get_first_key=False):
            return {'zone': {'memory': {'setpoint': '55'}}}

    script.Config = FakeConfig
    script.reconcile()
    assert fan.read_snapshot() == values
    assert script.PENDING.exists()
    publish_test_ack(fan, values)
    script.reconcile()
    assert not script.PENDING.exists()


def test_apply_warns_after_boot_and_fails_if_marker_cannot_be_created(tmp_path, monkeypatch):
    modules(monkeypatch)
    script = load_script(monkeypatch, ROOT / 'data/vyos-1x-files/src/conf_mode/system_hardware_fan.py',
                         'fan_conf_warning_test')
    script.PENDING = tmp_path / 'pending'
    warnings = []
    monkeypatch.setattr(script, 'Warning', warnings.append)
    script.apply({})
    assert 'pending asynchronous' in warnings[0]
    assert 'show hardware fan' in warnings[0]
    warnings.clear()
    monkeypatch.setattr(script, 'boot_configuration_complete', lambda: False)
    script.apply({})
    assert warnings == []
    script.PENDING = tmp_path / 'missing' / 'pending'
    with pytest.raises(OSError):
        script.apply({})


def test_acknowledgement_rejects_stopped_controller(tmp_path):
    fan = load_module()
    fan.STATE = tmp_path / 'state'
    path = tmp_path / 'ack'
    publish_test_ack(fan, fan.effective({}), path=path)
    child = subprocess.Popen([sys.executable, '-c',
                              'import os, signal; os.kill(os.getpid(), signal.SIGSTOP)'])
    try:
        _, status = os.waitpid(child.pid, os.WUNTRACED)
        assert os.WIFSTOPPED(status)
        fields = Path(f'/proc/{child.pid}/stat').read_text().rsplit(')', 1)[-1].split()
        data = json.loads(path.read_text())
        data['pid'] = child.pid
        data['start'] = int(fields[19])
        path.write_text(json.dumps(data))
        with pytest.raises(ValueError, match='not running'):
            fan.read_applied(path)
    finally:
        child.send_signal(signal.SIGKILL)
        child.wait(timeout=5)


def test_acknowledgement_requires_live_process_and_recent_tick(tmp_path):
    fan = load_module()
    fan.STATE = tmp_path / 'state'
    path = tmp_path / 'ack'
    publish_test_ack(fan, fan.effective({}), path=path)
    assert fan.read_applied(path)['zones'] == fan.DEFAULTS
    data = json.loads(path.read_text())
    data['updated'] = fan.clock_now() - fan.APPLIED_MAX_AGE - 1
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='stale'):
        fan.read_applied(path)
    data['updated'] = 10 ** 1000
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='acknowledgement time'):
        fan.read_applied(path)
    data['updated'] = fan.clock_now()
    data['pid'] = 999999999
    path.write_text(json.dumps(data))
    with pytest.raises(OSError):
        fan.read_applied(path)
    path.write_text('[' * 2000 + '0' + ']' * 2000)
    with pytest.raises(ValueError, match='Invalid fan state'):
        fan.read_applied(path)
    path.write_text('{"mode":"normal","mode":"failsafe"}')
    with pytest.raises(ValueError, match='Duplicate'):
        fan.read_applied(path)
    path.write_text(' ' * (fan.MAX_JSON_SIZE + 1))
    with pytest.raises(ValueError, match='too large'):
        fan.read_applied(path)


@pytest.mark.skipif(not os.environ.get('VYOS_1X_SOURCE'), reason='Pinned VyOS source unavailable')
def test_pinned_vyos_builds_fan_command_templates(tmp_path):
    source = Path(os.environ['VYOS_1X_SOURCE'])
    output = tmp_path / 'templates'
    output.mkdir()
    subprocess.run([sys.executable, str(source / 'scripts/build-command-templates'),
                    str(ROOT / 'data/vyos-1x-files/interface-definitions/system_hardware_fan.xml.in'),
                    str(source / 'schema/interface_definition.rng'), str(output)],
                   check=True, capture_output=True, text=True)
    node = (output / 'system/hardware/fan/node.def').read_text()
    assert '${vyshim} ${vyos_conf_scripts_dir}/system_hardware_fan.py' in node
    assert (output / 'system/hardware/fan/zone/node.tag/setpoint/node.def').exists()
    assert (output / 'system/hardware/fan/zone/node.tag/critical/node.def').exists()


@pytest.mark.skipif(not os.environ.get('VYOS_1X_SOURCE'), reason='Pinned VyOS source unavailable')
def test_pinned_boot_status_requires_success_and_latches_failure(tmp_path):
    source = Path(os.environ['VYOS_1X_SOURCE'])
    code = (
        'import importlib.util, sys\n'
        'from pathlib import Path\n'
        'import vyos.defaults as defaults\n'
        'root = Path(sys.argv[3]); status = root / "config-status"\n'
        'defaults.config_status = str(status)\n'
        'spec = importlib.util.spec_from_file_location("vyos.fan_control", sys.argv[1])\n'
        'fan = importlib.util.module_from_spec(spec); sys.modules[spec.name] = fan; spec.loader.exec_module(fan)\n'
        'spec = importlib.util.spec_from_file_location("fan_owner_boot", sys.argv[2])\n'
        'owner = importlib.util.module_from_spec(spec); spec.loader.exec_module(owner)\n'
        'fan.STATE = root / "failed-state"; fan.APPLIED = root / "failed-applied"; owner.PENDING = root / "pending"\n'
        'owner.acquire_commit_lock_file = lambda: (_ for _ in ()).throw(AssertionError("unexpected lock"))\n'
        'status.write_text("1"); owner.reconcile()\n'
        'assert fan.boot_failure_marker().exists() and not fan.verification_path().exists()\n'
        'status.write_text("0"); owner.reconcile()\n'
        'assert not fan.STATE.exists() and not fan.verification_path().exists()\n'
        'next_boot = root / "next-boot"; next_boot.mkdir()\n'
        'fan.STATE = next_boot / "state"; fan.APPLIED = next_boot / "applied"; owner.PENDING = next_boot / "pending"\n'
        'class Config:\n'
        '    def get_config_tree(self, effective=False): return {}\n'
        '    def get_config_dict(self, path, effective=False, get_first_key=False): return {}\n'
        'owner.Config = Config; owner.acquire_commit_lock_file = lambda: (object(), "")\n'
        'owner.release_commit_lock_file = lambda lock: None; owner.reconcile()\n'
        'assert fan.read_snapshot() == fan.DEFAULTS and fan.read_verification(fan.DEFAULTS)\n'
        'print("ok")\n')
    env = dict(os.environ, PYTHONPATH=str(source / 'python'))
    result = subprocess.run([sys.executable, '-c', code, str(MODULE),
                             str(ROOT / 'data/vyos-1x-files/src/conf_mode/system_hardware_fan.py'),
                             str(tmp_path)], env=env, text=True, capture_output=True, check=True)
    assert result.stdout.strip() == 'ok'


@pytest.mark.skipif(not os.environ.get('VYOS_1X_SOURCE'), reason='Pinned VyOS source unavailable')
def test_pinned_vyos_running_tree_reconciles_after_candidate(tmp_path):
    source = Path(os.environ['VYOS_1X_SOURCE'])
    code = (
        'import importlib.util, json, sys\n'
        'from pathlib import Path\n'
        'import vyos.config as config\n'
        'from vyos.configsource import ConfigSource\n'
        'config.multi_to_list = lambda path, data: data\n'
        'class Tree:\n'
        '    def __init__(self, value): self.value = value\n'
        '    def to_json(self): return json.dumps(self.value)\n'
        'source = ConfigSource()\n'
        'source._running_config = Tree({"system":{"hardware":{"fan":{"zone":{"memory":{"setpoint":"55"}}}}}})\n'
        'source._session_config = Tree({"system":{"hardware":{"fan":{"zone":{"memory":{"setpoint":"60"}}}}}})\n'
        'conf = config.Config(config_source=source)\n'
        'spec = importlib.util.spec_from_file_location("vyos.fan_control", sys.argv[1])\n'
        'fan = importlib.util.module_from_spec(spec); sys.modules[spec.name] = fan; spec.loader.exec_module(fan)\n'
        'spec = importlib.util.spec_from_file_location("fan_owner", sys.argv[2])\n'
        'owner = importlib.util.module_from_spec(spec); spec.loader.exec_module(owner)\n'
        'fan.STATE = Path(sys.argv[3]) / "state"; fan.APPLIED = Path(sys.argv[3]) / "applied"\n'
        'owner.PENDING = Path(sys.argv[3]) / "pending"; owner.Config = lambda: conf\n'
        'owner.boot_configuration_complete = lambda: True; owner.boot_configuration_success = lambda: True; owner.Warning = lambda message: None\n'
        'owner.acquire_commit_lock_file = lambda: (object(), ""); owner.release_commit_lock_file = lambda lock: None\n'
        'candidate = owner.get_config(conf); owner.verify(candidate); owner.apply(candidate); owner.reconcile()\n'
        'values = fan.read_snapshot(); assert owner.PENDING.exists()\n'
        'generation = fan.load_snapshot_record()[2]\n'
        'fan.write_applied(values, sensors={name:fan.clock_now() for name in fan.DEFAULTS}, generation=generation, chassis={"rpm":1000,"sampled":fan.clock_now()}); owner.reconcile()\n'
        'print(json.dumps([candidate, values["memory"], owner.PENDING.exists()]))\n')
    env = dict(os.environ, PYTHONPATH=str(source / 'python'))
    result = subprocess.run([sys.executable, '-c', code, str(MODULE),
                             str(ROOT / 'data/vyos-1x-files/src/conf_mode/system_hardware_fan.py'),
                             str(tmp_path)], env=env, text=True, capture_output=True, check=True)
    assert json.loads(result.stdout) == [
        {'zone': {'memory': {'setpoint': '60'}}}, {'setpoint': 55, 'critical': 80}, False]


def hold_lock(path, pipe):
    with open(path, 'w') as lock:
        fcntl.lockf(lock, fcntl.LOCK_EX)
        pipe.send(True)
        pipe.recv()


def probe_lock(path, pipe):
    with open(path, 'w') as lock:
        try:
            fcntl.lockf(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            pipe.send(False)
        else:
            pipe.send(True)


def test_lost_pending_marker_does_not_hide_running_config_change(tmp_path, monkeypatch):
    fan, _ = modules(monkeypatch)
    fan.STATE = tmp_path / 'state'
    fan.APPLIED = tmp_path / 'applied'
    script = load_script(monkeypatch, ROOT / 'data/vyos-1x-files/src/conf_mode/system_hardware_fan.py',
                         'fan_conf_lost_pending_test')
    script.PENDING = tmp_path / 'pending'
    fan.mark_boot_ready()
    old = fan.effective({'zone': {'memory': {'setpoint': '60'}}})
    fan.write_snapshot(old)
    fan.write_verification(old)
    class FakeConfig:
        def get_config_tree(self, effective=False):
            return {}

        def get_config_dict(self, path, effective=False, get_first_key=False):
            return {'zone': {'memory': {'setpoint': '55'}}}

    script.Config = FakeConfig
    assert not script.PENDING.exists()
    script.reconcile()
    assert fan.read_snapshot()['memory']['setpoint'] == 55
    assert script.PENDING.exists()


def test_unverified_boot_defaults_hold_max_until_reconciled(tmp_path, monkeypatch):
    fan, _, ctrl, writes = controller_fixture(tmp_path, monkeypatch, 'fan_pid_boot_max_test', ready=False)
    for _ in range(2):
        assert ctrl.step()[0]['fan1'] == 255
    assert writes and all(value == 255 for value in writes)
    assert not fan.APPLIED.exists()
    publish_test_snapshot(fan, fan.effective({}))
    assert ctrl.step()[0]['fan1'] < 255
    assert fan.read_applied()['mode'] == 'normal'


def test_missing_chassis_fan_does_not_acknowledge_with_auxiliary(tmp_path, monkeypatch):
    fan, daemon, ctrl, writes = controller_fixture(tmp_path, monkeypatch, 'fan_pid_missing_chassis_test')
    ctrl.step()
    assert fan.APPLIED.exists()
    missing = daemon.Controller(1, [
        {'name': 'fan1', 'duty_reg': 0x30, 'tach_file': 'fan1_input', 'present': False},
        {'name': 'fan2', 'duty_reg': 0x40, 'tach_file': 'fan2_input', 'present': True},
    ], '', dict(ctrl.zone_paths), {0x40: 60})
    missing.next_rescan_t = float('inf')
    writes.clear()
    pwms, _, _ = missing.step()
    assert pwms['fan2'] == 255
    assert writes and all(value == 255 for value in writes)
    assert not fan.APPLIED.exists()


@pytest.mark.parametrize('fans', [
    [],
    [{'name': 'fan2', 'duty_reg': 0x40, 'tach_file': 'fan2_input', 'present': True}],
    [{'name': 'fan1', 'duty_reg': 0x30, 'tach_file': 'fan1_input', 'present': False}],
])
def test_missing_chassis_variants_cannot_acknowledge(tmp_path, monkeypatch, fans):
    fan, daemon, ctrl, writes = controller_fixture(tmp_path, monkeypatch, 'fan_pid_missing_chassis_variants_test')
    ctrl.step()
    missing = daemon.Controller(1, fans, '', dict(ctrl.zone_paths), {0x30: 60, 0x40: 60})
    missing.next_rescan_t = float('inf')
    writes.clear()
    missing.step()
    assert writes and all(value == 255 for value in writes)
    assert not fan.APPLIED.exists()


def test_optional_auxiliary_fan_can_be_absent(tmp_path, monkeypatch):
    fan, daemon, ctrl, _ = controller_fixture(tmp_path, monkeypatch, 'fan_pid_optional_aux_test')
    single = daemon.Controller(1, [
        {'name': 'fan1', 'duty_reg': 0x30, 'tach_file': 'fan1_input', 'present': True},
        {'name': 'fan2', 'duty_reg': 0x40, 'tach_file': 'fan2_input', 'present': False},
    ], '', dict(ctrl.zone_paths), {0x30: 60})
    single.next_rescan_t = float('inf')
    assert single.step()[0]['fan1'] < 255
    assert fan.read_applied()['mode'] == 'normal'


@pytest.mark.parametrize('rpm', [0, None])
def test_lost_chassis_tach_invalidates_ack_and_requests_max(tmp_path, monkeypatch, rpm):
    fan, daemon, ctrl, writes = controller_fixture(tmp_path, monkeypatch, 'fan_pid_lost_chassis_test')
    ctrl.step()
    assert fan.APPLIED.exists()
    monkeypatch.setattr(daemon, 'fan_rpm', lambda *_: rpm)
    writes.clear()
    assert ctrl.step()[0]['fan1'] == 255
    assert writes[-1] == 255
    assert not fan.APPLIED.exists()


def test_zero_tach_during_settle_learns_floor_before_recovery(tmp_path, monkeypatch):
    fan, daemon, ctrl, writes = controller_fixture(tmp_path, monkeypatch, 'fan_pid_settle_stall_test')
    clock = [fan.clock_now()]
    monkeypatch.setattr(fan, 'clock_now', lambda: clock[0])
    monkeypatch.setattr(daemon, 'clock_now', lambda: clock[0])
    monkeypatch.setattr(daemon.time, 'monotonic', lambda: clock[0])
    ctrl.step()
    chassis = ctrl.fans[0]
    chassis['floor'] = 60
    chassis['floor_lo'] = daemon.FLOOR_MIN
    chassis['seek_settle'] = 2
    chassis['seek_blackout'] = 0
    chassis['last_written_pwm'] = 60
    monkeypatch.setattr(daemon, 'fan_rpm', lambda *_: 0)
    assert ctrl.step()[0]['fan1'] == 255
    assert chassis['floor_lo'] >= 60 + daemon.SEEK_STALL_MARGIN
    assert chassis['floor'] >= 60 + daemon.SEEK_STALL_BUMP
    assert chassis['recovering'] and chassis['seek_blackout'] == daemon.SEEK_BLACKOUT
    assert not fan.APPLIED.exists()
    monkeypatch.setattr(daemon, 'fan_rpm', lambda *_: 1200)
    for _ in range(5):
        clock[0] += 2
        fan.write_verification(fan.read_snapshot())
        assert ctrl.step()[0]['fan1'] == 255
        assert chassis['recovering']
    clock[0] += 2
    fan.write_verification(fan.read_snapshot())
    assert ctrl.step()[0]['fan1'] == 255
    assert not chassis['recovering']
    assert chassis['floor'] >= 60 + daemon.SEEK_STALL_BUMP
    clock[0] += 2
    fan.write_verification(fan.read_snapshot())
    ctrl.step()
    assert chassis['floor'] >= chassis['floor_lo'] > 60
    assert writes[-1] >= chassis['floor_lo']


@pytest.mark.parametrize('allow_seek', [True, False])
def test_settling_stall_learns_from_last_applied_duty(tmp_path, monkeypatch, allow_seek):
    fan, daemon, ctrl, _ = controller_fixture(tmp_path, monkeypatch, 'fan_pid_previous_duty_stall_test')
    chassis = ctrl.fans[0]
    chassis['floor'] = 60
    chassis['floor_lo'] = daemon.FLOOR_MIN
    chassis['seek_settle'] = 2
    chassis['seek_blackout'] = 1
    chassis['last_written_pwm'] = 72
    ctrl._refine_floor(chassis, allow_seek, (fan.clock_now(), 0))
    assert chassis['floor_lo'] >= 72 + daemon.SEEK_STALL_MARGIN
    assert chassis['floor'] >= 72 + daemon.SEEK_STALL_BUMP
    assert chassis['seek_settle'] == 0
    assert chassis['seek_blackout'] == daemon.SEEK_BLACKOUT


def test_stall_recovery_holds_max_through_hysteresis(tmp_path, monkeypatch):
    fan, daemon, ctrl, _ = controller_fixture(tmp_path, monkeypatch, 'fan_pid_stall_hysteresis_test')
    clock = [fan.clock_now()]
    monkeypatch.setattr(fan, 'clock_now', lambda: clock[0])
    monkeypatch.setattr(daemon, 'clock_now', lambda: clock[0])
    monkeypatch.setattr(daemon.time, 'monotonic', lambda: clock[0])
    ctrl.step()
    chassis = ctrl.fans[0]
    chassis['floor'] = 60
    chassis['last_written_pwm'] = 60
    chassis['seek_settle'] = 2
    monkeypatch.setattr(daemon, 'fan_rpm', lambda *_: 0)
    assert ctrl.step()[0]['fan1'] == 255
    monkeypatch.setattr(daemon, 'fan_rpm', lambda *_: 1200)
    for _ in range(5):
        clock[0] += 2
        fan.write_verification(fan.read_snapshot())
        assert ctrl.step()[0]['fan1'] == 255
        assert chassis['recovering']
        assert not fan.APPLIED.exists()
    clock[0] += 2
    fan.write_verification(fan.read_snapshot())
    assert ctrl.step()[0]['fan1'] == 255
    assert not chassis['recovering']
    assert chassis['floor_lo'] > 60


def test_settling_stall_above_calibration_ceiling_latches_maximum(tmp_path, monkeypatch):
    fan, daemon, ctrl, _ = controller_fixture(tmp_path, monkeypatch, 'fan_pid_above_floor_ceiling_test')
    chassis = ctrl.fans[0]
    chassis['floor'] = 60
    chassis['last_written_pwm'] = daemon.FLOOR_MAX + 20
    chassis['seek_settle'] = 1
    ctrl._refine_floor(chassis, False, (fan.clock_now(), 0))
    assert chassis['floor'] == daemon.PWM_MAX
    assert chassis['floor_lo'] == daemon.PWM_MAX


def test_stall_at_calibration_ceiling_latches_maximum(tmp_path, monkeypatch):
    fan, daemon, ctrl, _ = controller_fixture(tmp_path, monkeypatch, 'fan_pid_floor_ceiling_stall_test')
    chassis = ctrl.fans[0]
    chassis['floor'] = daemon.FLOOR_MAX
    chassis['last_written_pwm'] = daemon.FLOOR_MAX
    chassis['seek_settle'] = 1
    ctrl._refine_floor(chassis, True, (fan.clock_now(), 0))
    assert chassis['floor'] == daemon.PWM_MAX
    assert chassis['floor_lo'] == daemon.PWM_MAX


@pytest.mark.parametrize('rpm,duty', [(None, 60), (0, 255), (0, -1), (0, 0)])
def test_unreliable_or_noncalibration_tach_does_not_learn_floor(tmp_path, monkeypatch, rpm, duty):
    fan, daemon, ctrl, _ = controller_fixture(tmp_path, monkeypatch, 'fan_pid_unqualified_stall_test')
    chassis = ctrl.fans[0]
    chassis['floor'] = 60
    chassis['floor_lo'] = daemon.FLOOR_MIN
    chassis['seek_settle'] = 2
    chassis['last_written_pwm'] = duty
    ctrl._refine_floor(chassis, True, (fan.clock_now(), rpm))
    assert chassis['floor'] == 60
    assert chassis['floor_lo'] == daemon.FLOOR_MIN


def test_delayed_zero_tach_does_not_poison_learned_floor(tmp_path, monkeypatch):
    fan, daemon, ctrl, _ = controller_fixture(tmp_path, monkeypatch, 'fan_pid_delayed_stall_test')
    clock = [fan.clock_now()]
    monkeypatch.setattr(daemon, 'clock_now', lambda: clock[0])
    chassis = ctrl.fans[0]
    chassis['floor'] = 60
    chassis['floor_lo'] = daemon.FLOOR_MIN
    chassis['seek_settle'] = 2
    chassis['last_written_pwm'] = 60
    def delayed_rpm(*args):
        clock[0] += daemon.SENSOR_MAX_AGE + 1
        return 0
    monkeypatch.setattr(daemon, 'fan_rpm', delayed_rpm)
    ctrl._refine_floor(chassis, True)
    assert chassis['floor'] == 60
    assert chassis['floor_lo'] == daemon.FLOOR_MIN
    assert chassis['sampled_rpm'] is None


def test_chassis_tach_failure_preserves_floor_seek_and_recovers(tmp_path, monkeypatch):
    fan, daemon, ctrl, writes = controller_fixture(tmp_path, monkeypatch, 'fan_pid_chassis_recovery_test')
    clock = [fan.clock_now()]
    monkeypatch.setattr(fan, 'clock_now', lambda: clock[0])
    monkeypatch.setattr(daemon, 'clock_now', lambda: clock[0])
    monkeypatch.setattr(daemon.time, 'monotonic', lambda: clock[0])
    ctrl.step()
    chassis = ctrl.fans[0]
    chassis['seek_settle'] = 0
    chassis['seek_blackout'] = 0
    chassis['last_written_pwm'] = chassis['floor']
    previous_floor = chassis['floor']
    monkeypatch.setattr(daemon, 'fan_rpm', lambda *_: 0)
    ctrl.step()
    assert chassis['floor'] > previous_floor
    assert chassis['kick']
    assert writes[-1] == 255
    assert not fan.APPLIED.exists()
    monkeypatch.setattr(daemon, 'fan_rpm', lambda *_: 1000)
    for _ in range(5):
        clock[0] += 2
        fan.write_verification(fan.read_snapshot())
        ctrl.step()
        assert not fan.APPLIED.exists()
    clock[0] += 2
    fan.write_verification(fan.read_snapshot())
    ctrl.step()
    assert fan.read_applied()['mode'] == 'failsafe'
    clock[0] += 2
    fan.write_verification(fan.read_snapshot())
    ctrl.step()
    assert fan.read_applied()['mode'] == 'normal'


@pytest.mark.parametrize('bad', [0, -1, True, '1000', None])
def test_ack_rejects_invalid_chassis_rpm(tmp_path, bad):
    fan = load_module()
    fan.STATE = tmp_path / 'state'
    path = tmp_path / 'ack'
    publish_test_ack(fan, fan.effective({}), path=path)
    data = json.loads(path.read_text())
    data['chassis']['rpm'] = bad
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='chassis|Chassis'):
        fan.read_applied(path)


def test_ack_rejects_stale_chassis_tach_sample(tmp_path):
    fan = load_module()
    fan.STATE = tmp_path / 'state'
    path = tmp_path / 'ack'
    publish_test_ack(fan, fan.effective({}), path=path)
    data = json.loads(path.read_text())
    data['chassis']['sampled'] = fan.clock_now() - fan.SENSOR_MAX_AGE - 1
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='Chassis fan sample is stale'):
        fan.read_applied(path)


def test_ack_without_chassis_evidence_is_rejected(tmp_path):
    fan = load_module()
    fan.STATE = tmp_path / 'state'
    path = tmp_path / 'ack'
    publish_test_ack(fan, fan.effective({}), path=path)
    data = json.loads(path.read_text())
    data.pop('chassis', None)
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='acknowledgement|chassis'):
        fan.read_applied(path)


def test_early_boot_does_not_acknowledge_unreconciled_defaults(tmp_path, monkeypatch):
    fan, _, ctrl, _ = controller_fixture(tmp_path, monkeypatch, 'fan_pid_boot_ack_test', ready=False)
    ctrl.step()
    assert not fan.APPLIED.exists()


@pytest.mark.parametrize('name', [
    'ddr-thermal', 'serdes-thermal', 'fman-thermal', 'cluster-thermal',
    'sec-thermal', 'PCB', 'CPU', 'SFP+',
])
def test_missing_sensor_forces_max_and_invalidates_ack(tmp_path, monkeypatch, name):
    fan, _, ctrl, writes = controller_fixture(tmp_path, monkeypatch, 'fan_pid_missing_sensor_test')
    ctrl.zone_paths.pop(name)
    assert ctrl.step()[0]['fan1'] == 255
    assert writes[-1] == 255
    assert not fan.APPLIED.exists()


def test_known_missing_sensor_requests_max_before_other_reads(tmp_path, monkeypatch):
    fan, daemon, ctrl, writes = controller_fixture(tmp_path, monkeypatch, 'fan_pid_early_sensor_failure_test')
    ctrl.step()
    ctrl.zone_paths.pop('ddr-thermal')
    monkeypatch.setattr(daemon, 'read_sensor',
                        lambda *_: (_ for _ in ()).throw(RuntimeError('must not read another sensor')))
    assert ctrl.step()[0]['fan1'] == 255
    assert writes[-1] == 255
    assert not fan.APPLIED.exists()


@pytest.mark.parametrize('name', [
    'ddr-thermal', 'serdes-thermal', 'fman-thermal', 'cluster-thermal',
    'sec-thermal', 'PCB', 'CPU', 'SFP+',
])
def test_each_unreadable_sensor_forces_max(tmp_path, monkeypatch, name):
    fan, daemon, ctrl, writes = controller_fixture(tmp_path, monkeypatch, 'fan_pid_unreadable_sensor_test')
    ctrl.step()
    assert fan.APPLIED.exists()
    monkeypatch.setattr(daemon, 'read_sensor',
                        lambda zone, path: None if zone.name == name else 50.0)
    assert ctrl.step()[0]['fan1'] == 255
    assert writes[-1] == 255
    assert not fan.APPLIED.exists()


@pytest.mark.parametrize('value', [float('nan'), float('inf'), -274.0])
def test_invalid_sensor_numeric_values_force_max(tmp_path, monkeypatch, value):
    fan, daemon, ctrl, writes = controller_fixture(tmp_path, monkeypatch, 'fan_pid_invalid_sensor_test')
    ctrl.step()
    monkeypatch.setattr(daemon, 'read_sensor',
                        lambda zone, path: value if zone.name == 'ddr-thermal' else 50.0)
    assert ctrl.step()[0]['fan1'] == 255
    assert writes[-1] == 255
    assert not fan.APPLIED.exists()


def test_failed_sensor_read_is_not_acknowledged_as_fresh(tmp_path, monkeypatch):
    fan, daemon, ctrl, writes = controller_fixture(tmp_path, monkeypatch, 'fan_pid_sensor_read_test')
    ctrl.zone_paths = {zone.name: f'/fake/{zone.name}' for zone in daemon.ZONES}
    monkeypatch.setattr(daemon, 'read_sensor', lambda zone, path: daemon.read_temp(path), raising=False)
    ctrl.step()
    assert fan.APPLIED.exists()
    monkeypatch.setattr(daemon, 'read_temp', lambda path: None if path.endswith('ddr-thermal') else 50.0)
    assert ctrl.step()[0]['fan1'] == 255
    assert writes[-1] == 255
    assert not fan.APPLIED.exists()


def test_acknowledgement_without_sensor_evidence_is_rejected(tmp_path):
    fan = load_module()
    fan.STATE = tmp_path / 'state'
    path = tmp_path / 'ack'
    publish_test_ack(fan, fan.effective({}), path=path)
    data = json.loads(path.read_text())
    data.pop('sensors', None)
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='acknowledgement|sensor'):
        fan.read_applied(path)


def test_early_boot_output_does_not_claim_unverified_ack(tmp_path):
    fan = load_module()
    fan.STATE = tmp_path / 'state'
    fan.PENDING = tmp_path / 'pending'
    fan.APPLIED = tmp_path / 'applied'
    publish_test_ack(fan, fan.effective({}))
    status = fan.configuration_status()
    assert status['state'] == 'unknown'
    assert status['zones'] is None


def test_lost_pending_is_recreated_when_ack_is_missing(tmp_path, monkeypatch):
    fan, _ = modules(monkeypatch)
    fan.STATE = tmp_path / 'state'
    fan.APPLIED = tmp_path / 'applied'
    script = load_script(monkeypatch, ROOT / 'data/vyos-1x-files/src/conf_mode/system_hardware_fan.py',
                         'fan_conf_lost_ack_test')
    script.PENDING = tmp_path / 'pending'
    values = fan.effective({'zone': {'memory': {'setpoint': '55'}}})
    publish_test_snapshot(fan, values)
    class FakeConfig:
        def get_config_tree(self, effective=False):
            return {}

        def get_config_dict(self, path, effective=False, get_first_key=False):
            return {'zone': {'memory': {'setpoint': '55'}}}

    script.Config = FakeConfig
    script.reconcile()
    assert script.PENDING.exists()
    publish_test_ack(fan, values)
    script.reconcile()
    assert not script.PENDING.exists()


def test_running_config_unavailable_revokes_verification(tmp_path, monkeypatch):
    fan, _, ctrl, writes = controller_fixture(tmp_path, monkeypatch, 'fan_pid_lost_running_test')
    ctrl.step()
    assert fan.APPLIED.exists()
    script = load_script(monkeypatch, ROOT / 'data/vyos-1x-files/src/conf_mode/system_hardware_fan.py',
                         'fan_conf_lost_running_test')
    script.PENDING = fan.PENDING
    class BrokenConfig:
        def get_config_tree(self, effective=False):
            return None

    script.Config = BrokenConfig
    with pytest.raises(Exception, match='running configuration is unavailable'):
        script.reconcile()
    assert fan.PENDING.exists()
    assert not fan.verification_path().exists()
    assert ctrl.step()[0]['fan1'] == 255
    assert writes[-1] == 255
    assert not fan.APPLIED.exists()


def test_expired_running_config_verification_forces_max(tmp_path, monkeypatch):
    fan, _, ctrl, writes = controller_fixture(tmp_path, monkeypatch, 'fan_pid_stale_verify_test')
    ctrl.step()
    assert fan.APPLIED.exists()
    verification = fan.verification_path()
    data = json.loads(verification.read_text())
    data['updated'] = fan.clock_now() - fan.VERIFICATION_MAX_AGE - 1
    verification.write_text(json.dumps(data))
    assert ctrl.step()[0]['fan1'] == 255
    assert writes[-1] == 255
    assert not fan.APPLIED.exists()


def test_lost_pending_for_new_generation_waits_for_new_ack(tmp_path, monkeypatch):
    fan, _, ctrl, _ = controller_fixture(tmp_path, monkeypatch, 'fan_pid_generation_test')
    ctrl.step()
    previous = fan.read_applied()['generation']
    values = fan.read_snapshot()
    publish_test_snapshot(fan, values)
    assert fan.load_snapshot_record()[2] != previous
    script = load_script(monkeypatch, ROOT / 'data/vyos-1x-files/src/conf_mode/system_hardware_fan.py',
                         'fan_conf_generation_test')
    script.PENDING = fan.PENDING
    class FakeConfig:
        def get_config_tree(self, effective=False):
            return {}

        def get_config_dict(self, path, effective=False, get_first_key=False):
            return {}

    script.Config = FakeConfig
    script.reconcile()
    assert script.PENDING.exists()
    assert fan.configuration_status()['state'] == 'pending'
    ctrl.step()
    script.reconcile()
    assert not script.PENDING.exists()
    assert fan.read_applied()['generation'] != previous


@pytest.mark.parametrize('failure', ['raises', 'critical_open', 'unexpected_message', 'non_string'])
def test_commit_lock_outage_revokes_verification_and_fails_safe(tmp_path, monkeypatch, failure):
    fan, _, ctrl, writes = controller_fixture(tmp_path, monkeypatch, 'fan_pid_lock_outage_test')
    ctrl.step()
    assert fan.APPLIED.exists()
    script = load_script(monkeypatch, ROOT / 'data/vyos-1x-files/src/conf_mode/system_hardware_fan.py',
                         'fan_conf_lock_outage_test')
    script.PENDING = fan.PENDING
    if failure == 'raises':
        monkeypatch.setattr(script, 'acquire_commit_lock_file',
                            lambda: (_ for _ in ()).throw(OSError('lock failed')))
        with pytest.raises(OSError, match='lock failed'):
            script.reconcile()
    elif failure == 'critical_open':
        monkeypatch.setattr(script, 'acquire_commit_lock_file',
                            lambda: (None, 'Critical error opening commit lock file: EACCES'))
        with pytest.raises(Exception, match='Critical error opening commit lock file'):
            script.reconcile()
    elif failure == 'unexpected_message':
        monkeypatch.setattr(script, 'acquire_commit_lock_file', lambda: (None, 'unexpected lock error'))
        with pytest.raises(Exception, match='unexpected lock error'):
            script.reconcile()
    else:
        monkeypatch.setattr(script, 'acquire_commit_lock_file', lambda: (None, None))
        with pytest.raises(Exception, match='None'):
            script.reconcile()
    assert fan.PENDING.exists()
    assert not fan.verification_path().exists()
    assert ctrl.step()[0]['fan1'] == 255
    assert writes[-1] == 255
    assert not fan.APPLIED.exists()


def test_normal_commit_lock_contention_preserves_verification_until_lease_expires(tmp_path, monkeypatch):
    fan, daemon, ctrl, writes = controller_fixture(tmp_path, monkeypatch, 'fan_pid_lock_contention_test')
    clock = [fan.clock_now()]
    monkeypatch.setattr(fan, 'clock_now', lambda: clock[0])
    monkeypatch.setattr(daemon, 'clock_now', lambda: clock[0])
    fan.write_verification(fan.read_snapshot())
    assert ctrl.step()[0]['fan1'] < 255
    assert fan.APPLIED.exists()
    script = load_script(monkeypatch, ROOT / 'data/vyos-1x-files/src/conf_mode/system_hardware_fan.py',
                         'fan_conf_lock_contention_test')
    script.PENDING = fan.PENDING
    monkeypatch.setattr(script, 'acquire_commit_lock_file',
                        lambda: (None, script.COMMIT_LOCK_BUSY))
    script.reconcile()
    assert fan.verification_path().exists()
    assert not fan.PENDING.exists()
    assert ctrl.step()[0]['fan1'] < 255
    assert fan.APPLIED.exists()
    # A wedged fan commit with PENDING active also fails closed at PENDING_TIMEOUT.
    fan.mark_pending()
    clock[0] += fan.PENDING_TIMEOUT
    fan.write_verification(fan.read_snapshot())
    assert ctrl.step()[0]['fan1'] == 255
    assert fan.read_applied()['mode'] == 'failsafe'
    assert fan.configuration_status()['state'] == 'error'
    fan.PENDING.unlink(missing_ok=True)
    # Without PENDING, an unrelated wedged commit still fails closed when VERIFICATION_MAX_AGE expires.
    fan.write_verification(fan.read_snapshot())
    assert ctrl.step()[0]['fan1'] < 255
    clock[0] += fan.VERIFICATION_MAX_AGE + 1
    script.reconcile()
    assert ctrl.step()[0]['fan1'] == 255
    assert writes[-1] == 255
    assert not fan.APPLIED.exists()


def test_exiting_forced_max_arms_seek_blackout_and_preserves_learned_floor(tmp_path, monkeypatch):
    fan, daemon, ctrl, _ = controller_fixture(tmp_path, monkeypatch, 'fan_pid_force_max_blackout_test')
    clock = [fan.clock_now()]
    monkeypatch.setattr(fan, 'clock_now', lambda: clock[0])
    monkeypatch.setattr(daemon, 'clock_now', lambda: clock[0])
    monkeypatch.setattr(daemon.time, 'monotonic', lambda: clock[0])
    ctrl = daemon.Controller(1, [
        {'name': 'fan1', 'duty_reg': 0x30, 'tach_file': 'fan1_input', 'present': True},
        {'name': 'fan2', 'duty_reg': 0x40, 'tach_file': 'fan2_input', 'present': True},
    ], '', dict(ctrl.zone_paths), {0x30: 33, 0x40: 74})
    assert all(f['seek_blackout'] == daemon.SEEK_BLACKOUT for f in ctrl.fans)
    for f in ctrl.fans:
        f['seek_blackout'] = 0
    fan.write_verification(fan.read_snapshot())
    monkeypatch.setattr(daemon, 'fan_rpm', lambda f, _: 590)
    pwms = ctrl.step()[0]
    assert pwms == {'fan1': 33, 'fan2': 74}
    # Enter forced-MAX failsafe (missing verification) and spin fans up.
    fan.invalidate_verification()
    monkeypatch.setattr(daemon, 'fan_rpm', lambda f, _: 8661 if f['duty_reg'] == 0x30 else 1942)
    clock[0] += 2
    assert ctrl.step()[0] == {'fan1': 255, 'fan2': 255}
    # Restore verification while fans are still coasting down at high RPM.
    fan.write_verification(fan.read_snapshot())
    for _ in range(daemon.SEEK_BLACKOUT):
        clock[0] += 2
        pwms = ctrl.step()[0]
        assert pwms == {'fan1': 33, 'fan2': 74}
        assert ctrl.fans[0]['floor'] == 33
        assert ctrl.fans[1]['floor'] == 74
    # On tick SEEK_BLACKOUT + 1, normal floor convergence resumes.
    clock[0] += 2
    ctrl.step()
    assert ctrl.fans[0]['floor'] < 33
    assert ctrl.fans[1]['floor'] < 74
    ctrl.fans[0]['floor'] = 33
    ctrl.fans[1]['floor'] = 74
    # Recovery exit (probe_uncertain_fans) also arms seek_blackout on the recovered fan.
    ctrl.fans[0]['recovering'] = True
    ctrl.fans[0]['recovery_count'] = 0
    ctrl.fans[0]['max_since'] = clock[0] - daemon.PRESENCE_SPINUP_S - 1
    ctrl.fans[0]['max_boot'] = clock[0] - daemon.PRESENCE_SPINUP_S - 1
    for _ in range(daemon.TACH_RECOVERY_SAMPLES):
        clock[0] += 2
        fan.write_verification(fan.read_snapshot())
        ctrl.step()
    assert not ctrl.fans[0]['recovering']
    assert ctrl.fans[0]['seek_blackout'] == daemon.SEEK_BLACKOUT
    clock[0] += 2
    fan.write_verification(fan.read_snapshot())
    ctrl.step()
    assert ctrl.fans[0]['seek_blackout'] == daemon.SEEK_BLACKOUT - 1
    assert ctrl.fans[0]['floor'] == 33
    # High thermal demand followed by cool-down must also arm seek_blackout.
    monkeypatch.setattr(daemon, 'read_sensor', lambda zone, path: 78.0 if zone.name == 'ddr-thermal' else 50.0)
    monkeypatch.setattr(daemon, 'fan_rpm', lambda f, _: 4000)
    clock[0] += 2
    hot_pwms = ctrl.step()[0]
    assert hot_pwms['fan1'] > 33 + daemon.SEEK_MEASURE_TOL
    assert ctrl.fans[0]['seek_blackout'] == daemon.SEEK_BLACKOUT
    monkeypatch.setattr(daemon, 'read_sensor', lambda zone, path: 50.0)
    clock[0] += 2
    pwms = ctrl.step()[0]
    assert pwms['fan1'] == 33
    assert ctrl.fans[0]['floor'] == 33


def test_reconcile_daemon_loops_deduplicates_errors_and_stops(tmp_path, monkeypatch, capsys):
    fan, _ = modules(monkeypatch)
    fan.STATE = tmp_path / 'state'
    script = load_script(monkeypatch, ROOT / 'data/vyos-1x-files/src/conf_mode/system_hardware_fan.py',
                         'fan_conf_daemon_test')
    script.PENDING = tmp_path / 'pending'
    calls = []
    waits = []
    failed_closed = []
    stop_event = None
    monkeypatch.setattr(script, 'fail_closed', lambda: failed_closed.append(True))

    def fake_reconcile():
        calls.append(len(calls))
        if len(calls) in (1, 2):
            raise RuntimeError('vyconfd restarting')
        if len(calls) == 3:
            fan.mark_boot_ready()
            script.PENDING.write_text('0')
        elif len(calls) == 4:
            script.PENDING.unlink(missing_ok=True)
        elif len(calls) == 5:
            fan.mark_boot_failed()
            script.PENDING.write_text('0')
        elif len(calls) >= 6:
            stop_event.set()

    monkeypatch.setattr(script, 'reconcile', fake_reconcile)

    class FakeEvent:
        def __init__(self):
            self.flag = False

        def is_set(self):
            return self.flag

        def set(self):
            self.flag = True

        def wait(self, timeout=None):
            waits.append(timeout)
            return self.flag

    stop_event = FakeEvent()
    script.reconcile_daemon(interval=5.0, fast_interval=2.0, stop_event=stop_event)
    assert len(calls) == 6
    assert len(failed_closed) == 2
    # Before boot-ready (1, 2) and while pending (3) -> fast_interval; steady state (4) and boot_failed (5) -> interval
    assert waits[:5] == [2.0, 2.0, 2.0, 5.0, 5.0]
    stderr_lines = [line for line in capsys.readouterr().err.splitlines() if line.strip()]
    assert stderr_lines == ['vyconfd restarting']


def test_reconcile_daemon_handles_real_sigterm_without_deadlock(tmp_path, monkeypatch):
    import threading
    fan, _ = modules(monkeypatch)
    fan.STATE = tmp_path / 'state'
    fan.mark_boot_ready()
    script = load_script(monkeypatch, ROOT / 'data/vyos-1x-files/src/conf_mode/system_hardware_fan.py',
                         'fan_conf_sigterm_test')
    script.PENDING = tmp_path / 'pending'
    calls = []
    timer = None

    def fake_reconcile():
        nonlocal timer
        calls.append(True)
        if len(calls) == 1:
            timer = threading.Timer(0.1, lambda: os.kill(os.getpid(), signal.SIGTERM))
            timer.start()

    monkeypatch.setattr(script, 'reconcile', fake_reconcile)
    started = time.monotonic()
    try:
        script.reconcile_daemon(interval=30.0, fast_interval=30.0)
    finally:
        if timer is not None:
            timer.cancel()
            timer.join(2.0)
    assert calls == [True]
    assert time.monotonic() - started < 2.0


def test_repaired_snapshot_requires_new_daemon_acknowledgement(tmp_path, monkeypatch):
    fan, _, ctrl, _ = controller_fixture(tmp_path, monkeypatch, 'fan_pid_repaired_snapshot_test')
    ctrl.step()
    assert fan.APPLIED.exists()
    values = fan.read_snapshot()
    script = load_script(monkeypatch, ROOT / 'data/vyos-1x-files/src/conf_mode/system_hardware_fan.py',
                         'fan_conf_repaired_snapshot_test')
    script.PENDING = fan.PENDING
    class FakeConfig:
        def get_config_tree(self, effective=False):
            return {}

        def get_config_dict(self, path, effective=False, get_first_key=False):
            return {}

    script.Config = FakeConfig
    fan.STATE.write_text('{')
    script.reconcile()
    assert fan.read_snapshot() == values
    assert script.PENDING.exists()
    ctrl.step()
    script.reconcile()
    assert not script.PENDING.exists()


def test_missing_sensor_recovery_requires_a_fresh_sample(tmp_path, monkeypatch):
    fan, _, ctrl, writes = controller_fixture(tmp_path, monkeypatch, 'fan_pid_sensor_recovery_test')
    ctrl.step()
    assert fan.APPLIED.exists()
    ctrl.zone_paths.pop('CPU')
    assert ctrl.step()[0]['fan1'] == 255
    assert not fan.APPLIED.exists()
    ctrl.next_rescan_t = 0
    ctrl.step()
    assert fan.read_applied()['mode'] == 'normal'
    assert 'CPU' in ctrl.zone_paths
    assert writes


def test_stale_sensor_scan_forces_max(tmp_path, monkeypatch):
    fan, daemon, ctrl, writes = controller_fixture(tmp_path, monkeypatch, 'fan_pid_slow_sensor_test')
    ctrl.step()
    assert fan.APPLIED.exists()
    clock = [fan.clock_now()]
    monkeypatch.setattr(daemon, 'clock_now', lambda: clock[0])
    monkeypatch.setattr(daemon, 'read_verification', lambda *args: None)
    def slow_sensor(zone, path):
        clock[0] += 2.0
        return 50.0
    monkeypatch.setattr(daemon, 'read_sensor', slow_sensor)
    assert ctrl.step()[0]['fan1'] == 255
    assert writes[-1] == 255
    assert not fan.APPLIED.exists()


def test_individually_delayed_sensor_sample_is_not_fresh(tmp_path, monkeypatch):
    fan, daemon, ctrl, writes = controller_fixture(tmp_path, monkeypatch, 'fan_pid_slow_first_sensor_test')
    ctrl.step()
    assert fan.APPLIED.exists()
    clock = [fan.clock_now()]
    monkeypatch.setattr(daemon, 'clock_now', lambda: clock[0])
    monkeypatch.setattr(daemon, 'read_verification', lambda *args: None)
    def slow_sensor(zone, path):
        if zone.name == 'ddr-thermal':
            clock[0] += fan.SENSOR_MAX_AGE + 1
        return 50.0
    monkeypatch.setattr(daemon, 'read_sensor', slow_sensor)
    assert ctrl.step()[0]['fan1'] == 255
    assert writes[-1] == 255
    assert not fan.APPLIED.exists()


def test_freshness_clock_counts_suspend(monkeypatch):
    fan = load_module()
    monkeypatch.setattr(fan.time, 'clock_gettime', lambda clock: 200.0 if clock == time.CLOCK_BOOTTIME else 100.0)
    assert fan.clock_now() == 200.0


def test_sensor_ack_rejects_stale_or_missing_samples(tmp_path):
    fan = load_module()
    fan.STATE = tmp_path / 'state'
    path = tmp_path / 'applied'
    publish_test_ack(fan, fan.effective({}), path=path)
    data = json.loads(path.read_text())
    data['sensors']['memory'] = fan.clock_now() - fan.SENSOR_MAX_AGE - 1
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='stale'):
        fan.read_applied(path)
    del data['sensors']['memory']
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='sensor'):
        fan.read_applied(path)


def test_remote_temperature_fault_and_missing_fault_file(tmp_path, monkeypatch):
    modules(monkeypatch)
    daemon = load_script(monkeypatch, ROOT / 'board/scripts/fan-pid', 'fan_pid_remote_sensor_test')
    zone = next(zone for zone in daemon.ZONES if zone.name == 'CPU')
    temp = tmp_path / 'temp2_input'
    temp.write_text('54000')
    fault = tmp_path / 'temp2_fault'
    assert daemon.read_sensor(zone, str(temp)) is None
    fault.write_text('0')
    assert daemon.read_sensor(zone, str(temp)) == 54.0
    fault.write_text('1')
    assert daemon.read_sensor(zone, str(temp)) is None
    fault.write_text('invalid')
    assert daemon.read_sensor(zone, str(temp)) is None


def test_tach_partial_range_write_keeps_verified_channel_scales(monkeypatch):
    modules(monkeypatch)
    daemon = load_script(monkeypatch, ROOT / 'board/scripts/fan-pid', 'fan_pid_partial_range_test')
    registers = {0x32: 0x20, 0x42: 0x20}
    monkeypatch.setattr(daemon, 'i2c_read_byte', lambda fd, reg: registers[reg])
    def write(fd, direction, reg, size, value=0):
        if reg == 0x42:
            raise OSError('channel two write failed')
        registers[reg] = value
    monkeypatch.setattr(daemon, '_smbus_xfer', write)
    assert daemon.set_tach_range_500(1) == {0x30: 1, 0x40: 2}
    assert registers == {0x32: 0, 0x42: 0x20}


def test_range_write_error_uses_readback_and_preserves_other_bits(monkeypatch):
    modules(monkeypatch)
    daemon = load_script(monkeypatch, ROOT / 'board/scripts/fan-pid', 'fan_pid_range_write_ack_test')
    registers = {0x32: 0xa1, 0x42: 0x25}
    monkeypatch.setattr(daemon, 'i2c_read_byte', lambda fd, reg: registers[reg])
    def write(fd, direction, reg, size, value=0):
        registers[reg] = value
        if reg == 0x42:
            raise OSError('write status unknown')
    monkeypatch.setattr(daemon, '_smbus_xfer', write)
    assert daemon.set_tach_range_500(1) == {0x30: 1, 0x40: 1}
    assert registers == {0x32: 0x81, 0x42: 0x05}


def test_tach_write_ack_requires_range_readback(monkeypatch):
    modules(monkeypatch)
    daemon = load_script(monkeypatch, ROOT / 'board/scripts/fan-pid', 'fan_pid_range_readback_test')
    monkeypatch.setattr(daemon, 'i2c_read_byte', lambda fd, reg: 0x20)
    monkeypatch.setattr(daemon, '_smbus_xfer', lambda *args: None)
    assert daemon.set_tach_range_500(1) == {0x30: 2, 0x40: 2}


def test_tach_readback_error_leaves_channel_unknown(monkeypatch):
    modules(monkeypatch)
    daemon = load_script(monkeypatch, ROOT / 'board/scripts/fan-pid', 'fan_pid_failed_readback_test')
    reads = {0x32: 0, 0x42: 0}
    def read(fd, reg):
        reads[reg] += 1
        return None if reg == 0x42 and reads[reg] > 1 else 0x20
    monkeypatch.setattr(daemon, 'i2c_read_byte', read)
    monkeypatch.setattr(daemon, '_smbus_xfer', lambda *args: None)
    assert daemon.set_tach_range_500(1) == {0x30: 2, 0x40: None}


def test_raw_tach_uses_each_confirmed_range(monkeypatch):
    modules(monkeypatch)
    daemon = load_script(monkeypatch, ROOT / 'board/scripts/fan-pid', 'fan_pid_raw_multiplier_test')
    modes = {0x30: 1, 0x40: 2}
    monkeypatch.setattr(daemon, 'current_tach_mode', lambda fd, reg: modes[reg])
    count = 4000
    raw = count << 3
    monkeypatch.setattr(daemon, 'i2c_read_word', lambda *_: ((raw & 0xff) << 8) | (raw >> 8))
    fan1 = {'duty_reg': 0x30, 'tach_reg': 0x3e}
    fan2 = {'duty_reg': 0x40, 'tach_reg': 0x4e}
    assert daemon.fan_rpm(fan1, 1) == 3932160 // count
    assert daemon.fan_rpm(fan2, 1) == 2 * 3932160 // count


def test_fan_tach_mode_change_during_read_is_rejected(monkeypatch):
    modules(monkeypatch)
    daemon = load_script(monkeypatch, ROOT / 'board/scripts/fan-pid', 'fan_pid_range_race_test')
    modes = iter([1, 2])
    monkeypatch.setattr(daemon, 'current_tach_mode', lambda *_: next(modes))
    monkeypatch.setattr(daemon, 'read_fan_rpm_raw', lambda *_: 1500)
    assert daemon.fan_rpm({'duty_reg': 0x30, 'tach_reg': 0x3e}, 1) is None
    assert daemon.TACH_MODES[0x30] == 2


def test_tach_export_reports_each_confirmed_range(tmp_path, monkeypatch):
    modules(monkeypatch)
    daemon = load_script(monkeypatch, ROOT / 'board/scripts/fan-pid', 'fan_pid_range_export_test')
    daemon.TACH_MODES.update({0x30: 1, 0x40: 2})
    monkeypatch.setattr(daemon, 'FLOOR_STATE_DIR', str(tmp_path / 'fan-pid'))
    monkeypatch.setattr(daemon, 'FLOOR_STATE_FILE', str(tmp_path / 'fan-pid' / 'floors'))
    daemon.Controller(1, [
        {'name': 'fan1', 'duty_reg': 0x30, 'tach_file': 'fan1_input', 'present': True},
        {'name': 'fan2', 'duty_reg': 0x40, 'tach_file': 'fan2_input', 'present': False},
    ], '', {}, {0x30: 96, 0x40: 96})
    export = Path(daemon.FLOOR_STATE_FILE).read_text()
    assert 'tach_source=raw-m1\n' in export
    assert 'tach_range:0x30=m1\n' in export
    assert 'tach_range:0x40=m2\n' in export
    assert 'tach_recovery:0x40=1\n' in export


def test_tach_unknown_range_does_not_use_sysfs_fallback(monkeypatch):
    modules(monkeypatch)
    daemon = load_script(monkeypatch, ROOT / 'board/scripts/fan-pid', 'fan_pid_unknown_range_test')
    monkeypatch.setattr(daemon, 'i2c_read_byte', lambda *_: None)
    monkeypatch.setattr(daemon, 'read_fan_rpm', lambda *_: 9999)
    fan = {'name': 'fan1', 'duty_reg': 0x30, 'tach_reg': 0x3e, 'tach_path': '/fake'}
    assert daemon.fan_rpm(fan, 1) is None


def test_m2_fallback_repeatedly_holds_max_and_does_not_seek(tmp_path, monkeypatch):
    fan, daemon, ctrl, writes = controller_fixture(tmp_path, monkeypatch, 'fan_pid_m2_fallback_test')
    clock = [fan.clock_now()]
    monkeypatch.setattr(fan, 'clock_now', lambda: clock[0])
    monkeypatch.setattr(daemon, 'clock_now', lambda: clock[0])
    monkeypatch.setattr(daemon.time, 'monotonic', lambda: clock[0])
    retries = []
    monkeypatch.setattr(daemon, 'current_tach_mode', lambda fd, reg: 2 if reg == 0x30 else 1, raising=False)
    def configure(fd, reg):
        retries.append(reg)
        return 2 if reg == 0x30 else 1
    monkeypatch.setattr(daemon, 'configure_tach_channel', configure, raising=False)
    start_floor = ctrl.fans[0]['floor']
    for _ in range(4):
        assert ctrl.step()[0]['fan1'] == 255
        assert not fan.APPLIED.exists()
        clock[0] += 6
    assert retries.count(0x30) >= 2
    assert ctrl.fans[0]['floor'] == start_floor
    assert writes and all(value == 255 for value in writes)


def test_unconfirmed_m2_aux_does_not_misscale_or_block_m1_chassis(tmp_path, monkeypatch):
    fan, daemon, template, _ = controller_fixture(tmp_path, monkeypatch, 'fan_pid_partial_mode_runtime_test')
    clock = [fan.clock_now()]
    monkeypatch.setattr(fan, 'clock_now', lambda: clock[0])
    monkeypatch.setattr(daemon, 'clock_now', lambda: clock[0])
    monkeypatch.setattr(daemon.time, 'monotonic', lambda: clock[0])
    modes = {0x30: 1, 0x40: 2}
    monkeypatch.setattr(daemon, 'current_tach_mode', lambda fd, reg: modes[reg])
    monkeypatch.setattr(daemon, 'configure_tach_channel', lambda fd, reg: modes[reg])
    monkeypatch.setattr(daemon, 'fan_rpm', lambda item, fd: 1200 if item['name'] == 'fan1' else 0)
    ctrl = daemon.Controller(1, [
        {'name': 'fan1', 'duty_reg': 0x30, 'tach_file': 'fan1_input', 'present': True},
        {'name': 'fan2', 'duty_reg': 0x40, 'tach_file': 'fan2_input', 'present': False},
    ], '', dict(template.zone_paths), {0x30: 60, 0x40: 96})
    ctrl.next_rescan_t = float('inf')
    writes = []
    monkeypatch.setattr(daemon, 'i2c_write_pwm', lambda fd, value, reg: writes.append((reg, value)) or True)
    assert ctrl.step()[0]['fan1'] < 255
    clock[0] += 4
    assert ctrl.step()[0]['fan1'] < 255
    assert fan.read_applied()['mode'] == 'normal'
    assert all(value == 255 for reg, value in writes if reg == 0x40)
    assert ctrl.fans[1]['tach_mode'] == 2 and ctrl.fans[1]['recovering']


def test_confirmed_m2_aux_forces_max_without_ack(tmp_path, monkeypatch):
    fan, daemon, template, _ = controller_fixture(tmp_path, monkeypatch, 'fan_pid_known_m2_aux_test')
    modes = {0x30: 1, 0x40: 2}
    monkeypatch.setattr(daemon, 'current_tach_mode', lambda fd, reg: modes[reg])
    monkeypatch.setattr(daemon, 'configure_tach_channel', lambda fd, reg: modes[reg])
    ctrl = daemon.Controller(1, [
        {'name': 'fan1', 'duty_reg': 0x30, 'tach_file': 'fan1_input', 'present': True},
        {'name': 'fan2', 'duty_reg': 0x40, 'tach_file': 'fan2_input', 'present': True},
    ], '', dict(template.zone_paths), {0x30: 60, 0x40: 60})
    ctrl.next_rescan_t = float('inf')
    assert ctrl.step()[0] == {'fan1': 255, 'fan2': 255}
    assert ctrl.fans[1]['recovering']
    assert not fan.APPLIED.exists()


def test_m2_to_m1_transition_revalidates_spinup(tmp_path, monkeypatch):
    fan, daemon, ctrl, _ = controller_fixture(tmp_path, monkeypatch, 'fan_pid_range_recovery_test')
    clock = [fan.clock_now()]
    monkeypatch.setattr(fan, 'clock_now', lambda: clock[0])
    monkeypatch.setattr(daemon, 'clock_now', lambda: clock[0])
    monkeypatch.setattr(daemon.time, 'monotonic', lambda: clock[0])
    modes = {0x30: 2, 0x40: 1}
    monkeypatch.setattr(daemon, 'current_tach_mode', lambda fd, reg: modes[reg])
    monkeypatch.setattr(daemon, 'configure_tach_channel', lambda fd, reg: modes[reg])
    assert ctrl.step()[0]['fan1'] == 255
    assert not fan.APPLIED.exists()
    modes[0x30] = 1
    ctrl.step()
    assert ctrl.fans[0]['recovering']
    clock[0] += 4
    ctrl.step()
    assert ctrl.fans[0]['recovering']
    assert not fan.APPLIED.exists()
    clock[0] += 2
    assert ctrl.step()[0]['fan1'] == 255
    assert not ctrl.fans[0]['recovering']
    clock[0] += 2
    ctrl.step()
    assert fan.read_applied()['mode'] == 'normal'


def test_missing_chassis_reprobes_after_spinup_and_two_samples(tmp_path, monkeypatch):
    fan, daemon, template, writes = controller_fixture(tmp_path, monkeypatch, 'fan_pid_reprobe_test')
    clock = [fan.clock_now()]
    monkeypatch.setattr(fan, 'clock_now', lambda: clock[0])
    monkeypatch.setattr(daemon, 'clock_now', lambda: clock[0])
    monkeypatch.setattr(daemon.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(daemon, 'current_tach_mode', lambda *_: 1, raising=False)
    monkeypatch.setattr(daemon, 'configure_tach_channel', lambda *_: 1, raising=False)
    rates = {'fan1': 0, 'fan2': 0}
    monkeypatch.setattr(daemon, 'fan_rpm', lambda item, fd: rates[item['name']])
    detected = daemon.detect_present_fans(1, '')
    ctrl = daemon.Controller(1, detected, '', dict(template.zone_paths), {0x30: 96, 0x40: 96})
    ctrl.next_rescan_t = float('inf')
    writes.clear()
    ctrl.step()
    assert not ctrl.fans[0]['present']
    rates['fan1'] = 1200
    for seconds in (2, 2):
        clock[0] += seconds
        ctrl.step()
        assert not ctrl.fans[0]['present']
        assert not fan.APPLIED.exists()
    clock[0] += 2
    ctrl.step()
    assert ctrl.fans[0]['present']
    assert writes and all(value == 255 for value in writes)
    clock[0] += 2
    ctrl.step()
    assert fan.read_applied()['mode'] == 'normal'


def test_suspend_restarts_uncertain_fan_spinup(tmp_path, monkeypatch):
    fan, daemon, template, _ = controller_fixture(tmp_path, monkeypatch, 'fan_pid_suspend_reprobe_test')
    boot = [fan.clock_now()]
    mono = [boot[0]]
    monkeypatch.setattr(fan, 'clock_now', lambda: boot[0])
    monkeypatch.setattr(daemon, 'clock_now', lambda: boot[0])
    monkeypatch.setattr(daemon.time, 'monotonic', lambda: mono[0])
    rates = {'fan1': 0, 'fan2': 0}
    monkeypatch.setattr(daemon, 'fan_rpm', lambda item, fd: rates[item['name']])
    ctrl = daemon.Controller(1, daemon.detect_present_fans(1, ''), '',
                             dict(template.zone_paths), {0x30: 96, 0x40: 96})
    ctrl.next_rescan_t = float('inf')
    ctrl.step()
    rates['fan1'] = 1200
    boot[0] += 4
    mono[0] += 4
    ctrl.step()
    assert not ctrl.fans[0]['present']
    boot[0] += 2
    ctrl.step()
    assert not ctrl.fans[0]['present']
    assert not fan.APPLIED.exists()


@pytest.mark.parametrize('failure', ['write', 'readback'])
def test_uncertain_chassis_requires_verified_max_before_recovery(tmp_path, monkeypatch, failure):
    fan, daemon, template, _ = controller_fixture(tmp_path, monkeypatch, 'fan_pid_unverified_spinup_test')
    clock = [fan.clock_now()]
    monkeypatch.setattr(fan, 'clock_now', lambda: clock[0])
    monkeypatch.setattr(daemon, 'clock_now', lambda: clock[0])
    monkeypatch.setattr(daemon.time, 'monotonic', lambda: clock[0])
    rates = {'fan1': 0, 'fan2': 0}
    monkeypatch.setattr(daemon, 'fan_rpm', lambda item, fd: rates[item['name']])
    ctrl = daemon.Controller(1, daemon.detect_present_fans(1, ''), '',
                             dict(template.zone_paths), {0x30: 96, 0x40: 96})
    ctrl.next_rescan_t = float('inf')
    rates['fan1'] = 1200
    if failure == 'write':
        monkeypatch.setattr(daemon, 'i2c_write_pwm', lambda fd, value, reg: reg != 0x30)
    else:
        monkeypatch.setattr(daemon, 'i2c_read_byte', lambda fd, reg: 0 if reg == 0x30 else 255)
    for _ in range(4):
        ctrl.step()
        clock[0] += 2
    assert not ctrl.fans[0]['present']
    assert not fan.APPLIED.exists()


def test_recovery_needs_two_distinct_positive_samples(tmp_path, monkeypatch):
    fan, daemon, template, _ = controller_fixture(tmp_path, monkeypatch, 'fan_pid_distinct_samples_test')
    clock = [fan.clock_now()]
    monkeypatch.setattr(fan, 'clock_now', lambda: clock[0])
    monkeypatch.setattr(daemon, 'clock_now', lambda: clock[0])
    monkeypatch.setattr(daemon.time, 'monotonic', lambda: clock[0])
    rates = {'fan1': 0, 'fan2': 0}
    monkeypatch.setattr(daemon, 'fan_rpm', lambda item, fd: rates[item['name']])
    ctrl = daemon.Controller(1, daemon.detect_present_fans(1, ''), '',
                             dict(template.zone_paths), {0x30: 96, 0x40: 96})
    ctrl.next_rescan_t = float('inf')
    ctrl.step()
    rates['fan1'] = 1200
    clock[0] += 4
    ctrl.step()
    clock[0] += 0.2
    ctrl.step()
    assert not ctrl.fans[0]['present']
    rates['fan1'] = 0
    clock[0] += 2
    ctrl.step()
    rates['fan1'] = 1200
    clock[0] += 2
    ctrl.step()
    assert not ctrl.fans[0]['present']
    clock[0] += 2
    ctrl.step()
    assert ctrl.fans[0]['present']


def test_unconfirmed_aux_stays_max_without_blocking_chassis(tmp_path, monkeypatch):
    fan, daemon, template, _ = controller_fixture(tmp_path, monkeypatch, 'fan_pid_optional_reprobe_test')
    clock = [fan.clock_now()]
    monkeypatch.setattr(fan, 'clock_now', lambda: clock[0])
    monkeypatch.setattr(daemon, 'clock_now', lambda: clock[0])
    monkeypatch.setattr(daemon.time, 'monotonic', lambda: clock[0])
    rates = {'fan1': 1200, 'fan2': 0}
    monkeypatch.setattr(daemon, 'fan_rpm', lambda item, fd: rates[item['name']])
    ctrl = daemon.Controller(1, [
        {'name': 'fan1', 'duty_reg': 0x30, 'tach_file': 'fan1_input', 'present': True},
        {'name': 'fan2', 'duty_reg': 0x40, 'tach_file': 'fan2_input', 'present': False},
    ], '', dict(template.zone_paths), {0x30: 60, 0x40: 96})
    ctrl.next_rescan_t = float('inf')
    writes = []
    monkeypatch.setattr(daemon, 'i2c_write_pwm', lambda fd, value, reg: writes.append((reg, value)) or True)
    for _ in range(3):
        ctrl.step()
        clock[0] += 2
    assert fan.read_applied()['mode'] == 'normal'
    assert all(value == 255 for reg, value in writes if reg == 0x40)
    assert not ctrl.fans[1]['present']


def test_optional_aux_appearance_stays_max_until_recovered(tmp_path, monkeypatch):
    fan, daemon, template, _ = controller_fixture(tmp_path, monkeypatch, 'fan_pid_aux_arrival_test')
    clock = [fan.clock_now()]
    monkeypatch.setattr(fan, 'clock_now', lambda: clock[0])
    monkeypatch.setattr(daemon, 'clock_now', lambda: clock[0])
    monkeypatch.setattr(daemon.time, 'monotonic', lambda: clock[0])
    rates = {'fan1': 1200, 'fan2': 0}
    monkeypatch.setattr(daemon, 'fan_rpm', lambda item, fd: rates[item['name']])
    ctrl = daemon.Controller(1, [
        {'name': 'fan1', 'duty_reg': 0x30, 'tach_file': 'fan1_input', 'present': True},
        {'name': 'fan2', 'duty_reg': 0x40, 'tach_file': 'fan2_input', 'present': False},
    ], '', dict(template.zone_paths), {0x30: 60, 0x40: 96})
    ctrl.next_rescan_t = float('inf')
    ctrl.step()
    assert fan.read_applied()['mode'] == 'normal'
    rates['fan2'] = 1200
    clock[0] += 4
    assert ctrl.step()[0] == {'fan1': 255, 'fan2': 255}
    assert ctrl.fans[1]['ever_seen'] and not ctrl.fans[1]['present']
    assert not fan.APPLIED.exists()
    clock[0] += 2
    assert ctrl.step()[0]['fan2'] == 255
    assert ctrl.fans[1]['present'] and not ctrl.fans[1]['recovering']
    clock[0] += 2
    ctrl.step()
    assert fan.read_applied()['mode'] == 'normal'


def test_confirmed_aux_loss_cannot_be_silently_ignored(tmp_path, monkeypatch):
    fan, daemon, template, _ = controller_fixture(tmp_path, monkeypatch, 'fan_pid_known_aux_loss_test')
    clock = [fan.clock_now()]
    monkeypatch.setattr(fan, 'clock_now', lambda: clock[0])
    monkeypatch.setattr(daemon, 'clock_now', lambda: clock[0])
    monkeypatch.setattr(daemon.time, 'monotonic', lambda: clock[0])
    rates = {'fan1': 1200, 'fan2': 1200}
    monkeypatch.setattr(daemon, 'fan_rpm', lambda item, fd: rates[item['name']])
    ctrl = daemon.Controller(1, [
        {'name': 'fan1', 'duty_reg': 0x30, 'tach_file': 'fan1_input', 'present': True},
        {'name': 'fan2', 'duty_reg': 0x40, 'tach_file': 'fan2_input', 'present': True},
    ], '', dict(template.zone_paths), {0x30: 60, 0x40: 60})
    ctrl.next_rescan_t = float('inf')
    ctrl.step()
    assert fan.APPLIED.exists()
    rates['fan2'] = 0
    pwms, _, _ = ctrl.step()
    assert pwms == {'fan1': 255, 'fan2': 255}
    assert ctrl.fans[1]['present'] and ctrl.fans[1]['recovering']
    assert not fan.APPLIED.exists()
    rates['fan2'] = 1200
    for _ in range(5):
        clock[0] += 2
        fan.write_verification(fan.read_snapshot())
        assert ctrl.step()[0] == {'fan1': 255, 'fan2': 255}
        assert ctrl.fans[1]['recovering']
    clock[0] += 2
    fan.write_verification(fan.read_snapshot())
    ctrl.step()
    assert not ctrl.fans[1]['recovering']
    clock[0] += 2
    fan.write_verification(fan.read_snapshot())
    ctrl.step()
    assert fan.read_applied()['mode'] == 'normal'


def test_range_read_failure_before_ack_revokes_output(tmp_path, monkeypatch):
    fan, daemon, ctrl, writes = controller_fixture(tmp_path, monkeypatch, 'fan_pid_final_range_check_test')
    reads = []
    def mode(fd, reg):
        reads.append(reg)
        return 1 if reads.count(reg) == 1 else None
    monkeypatch.setattr(daemon, 'current_tach_mode', mode)
    ctrl.step()
    assert reads.count(0x30) >= 2
    assert writes[-1] == 255
    assert not fan.APPLIED.exists()


def test_failed_confirmed_pwm_write_requires_new_spinup(tmp_path, monkeypatch):
    fan, daemon, ctrl, _ = controller_fixture(tmp_path, monkeypatch, 'fan_pid_failed_pwm_spinup_test')
    ctrl.step()
    assert fan.APPLIED.exists()
    ctrl.fans[0]['last_written_pwm'] = -1
    monkeypatch.setattr(daemon, 'i2c_write_pwm', lambda *_: False)
    ctrl.step()
    assert not fan.APPLIED.exists()
    monkeypatch.setattr(daemon, 'i2c_write_pwm', lambda *_: True)
    ctrl.step()
    assert not fan.APPLIED.exists()
    assert ctrl.fans[0]['recovering']


def test_failed_max_write_on_unknown_aux_blocks_ack(tmp_path, monkeypatch):
    fan, daemon, template, _ = controller_fixture(tmp_path, monkeypatch, 'fan_pid_aux_max_write_test')
    ctrl = daemon.Controller(1, [
        {'name': 'fan1', 'duty_reg': 0x30, 'tach_file': 'fan1_input', 'present': True},
        {'name': 'fan2', 'duty_reg': 0x40, 'tach_file': 'fan2_input', 'present': False},
    ], '', dict(template.zone_paths), {0x30: 60, 0x40: 96})
    ctrl.next_rescan_t = float('inf')
    fan.PENDING.write_text(str(fan.clock_now() - 60))
    count = {0x40: 0}
    def write(fd, value, reg):
        if reg == 0x40:
            count[reg] += 1
            return count[reg] != 2
        return True
    monkeypatch.setattr(daemon, 'i2c_write_pwm', write)
    ctrl.step()
    assert not fan.APPLIED.exists()


def test_startup_whistle_never_lowers_unconfirmed_aux(tmp_path, monkeypatch):
    _, daemon, ctrl, _ = controller_fixture(tmp_path, monkeypatch, 'fan_pid_uncertain_whistle_test')
    writes = []
    monkeypatch.setattr(daemon, 'i2c_write_pwm', lambda fd, value, reg: writes.append((reg, value)) or True)
    monkeypatch.setattr(daemon.time, 'sleep', lambda _: None)
    daemon.play_startup_whistle(1, ctrl.zone_paths, ctrl.fans)
    assert any(reg == 0x30 and value < 255 for reg, value in writes)
    assert all(value == 255 for reg, value in writes if reg == 0x40)


def test_presence_detection_requires_verified_max_output(monkeypatch):
    modules(monkeypatch)
    daemon = load_script(monkeypatch, ROOT / 'board/scripts/fan-pid', 'fan_pid_detection_max_test')
    daemon.TACH_MODES.update({0x30: 1, 0x40: 1})
    monkeypatch.setattr(daemon, 'fan_rpm', lambda *_: 1200)
    detected = daemon.detect_present_fans(1, '', verified_outputs={0x30: False, 0x40: True})
    assert not detected[0]['present']
    assert detected[1]['present']


def test_known_tach_loss_does_not_issue_lower_duty_first(tmp_path, monkeypatch):
    fan, daemon, ctrl, _ = controller_fixture(tmp_path, monkeypatch, 'fan_pid_tach_precheck_test')
    ctrl.step()
    assert fan.APPLIED.exists()
    monkeypatch.setattr(daemon, 'fan_rpm', lambda *_: 0)
    writes = []
    monkeypatch.setattr(daemon, 'i2c_write_pwm', lambda fd, value, reg: writes.append(value) or True)
    ctrl.step()
    assert writes and all(value == 255 for value in writes)
    assert not fan.APPLIED.exists()


@pytest.mark.parametrize('first,second,expected', [
    (False, True, False),
    (True, False, False),
    (True, True, True),
])
def test_startup_detection_requires_both_spinup_samples(monkeypatch, first, second, expected):
    modules(monkeypatch)
    daemon = load_script(monkeypatch, ROOT / 'board/scripts/fan-pid', 'fan_pid_double_detection_test')
    initial = [{'duty_reg': 0x30, 'present': first, 'ever_seen': first, 'tach_ok': first,
                'sampled_at': 100.0, 'tach_mode': 1}]
    final = [{'duty_reg': 0x30, 'present': second, 'ever_seen': second, 'tach_ok': second,
              'sampled_at': 102.0, 'tach_mode': 1}]
    merged = daemon.combine_startup_detections(initial, final)
    assert merged[0]['present'] is expected
    assert merged[0]['tach_ok'] is expected
    assert merged[0]['ever_seen'] is (first or second)


def test_spinup_rechecks_max_output_before_detection(monkeypatch):
    modules(monkeypatch)
    daemon = load_script(monkeypatch, ROOT / 'board/scripts/fan-pid', 'fan_pid_spinup_readback_test')
    daemon.TACH_MODES.update({0x30: 1, 0x40: 1})
    clock = [100.0]
    monkeypatch.setattr(daemon.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(daemon, 'clock_now', lambda: clock[0])
    monkeypatch.setattr(daemon.time, 'sleep', lambda delay: clock.__setitem__(0, clock[0] + delay))
    monkeypatch.setattr(daemon, 'i2c_write_pwm', lambda *_: True)
    monkeypatch.setattr(daemon, 'fan_rpm', lambda *_: 1200)
    reads = {0x30: 0, 0x40: 0}
    def read(fd, reg):
        reads[reg] += 1
        return 0 if reg == 0x30 and reads[reg] > 1 else 255
    monkeypatch.setattr(daemon, 'i2c_read_byte', read)
    fans = daemon.spinup_and_detect(1, '')
    assert clock[0] >= 100.0 + daemon.PRESENCE_SPINUP_S
    assert not fans[0]['present'] and fans[0]['ever_seen']
    assert fans[1]['present']


@pytest.mark.parametrize('elapsed,first_mode,second_mode,expected', [
    (1.0, 1, 1, False),
    (2.0, 1, 1, True),
    (11.0, 1, 1, False),
    (3.0, 1, 2, False),
])
def test_startup_samples_must_be_fresh_and_same_range(monkeypatch, elapsed, first_mode, second_mode, expected):
    modules(monkeypatch)
    daemon = load_script(monkeypatch, ROOT / 'board/scripts/fan-pid', 'fan_pid_startup_sample_freshness_test')
    first = [{'duty_reg': 0x30, 'present': True, 'ever_seen': True, 'tach_ok': True,
              'sampled_at': 100.0, 'tach_mode': first_mode}]
    second = [{'duty_reg': 0x30, 'present': True, 'ever_seen': True, 'tach_ok': True,
               'sampled_at': 100.0 + elapsed, 'tach_mode': second_mode}]
    assert daemon.combine_startup_detections(first, second)[0]['present'] is expected


def test_startup_spinup_rejects_suspend_during_max_hold(monkeypatch):
    modules(monkeypatch)
    daemon = load_script(monkeypatch, ROOT / 'board/scripts/fan-pid', 'fan_pid_boot_spinup_suspend_test')
    daemon.TACH_MODES.update({0x30: 1, 0x40: 1})
    mono = [100.0]
    boot = [100.0]
    monkeypatch.setattr(daemon.time, 'monotonic', lambda: mono[0])
    monkeypatch.setattr(daemon, 'clock_now', lambda: boot[0])
    def sleep(delay):
        mono[0] += delay
        boot[0] += delay
        if boot[0] - mono[0] < 1:
            boot[0] += 2
    monkeypatch.setattr(daemon.time, 'sleep', sleep)
    monkeypatch.setattr(daemon, 'i2c_write_pwm', lambda *_: True)
    monkeypatch.setattr(daemon, 'i2c_read_byte', lambda *_: 255)
    monkeypatch.setattr(daemon, 'fan_rpm', lambda *_: 1200)
    fans = daemon.spinup_and_detect(1, '')
    assert all(not fan['present'] for fan in fans)
    assert all(fan['ever_seen'] for fan in fans)


@pytest.mark.parametrize('compatible', [
    b'fsl,ls1046a\0',
    b'mono,gateway-dk-extra\0fsl,ls1046a\0',
    b'other,board\0',
    b'',
])
def test_daemon_board_gate_rejects_non_mono_before_discovery(monkeypatch, compatible):
    modules(monkeypatch)
    daemon = load_script(monkeypatch, ROOT / 'board/scripts/fan-pid', 'fan_pid_board_gate_test')
    monkeypatch.setattr(daemon, 'open', lambda path, mode: io.BytesIO(compatible), raising=False)
    monkeypatch.setattr(daemon, 'notify_watchdog', lambda: None)
    monkeypatch.setattr(daemon, 'find_bound_emc2305', lambda: pytest.fail('unexpected device discovery'), raising=False)
    monkeypatch.setattr(daemon, 'find_emc2305_pwm', lambda *args: pytest.fail('unexpected hwmon discovery'))
    assert daemon.main() == 0


@pytest.mark.parametrize('compatible', [
    b'mono,gateway-dk\0fsl,ls1046a\0',
    b'fsl,ls1046a\0mono,gateway-dk\0',
    b'mono,gateway-dk\0',
])
def test_daemon_accepts_exact_mono_compatible(monkeypatch, compatible):
    modules(monkeypatch)
    daemon = load_script(monkeypatch, ROOT / 'board/scripts/fan-pid', 'fan_pid_mono_gate_test')
    monkeypatch.setattr(daemon, 'open', lambda path, mode: io.BytesIO(compatible), raising=False)
    monkeypatch.setattr(daemon, 'notify_watchdog', lambda: None)
    monkeypatch.setattr(daemon, 'invalidate_applied', lambda: None)
    calls = []
    def discovery():
        calls.append(True)
        raise RuntimeError('no device')
    monkeypatch.setattr(daemon, 'find_bound_emc2305', discovery)
    assert daemon.main() == 1
    assert calls == [True]


def fake_emc_tree(tmp_path, monkeypatch, daemon):
    devices = tmp_path / 'devices'
    devices.mkdir()
    driver = tmp_path / 'drivers' / 'emc2305'
    driver.mkdir(parents=True)
    hwmon = tmp_path / 'hwmon'
    hwmon.mkdir()
    monkeypatch.setattr(daemon, 'I2C_DEVICES_ROOT', str(devices), raising=False)
    monkeypatch.setattr(daemon, 'EMC2305_DRIVER_ROOT', str(driver), raising=False)
    monkeypatch.setattr(daemon, 'HWMON_ROOT', str(hwmon), raising=False)
    return devices, driver, hwmon


def test_daemon_selects_only_unique_bound_device_and_matching_hwmon(tmp_path, monkeypatch):
    modules(monkeypatch)
    daemon = load_script(monkeypatch, ROOT / 'board/scripts/fan-pid', 'fan_pid_device_selection_test')
    devices, driver, hwmon = fake_emc_tree(tmp_path, monkeypatch, daemon)
    wrong = devices / '3-002e'
    wrong.mkdir()
    bound = devices / '7-002e'
    bound.mkdir()
    (bound / 'driver').symlink_to(driver)
    for index, device in [(1, wrong), (2, bound)]:
        entry = hwmon / f'hwmon{index}'
        entry.mkdir()
        (entry / 'name').write_text('emc2305')
        (entry / 'pwm1').write_text('255')
        (entry / 'fan1_input').write_text('1000')
        (entry / 'device').symlink_to(device)
    bus, device = daemon.find_bound_emc2305()
    assert bus == 7 and Path(device).resolve() == bound.resolve()
    pwm, tach = daemon.find_emc2305_pwm(device)
    assert Path(pwm) == hwmon / 'hwmon2' / 'pwm1'
    assert Path(tach) == hwmon / 'hwmon2' / 'fan1_input'


@pytest.mark.parametrize('count', [0, 2])
def test_daemon_refuses_missing_or_ambiguous_bound_devices(tmp_path, monkeypatch, count):
    modules(monkeypatch)
    daemon = load_script(monkeypatch, ROOT / 'board/scripts/fan-pid', 'fan_pid_ambiguous_device_test')
    devices, driver, _ = fake_emc_tree(tmp_path, monkeypatch, daemon)
    for bus in range(count):
        entry = devices / f'{bus + 7}-002e'
        entry.mkdir()
        (entry / 'driver').symlink_to(driver)
    with pytest.raises(RuntimeError, match='bound emc2305'):
        daemon.find_bound_emc2305()


def test_daemon_does_not_open_ambiguous_controllers(tmp_path, monkeypatch):
    modules(monkeypatch)
    daemon = load_script(monkeypatch, ROOT / 'board/scripts/fan-pid', 'fan_pid_ambiguous_main_test')
    devices, driver, _ = fake_emc_tree(tmp_path, monkeypatch, daemon)
    for bus in (7, 8):
        entry = devices / f'{bus}-002e'
        entry.mkdir()
        (entry / 'driver').symlink_to(driver)
    monkeypatch.setattr(daemon, 'open', lambda path, mode: io.BytesIO(b'mono,gateway-dk\0fsl,ls1046a\0'), raising=False)
    monkeypatch.setattr(daemon, 'notify_watchdog', lambda: None)
    monkeypatch.setattr(daemon, 'invalidate_applied', lambda: None)
    monkeypatch.setattr(daemon.os, 'open', lambda *_: pytest.fail('I2C opened for ambiguous controller'))
    assert daemon.main() == 1


def test_daemon_opens_only_revalidated_bound_i2c(tmp_path, monkeypatch):
    modules(monkeypatch)
    daemon = load_script(monkeypatch, ROOT / 'board/scripts/fan-pid', 'fan_pid_verified_open_test')
    devices, driver, _ = fake_emc_tree(tmp_path, monkeypatch, daemon)
    device = devices / '7-002e'
    device.mkdir()
    (device / 'driver').symlink_to(driver)
    devroot = tmp_path / 'dev'
    devroot.mkdir()
    (devroot / 'i2c-7').touch()
    monkeypatch.setattr(daemon, 'I2C_DEV_ROOT', str(devroot))
    calls = []
    monkeypatch.setattr(daemon.os, 'open', lambda path, flags: calls.append((path, flags)) or 42)
    monkeypatch.setattr(daemon.fcntl, 'ioctl', lambda *args: calls.append(args))
    assert daemon.find_emc2305_i2c(daemon.find_bound_emc2305()) == 42
    assert calls[0] == (str(devroot / 'i2c-7'), os.O_RDWR | os.O_CLOEXEC)
    assert calls[1] == (42, daemon.I2C_SLAVE_FORCE, daemon.EMC2305_I2C_ADDR)


def test_daemon_refuses_device_change_before_open(monkeypatch):
    modules(monkeypatch)
    daemon = load_script(monkeypatch, ROOT / 'board/scripts/fan-pid', 'fan_pid_rebind_test')
    monkeypatch.setattr(daemon, 'find_bound_emc2305', lambda: (8, '/sys/devices/8-002e'), raising=False)
    monkeypatch.setattr(daemon.os, 'open', lambda *_: pytest.fail('I2C opened after device changed'))
    with pytest.raises(RuntimeError, match='changed'):
        daemon.find_emc2305_i2c((7, '/sys/devices/7-002e'))


def test_daemon_rejects_wrong_driver_with_same_basename(tmp_path, monkeypatch):
    modules(monkeypatch)
    daemon = load_script(monkeypatch, ROOT / 'board/scripts/fan-pid', 'fan_pid_wrong_driver_test')
    devices, _, _ = fake_emc_tree(tmp_path, monkeypatch, daemon)
    other = tmp_path / 'other' / 'emc2305'
    other.mkdir(parents=True)
    device = devices / '7-002e'
    device.mkdir()
    (device / 'driver').symlink_to(other)
    with pytest.raises(RuntimeError, match='bound emc2305'):
        daemon.find_bound_emc2305()


def test_daemon_refuses_duplicate_hwmon_for_bound_device(tmp_path, monkeypatch):
    modules(monkeypatch)
    daemon = load_script(monkeypatch, ROOT / 'board/scripts/fan-pid', 'fan_pid_ambiguous_hwmon_test')
    devices, driver, hwmon = fake_emc_tree(tmp_path, monkeypatch, daemon)
    bound = devices / '7-002e'
    bound.mkdir()
    (bound / 'driver').symlink_to(driver)
    for index in (1, 2):
        entry = hwmon / f'hwmon{index}'
        entry.mkdir()
        (entry / 'name').write_text('emc2305')
        (entry / 'pwm1').write_text('255')
        (entry / 'fan1_input').write_text('1000')
        (entry / 'device').symlink_to(bound)
    with pytest.raises(RuntimeError, match='one hwmon'):
        daemon.find_emc2305_pwm(str(bound))


def test_daemon_closes_bus_if_binding_changes_after_open(monkeypatch):
    modules(monkeypatch)
    daemon = load_script(monkeypatch, ROOT / 'board/scripts/fan-pid', 'fan_pid_rebind_after_open_test')
    expected = (7, '/sys/devices/7-002e')
    choices = iter([expected, expected, (8, '/sys/devices/8-002e')])
    monkeypatch.setattr(daemon, 'find_bound_emc2305', lambda: next(choices))
    monkeypatch.setattr(daemon.os.path, 'exists', lambda _: True)
    monkeypatch.setattr(daemon.os, 'open', lambda *_: 42)
    closed = []
    monkeypatch.setattr(daemon.os, 'close', closed.append)
    monkeypatch.setattr(daemon.fcntl, 'ioctl', lambda *_: pytest.fail('I2C claimed after binding changed'))
    with pytest.raises(RuntimeError, match='changed'):
        daemon.find_emc2305_i2c(expected)
    assert closed == [42]


def test_daemon_closes_bus_if_binding_changes_during_claim(monkeypatch):
    modules(monkeypatch)
    daemon = load_script(monkeypatch, ROOT / 'board/scripts/fan-pid', 'fan_pid_rebind_during_claim_test')
    expected = (7, '/sys/devices/7-002e')
    choices = iter([expected, expected, expected, (8, '/sys/devices/8-002e')])
    monkeypatch.setattr(daemon, 'find_bound_emc2305', lambda: next(choices))
    monkeypatch.setattr(daemon.os.path, 'exists', lambda _: True)
    monkeypatch.setattr(daemon.os, 'open', lambda *_: 42)
    closed = []
    monkeypatch.setattr(daemon.os, 'close', closed.append)
    monkeypatch.setattr(daemon.fcntl, 'ioctl', lambda *_: 0)
    with pytest.raises(RuntimeError, match='changed'):
        daemon.find_emc2305_i2c(expected)
    assert closed == [42]


def test_recovery_export_distinguishes_required_and_unknown_aux(tmp_path, monkeypatch):
    modules(monkeypatch)
    daemon = load_script(monkeypatch, ROOT / 'board/scripts/fan-pid', 'fan_pid_recovery_export_test')
    daemon.TACH_MODES.update({0x30: 1, 0x40: 1})
    monkeypatch.setattr(daemon, 'FLOOR_STATE_DIR', str(tmp_path / 'fan-pid'))
    monkeypatch.setattr(daemon, 'FLOOR_STATE_FILE', str(tmp_path / 'fan-pid' / 'floors'))
    daemon.Controller(1, [
        {'name': 'fan1', 'duty_reg': 0x30, 'tach_file': 'fan1_input', 'present': False},
        {'name': 'fan2', 'duty_reg': 0x40, 'tach_file': 'fan2_input', 'present': False},
    ], '', {}, {0x30: 96, 0x40: 96})
    export = Path(daemon.FLOOR_STATE_FILE).read_text()
    assert 'present:0x30=0\n' in export and 'present:0x40=0\n' in export
    assert 'fan_state:0x30=recovering\n' in export
    assert 'fan_state:0x40=unknown\n' in export


def test_recovery_export_preserves_seen_auxiliary_status(tmp_path, monkeypatch):
    modules(monkeypatch)
    daemon = load_script(monkeypatch, ROOT / 'board/scripts/fan-pid', 'fan_pid_seen_aux_export_test')
    daemon.TACH_MODES.update({0x30: 1, 0x40: 1})
    monkeypatch.setattr(daemon, 'FLOOR_STATE_DIR', str(tmp_path / 'fan-pid'))
    monkeypatch.setattr(daemon, 'FLOOR_STATE_FILE', str(tmp_path / 'fan-pid' / 'floors'))
    daemon.Controller(1, [
        {'name': 'fan1', 'duty_reg': 0x30, 'tach_file': 'fan1_input', 'present': True},
        {'name': 'fan2', 'duty_reg': 0x40, 'tach_file': 'fan2_input', 'present': False, 'ever_seen': True},
    ], '', {}, {0x30: 96, 0x40: 96})
    export = Path(daemon.FLOOR_STATE_FILE).read_text()
    assert 'present:0x30=1\n' in export and 'fan_state:0x30=present\n' in export
    assert 'present:0x40=0\n' in export and 'fan_state:0x40=recovering\n' in export


def test_floor_export_records_selected_controller_identity(tmp_path, monkeypatch):
    modules(monkeypatch)
    daemon = load_script(monkeypatch, ROOT / 'board/scripts/fan-pid', 'fan_pid_controller_export_test')
    daemon.TACH_MODES.update({0x30: 1, 0x40: 1})
    monkeypatch.setattr(daemon, 'FLOOR_STATE_DIR', str(tmp_path / 'fan-pid'))
    monkeypatch.setattr(daemon, 'FLOOR_STATE_FILE', str(tmp_path / 'fan-pid' / 'floors'))
    daemon.Controller(1, [
        {'name': 'fan1', 'duty_reg': 0x30, 'tach_file': 'fan1_input', 'present': True},
    ], '', {}, {0x30: 96}, controller_device='/sys/devices/platform/i2c-7/7-002e')
    assert 'controller_device=/sys/devices/platform/i2c-7/7-002e\n' in Path(daemon.FLOOR_STATE_FILE).read_text()


def test_daemon_watchdog_notifies_systemd(tmp_path, monkeypatch):
    modules(monkeypatch)
    daemon = load_script(monkeypatch, ROOT / 'board/scripts/fan-pid', 'fan_pid_watchdog_test')
    address = str(tmp_path / 'notify.sock')
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    listener.settimeout(1)
    try:
        listener.bind(address)
        monkeypatch.setenv('NOTIFY_SOCKET', address)
        daemon.notify_watchdog()
        assert listener.recv(256) == b'WATCHDOG=1'
    finally:
        listener.close()
    unit = (ROOT / 'board/systemd/fan-pid.service').read_text()
    assert 'WatchdogSec=30s' in unit
    assert 'Restart=on-failure' in unit


@pytest.mark.skipif(not shutil.which('systemd-analyze'), reason='systemd-analyze unavailable')
def test_systemd_units_validate_with_host_fan_binary(tmp_path):
    source = (ROOT / 'board/systemd/fan-pid.service').read_text()
    assert 'ExecStart=/usr/local/bin/fan-pid' in source
    unit = tmp_path / 'fan-pid.service'
    unit.write_text(source.replace('ExecStart=/usr/local/bin/fan-pid',
                                   f'ExecStart={sys.executable} {ROOT / "board/scripts/fan-pid"}'))
    subprocess.run(['systemd-analyze', 'verify', str(unit),
                    str(ROOT / 'board/systemd/vyos-fan-reconcile.service')],
                   check=True, text=True, capture_output=True)


@pytest.mark.parametrize('fail', [False, True])
def test_watchdog_ping_follows_completed_control_tick(monkeypatch, fail):
    modules(monkeypatch)
    daemon = load_script(monkeypatch, ROOT / 'board/scripts/fan-pid', 'fan_pid_main_watchdog_test')
    events = []
    fd = os.open('/dev/null', os.O_RDWR)
    monkeypatch.setattr(daemon, 'open', lambda path, mode: io.BytesIO(b'mono,gateway-dk\0fsl,ls1046a\0'), raising=False)
    monkeypatch.setattr(daemon, 'find_bound_emc2305', lambda: (7, '/fake/device'))
    monkeypatch.setattr(daemon, 'find_emc2305_pwm', lambda device: ('/fake/pwm1', '/fake/fan1'))
    monkeypatch.setattr(daemon, 'find_emc2305_i2c', lambda expected: fd)
    monkeypatch.setattr(daemon, 'i2c_write_all_fans', lambda *_: events.append('max'))
    monkeypatch.setattr(daemon, 'set_tach_range_500', lambda *_: None)
    monkeypatch.setattr(daemon, 'neutralize_kernel_cooling_devices', lambda: None)
    monkeypatch.setattr(daemon, 'find_thermal_zones', lambda: {'ddr-thermal': '/fake'})
    monkeypatch.setattr(daemon, 'play_startup_whistle', lambda *_: None)
    monkeypatch.setattr(daemon, 'detect_present_fans', lambda *_, **__: [])
    monkeypatch.setattr(daemon, 'invalidate_applied', lambda: None)
    monkeypatch.setattr(daemon, 'notify_watchdog', lambda: events.append('ping'))
    monkeypatch.setattr(daemon, 'PRESENCE_SPINUP_S', 0)
    monkeypatch.setattr(daemon.signal, 'signal', lambda *_: None)
    class FakeController:
        def __init__(self, *args, **kwargs):
            self.output_failed = False

        def step(self):
            events.append('step')
            daemon._running = False
            if fail:
                raise OSError('sensor read')
            return {}, {}, {}

        def maybe_log(self, *args):
            pass

        def invalidate_settings_ack(self):
            events.append('invalidate')

        def force_max(self):
            events.append('forced-max')

    monkeypatch.setattr(daemon, 'Controller', FakeController)
    assert daemon.main() == 0
    index = events.index('step')
    assert 'ping' in events[index + 1:]
    if fail:
        assert events.index('forced-max', index) < events.index('ping', index)


def test_daemon_watchdog_supports_abstract_socket(monkeypatch):
    modules(monkeypatch)
    daemon = load_script(monkeypatch, ROOT / 'board/scripts/fan-pid', 'fan_pid_abstract_watchdog_test')
    address = f'fan-pid-test-{os.getpid()}-{time.monotonic_ns()}'
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    listener.settimeout(1)
    try:
        listener.bind('\0' + address)
        monkeypatch.setenv('NOTIFY_SOCKET', '@' + address)
        daemon.notify_watchdog()
        assert listener.recv(256) == b'WATCHDOG=1'
    finally:
        listener.close()


def test_critical_sensor_skips_startup_whistle(tmp_path, monkeypatch):
    _, daemon, ctrl, writes = controller_fixture(tmp_path, monkeypatch, 'fan_pid_critical_whistle_test')
    monkeypatch.setattr(daemon, 'read_sensor',
                        lambda zone, path: 90.0 if zone.name == 'ddr-thermal' else 50.0)
    monkeypatch.setattr(daemon.time, 'sleep', lambda _: None)
    daemon.play_startup_whistle(1, ctrl.zone_paths, ctrl.fans)
    assert writes and all(value == 255 for value in writes)


def test_missing_sensor_skips_startup_whistle(tmp_path, monkeypatch):
    _, daemon, ctrl, writes = controller_fixture(tmp_path, monkeypatch, 'fan_pid_missing_whistle_sensor_test')
    ctrl.zone_paths.pop('CPU')
    monkeypatch.setattr(daemon.time, 'sleep', lambda _: None)
    daemon.play_startup_whistle(1, ctrl.zone_paths, ctrl.fans)
    assert writes and all(value == 255 for value in writes)


@pytest.mark.skipif(not os.environ.get('VYOS_1X_SOURCE'), reason='Pinned VyOS source unavailable')
def test_pinned_commit_lock_blocks_reconcile_and_covers_publication(tmp_path, monkeypatch):
    source = Path(os.environ['VYOS_1X_SOURCE'])
    fan, _ = modules(monkeypatch)
    fan.STATE = tmp_path / 'state'
    fan.APPLIED = tmp_path / 'applied'
    defaults = types.ModuleType('vyos.defaults')
    defaults.commit_lock = str(tmp_path / 'commit.lock')
    monkeypatch.setitem(sys.modules, 'vyos.defaults', defaults)
    lock_api = load_script(monkeypatch, source / 'python/vyos/utils/commit.py', 'fan_real_commit_lock')
    script = load_script(monkeypatch, ROOT / 'data/vyos-1x-files/src/conf_mode/system_hardware_fan.py',
                         'fan_conf_real_lock_test')
    script.PENDING = tmp_path / 'pending'
    script.acquire_commit_lock_file = lock_api.acquire_commit_lock_file
    script.release_commit_lock_file = lock_api.release_commit_lock_file
    script.commit_in_progress2 = lock_api.commit_in_progress2
    context = multiprocessing.get_context('fork')

    def check_lock_is_held():
        parent, child = context.Pipe()
        probe = context.Process(target=probe_lock, args=(defaults.commit_lock, child))
        probe.start()
        try:
            assert parent.poll(5) and parent.recv() is False
            probe.join(5)
            assert not probe.is_alive()
        finally:
            if probe.is_alive():
                probe.terminate()
                probe.join(5)
            parent.close()
            child.close()

    class FakeConfig:
        def get_config_tree(self, effective=False):
            check_lock_is_held()
            return {}

        def get_config_dict(self, path, effective=False, get_first_key=False):
            check_lock_is_held()
            return {'zone': {'memory': {'setpoint': '55'}}}

    script.Config = FakeConfig
    write_snapshot = script.write_snapshot

    def write_while_locked(values):
        check_lock_is_held()
        return write_snapshot(values)

    script.write_snapshot = write_while_locked
    write_verification = script.write_verification

    def verify_while_locked(values, generation):
        check_lock_is_held()
        return write_verification(values, generation)

    script.write_verification = verify_while_locked
    script.apply({})
    parent, child = context.Pipe()
    holder = context.Process(target=hold_lock, args=(defaults.commit_lock, child))
    holder.start()
    try:
        assert parent.poll(5) and parent.recv() is True
        script.reconcile()
        assert script.PENDING.exists() and not fan.STATE.exists()
        parent.send(True)
        holder.join(5)
        assert not holder.is_alive()
        script.reconcile()
        assert fan.read_snapshot()['memory']['setpoint'] == 55
        assert script.PENDING.exists()
        publish_test_ack(fan, fan.read_snapshot())
        script.reconcile()
        assert not script.PENDING.exists()
    finally:
        if holder.is_alive():
            holder.terminate()
            holder.join(5)
        parent.close()
        child.close()


def test_boot_pending_grace_and_timeout_syslog_dedup(tmp_path, monkeypatch):
    fan, daemon, ctrl, writes = controller_fixture(tmp_path, monkeypatch, 'fan_pid_boot_grace_dedup_test')
    clock = [100.0]
    monkeypatch.setattr(fan, 'clock_now', lambda: clock[0])
    monkeypatch.setattr(daemon, 'clock_now', lambda: clock[0])
    logs = []
    monkeypatch.setattr(daemon.syslog, 'syslog', lambda prio, msg: logs.append((prio, msg)))

    # Simulate early boot before boot_marker exists: PENDING created at boot start.
    fan.boot_marker().unlink(missing_ok=True)
    fan.verification_path().unlink(missing_ok=True)
    fan.STATE.unlink(missing_ok=True)
    fan.snapshot_marker(fan.STATE).unlink(missing_ok=True)
    ctrl.snapshot_seen = False
    fan.mark_pending()

    # At +60s (> PENDING_TIMEOUT=30s, < BOOT_PENDING_TIMEOUT=180s), normal VyOS boot
    # is still in progress: fans remain fail-safe MAX, status is unknown (not timeout error),
    # and no timeout syslog spam is emitted.
    clock[0] += 60.0
    assert ctrl.step()[0]['fan1'] == 255
    assert fan.configuration_status()['state'] == 'unknown'
    assert not any('reconciliation timed out' in msg for _, msg in logs)

    # At +BOOT_PENDING_TIMEOUT, wedged boot escalates to error and logs ONCE across 3 ticks.
    clock[0] = 100.0 + fan.BOOT_PENDING_TIMEOUT
    for _ in range(3):
        assert ctrl.step()[0]['fan1'] == 255
    assert fan.configuration_status()['state'] == 'error'
    timeout_logs = [msg for _, msg in logs if 'reconciliation timed out' in msg]
    assert len(timeout_logs) == 1

    # Once boot reconciles and PENDING clears, recovery is logged once.
    fan.mark_boot_ready()
    gen = fan.write_snapshot(fan.DEFAULTS)
    fan.write_verification(fan.DEFAULTS, gen)
    fan.PENDING.unlink(missing_ok=True)
    assert ctrl.step()[0]['fan1'] < 255
    assert any('reconciliation recovered' in msg for _, msg in logs)

    # Post-boot commits still strictly time out at PENDING_TIMEOUT (30s) and dedup syslog.
    logs.clear()
    fan.mark_pending()
    clock[0] += fan.PENDING_TIMEOUT
    fan.write_verification(fan.DEFAULTS, gen)
    for _ in range(3):
        assert ctrl.step()[0]['fan1'] == 255
    assert fan.configuration_status()['state'] == 'error'
    timeout_logs = [msg for _, msg in logs if 'reconciliation timed out' in msg]
    assert len(timeout_logs) == 1
