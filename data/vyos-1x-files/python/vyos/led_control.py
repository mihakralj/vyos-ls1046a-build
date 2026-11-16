import fcntl
import json
import os
import re
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path

from vyos import ConfigError

STATE = Path('/run/vyos-led-state.json')
PENDING = Path('/run/vyos-led-pending')
LOCK = Path('/run/vyos-led.lock')
LEDS = Path('/sys/class/leds')
CHANNELS = ('red', 'green', 'blue', 'white')


def validate(config):
    if not isinstance(config, dict):
        raise ConfigError('Invalid LED configuration')
    mode = config.get('mode', 'auto')
    color = config.get('color')
    if mode not in ('auto', 'fixed', 'off'):
        raise ConfigError('Invalid LED mode')
    if mode == 'fixed' and not color:
        raise ConfigError('LED fixed mode requires a color')
    if 'color' in config and (not isinstance(color, str) or not re.fullmatch(r'[0-9a-fA-F]{8}', color)):
        raise ConfigError('LED color must be eight hexadecimal digits RRGGBBWW')


def read_state():
    try:
        config = json.loads(STATE.read_text())
    except FileNotFoundError:
        return {'mode': 'auto'}
    except json.JSONDecodeError as error:
        raise ConfigError(f'Invalid LED runtime state: {error}') from error
    validate(config)
    return config


def auto_allowed():
    if PENDING.exists() or not STATE.exists():
        return False
    config = read_state()
    return not PENDING.exists() and config.get('mode', 'auto') == 'auto'


@contextmanager
def state_lock():
    fd = os.open(LOCK, os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(fd, 'r+') as lock:
        os.fchmod(lock.fileno(), 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def write_json(path, data, mode):
    fd, name = tempfile.mkstemp(prefix=f'.{path.name}.', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as output:
            json.dump(data, output)
            os.fchmod(output.fileno(), mode)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def mark_pending(allow_default_after_failed_boot=None):
    if allow_default_after_failed_boot is None and PENDING.exists():
        return
    write_json(PENDING, {'allow_default_after_failed_boot': allow_default_after_failed_boot is True}, 0o600)


def pending_allows_default():
    try:
        data = json.loads(PENDING.read_text())
    except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError):
        return False
    return isinstance(data, dict) and data.get('allow_default_after_failed_boot') is True


def write_state(config):
    write_json(STATE, config, 0o644)


def apply_hardware(config):
    mode = config.get('mode', 'auto')
    if mode == 'auto':
        subprocess.run(['systemctl', 'start', 'monoledd.service'], check=True)
        return
    subprocess.run(['systemctl', 'stop', 'monoledd.service'], check=True)
    paths = [LEDS / f'status:{channel}' for channel in CHANNELS]
    if not all(path.is_dir() for path in paths):
        return
    color = config['color'] if mode == 'fixed' else '00000000'
    for index, path in enumerate(paths):
        trigger = path / 'trigger'
        current_trigger = trigger.read_text().strip().split()
        if current_trigger != ['none'] and '[none]' not in current_trigger:
            trigger.write_text('none')
        brightness = path / 'brightness'
        value = int(color[index * 2:index * 2 + 2], 16)
        if int(brightness.read_text()) != value:
            brightness.write_text(str(value))


def apply_state(config=None, allow_default=True, pending=False, post_boot_delete=False, running_default=False):
    with state_lock():
        had_state = STATE.exists()
        had_pending = PENDING.exists()
        if config is None and not had_state and not allow_default:
            return
        previous = read_state()
        candidate = previous if config is None else dict(config)
        validate(candidate)
        if pending:
            confirmed_default = had_state and not had_pending and not previous
            pending_default = had_pending and pending_allows_default()
            allow_failed_boot_default = post_boot_delete or (
                running_default and (confirmed_default or pending_default)
            )
            mark_pending(allow_failed_boot_default)
        if config is not None or not had_state:
            write_state(candidate)
        try:
            apply_hardware(candidate)
        except Exception as error:
            try:
                if had_state:
                    write_state(previous)
                    apply_hardware(previous)
                else:
                    STATE.unlink(missing_ok=True)
                    subprocess.run(['systemctl', 'stop', 'monoledd.service'], check=True)
            except Exception as restore_error:
                raise ConfigError(f'LED apply failed: {error}; restoration failed: {restore_error}') from error
            raise


def acquire_commit_lock():
    from vyos.utils.commit import acquire_commit_lock_file
    return acquire_commit_lock_file()


def release_commit_lock(lock):
    from vyos.utils.commit import release_commit_lock_file
    release_commit_lock_file(lock)


def read_running_config():
    from vyos.config import Config
    conf = Config()
    if conf.get_config_tree(effective=True) is None:
        raise ConfigError('VyOS running configuration is unavailable')
    config = conf.get_config_dict(['system', 'hardware', 'led'], effective=True, get_first_key=True)
    validate(config)
    return dict(config)


def invalidate_state():
    with state_lock():
        STATE.unlink(missing_ok=True)
        apply_hardware({'mode': 'off'})


def apply_confirmed_state(config, allow_default):
    with state_lock():
        pending_default = pending_allows_default()
        if not config and not allow_default and not pending_default:
            STATE.unlink(missing_ok=True)
            apply_hardware({'mode': 'off'})
            PENDING.unlink(missing_ok=True)
            return
        mark_pending()
        write_state(config)
        if config.get('mode', 'auto') == 'auto':
            PENDING.unlink()
            try:
                apply_hardware(config)
            except Exception as error:
                mark_pending(pending_default)
                try:
                    subprocess.run(['systemctl', 'stop', 'monoledd.service'], check=True)
                except Exception as stop_error:
                    raise ConfigError(f'LED daemon start failed: {error}; '
                                      f'failed to stop daemon: {stop_error}') from error
                raise
        else:
            apply_hardware(config)
            PENDING.unlink()


def reconcile_state(allow_default=True):
    with state_lock():
        if not PENDING.exists():
            if STATE.exists():
                apply_hardware(read_state())
                return
            if not allow_default:
                apply_hardware({'mode': 'off'})
                return
    commit_lock, message = acquire_commit_lock()
    if commit_lock is None:
        if message.startswith('Critical error opening commit lock file'):
            raise ConfigError(message)
        return
    try:
        try:
            running = read_running_config()
            validate(running)
        except Exception as error:
            try:
                invalidate_state()
            except Exception as stop_error:
                raise ConfigError(f'Unable to read running LED configuration: {error}; '
                                  f'failed to stop LED daemon: {stop_error}') from error
            raise ConfigError(f'Unable to read running LED configuration: {error}') from error
        apply_confirmed_state(running, allow_default)
    finally:
        release_commit_lock(commit_lock)
