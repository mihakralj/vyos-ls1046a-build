#!/usr/bin/env python3

import os
import select
import signal
import sys
from pathlib import Path

from vyos import ConfigError
from vyos.base import Warning
from vyos.config import Config
from vyos.fan_control import (PENDING_TIMEOUT, boot_failure_latched, boot_failure_marker,
                              boot_marker, effective, invalidate_verification,
                              load_snapshot_record, mark_boot_failed, mark_boot_ready, mark_pending,
                              read_applied, read_verification, write_snapshot, write_verification)
from vyos.utils.boot import boot_configuration_complete, boot_configuration_success
from vyos.utils.commit import acquire_commit_lock_file, commit_in_progress2, release_commit_lock_file

PATH = ['system', 'hardware', 'fan']
PENDING = Path('/run/vyos-fan-pending')
COMMIT_LOCK_BUSY = 'Configuration system locked by another commit in progress'
RECONCILE_INTERVAL = 5.0
RECONCILE_FAST_INTERVAL = 2.0


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


def _refresh_steady_state():
    """Refresh the verification lease without the commit lock when nothing is
    pending and the published snapshot already matches the running config.

    VyOS commits take the commit lock non-blocking, so holding it on every tick
    makes operator commits fail at random. Any doubt (pending work, read error,
    a commit around the read) returns False and defers to the locked path.
    """
    try:
        if PENDING.exists() or not boot_marker().exists() or commit_in_progress2():
            return False
        conf = Config()
        if conf.get_config_tree(effective=True) is None:
            return False
        values = effective(conf.get_config_dict(PATH, effective=True, get_first_key=True))
        current, _, generation = load_snapshot_record()
        read_verification(values, generation)
        applied = read_applied()
        if (current != values or applied['zones'] != values or applied['generation'] != generation or
                PENDING.exists() or not boot_marker().exists() or
                commit_in_progress2() or not boot_approved()):
            return False
        write_verification(values, generation)
    except Exception:
        return False
    return True


def reconcile():
    if not boot_approved():
        return
    if _refresh_steady_state():
        return
    try:
        lock, message = acquire_commit_lock_file()
    except Exception:
        fail_closed()
        raise
    if lock is None:
        if message != COMMIT_LOCK_BUSY:
            fail_closed()
            raise ConfigError(str(message))
        return
    try:
        if not boot_approved():
            return
        mark_boot_ready()
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


def _reconcile_delay(interval, fast_interval):
    try:
        if boot_failure_latched():
            return interval
        if PENDING.exists() or not boot_marker().exists():
            return fast_interval
    except OSError:
        return fast_interval
    return interval


class _SignalStop:
    def __init__(self):
        self._stopped = False
        self._r_fd, self._w_fd = os.pipe()
        os.set_blocking(self._r_fd, False)
        os.set_blocking(self._w_fd, False)
        self._prev_term = signal.signal(signal.SIGTERM, self._handle)
        self._prev_int = signal.signal(signal.SIGINT, self._handle)

    def _handle(self, _sig, _frame):
        self._stopped = True
        try:
            os.write(self._w_fd, b'\x00')
        except OSError:
            pass

    def is_set(self):
        return self._stopped

    def wait(self, timeout):
        if self._stopped:
            return True
        try:
            ready, _, _ = select.select([self._r_fd], [], [], timeout)
            if ready:
                try:
                    os.read(self._r_fd, 64)
                except OSError:
                    pass
        except InterruptedError:
            pass
        return self._stopped

    def close(self):
        signal.signal(signal.SIGTERM, self._prev_term)
        signal.signal(signal.SIGINT, self._prev_int)
        os.close(self._r_fd)
        os.close(self._w_fd)


def reconcile_daemon(interval=RECONCILE_INTERVAL, fast_interval=RECONCILE_FAST_INTERVAL, stop_event=None):
    owned_stop = _SignalStop() if stop_event is None else None
    stopper = owned_stop if owned_stop is not None else stop_event
    last_error = None
    try:
        while not stopper.is_set():
            try:
                reconcile()
                last_error = None
            except Exception as error:
                try:
                    fail_closed()
                except OSError:
                    pass
                message = str(error) or type(error).__name__
                if message != last_error:
                    print(message, file=sys.stderr, flush=True)
                    last_error = message
            if stopper.is_set():
                break
            stopper.wait(_reconcile_delay(interval, fast_interval))
    finally:
        if owned_stop is not None:
            owned_stop.close()


if __name__ == '__main__':
    try:
        if sys.argv[1:] == ['--reconcile']:
            reconcile()
        elif sys.argv[1:] == ['--reconcile-daemon']:
            reconcile_daemon()
        else:
            config = get_config()
            verify(config)
            generate(config)
            apply(config)
    except (ConfigError, OSError, ValueError) as error:
        print(error, file=sys.stderr)
        sys.exit(1)
