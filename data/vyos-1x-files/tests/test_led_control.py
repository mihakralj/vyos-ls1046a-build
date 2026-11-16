import fcntl
import importlib.util
import io
import json
import multiprocessing
import os
import runpy
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
vyos = types.ModuleType('vyos')
vyos.__path__ = []
vyos.ConfigError = type('ConfigError', (Exception,), {})
spec = importlib.util.spec_from_file_location('vyos.led_control', ROOT / 'python/vyos/led_control.py')
led = importlib.util.module_from_spec(spec)
with patch.dict(sys.modules, {'vyos': vyos, spec.name: led}):
    spec.loader.exec_module(led)


def hold_commit_lock(path, pipe):
    with open(path, 'w', encoding='utf-8') as lock:
        fcntl.lockf(lock, fcntl.LOCK_EX)
        pipe.send('locked')
        pipe.recv()


def probe_commit_lock(path, pipe):
    with open(path, 'w', encoding='utf-8') as lock:
        try:
            fcntl.lockf(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            pipe.send(False)
        else:
            pipe.send(True)


class ConfigSnapshot:
    def __init__(self, candidate, running, exists=True, available=True):
        self.candidate = candidate
        self.running = running
        self.exists = exists
        self.available = available

    def get_config_dict(self, path, get_first_key=False, effective=False):
        return self.running.copy() if effective else self.candidate.copy()

    def get_config_tree(self, effective=False):
        return object() if self.available else None

    def exists_effective(self, path):
        return self.exists


class LedTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.state = root / 'state.json'
        self.leds = root / 'leds'
        self.leds.mkdir()
        self.patches = [patch.dict(sys.modules, {'vyos': vyos, 'vyos.led_control': led}),
                        patch.object(led, 'STATE', self.state),
                        patch.object(led, 'PENDING', root / 'pending', create=True),
                        patch.object(led, 'LOCK', root / 'lock'),
                        patch.object(led, 'LEDS', self.leds),
                        patch.object(led.subprocess, 'run')]
        for item in self.patches:
            item.start()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.stop_patches)

    def stop_patches(self):
        for item in reversed(self.patches):
            item.stop()

    def devices(self):
        for channel in led.CHANNELS:
            path = self.leds / f'status:{channel}'
            path.mkdir()
            (path / 'trigger').write_text('timer')
            (path / 'brightness').write_text('0')

    def owner(self, boot_complete=True):
        boot = types.ModuleType('vyos.utils.boot')
        boot.boot_configuration_complete = lambda: boot_complete
        boot.boot_configuration_success = lambda: False
        utils = types.ModuleType('vyos.utils')
        utils.__path__ = []
        config = types.ModuleType('vyos.config')
        config.Config = object
        modules = {'vyos.utils': utils, 'vyos.utils.boot': boot, 'vyos.config': config}
        with patch.dict(sys.modules, modules):
            return runpy.run_path(str(ROOT / 'src/conf_mode/system_hardware_led.py'))

    def reconcile_running(self, running, allow_default=False):
        with patch.object(led, 'read_running_config', return_value=running), \
             patch.object(led, 'acquire_commit_lock', return_value=(object(), '')), \
             patch.object(led, 'release_commit_lock'):
            led.reconcile_state(allow_default=allow_default)

    def test_validation(self):
        for config in ({}, {'mode': 'auto'}, {'mode': 'off'},
                       {'color': '12345678'}, {'mode': 'auto', 'color': '12345678'},
                       {'mode': 'off', 'color': '12345678'},
                       {'mode': 'fixed', 'color': '12aBCd00'}):
            led.validate(config)
        for config in ({'mode': 'fixed'},
                       {'mode': 'fixed', 'color': '#12345678'},
                       {'mode': 'bad'}):
            with self.assertRaises(vyos.ConfigError):
                led.validate(config)

    def test_invalid_color_rejected_in_every_mode(self):
        for mode in (None, 'auto', 'off', 'fixed'):
            for color in ('', '1234567', '123456789', '#12345678', '1234567G', None, 0):
                with self.subTest(mode=mode, color=color):
                    config = {'color': color}
                    if mode is not None:
                        config['mode'] = mode
                    with self.assertRaises(vyos.ConfigError):
                        led.validate(config)

    def test_color_is_retained_across_mode_transitions(self):
        self.devices()
        owner = self.owner()
        color = 'A1b2C3d4'
        default = {'color': color}
        fixed = {'mode': 'fixed', 'color': color}
        off = {'mode': 'off', 'color': color}
        auto = {'mode': 'auto', 'color': color}

        def commit(candidate, running, exists=True):
            settings = owner['get_config'](ConfigSnapshot(candidate, running, exists=exists))
            owner['verify'](settings)
            owner['apply'](settings)
            self.reconcile_running(candidate, allow_default=True)
            self.assertEqual(json.loads(self.state.read_text()), candidate)

        with patch.object(Path, 'write_text', side_effect=AssertionError('auto must not write LEDs')):
            commit(default, {}, exists=False)
        self.assertTrue(led.auto_allowed())
        commit(fixed, default)
        for channel, value in zip(led.CHANNELS, (161, 178, 195, 212)):
            self.assertEqual((self.leds / f'status:{channel}' / 'brightness').read_text(), str(value))
        commit(off, fixed)
        for channel in led.CHANNELS:
            self.assertEqual((self.leds / f'status:{channel}' / 'brightness').read_text(), '0')
        (self.leds / 'status:red' / 'brightness').write_text('255')
        led.reconcile_state(allow_default=False)
        self.assertEqual((self.leds / 'status:red' / 'brightness').read_text(), '0')
        self.assertEqual(json.loads(self.state.read_text()), off)
        commit(fixed, off)
        for channel, value in zip(led.CHANNELS, (161, 178, 195, 212)):
            self.assertEqual((self.leds / f'status:{channel}' / 'brightness').read_text(), str(value))
        led.subprocess.run.reset_mock()
        with patch.object(Path, 'write_text', side_effect=AssertionError('auto must not write LEDs')):
            commit(auto, fixed)
        self.assertTrue(led.auto_allowed())
        self.assertTrue(led.subprocess.run.call_args_list)
        self.assertTrue(all(call.args[0][1] == 'start' for call in led.subprocess.run.call_args_list))
        with patch.object(Path, 'write_text', side_effect=AssertionError('auto must not write LEDs')):
            led.reconcile_state(allow_default=False)
        self.assertEqual(json.loads(self.state.read_text()), auto)
        commit(fixed, auto)
        for channel, value in zip(led.CHANNELS, (161, 178, 195, 212)):
            self.assertEqual((self.leds / f'status:{channel}' / 'brightness').read_text(), str(value))

    def test_uncommitted_color_only_cannot_override_failed_boot(self):
        led.apply_state({'color': '12345678'}, pending=True)
        self.reconcile_running({})
        self.assertFalse(self.state.exists())
        self.assertFalse(led.auto_allowed())

    def test_effective_color_only_uses_auto_after_failed_boot(self):
        settings = {'color': '12345678'}
        led.apply_state(settings, pending=True)
        self.reconcile_running(settings)
        self.assertEqual(json.loads(self.state.read_text()), settings)
        self.assertTrue(led.auto_allowed())

    def test_show_retained_color_in_nonfixed_modes(self):
        spec = importlib.util.spec_from_file_location('show_led', ROOT / 'src/op_mode/show_hardware_led.py')
        show = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(show)
        for mode in (None, 'auto', 'off'):
            with self.subTest(mode=mode):
                config = {'color': '12aBCd00'}
                if mode is not None:
                    config['mode'] = mode
                led.apply_state(config)
                output = io.StringIO()
                with patch('sys.stdout', output):
                    show.main()
                self.assertIn(f"Mode: {mode or 'auto'}", output.getvalue())
                self.assertIn('Configured color: 12ABCD00', output.getvalue())

    def test_invalid_color_does_not_change_runtime_state(self):
        led.apply_state({'mode': 'fixed', 'color': '12345678'})
        led.subprocess.run.reset_mock()
        with self.assertRaises(vyos.ConfigError):
            led.apply_state({'mode': 'off', 'color': ''}, pending=True)
        self.assertEqual(json.loads(self.state.read_text()), {'mode': 'fixed', 'color': '12345678'})
        self.assertFalse(led.PENDING.exists())
        led.subprocess.run.assert_not_called()

    def test_default_and_deleted_node_start_auto(self):
        led.apply_state()
        led.subprocess.run.assert_called_with(['systemctl', 'start', 'monoledd.service'], check=True)
        led.apply_state({})
        self.assertEqual(json.loads(self.state.read_text()), {})

    def test_fixed_and_rebind(self):
        led.apply_state({'mode': 'fixed', 'color': '12345678'})
        self.devices()
        led.apply_state()
        led.subprocess.run.assert_called_with(['systemctl', 'stop', 'monoledd.service'], check=True)
        for channel, value in zip(led.CHANNELS, (18, 52, 86, 120)):
            path = self.leds / f'status:{channel}'
            self.assertEqual((path / 'trigger').read_text(), 'none')
            self.assertEqual((path / 'brightness').read_text(), str(value))

    def test_off_and_transition_to_auto(self):
        self.devices()
        led.apply_state({'mode': 'off'})
        for channel in led.CHANNELS:
            self.assertEqual((self.leds / f'status:{channel}' / 'brightness').read_text(), '0')
        led.apply_state({'mode': 'auto'})
        led.subprocess.run.assert_called_with(['systemctl', 'start', 'monoledd.service'], check=True)

    def test_show_reports_configured_and_current_color(self):
        self.devices()
        led.apply_state({'mode': 'fixed', 'color': '12345678'})
        spec = importlib.util.spec_from_file_location('show_led', ROOT / 'src/op_mode/show_hardware_led.py')
        show = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(show)
        output = io.StringIO()
        with patch('sys.stdout', output):
            show.main()
        self.assertIn('Mode: fixed', output.getvalue())
        self.assertIn('Current color: 12345678', output.getvalue())

    def test_show_does_not_claim_auto_without_state(self):
        spec = importlib.util.spec_from_file_location('show_led', ROOT / 'src/op_mode/show_hardware_led.py')
        show = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(show)
        output = io.StringIO()
        with patch('sys.stdout', output):
            show.main()
        self.assertIn('Mode: unavailable', output.getvalue())

    def test_auto_condition_blocks_preconfiguration_and_fixed(self):
        boot = types.ModuleType('vyos.utils.boot')
        boot.boot_configuration_complete = lambda: False
        boot.boot_configuration_success = lambda: False
        utils = types.ModuleType('vyos.utils')
        utils.__path__ = []
        config = types.ModuleType('vyos.config')
        config.Config = object
        modules = {'vyos.utils': utils, 'vyos.utils.boot': boot, 'vyos.config': config}
        script = str(ROOT / 'src/conf_mode/system_hardware_led.py')
        with patch.dict(sys.modules, modules), patch.object(sys, 'argv', [script, '--auto-allowed']):
            with self.assertRaises(SystemExit) as result:
                runpy.run_path(script, run_name='__main__')
            self.assertEqual(result.exception.code, 1)
            self.state.write_text('{"mode":"fixed","color":"01020304"}')
            with self.assertRaises(SystemExit) as result:
                runpy.run_path(script, run_name='__main__')
            self.assertEqual(result.exception.code, 1)
            self.state.write_text('{"mode":"auto"}')
            with self.assertRaises(SystemExit) as result:
                runpy.run_path(script, run_name='__main__')
            self.assertEqual(result.exception.code, 0)

    def test_failed_boot_never_defaults_to_auto(self):
        boot = types.ModuleType('vyos.utils.boot')
        boot.boot_configuration_complete = lambda: True
        boot.boot_configuration_success = lambda: False
        utils = types.ModuleType('vyos.utils')
        utils.__path__ = []
        config = types.ModuleType('vyos.config')
        config.Config = object
        modules = {'vyos.utils': utils, 'vyos.utils.boot': boot, 'vyos.config': config}
        script = str(ROOT / 'src/conf_mode/system_hardware_led.py')
        with patch.dict(sys.modules, modules):
            with patch.object(sys, 'argv', [script, '--reconcile']):
                runpy.run_path(script, run_name='__main__')
            self.assertFalse(self.state.exists())
            led.subprocess.run.assert_called_once_with(['systemctl', 'stop', 'monoledd.service'], check=True)
            with patch.object(sys, 'argv', [script, '--auto-allowed']):
                with self.assertRaises(SystemExit) as result:
                    runpy.run_path(script, run_name='__main__')
                self.assertEqual(result.exception.code, 1)

    def test_failed_boot_without_led_state_remains_off_after_rebind(self):
        self.devices()
        (self.leds / 'status:red' / 'brightness').write_text('255')
        led.reconcile_state(allow_default=False)
        self.assertFalse(self.state.exists())
        self.assertEqual((self.leds / 'status:red' / 'brightness').read_text(), '0')
        self.assertFalse(led.auto_allowed())

    def test_rebind_timer_retries_coalesced_device_events(self):
        project = ROOT.parents[1]
        timer = (project / 'board/systemd/vyos-led-reconcile.timer').read_text()
        service = (project / 'board/systemd/vyos-led-reconcile.service').read_text()
        rule = (project / 'board/scripts/10-lp5812-monoledd.rules').read_text()
        tmpfiles = (project / 'board/systemd/monoledd.tmpfiles').read_text()
        installer = (project / 'bin/ci-setup-vyos-build.sh').read_text()
        self.assertIn('OnUnitInactiveSec=', timer)
        self.assertIn('Unit=vyos-led-reconcile.service', timer)
        self.assertNotIn('RemainAfterExit=yes', service)
        self.assertIn('vyos-led-reconcile.timer', rule)
        self.assertIn('vyos-led-reconcile.timer', tmpfiles)
        self.assertIn('board/systemd/vyos-led-reconcile.timer', installer)
        led.apply_state({'mode': 'fixed', 'color': '12345678'})
        self.devices()
        led.apply_state()
        self.assertEqual((self.leds / 'status:red' / 'brightness').read_text(), '18')

    def test_timer_corrects_failed_commit_after_owner_apply(self):
        self.devices()
        old = {'mode': 'fixed', 'color': '10203040'}
        led.apply_state(old)
        boot = types.ModuleType('vyos.utils.boot')
        boot.boot_configuration_complete = lambda: True
        boot.boot_configuration_success = lambda: True
        utils = types.ModuleType('vyos.utils')
        utils.__path__ = []
        config = types.ModuleType('vyos.config')
        config.Config = object
        modules = {'vyos.utils': utils, 'vyos.utils.boot': boot, 'vyos.config': config}
        script = str(ROOT / 'src/conf_mode/system_hardware_led.py')
        with patch.dict(sys.modules, modules):
            owner = runpy.run_path(script)
            owner['apply']({'mode': 'auto'})
            with patch.object(led, 'read_running_config', return_value=old, create=True), \
                 patch.object(led, 'acquire_commit_lock', return_value=(object(), ''), create=True), \
                 patch.object(led, 'release_commit_lock', create=True), \
                 patch.object(sys, 'argv', [script, '--reconcile']):
                runpy.run_path(script, run_name='__main__')
        self.assertEqual(json.loads(self.state.read_text()), old)
        self.assertEqual((self.leds / 'status:red' / 'brightness').read_text(), '16')

    def test_failed_commit_reconciles_to_effective_config(self):
        self.devices()
        old = {'mode': 'fixed', 'color': '10203040'}
        led.apply_state(old)
        led.apply_state({'mode': 'auto'}, pending=True)
        self.assertTrue(led.PENDING.exists())
        with patch.object(led, 'read_running_config', return_value=old), \
             patch.object(led, 'acquire_commit_lock', return_value=(object(), '')), \
             patch.object(led, 'release_commit_lock'):
            led.reconcile_state(allow_default=True)
        self.assertEqual(json.loads(self.state.read_text()), old)
        self.assertEqual((self.leds / 'status:red' / 'brightness').read_text(), '16')
        self.assertFalse(led.PENDING.exists())

    def test_failed_fixed_candidate_restores_effective_off(self):
        self.devices()
        led.apply_state({'mode': 'off'})
        led.apply_state({'mode': 'fixed', 'color': '11223344'}, pending=True)
        self.assertEqual((self.leds / 'status:red' / 'brightness').read_text(), '17')
        with patch.object(led, 'read_running_config', return_value={'mode': 'off'}), \
             patch.object(led, 'acquire_commit_lock', return_value=(object(), '')), \
             patch.object(led, 'release_commit_lock'):
            led.reconcile_state(allow_default=True)
        self.assertEqual(json.loads(self.state.read_text()), {'mode': 'off'})
        for channel in led.CHANNELS:
            self.assertEqual((self.leds / f'status:{channel}' / 'brightness').read_text(), '0')

    def test_partial_commit_retains_effective_candidate(self):
        self.devices()
        led.apply_state({'mode': 'off'})
        candidate = {'mode': 'fixed', 'color': '11223344'}
        led.apply_state(candidate, pending=True)
        with patch.object(led, 'read_running_config', return_value=candidate), \
             patch.object(led, 'acquire_commit_lock', return_value=(object(), '')), \
             patch.object(led, 'release_commit_lock'):
            led.reconcile_state(allow_default=True)
        self.assertEqual(json.loads(self.state.read_text()), candidate)
        self.assertFalse(led.PENDING.exists())

    def test_failed_boot_successful_led_deletion_restores_auto(self):
        old = {'mode': 'fixed', 'color': '10203040'}
        led.apply_state(old)
        boot = types.ModuleType('vyos.utils.boot')
        boot.boot_configuration_complete = lambda: True
        boot.boot_configuration_success = lambda: False
        utils = types.ModuleType('vyos.utils')
        utils.__path__ = []
        config = types.ModuleType('vyos.config')
        config.Config = object
        modules = {'vyos.utils': utils, 'vyos.utils.boot': boot, 'vyos.config': config}

        class Snapshot:
            def get_config_dict(self, path, get_first_key=False, effective=False):
                return old if effective else {}

            def get_config_tree(self, effective=False):
                return object()

            def exists_effective(self, path):
                return True

        script = str(ROOT / 'src/conf_mode/system_hardware_led.py')
        with patch.dict(sys.modules, modules):
            owner = runpy.run_path(script)
            settings = owner['get_config'](Snapshot())
            owner['verify'](settings)
            owner['apply'](settings)
            with patch.object(led, 'read_running_config', return_value={}), \
                 patch.object(led, 'acquire_commit_lock', return_value=(object(), '')), \
                 patch.object(led, 'release_commit_lock'), \
                 patch.object(sys, 'argv', [script, '--reconcile']):
                runpy.run_path(script, run_name='__main__')
        self.assertEqual(json.loads(self.state.read_text()), {})
        self.assertFalse(led.PENDING.exists())
        self.assertTrue(led.auto_allowed())
        led.subprocess.run.assert_called_with(['systemctl', 'start', 'monoledd.service'], check=True)

    def test_postboot_deletion_with_no_runtime_state_starts_auto(self):
        old = {'mode': 'fixed', 'color': '10203040'}
        owner = self.owner()
        settings = owner['get_config'](ConfigSnapshot({}, old))
        owner['apply'](settings)
        self.reconcile_running({})
        self.assertEqual(json.loads(self.state.read_text()), {})
        self.assertTrue(led.auto_allowed())

    def test_failed_boot_unsettled_led_then_successful_delete_starts_auto(self):
        old = {'mode': 'fixed', 'color': '10203040'}
        boot_owner = self.owner(boot_complete=False)
        boot_owner['apply'](boot_owner['get_config'](ConfigSnapshot(old, {}, exists=False)))
        self.assertFalse(led.pending_allows_default())
        self.assertFalse(led.auto_allowed())
        owner = self.owner()
        owner['apply'](owner['get_config'](ConfigSnapshot({}, old)))
        self.reconcile_running({})
        self.assertEqual(json.loads(self.state.read_text()), {})
        self.assertTrue(led.auto_allowed())

    def test_postboot_deletion_of_empty_led_node_starts_auto(self):
        owner = self.owner()
        owner['apply'](owner['get_config'](ConfigSnapshot({}, {}, exists=True)))
        self.reconcile_running({})
        self.assertEqual(json.loads(self.state.read_text()), {})
        self.assertTrue(led.auto_allowed())

    def test_boot_time_deletion_remains_fail_closed(self):
        led.apply_state({'mode': 'off'})
        owner = self.owner(boot_complete=False)
        settings = owner['get_config'](ConfigSnapshot({}, {'mode': 'off'}))
        owner['apply'](settings)
        self.reconcile_running({})
        self.assertFalse(self.state.exists())
        self.assertFalse(led.auto_allowed())

    def test_failed_postboot_deletion_keeps_running_mode(self):
        old = {'mode': 'fixed', 'color': '10203040'}
        owner = self.owner()
        settings = owner['get_config'](ConfigSnapshot({}, old))
        owner['apply'](settings)
        self.reconcile_running(old)
        self.assertEqual(json.loads(self.state.read_text()), old)
        self.assertFalse(led.auto_allowed())

    def test_postboot_deletion_without_running_evidence_stays_off(self):
        for snapshot in (ConfigSnapshot({}, {}, exists=False),
                         ConfigSnapshot({}, {'mode': 'off'}, available=False)):
            with self.subTest(available=snapshot.available, exists=snapshot.exists):
                owner = self.owner()
                owner['apply'](owner['get_config'](snapshot))
                self.reconcile_running({})
                self.assertFalse(self.state.exists())
                self.assertFalse(led.auto_allowed())

    def test_corrupt_pending_metadata_does_not_authorize_auto(self):
        self.state.write_text('{}')
        led.PENDING.write_text('{broken')
        self.reconcile_running({})
        self.assertFalse(self.state.exists())
        self.assertFalse(led.auto_allowed())

    def test_pending_metadata_remains_atomic_when_replace_fails(self):
        led.mark_pending(True)
        with patch.object(led.os, 'replace', side_effect=OSError('rename failed')):
            with self.assertRaises(OSError):
                led.mark_pending(False)
        self.assertTrue(led.pending_allows_default())
        self.assertEqual(led.PENDING.stat().st_mode & 0o777, 0o600)
        self.assertEqual(list(led.PENDING.parent.glob('.pending.*')), [])

    def test_postboot_deletion_retries_after_daemon_start_failure(self):
        owner = self.owner()
        owner['apply'](owner['get_config'](ConfigSnapshot({}, {'mode': 'off'})))

        def fail_start(args, **kwargs):
            if args[1] == 'start':
                raise subprocess.CalledProcessError(1, args)

        led.subprocess.run.side_effect = fail_start
        with self.assertRaises(subprocess.CalledProcessError):
            self.reconcile_running({})
        self.assertTrue(led.PENDING.exists())
        self.assertTrue(led.pending_allows_default())
        self.assertFalse(led.auto_allowed())
        led.subprocess.run.side_effect = None
        self.reconcile_running({})
        self.assertTrue(led.auto_allowed())

    def test_postboot_deletion_reapplies_auto_on_rebind(self):
        owner = self.owner()
        owner['apply'](owner['get_config'](ConfigSnapshot({}, {'mode': 'off'})))
        self.reconcile_running({})
        led.subprocess.run.reset_mock()
        led.reconcile_state(allow_default=False)
        led.subprocess.run.assert_called_once_with(['systemctl', 'start', 'monoledd.service'], check=True)

    def test_failed_update_after_confirmed_postboot_deletion_restores_auto(self):
        owner = self.owner()
        owner['apply'](owner['get_config'](ConfigSnapshot({}, {'mode': 'off'})))
        self.reconcile_running({})
        candidate = {'mode': 'fixed', 'color': '01020304'}
        owner['apply'](owner['get_config'](ConfigSnapshot(candidate, {}, exists=False)))
        self.reconcile_running({})
        self.assertEqual(json.loads(self.state.read_text()), {})
        self.assertTrue(led.auto_allowed())

    def test_overlapping_postboot_deletion_and_failed_update_restores_auto(self):
        owner = self.owner()
        owner['apply'](owner['get_config'](ConfigSnapshot({}, {'mode': 'off'})))
        candidate = {'mode': 'fixed', 'color': '01020304'}
        owner['apply'](owner['get_config'](ConfigSnapshot(candidate, {}, exists=False)))
        self.reconcile_running({})
        self.assertEqual(json.loads(self.state.read_text()), {})
        self.assertTrue(led.auto_allowed())

    def test_overlapping_update_cannot_reuse_uncommitted_deletion(self):
        owner = self.owner()
        owner['apply'](owner['get_config'](ConfigSnapshot({}, {'mode': 'off'})))
        candidate = {'mode': 'fixed', 'color': '01020304'}
        owner['apply'](owner['get_config'](ConfigSnapshot(candidate, {'mode': 'off'})))
        self.reconcile_running({})
        self.assertFalse(self.state.exists())
        self.assertFalse(led.auto_allowed())

    def test_committed_deletion_recovers_default_auto(self):
        led.apply_state({'mode': 'off'})
        led.apply_state({}, pending=True)
        with patch.object(led, 'read_running_config', return_value={}), \
             patch.object(led, 'acquire_commit_lock', return_value=(object(), '')), \
             patch.object(led, 'release_commit_lock'):
            led.reconcile_state(allow_default=True)
        self.assertEqual(json.loads(self.state.read_text()), {})
        self.assertFalse(led.PENDING.exists())
        self.assertTrue(led.auto_allowed())
        led.subprocess.run.assert_called_with(['systemctl', 'start', 'monoledd.service'], check=True)

    def test_crash_after_pending_marker_recovers_running_state(self):
        led.apply_state({'mode': 'auto'})
        led.mark_pending()
        self.assertFalse(led.auto_allowed())
        with patch.object(led, 'read_running_config', return_value={'mode': 'auto'}), \
             patch.object(led, 'acquire_commit_lock', return_value=(object(), '')), \
             patch.object(led, 'release_commit_lock'):
            led.reconcile_state(allow_default=True)
        self.assertTrue(led.auto_allowed())
        self.assertFalse(led.PENDING.exists())

    def test_settled_rebind_does_not_lock_vyos_commit(self):
        self.devices()
        led.apply_state({'mode': 'fixed', 'color': '12345678'})
        (self.leds / 'status:red' / 'brightness').write_text('0')
        with patch.object(led, 'acquire_commit_lock') as acquire:
            led.reconcile_state(allow_default=True)
        acquire.assert_not_called()
        self.assertEqual((self.leds / 'status:red' / 'brightness').read_text(), '18')

    def test_pending_candidate_not_reapplied_while_commit_locked(self):
        led.apply_state({'mode': 'fixed', 'color': '01020304'}, pending=True)
        led.subprocess.run.reset_mock()
        with patch.object(led, 'acquire_commit_lock', return_value=(None, 'busy')), \
             patch.object(led, 'read_running_config') as read:
            led.reconcile_state(allow_default=True)
        read.assert_not_called()
        led.subprocess.run.assert_not_called()
        self.assertTrue(led.PENDING.exists())

    def test_pending_auto_is_gated_until_running_config_confirmed(self):
        allowed_at_start = []

        def track_start(args, **kwargs):
            if args[1] == 'start':
                allowed_at_start.append(led.auto_allowed())

        led.subprocess.run.side_effect = track_start
        led.apply_state({'mode': 'auto'}, pending=True)
        self.assertFalse(led.auto_allowed())
        with patch.object(led, 'read_running_config', return_value={'mode': 'auto'}), \
             patch.object(led, 'acquire_commit_lock', return_value=(object(), '')), \
             patch.object(led, 'release_commit_lock'):
            led.reconcile_state(allow_default=True)
        self.assertEqual(allowed_at_start, [False, True])
        self.assertTrue(led.auto_allowed())

    def test_show_marks_unconfirmed_mode_pending(self):
        led.apply_state({'mode': 'off'}, pending=True)
        spec = importlib.util.spec_from_file_location('show_led', ROOT / 'src/op_mode/show_hardware_led.py')
        show = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(show)
        output = io.StringIO()
        with patch('sys.stdout', output):
            show.main()
        self.assertIn('Mode: off (pending)', output.getvalue())

    def test_failed_boot_clears_uncommitted_led_state(self):
        self.devices()
        led.apply_state({'mode': 'fixed', 'color': '01020304'}, pending=True)
        led.subprocess.run.reset_mock()
        with patch.object(led, 'read_running_config', return_value={}), \
             patch.object(led, 'acquire_commit_lock', return_value=(object(), '')), \
             patch.object(led, 'release_commit_lock'):
            led.reconcile_state(allow_default=False)
        self.assertFalse(self.state.exists())
        self.assertFalse(led.PENDING.exists())
        for channel in led.CHANNELS:
            self.assertEqual((self.leds / f'status:{channel}' / 'brightness').read_text(), '0')
        led.subprocess.run.assert_called_once_with(['systemctl', 'stop', 'monoledd.service'], check=True)

    def test_unavailable_commit_lock_reports_and_retains_pending(self):
        led.apply_state({'mode': 'auto'}, pending=True)
        with patch.object(led, 'acquire_commit_lock',
                          return_value=(None, 'Critical error opening commit lock file')):
            with self.assertRaisesRegex(vyos.ConfigError, 'Critical error opening commit lock file'):
                led.reconcile_state(allow_default=True)
        self.assertTrue(led.PENDING.exists())
        self.assertFalse(led.auto_allowed())

    @unittest.skipUnless(os.getenv('VYOS_1X_SOURCE'), 'Pinned VyOS source is unavailable')
    def test_reconcile_uses_real_vyos_posix_commit_lock(self):
        source = Path(os.environ['VYOS_1X_SOURCE'])
        spec = importlib.util.spec_from_file_location('vyos.utils.commit', source / 'python/vyos/utils/commit.py')
        commit = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(commit)
        defaults = types.ModuleType('vyos.defaults')
        defaults.commit_lock = str(Path(self.temp.name) / 'commit.lock')
        utils = types.ModuleType('vyos.utils')
        utils.__path__ = []
        modules = {'vyos.defaults': defaults, 'vyos.utils': utils, 'vyos.utils.commit': commit}
        led.apply_state({'mode': 'fixed', 'color': '01020304'}, pending=True)
        led.subprocess.run.reset_mock()
        context = multiprocessing.get_context('fork')

        def assert_commit_blocked():
            probe_parent, probe_child = context.Pipe()
            probe = context.Process(target=probe_commit_lock, args=(defaults.commit_lock, probe_child))
            probe.start()
            try:
                self.assertTrue(probe_parent.poll(5))
                self.assertFalse(probe_parent.recv())
                probe.join(5)
                self.assertFalse(probe.is_alive())
            finally:
                if probe.is_alive():
                    probe.terminate()
                    probe.join(5)
                probe_parent.close()
                probe_child.close()

        def read_running():
            assert_commit_blocked()
            return {'mode': 'off'}

        original_apply = led.apply_hardware

        def apply_while_locked(config):
            assert_commit_blocked()
            original_apply(config)

        parent, child = context.Pipe()
        process = context.Process(target=hold_commit_lock, args=(defaults.commit_lock, child))
        process.start()
        try:
            self.assertTrue(parent.poll(5))
            self.assertEqual(parent.recv(), 'locked')
            with patch.dict(sys.modules, modules), \
                 patch.object(led, 'read_running_config', side_effect=read_running) as read, \
                 patch.object(led, 'apply_hardware', side_effect=apply_while_locked):
                led.reconcile_state(allow_default=True)
                read.assert_not_called()
                led.subprocess.run.assert_not_called()
                self.assertTrue(led.PENDING.exists())
                parent.send('release')
                process.join(5)
                self.assertFalse(process.is_alive())
                led.reconcile_state(allow_default=True)
                read.assert_called_once()
                self.assertEqual(json.loads(self.state.read_text()), {'mode': 'off'})
                self.assertFalse(led.PENDING.exists())
        finally:
            if process.is_alive():
                process.terminate()
                process.join(5)
            parent.close()
            child.close()

    @unittest.skipUnless(os.getenv('VYOS_1X_SOURCE'), 'Pinned VyOS source is unavailable')
    def test_running_lookup_uses_pinned_vyos_effective_tree(self):
        self.patches[-1].stop()
        code = (
            'import importlib.util, json, sys\n'
            'import vyos.config as config\n'
            'from vyos.configsource import ConfigSource\n'
            'config.multi_to_list = lambda path, data: data\n'
            'class Tree:\n'
            '    def __init__(self, value): self.value = value\n'
            '    def to_json(self): return json.dumps(self.value)\n'
            'source = ConfigSource()\n'
            'source._running_config = Tree({"system":{"hardware":{"led":{"mode":"off"}}}})\n'
            'source._session_config = Tree({"system":{"hardware":{"led":{"mode":"auto"}}}})\n'
            'running = config.Config(config_source=source)\n'
            'config.Config = lambda: running\n'
            'spec = importlib.util.spec_from_file_location("led_under_test", sys.argv[1])\n'
            'module = importlib.util.module_from_spec(spec)\n'
            'spec.loader.exec_module(module)\n'
            'print(json.dumps(module.read_running_config()))\n')
        env = os.environ.copy()
        env['PYTHONPATH'] = str(Path(os.environ['VYOS_1X_SOURCE']) / 'python')
        result = subprocess.run([sys.executable, '-c', code, str(ROOT / 'python/vyos/led_control.py')],
                                env=env, capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(result.stdout), {'mode': 'off'})

    @unittest.skipUnless(os.getenv('VYOS_1X_SOURCE'), 'Pinned VyOS source is unavailable')
    def test_postboot_delete_detection_uses_pinned_vyos_snapshots(self):
        self.patches[-1].stop()
        code = (
            'import importlib.util, json, sys\n'
            'import vyos.config as config\n'
            'from vyos.configsource import ConfigSource\n'
            'config.multi_to_list = lambda path, data: data\n'
            'class Tree:\n'
            '    def __init__(self, value): self.value = value\n'
            '    def to_json(self): return json.dumps(self.value)\n'
            '    def exists(self, path):\n'
            '        node = self.value\n'
            '        for key in path:\n'
            '            if not isinstance(node, dict) or key not in node: return False\n'
            '            node = node[key]\n'
            '        return True\n'
            'source = ConfigSource()\n'
            'source._running_config = Tree({"system":{"hardware":{"led":{"mode":"off"}}}})\n'
            'source._session_config = Tree({"system":{"hardware":{}}})\n'
            'conf = config.Config(config_source=source)\n'
            'spec = importlib.util.spec_from_file_location("vyos.led_control", sys.argv[1])\n'
            'led = importlib.util.module_from_spec(spec)\n'
            'sys.modules[spec.name] = led\n'
            'spec.loader.exec_module(led)\n'
            'spec = importlib.util.spec_from_file_location("led_owner", sys.argv[2])\n'
            'owner = importlib.util.module_from_spec(spec)\n'
            'spec.loader.exec_module(owner)\n'
            'owner.boot_configuration_complete = lambda: True\n'
            'after = owner.get_config(conf)\n'
            'owner.boot_configuration_complete = lambda: False\n'
            'during = owner.get_config(conf)\n'
            'print(json.dumps([dict(after), after.post_boot_delete, after.running_default, '
            'during.post_boot_delete]))\n')
        env = os.environ.copy()
        env['PYTHONPATH'] = str(Path(os.environ['VYOS_1X_SOURCE']) / 'python')
        result = subprocess.run([sys.executable, '-c', code,
                                 str(ROOT / 'python/vyos/led_control.py'),
                                 str(ROOT / 'src/conf_mode/system_hardware_led.py')],
                                env=env, capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(result.stdout), [{}, True, False, False])

    def test_missing_running_tree_does_not_mean_default_auto(self):
        config = types.ModuleType('vyos.config')
        config.Config = lambda: types.SimpleNamespace(get_config_tree=lambda effective: None)
        with patch.dict(sys.modules, {'vyos.config': config}):
            with self.assertRaises(vyos.ConfigError):
                led.read_running_config()

    def test_failed_boot_preserves_only_effective_explicit_led_state(self):
        self.devices()
        led.apply_state({'mode': 'auto'}, pending=True)
        effective = {'mode': 'fixed', 'color': '12345678'}
        with patch.object(led, 'read_running_config', return_value=effective), \
             patch.object(led, 'acquire_commit_lock', return_value=(object(), '')), \
             patch.object(led, 'release_commit_lock'):
            led.reconcile_state(allow_default=False)
        self.assertEqual(json.loads(self.state.read_text()), effective)
        self.assertEqual((self.leds / 'status:red' / 'brightness').read_text(), '18')
        self.assertFalse(led.PENDING.exists())

    def test_confirmed_write_failure_never_restores_stale_candidate(self):
        self.devices()
        led.apply_state({'mode': 'auto'}, pending=True)
        effective = {'mode': 'fixed', 'color': '10203040'}
        original = Path.write_text

        def fail_green(path, data, *args, **kwargs):
            if path == self.leds / 'status:green' / 'brightness' and data == '32':
                raise OSError('injected LED write failure')
            return original(path, data, *args, **kwargs)

        with patch.object(led, 'read_running_config', return_value=effective), \
             patch.object(led, 'acquire_commit_lock', return_value=(object(), '')), \
             patch.object(led, 'release_commit_lock') as release, \
             patch.object(Path, 'write_text', fail_green):
            with self.assertRaises(OSError):
                led.reconcile_state(allow_default=True)
            release.assert_called_once()
        self.assertEqual(json.loads(self.state.read_text()), effective)
        self.assertTrue(led.PENDING.exists())
        self.assertFalse(led.auto_allowed())

    def test_confirmed_auto_start_failure_keeps_retry_gate(self):
        led.apply_state({'mode': 'off'})
        led.apply_state({'mode': 'auto'}, pending=True)

        def fail_start(args, **kwargs):
            if args[1] == 'start':
                raise subprocess.CalledProcessError(1, args)

        led.subprocess.run.side_effect = fail_start
        with patch.object(led, 'read_running_config', return_value={'mode': 'auto'}), \
             patch.object(led, 'acquire_commit_lock', return_value=(object(), '')), \
             patch.object(led, 'release_commit_lock') as release:
            with self.assertRaises(subprocess.CalledProcessError):
                led.reconcile_state(allow_default=True)
            release.assert_called_once()
        self.assertTrue(led.PENDING.exists())
        self.assertFalse(led.auto_allowed())

    def test_unreadable_running_config_invalidates_candidate_and_retries(self):
        led.apply_state({'mode': 'auto'}, pending=True)
        led.subprocess.run.reset_mock()
        with patch.object(led, 'read_running_config', side_effect=OSError('backend unavailable')), \
             patch.object(led, 'acquire_commit_lock', return_value=(object(), '')), \
             patch.object(led, 'release_commit_lock'):
            with self.assertRaises(vyos.ConfigError):
                led.reconcile_state(allow_default=True)
        self.assertFalse(self.state.exists())
        self.assertTrue(led.PENDING.exists())
        led.subprocess.run.assert_called_once_with(['systemctl', 'stop', 'monoledd.service'], check=True)
        with patch.object(led, 'read_running_config', return_value={'mode': 'off'}), \
             patch.object(led, 'acquire_commit_lock', return_value=(object(), '')), \
             patch.object(led, 'release_commit_lock'):
            led.reconcile_state(allow_default=True)
        self.assertEqual(json.loads(self.state.read_text()), {'mode': 'off'})
        self.assertFalse(led.PENDING.exists())

    def test_default_not_inferred_after_boot_failure(self):
        led.apply_state(allow_default=False)
        self.assertFalse(self.state.exists())
        led.subprocess.run.assert_not_called()

    def test_brightness_error_restores_previous_state_and_color(self):
        self.devices()
        old = {'mode': 'fixed', 'color': '10203040'}
        led.apply_state(old)
        original = Path.write_text

        def fail_green(path, data, *args, **kwargs):
            if path == self.leds / 'status:green' / 'brightness' and data == '34':
                raise OSError('injected LED write failure')
            return original(path, data, *args, **kwargs)

        with patch.object(Path, 'write_text', fail_green):
            with self.assertRaises(OSError):
                led.apply_state({'mode': 'fixed', 'color': '11223344'})
        self.assertEqual(json.loads(self.state.read_text()), old)
        for channel, value in zip(led.CHANNELS, (16, 32, 48, 64)):
            self.assertEqual((self.leds / f'status:{channel}' / 'brightness').read_text(), str(value))

    def test_service_start_error_restores_previous_off_state(self):
        self.devices()
        old = {'mode': 'off'}
        led.apply_state(old)

        def fail_start(args, **kwargs):
            if args[1] == 'start':
                raise subprocess.CalledProcessError(1, args)

        led.subprocess.run.side_effect = fail_start
        with self.assertRaises(subprocess.CalledProcessError):
            led.apply_state({'mode': 'auto'})
        self.assertEqual(json.loads(self.state.read_text()), old)
        self.assertEqual((self.leds / 'status:red' / 'brightness').read_text(), '0')

    def test_invalid_runtime_state_fails_closed(self):
        self.state.write_text('{broken')
        with self.assertRaises(vyos.ConfigError):
            led.auto_allowed()
        with self.assertRaises(vyos.ConfigError):
            led.apply_state()
        led.subprocess.run.assert_not_called()

    def test_state_not_group_writable_under_configd_umask(self):
        old_umask = os.umask(0o002)
        try:
            led.apply_state({'mode': 'off'}, pending=True)
        finally:
            os.umask(old_umask)
        self.assertEqual(self.state.stat().st_mode & 0o777, 0o644)
        self.assertEqual(led.PENDING.stat().st_mode & 0o777, 0o600)
        self.assertEqual(led.LOCK.stat().st_mode & 0o777, 0o600)

    def test_config_script_has_shebang_without_source_execute_bit(self):
        script = ROOT / 'src/conf_mode/system_hardware_led.py'
        self.assertEqual(script.read_text().splitlines()[0], '#!/usr/bin/env python3')
        self.assertEqual(script.stat().st_mode & 0o111, 0)

    @unittest.skipUnless(os.getenv('VYOS_1X_SOURCE'), 'Pinned VyOS source is unavailable')
    def test_pinned_vyos_builds_led_command_templates(self):
        self.patches[-1].stop()
        source = Path(os.environ['VYOS_1X_SOURCE'])
        output = Path(self.temp.name) / 'templates'
        output.mkdir()
        subprocess.run([sys.executable, str(source / 'scripts/build-command-templates'),
                        str(ROOT / 'interface-definitions/system_hardware_led.xml.in'),
                        str(source / 'schema/interface_definition.rng'), str(output)], check=True)
        node = (output / 'system/hardware/led/node.def').read_text()
        self.assertIn('${vyshim} ${vyos_conf_scripts_dir}/system_hardware_led.py', node)
        subprocess.run([sys.executable, str(source / 'scripts/build-command-op-templates'),
                        str(ROOT / 'op-mode-definitions/show-hardware-led.xml.in'),
                        str(source / 'schema/op-mode-definition.rng'), str(output)], check=True)
        node = (output / 'show/hardware/led/node.def').read_text()
        self.assertIn('show_hardware_led.py', node)

    @unittest.skipUnless(shutil.which('dh_fixperms') and shutil.which('fakeroot'),
                         'Debhelper or fakeroot is unavailable')
    def test_packaged_config_script_runs_without_vyshim(self):
        self.patches[-1].stop()
        root = Path(self.temp.name)
        debian = root / 'debian'
        script_dir = debian / 'vyos-led-test/usr/libexec/vyos/conf_mode'
        script_dir.mkdir(parents=True)
        (debian / 'control').write_text(
            'Source: vyos-led-test\nSection: misc\nPriority: optional\n'
            'Maintainer: Test <test@example.invalid>\nRules-Requires-Root: no\n\n'
            'Package: vyos-led-test\nArchitecture: all\nDescription: test\n')
        (debian / 'compat').write_text('13\n')
        (debian / 'changelog').write_text(
            'vyos-led-test (1) unstable; urgency=medium\n\n  * Test.\n\n'
            ' -- Test <test@example.invalid>  Mon, 16 Nov 2026 00:00:00 +0000\n')
        script = script_dir / 'system_hardware_led.py'
        shutil.copyfile(ROOT / 'src/conf_mode/system_hardware_led.py', script)
        script.chmod(0o644)
        subprocess.run(['fakeroot', 'dh_fixperms', '-pvyos-led-test'], cwd=root, check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertTrue(script.stat().st_mode & 0o111)
        package = root / 'fake/vyos'
        (package / 'utils').mkdir(parents=True)
        (package / '__init__.py').write_text('class ConfigError(Exception): pass\n')
        (package / 'utils/__init__.py').write_text('')
        (package / 'utils/boot.py').write_text(
            'def boot_configuration_complete(): return True\n'
            'def boot_configuration_success(): return True\n')
        (package / 'config.py').write_text(
            'class Config:\n'
            '    def get_config_dict(self, *args, **kwargs): return {"mode": "fixed"}\n'
            '    def get_config_tree(self, effective=False): return None\n')
        (package / 'led_control.py').write_text(
            'from vyos import ConfigError\n'
            'def validate(config): raise ConfigError("direct fallback reached")\n'
            'def apply_state(config, pending=False): pass\n'
            'def reconcile_state(allow_default=True): pass\n'
            'def auto_allowed(): return False\n')
        env = os.environ.copy()
        env['PYTHONPATH'] = str(package.parent)
        result = subprocess.run([str(script)], env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn('direct fallback reached', result.stderr)

    def test_stop_failure_does_not_write_led(self):
        self.devices()
        led.apply_state({'mode': 'auto'})

        def fail_stop(args, **kwargs):
            if args[1] == 'stop':
                raise RuntimeError('stop failed')

        led.subprocess.run.side_effect = fail_stop
        with self.assertRaises(RuntimeError):
            led.apply_state({'mode': 'fixed', 'color': '01020304'})
        self.assertEqual(json.loads(self.state.read_text()), {'mode': 'auto'})
        self.assertEqual((self.leds / 'status:red' / 'trigger').read_text(), 'timer')

    def test_unchanged_fixed_state_does_not_rewrite_sysfs(self):
        self.devices()
        led.apply_state({'mode': 'fixed', 'color': '12345678'})
        with patch.object(Path, 'write_text', side_effect=AssertionError('unexpected sysfs write')):
            led.apply_state()

    def test_rollback_failure_reports_both_errors(self):
        self.devices()
        led.apply_state({'mode': 'off'})
        led.subprocess.run.side_effect = RuntimeError('systemctl failed')
        with self.assertRaisesRegex(vyos.ConfigError, 'apply failed:.*restoration failed:'):
            led.apply_state({'mode': 'auto'})
        self.assertEqual(json.loads(self.state.read_text()), {'mode': 'off'})


if __name__ == '__main__':
    unittest.main()
