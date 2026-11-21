#!/usr/bin/env python3

import sys
from pathlib import Path

from vyos import ConfigError
from vyos.base import Warning
from vyos.config import Config
from vyos.fan_control import (PENDING_TIMEOUT, boot_failure_marker, effective, invalidate_verification,
                              load_snapshot_record, mark_boot_failed, mark_boot_ready, mark_pending,
                              read_applied, read_verification, write_snapshot, write_verification)
from vyos.utils.boot import boot_configuration_complete, boot_configuration_success
from vyos.utils.commit import acquire_commit_lock_file, release_commit_lock_file

PATH = ['system', 'hardware', 'fan']
PENDING = Path('/run/vyos-fan-pending')


def get_config(config=None):
    conf = config if config is not None else Config()
    return conf.get_config_dict(PATH, get_first_key=True)


def verify(config):
    try:
        effective(config)
    except ValueError as error:
        raise ConfigError(str(error)) from error


def generate(config):
    pass


def apply(config):
    mark_pending(PENDING)
    approval = boot_approval()
    if approval == 'failed':
        Warning('Fan control remains fail-safe after a failed boot configuration; a successful reboot is required.')
    elif approval == 'success':
        Warning(f'Fan settings are pending asynchronous application; check show hardware fan. '
                f'If unresolved after {int(PENDING_TIMEOUT)} seconds, full-speed failsafe is requested.')


def fail_closed():
    try:
        invalidate_verification()
    finally:
        mark_pending(PENDING)


def latch_boot_failure():
    try:
        mark_boot_failed()
    finally:
        fail_closed()


def explicit_boot_failure():
    try:
        from vyos.defaults import config_status
        with open(config_status, encoding='ascii') as status_file:
            text = status_file.read(65)
        if len(text) > 64:
            return False
        return int(text.strip()) != 0
    except (ImportError, OSError, UnicodeError, ValueError):
        return False


def boot_approval():
    try:
        boot_failure_marker().lstat()
    except FileNotFoundError:
        pass
    except OSError:
        fail_closed()
        return 'unknown'
    else:
        fail_closed()
        return 'failed'
    try:
        complete = boot_configuration_complete()
        successful = boot_configuration_success() if complete else False
    except (OSError, ValueError):
        fail_closed()
        return 'unknown'
    if not complete:
        fail_closed()
        return 'unknown'
    if successful:
        return 'success'
    if explicit_boot_failure():
        latch_boot_failure()
        return 'failed'
    fail_closed()
    return 'unknown'


def boot_approved():
    return boot_approval() == 'success'


def reconcile():
    if not boot_approved():
        return
    try:
        mark_boot_ready()
        lock, message = acquire_commit_lock_file()
    except Exception:
        fail_closed()
        raise
    if lock is None:
        fail_closed()
        if message.startswith('Critical error opening commit lock file'):
            raise ConfigError(message)
        return
    try:
        if not boot_approved():
            return
        conf = Config()
        if conf.get_config_tree(effective=True) is None:
            raise ConfigError('VyOS running configuration is unavailable')
        config = conf.get_config_dict(PATH, effective=True, get_first_key=True)
        values = effective(config)
        if not boot_approved():
            return
        try:
            current, _, generation = load_snapshot_record()
        except (OSError, ValueError):
            current, generation = None, None
        try:
            read_verification(values, generation)
            verified = True
        except (OSError, ValueError):
            verified = False
        try:
            applied = read_applied()
        except (OSError, ValueError):
            applied = None
        if (current != values or not verified or applied is None or
                applied['zones'] != values or applied['generation'] != generation):
            mark_pending(PENDING)
        if current != values:
            generation = write_snapshot(values)
        if not boot_approved():
            return
        write_verification(values, generation)
        if not boot_approved():
            return
        if (PENDING.exists() and applied is not None and applied['zones'] == values and
                applied['generation'] == generation):
            PENDING.unlink(missing_ok=True)
    except Exception:
        fail_closed()
        raise
    finally:
        release_commit_lock_file(lock)


if __name__ == '__main__':
    try:
        if sys.argv[1:] == ['--reconcile']:
            reconcile()
        else:
            config = get_config()
            verify(config)
            generate(config)
            apply(config)
    except (ConfigError, OSError, ValueError) as error:
        print(error, file=sys.stderr)
        sys.exit(1)
