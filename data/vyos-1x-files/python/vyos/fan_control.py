import hashlib
import json
import math
import os
import re
import secrets
import sys
import tempfile
import time
from pathlib import Path

STATE = Path('/run/vyos-fan-zones.json')
PENDING = Path('/run/vyos-fan-pending')
APPLIED = Path('/run/fan-pid/applied.json')
FLOOR_EXPORT = Path('/run/fan-pid/floors')
FLOOR_EXPORT_MAX_AGE = 10.0
MAX_FLOOR_EXPORT_SIZE = 16384
PENDING_TIMEOUT = 30.0
APPLIED_MAX_AGE = 15.0
VERIFICATION_MAX_AGE = 30.0
SENSOR_MAX_AGE = 10.0
MAX_JSON_SIZE = 8192
DEFAULTS = {
    'memory': {'setpoint': 65, 'critical': 80},
    'serdes': {'setpoint': 70, 'critical': 85},
    'fman': {'setpoint': 70, 'critical': 87},
    'cpu-int': {'setpoint': 65, 'critical': 85},
    'crypto': {'setpoint': 65, 'critical': 85},
    'pcb': {'setpoint': 60, 'critical': 85},
    'cpu-ext': {'setpoint': 70, 'critical': 85},
    'sfp-plus': {'setpoint': 60, 'critical': 85},
}
ZONE_NAMES = {
    'memory': 'ddr-thermal', 'serdes': 'serdes-thermal',
    'fman': 'fman-thermal', 'cpu-int': 'cluster-thermal',
    'crypto': 'sec-thermal', 'pcb': 'PCB', 'cpu-ext': 'CPU',
    'sfp-plus': 'SFP+',
}


def clock_now():
    return time.clock_gettime(time.CLOCK_BOOTTIME)


def effective(config):
    if not isinstance(config, dict) or set(config) - {'zone'}:
        raise ValueError('Invalid fan configuration')
    zones = config.get('zone', {})
    if not isinstance(zones, dict) or set(zones) - DEFAULTS.keys():
        raise ValueError('Invalid fan zone')
    result = {name: dict(values) for name, values in DEFAULTS.items()}
    for name, options in zones.items():
        if not isinstance(options, dict) or set(options) - {'setpoint', 'critical'}:
            raise ValueError(f'Invalid fan zone settings: {name}')
        for key, value in options.items():
            if type(value) not in (str, int) or not re.fullmatch(r'[0-9]{1,3}', str(value)):
                raise ValueError(f'{name} {key} must be an integer from 0 to 100')
            number = int(value)
            if number > 100:
                raise ValueError(f'{name} {key} must be an integer from 0 to 100')
            result[name][key] = number
        if result[name]['setpoint'] >= result[name]['critical']:
            raise ValueError(f'{name} setpoint must be below critical')
        if result[name]['critical'] > DEFAULTS[name]['critical']:
            raise ValueError(f'{name} critical cannot exceed {DEFAULTS[name]["critical"]} C')
    return result


def validate_snapshot(data):
    if not isinstance(data, dict) or set(data) != set(DEFAULTS):
        raise ValueError('Invalid fan snapshot zones')
    for options in data.values():
        if not isinstance(options, dict) or set(options) != {'setpoint', 'critical'}:
            raise ValueError('Invalid fan snapshot fields')
    return effective({'zone': data})


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate fan state field')
        result[key] = value
    return result


def _read_json(path):
    try:
        with path.open(encoding='utf-8') as source:
            text = source.read(MAX_JSON_SIZE + 1)
        if len(text) > MAX_JSON_SIZE:
            raise ValueError('fan state is too large')
        return json.loads(text, object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, ValueError, RecursionError) as error:
        raise ValueError(f'Invalid fan state: {error}') from error


def snapshot_marker(path):
    return path.with_name(path.name + '.established')


def boot_marker(path=None):
    path = STATE if path is None else path
    return path.parent / 'vyos-fan-boot-ready'


def boot_failure_marker(path=None):
    path = STATE if path is None else path
    return path.parent / 'vyos-fan-boot-failed'


def boot_failure_latched(path=None):
    try:
        boot_failure_marker(path).lstat()
        return True
    except FileNotFoundError:
        return False


def mark_boot_failed(path=None):
    try:
        fd = os.open(boot_failure_marker(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return
    os.close(fd)


def verification_path(path=None):
    path = STATE if path is None else path
    return path.parent / 'vyos-fan-verified.json'


def mark_boot_ready(path=None):
    try:
        fd = os.open(boot_marker(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError:
        return
    os.close(fd)


def _exists(path):
    try:
        path.stat()
        return True
    except FileNotFoundError:
        return False


def _generation(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{32}', value):
        raise ValueError('Invalid fan snapshot generation')
    return value


def snapshot_digest(values, generation):
    data = {'zones': values, 'generation': _generation(generation)}
    encoded = json.dumps(data, sort_keys=True, separators=(',', ':')).encode('ascii')
    return hashlib.sha256(encoded).hexdigest()


def load_snapshot_record(path=None, require=False):
    path = STATE if path is None else path
    try:
        data = _read_json(path)
    except FileNotFoundError as error:
        if require or _exists(snapshot_marker(path)) or _exists(boot_marker(path)):
            raise ValueError('Fan snapshot is missing after initialization') from error
        return effective({}), False, None
    if not isinstance(data, dict) or set(data) != {'zones', 'sha256', 'generation'}:
        raise ValueError('Invalid fan snapshot envelope')
    values = validate_snapshot(data['zones'])
    generation = _generation(data['generation'])
    if not isinstance(data['sha256'], str) or data['sha256'] != snapshot_digest(values, generation):
        raise ValueError('Fan snapshot checksum mismatch')
    return values, True, generation


def load_snapshot(path=None, require=False):
    values, persisted, _ = load_snapshot_record(path, require)
    return values, persisted


def read_snapshot(path=None, require=False):
    return load_snapshot_record(path, require)[0]


def _write_atomic(path, data, before_replace=None):
    fd, name = tempfile.mkstemp(prefix=f'.{path.name}.', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as output:
            json.dump(data, output, sort_keys=True)
            os.fchmod(output.fileno(), 0o644)
        if before_replace is not None:
            before_replace()
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def _timestamp(value, description):
    if type(value) not in (int, float):
        raise ValueError(f'Invalid {description} time')
    try:
        value = float(value)
    except OverflowError as error:
        raise ValueError(f'Invalid {description} time') from error
    if not math.isfinite(value) or value < 0:
        raise ValueError(f'Invalid {description} time')
    return value


def _fresh(value, max_age, description, now=None):
    now = clock_now() if now is None else now
    age = now - _timestamp(value, description)
    if age < -1 or age > max_age:
        raise ValueError(f'{description} is stale')
    return age


def _resolve_generation(values, generation, path):
    if generation is None:
        current, persisted, generation = load_snapshot_record(path)
        if not persisted or current != values:
            raise ValueError('Fan snapshot generation is unavailable')
    return _generation(generation)


def write_verification(values, generation=None, path=None):
    if boot_failure_latched(path):
        raise ValueError('Boot fan configuration failed; successful reboot required')
    values = validate_snapshot(values)
    generation = _resolve_generation(values, generation, path)
    _write_atomic(verification_path(path), {'sha256': snapshot_digest(values, generation),
                                            'generation': generation, 'updated': clock_now()})


def read_verification(values, generation=None, path=None):
    if boot_failure_latched(path):
        raise ValueError('Boot fan configuration failed; successful reboot required')
    if not _exists(boot_marker(path)):
        raise ValueError('Boot fan configuration is not reconciled')
    values = validate_snapshot(values)
    generation = _resolve_generation(values, generation, path)
    data = _read_json(verification_path(path))
    if not isinstance(data, dict) or set(data) != {'sha256', 'generation', 'updated'}:
        raise ValueError('Invalid fan configuration verification')
    _fresh(data['updated'], VERIFICATION_MAX_AGE, 'Fan configuration verification')
    if data['generation'] != generation or data['sha256'] != snapshot_digest(values, generation):
        raise ValueError('Fan configuration verification does not match the snapshot')
    return data


def invalidate_verification(path=None):
    verification_path(path).unlink(missing_ok=True)


def write_snapshot(values, path=None):
    path = STATE if path is None else path
    values = validate_snapshot(values)

    def establish():
        try:
            fd = os.open(snapshot_marker(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        except FileExistsError:
            return
        os.close(fd)

    generation = secrets.token_hex(16)
    _write_atomic(path, {'zones': values, 'generation': generation,
                         'sha256': snapshot_digest(values, generation)}, establish)
    return generation


def mark_pending(path=None):
    path = PENDING if path is None else path
    fd, name = tempfile.mkstemp(prefix=f'.{path.name}.', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as output:
            output.write(str(clock_now()))
            os.fchmod(output.fileno(), 0o644)
        try:
            os.link(name, path)
        except FileExistsError:
            pass
    finally:
        Path(name).unlink(missing_ok=True)


def pending_age(path=None):
    path = PENDING if path is None else path
    try:
        with path.open(encoding='ascii') as source:
            text = source.read(65)
    except FileNotFoundError:
        return None
    except UnicodeError as error:
        raise ValueError('Invalid fan pending state') from error
    try:
        started = float(text)
    except ValueError as error:
        raise ValueError('Invalid fan pending state') from error
    now = clock_now()
    if len(text) > 64 or not math.isfinite(started) or started < 0 or started > now + 1:
        raise ValueError('Invalid fan pending state')
    return max(0.0, now - started)


def _process_start(pid):
    with Path(f'/proc/{pid}/stat').open(encoding='ascii') as source:
        fields = source.read().rsplit(')', 1)[-1].split()
    if len(fields) < 20 or fields[0] in ('Z', 'X', 'x', 'T', 't'):
        raise ValueError('Fan controller process is not running')
    try:
        return int(fields[19])
    except ValueError as error:
        raise ValueError('Invalid fan controller process identity') from error


def process_start_time(pid):
    return _process_start(pid)


def read_floor_export(path=None):
    path = FLOOR_EXPORT if path is None else path
    try:
        with path.open(encoding='utf-8') as source:
            text = source.read(MAX_FLOOR_EXPORT_SIZE + 1)
    except UnicodeError as error:
        raise ValueError('Invalid fan telemetry encoding') from error
    if len(text) > MAX_FLOOR_EXPORT_SIZE:
        raise ValueError('Fan telemetry is too large')
    lines = text.splitlines()
    fields = {}
    for line in lines:
        key, sep, value = line.partition('=')
        if not sep:
            continue
        if (key.startswith(('export_', 'rpm:', 'rpm_sample:', 'present:', 'fan_state:',
                            'tach_recovery:', 'tach_range:')) or key in ('0x30', '0x40', 'controller_device', 'tach_source')):
            if key in fields:
                raise ValueError('Duplicate fan telemetry field')
            fields[key] = value.strip()
    if fields.get('export_version') != '1':
        raise ValueError('Fan telemetry freshness is unavailable')
    try:
        updated_text = fields['export_updated']
        pid_text = fields['export_pid']
        start_text = fields['export_start']
        if (len(updated_text) > 64 or not re.fullmatch(r'[0-9]{1,10}', pid_text) or
                not re.fullmatch(r'[0-9]{1,20}', start_text)):
            raise ValueError('Invalid fan telemetry identity')
        updated = float(updated_text)
        pid = int(pid_text)
        start = int(start_text)
    except (KeyError, ValueError, OverflowError) as error:
        raise ValueError('Invalid fan telemetry metadata') from error
    if not 0 < pid <= 2147483647 or not 0 <= start <= 18446744073709551615:
        raise ValueError('Invalid fan telemetry identity')
    now = clock_now()
    _fresh(updated, FLOOR_EXPORT_MAX_AGE, 'Fan telemetry', now)
    if _process_start(pid) != start:
        raise ValueError('Fan telemetry process identity changed')
    invalid = set()
    for reg in ('0x30', '0x40'):
        rpm_key = f'rpm:{reg}'
        if rpm_key not in fields:
            continue
        try:
            sampled_text = fields[f'rpm_sample:{reg}']
            if (len(sampled_text) > 64 or len(fields[rpm_key]) > 10 or
                    not re.fullmatch(r'[0-9]+', fields[rpm_key])):
                raise ValueError('Invalid RPM')
            sampled = float(sampled_text)
            _fresh(sampled, SENSOR_MAX_AGE, 'Fan tach sample', now)
            if sampled > updated + 0.001:
                raise ValueError('Fan tach sample is newer than export')
        except (KeyError, ValueError, OverflowError):
            invalid.add(rpm_key)
    sanitized = [line for line in lines if line.partition('=')[0] not in invalid]
    sanitized.extend(f'rpm_unavailable:{key[4:]}=1' for key in sorted(invalid))
    return '\n'.join(sanitized) + '\n'


def _validate_samples(samples, updated, now=None):
    if not isinstance(samples, dict) or set(samples) != set(DEFAULTS):
        raise ValueError('Incomplete fan sensor acknowledgement')
    now = clock_now() if now is None else now
    for name, sampled in samples.items():
        sampled = _timestamp(sampled, f'{name} sensor')
        _fresh(sampled, SENSOR_MAX_AGE, f'{name} sensor sample', now)
        if sampled > updated + 0.001:
            raise ValueError(f'Invalid {name} sensor sample time')


def _validate_chassis(chassis, updated, now=None):
    if not isinstance(chassis, dict) or set(chassis) != {'rpm', 'sampled'}:
        raise ValueError('Invalid chassis fan acknowledgement')
    if type(chassis['rpm']) is not int or chassis['rpm'] <= 0:
        raise ValueError('Chassis fan tach is unavailable')
    sampled = _timestamp(chassis['sampled'], 'chassis fan')
    _fresh(sampled, SENSOR_MAX_AGE, 'Chassis fan sample', now)
    if sampled > updated + 0.001:
        raise ValueError('Invalid chassis fan sample time')


def write_applied(values, mode='normal', path=None, sensors=None, generation=None, chassis=None):
    path = APPLIED if path is None else path
    if mode not in ('normal', 'failsafe'):
        raise ValueError('Invalid fan controller mode')
    generation = _generation(generation)
    updated = clock_now()
    _validate_samples(sensors, updated, updated)
    _validate_chassis(chassis, updated, updated)
    pid = os.getpid()
    _write_atomic(path, {'zones': validate_snapshot(values), 'generation': generation,
                         'updated': updated, 'mode': mode, 'pid': pid,
                         'start': _process_start(pid), 'sensors': sensors, 'chassis': chassis})


def read_applied(path=None):
    path = APPLIED if path is None else path
    data = _read_json(path)
    if (not isinstance(data, dict) or
            set(data) != {'zones', 'generation', 'updated', 'mode', 'pid', 'start', 'sensors', 'chassis'}):
        raise ValueError('Invalid fan controller acknowledgement')
    generation = _generation(data['generation'])
    updated = _timestamp(data['updated'], 'fan controller acknowledgement')
    now = clock_now()
    _fresh(updated, APPLIED_MAX_AGE, 'Fan controller acknowledgement', now)
    _validate_samples(data['sensors'], updated, now)
    _validate_chassis(data['chassis'], updated, now)
    if data['mode'] not in ('normal', 'failsafe'):
        raise ValueError('Invalid fan controller acknowledgement mode')
    if (type(data['pid']) is not int or not 0 < data['pid'] <= 2147483647 or
            type(data['start']) is not int or not 0 <= data['start'] <= 18446744073709551615):
        raise ValueError('Invalid fan controller process identity')
    if _process_start(data['pid']) != data['start']:
        raise ValueError('Fan controller process identity changed')
    return {'zones': validate_snapshot(data['zones']), 'mode': data['mode'], 'generation': generation}


def invalidate_applied(path=None):
    (APPLIED if path is None else path).unlink(missing_ok=True)


def configuration_status(state_path=None, pending_path=None, applied_path=None):
    try:
        boot_failed = boot_failure_latched(state_path)
    except OSError:
        boot_failed = True
    try:
        desired, persisted, generation = load_snapshot_record(state_path)
        snapshot_error = None
    except (OSError, ValueError) as error:
        desired = None
        persisted = False
        generation = None
        snapshot_error = str(error)
    boot_ready = True
    try:
        boot_ready = _exists(boot_marker(state_path))
        if not persisted:
            raise ValueError('Boot fan configuration is not reconciled')
        read_verification(desired, generation, state_path)
        verification_error = None
    except (OSError, ValueError) as error:
        verification_error = str(error)
    try:
        age = pending_age(pending_path)
        pending_error = None
    except (OSError, ValueError, UnicodeError) as error:
        age = None
        pending_error = str(error)
    try:
        applied = read_applied(applied_path)
    except (OSError, ValueError):
        applied = None
    zones = applied['zones'] if applied and verification_error is None else None
    if boot_failed:
        state, message = 'error', 'Boot fan configuration failed or is unreadable; successful reboot required'
    elif snapshot_error:
        state, message = 'error', f'Fan snapshot unavailable: {snapshot_error}'
    elif pending_error:
        state, message = 'error', f'Fan configuration pending state invalid: {pending_error}'
    elif age is not None and age >= PENDING_TIMEOUT:
        state, message = 'error', 'Fan configuration reconciliation timed out; full-speed failsafe requested'
    elif verification_error:
        state = 'error' if boot_ready else 'unknown'
        message = f'Fan configuration not verified: {verification_error}'
    elif applied is None:
        state, message = 'unknown', 'No fresh fan controller acknowledgement'
    elif applied['mode'] == 'failsafe':
        state, message = 'error', 'Fan controller reports configuration failsafe'
    elif age is not None or desired != zones or applied['generation'] != generation:
        state, message = 'pending', 'Fan configuration is pending reconciliation or controller application'
    else:
        state, message = 'ok', 'Fan configuration applied'
    return {'state': state, 'message': message, 'zones': zones}


def main():
    if sys.argv[1:] == ['--floor-export']:
        try:
            sys.stdout.write(read_floor_export())
            return 0
        except (OSError, ValueError) as error:
            print(f'Fan telemetry unavailable: {error}', file=sys.stderr)
            return 1
    status = configuration_status()
    print(f'__status__ {status["state"]}')
    print(f'__message__ {status["message"]}')
    for name in DEFAULTS:
        values = status['zones'][name] if status['zones'] is not None else None
        setpoint = values['setpoint'] if values else '?'
        critical = values['critical'] if values else '?'
        print(f'{ZONE_NAMES[name]} {setpoint} {critical}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
