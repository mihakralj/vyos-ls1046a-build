#!/usr/bin/env python3
#
# Copyright (C) VyOS Inc.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.
#
# op-mode: show hardware fan
#
# Thermal zone and cooling fan status for the NXP LS1046A Mono Gateway DK.
#
# Op-mode layer over the same data /usr/local/bin/fan-check reads:
#   - the five SoC thermal zones from /sys/class/thermal
#   - the EMC2305 PWM fan controller from /sys/class/hwmon
#   - fan-pid's live floor/RPM export from /run/fan-pid/floors
#

import os
import re
from pathlib import Path

from vyos.fan_control import DEFAULTS, ZONE_NAMES, configuration_status, read_floor_export

ZONE_LABELS = {
    'ddr-thermal': 'Memory',
    'serdes-thermal': 'SerDes',
    'fman-thermal': 'FMan',
    'cluster-thermal': 'CPU-int',
    'sec-thermal': 'Crypto',
}

HWMON_ZONES = {
    'PCB': ('i2c-mux@70/i2c@1/temp-sensor@4c', 'temp1_input'),
    'CPU-ext': ('i2c-mux@70/i2c@1/temp-sensor@4c', 'temp2_input'),
    'SFP+': ('i2c-mux@70/i2c@2/temp-sensor@4c', 'temp1_input'),
}
# type-string aliases tolerated across kernels (mainline 6.18 vs SDK 6.6).
ZONE_ALIASES = {
    'ddr-thermal':     ('ddr_thermal',),
    'serdes-thermal':  ('serdes_thermal',),
    'fman-thermal':    ('fman_thermal',),
    'cluster-thermal': ('cluster_thermal', 'core_cluster'),
    'sec-thermal':     ('sec_thermal',),
}
HARD_FAULT_TEMP = 100.0
PWM_MAX = 255
FLOOR_STATE_FILE = '/run/fan-pid/floors'
HWMON_NAME = 'emc2305'
# Test hook: prepend a fake sysfs root (used by the sanity harness only).
SYS_ROOT = os.environ.get('VYOS_FAN_SYS_ROOT', '')

# fanN -> EMC2305 duty register exported by fan-pid in the floors file.
FAN_REGS = {1: '0x30', 2: '0x40'}


def _read_text(path: str):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def _read_int(path: str):
    val = _read_text(path)
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


def _read_temp_c(zone_path: str):
    """MillidegC zone temp -> degC float (1 decimal), or None."""
    m = _read_int(zone_path)
    if m is None:
        return None
    try:
        temp = m / 1000.0
    except OverflowError:
        return None
    return round(temp, 1) if temp >= -273.15 else None


def _is_ls1046a() -> bool:
    compat = _read_text(f'{SYS_ROOT}/proc/device-tree/compatible')
    return bool(compat and 'fsl,ls1046a' in compat)


def _find_thermal_zones():
    """Map canonical zone names to their <zone>/temp paths."""
    found = {}
    base = f'{SYS_ROOT}/sys/class/thermal'
    if not os.path.isdir(base):
        return found
    for entry in sorted(os.listdir(base)):
        if not entry.startswith('thermal_zone'):
            continue
        zone_dir = f'{base}/{entry}'
        ztype = _read_text(f'{zone_dir}/type')
        if ztype is None or not os.path.isfile(f'{zone_dir}/temp'):
            continue
        for name in ZONE_ALIASES:
            if ztype == name or ztype in ZONE_ALIASES[name]:
                found[name] = f'{zone_dir}/temp'
                break
    return found


def _find_hwmon_zones():
    found = {}
    base = f'{SYS_ROOT}/sys/class/hwmon'
    if not os.path.isdir(base):
        return found
    for entry in sorted(os.listdir(base)):
        hwmon_dir = f'{base}/{entry}'
        if _read_text(f'{hwmon_dir}/name') != 'tmp431':
            continue
        of_node = os.path.realpath(f'{hwmon_dir}/device/of_node')
        for name, (suffix, attr) in HWMON_ZONES.items():
            path = f'{hwmon_dir}/{attr}'
            if of_node.endswith('/' + suffix) and os.path.isfile(path):
                found[name] = path
    return found


def _find_bound_emc2305():
    devices = f'{SYS_ROOT}/sys/bus/i2c/devices'
    driver = f'{SYS_ROOT}/sys/bus/i2c/drivers/emc2305'
    if not os.path.isdir(driver):
        return None
    try:
        entries = os.listdir(devices)
    except OSError:
        return None
    matches = []
    for entry in entries:
        if not re.fullmatch(r'\d+-002e', entry):
            continue
        device = os.path.join(devices, entry)
        link = os.path.join(device, 'driver')
        if os.path.islink(link) and os.path.realpath(link) == os.path.realpath(driver):
            matches.append(os.path.realpath(device))
    return matches[0] if len(matches) == 1 else None


def _find_emc2305(expected_device=None):
    device = _find_bound_emc2305()
    if device is None or (expected_device is not None and expected_device != device):
        return None
    base = f'{SYS_ROOT}/sys/class/hwmon'
    try:
        entries = sorted(os.listdir(base))
    except OSError:
        return None
    matches = []
    for entry in entries:
        if not entry.startswith('hwmon'):
            continue
        hwmon_dir = os.path.join(base, entry)
        if (_read_text(f'{hwmon_dir}/name') == HWMON_NAME and
                os.path.realpath(f'{hwmon_dir}/device') == device):
            matches.append(hwmon_dir)
    if len(matches) != 1 or _find_bound_emc2305() != device:
        return None
    return matches[0]


def _read_floor_state():
    floors = {}
    rpms = {}
    presents = {}
    states = {}
    recoveries = {}
    tach_source = 'raw-m1'
    controller_device = None
    rpm_unavailable = set()
    invalid_presence = set()
    path = Path(f'{SYS_ROOT}{FLOOR_STATE_FILE}')
    try:
        lines = read_floor_export(path).splitlines()
    except (OSError, ValueError) as error:
        return floors, rpms, tach_source, presents, states, controller_device, rpm_unavailable, str(error)
    for line in lines:
        key, sep, val = line.partition('=')
        if not sep:
            continue
        if key in ('0x30', '0x40'):
            try:
                floors[key] = int(val)
            except ValueError:
                pass
        elif key.startswith('present:') and key[8:] in FAN_REGS.values():
            if val.strip() in ('0', '1'):
                presents[key[8:]] = val.strip() == '1'
            else:
                invalid_presence.add(key[8:])
        elif key.startswith('rpm_unavailable:') and key[16:] in FAN_REGS.values():
            rpm_unavailable.add(key[16:])
        elif key.startswith('rpm:'):
            try:
                rpms[key[4:]] = int(val)
            except ValueError:
                pass
        elif key.startswith('fan_state:') and key[10:] in FAN_REGS.values():
            state = val.strip()
            states[key[10:]] = state if state in ('present', 'recovering', 'unknown') else 'unknown'
        elif key.startswith('tach_recovery:') and key[14:] in FAN_REGS.values():
            recoveries[key[14:]] = val.strip() != '0'
        elif key == 'tach_source':
            tach_source = val if val in ('raw-m1', 'sysfs-m2') else 'raw-m1'
        elif key == 'controller_device':
            controller_device = val.strip()
    for reg, recovering in recoveries.items():
        if recovering and states.get(reg) not in ('recovering', 'unknown'):
            states[reg] = 'recovering' if reg == '0x30' or presents.get(reg, True) else 'unknown'
    for reg in FAN_REGS.values():
        if reg in invalid_presence or reg not in presents:
            if states.get(reg) != 'recovering':
                states[reg] = 'unknown'
    return floors, rpms, tach_source, presents, states, controller_device, rpm_unavailable, None


def _classify_zone(temp: float, setpoint: float, crit: float) -> str:
    if temp >= HARD_FAULT_TEMP or temp >= crit:
        return 'crit'
    if temp >= setpoint:
        return 'hot'
    if temp >= setpoint - 5:
        return 'warm'
    return 'cool'


def _fan_state(exists, fault, pwm, rpm, floor, present=True, exported_state=None,
               presence_known=False) -> str:
    if fault == 1:
        return 'fault'
    if exported_state in ('recovering', 'unknown'):
        return exported_state
    if exported_state == 'present' and not present:
        return 'unknown'
    if not present:
        return 'not present'
    if not exists:
        return 'read-error' if presence_known else 'not present'
    if not presence_known and floor == PWM_MAX and (rpm == 0 or rpm is None):
        return 'not present'
    if not presence_known and floor == 0 and rpm == 0:
        return 'not present'
    if (pwm is None or rpm is None or fault not in (0, 1) or
            not 0 <= pwm <= PWM_MAX or rpm < 0 or not 0 <= floor <= PWM_MAX):
        return 'read-error'
    if pwm == 0:
        return 'off'
    if rpm == 0:
        if floor and pwm >= floor:
            return 'stalled'
        if floor and pwm < floor:
            return 'blind'
        return 'no-tach' if presence_known or floor else 'not present'
    if pwm >= PWM_MAX:
        return 'max'
    return 'running'


def _read_zones(zones_found, values):
    zones = []
    for key in DEFAULTS:
        settings = values[key] if values is not None else None
        name = ZONE_NAMES[key]
        name = 'CPU-ext' if name == 'CPU' else name
        setpoint = settings['setpoint'] if settings else None
        crit = settings['critical'] if settings else None
        path = zones_found.get(name)
        temp = _read_temp_c(path) if path else None
        sensor_fault = _read_int(str(Path(path).with_name('temp2_fault'))) if name == 'CPU-ext' and path else 0
        if sensor_fault is None:
            temp = None
            state = 'unreadable'
        elif sensor_fault != 0:
            temp = None
            state = 'fault'
        elif temp is None:
            state = 'unreadable' if path else 'missing'
        elif settings is None:
            state = 'unknown'
        else:
            state = _classify_zone(temp, setpoint, crit)
        zones.append({'zone': name, 'temp_c': temp, 'state': state,
                      'setpoint_c': setpoint, 'critical_c': crit})
    return zones


def show_thermal_zones(zones):
    print(f"  {'zone':<16}  {'temp':>7}  {'setpoint':>8}  {'crit':>8}  state")
    print('  ' + '-' * 63)
    for zone in zones:
        label = ZONE_LABELS.get(zone['zone'], zone['zone'])
        if zone['state'] == 'missing':
            print(f'  {label:<16}  (not present)')
        elif zone['state'] == 'unreadable':
            print(f'  {label:<16}  (read failed)')
        elif zone['state'] == 'fault':
            print(f'  {label:<16}  (sensor fault)')
        elif zone['state'] == 'unknown':
            print(f"  {label:<16}  {zone['temp_c']:>6.1f} C  {'?':>6} C  {'?':>6} C  [UNKNOWN]")
        else:
            print(f"  {label:<16}  {zone['temp_c']:>6.1f} C  "
                  f"{zone['setpoint_c']:>6.0f} C  {zone['critical_c']:>6.0f} C  "
                  f"[{zone['state'].upper()}]")


def _read_fans(hwmon, floors, rpms, tach_source, presents, states, rpm_unavailable):
    fans = []
    for fan_num in (1, 2):
        reg = FAN_REGS[fan_num]
        pwm_path = f'{hwmon}/pwm{fan_num}' if hwmon else ''
        rpm_path = f'{hwmon}/fan{fan_num}_input' if hwmon else ''
        fault_path = f'{hwmon}/fan{fan_num}_fault' if hwmon else ''
        exists = bool(hwmon and os.path.isfile(pwm_path) and os.path.isfile(rpm_path))
        pwm = _read_int(pwm_path) if hwmon else None
        fault = _read_int(fault_path) if hwmon else None
        floor = floors.get(reg, 0)
        rpm = rpms.get(reg)
        if rpm is None and hwmon and reg not in rpm_unavailable and tach_source != 'raw-m1':
            rpm = _read_int(rpm_path)
        present = presents.get(reg, True)
        exported_state = states.get(reg)
        presence_known = reg in presents or exported_state == 'present'
        state = _fan_state(exists, fault, pwm, rpm, floor, present, exported_state, presence_known)
        uncertain = (state in ('recovering', 'unknown') or
                     exported_state in ('recovering', 'unknown') or
                     (presence_known and present and state == 'read-error') or (fault == 1 and not present))
        is_present = bool(exists and present and state != 'not present')
        presence = 'unknown' if uncertain else 'present' if is_present else 'absent'
        visible = is_present or uncertain or fault == 1
        fans.append({'fan': f'fan{fan_num}', 'exists': exists, 'present': is_present,
                     'presence': presence,
                     'duty': pwm if visible else None,
                     'rpm': rpm if visible else None,
                     'min_duty': floor if visible else None,
                     'max_duty': PWM_MAX if visible else None,
                     'state': state, 'fault': fault if visible else None})
    return fans


def show_fans(hwmon, fans):
    print('\nEMC2305 PWM fan controller')
    if hwmon is None:
        print('  NOT FOUND — EMC2305 unavailable, unbound, or ambiguous')
        return
    print(f'  hwmon path : {hwmon}')
    print(f"  {'fan':<5}  {'min':>4}  {'cur':>4}  {'max':>4}  {'%':>5}  "
          f"{'RPM':>8}  {'fault':<5}  state")
    print('  ' + '-' * 74)
    for fan in fans:
        label = f"Fan {fan['fan'][-1]}"
        if fan['state'] == 'not present' and fan['fault'] != 1:
            print(f"  {label:<5}  {'-':>4}  {'-':>4}  {'-':>4}  {'-':>5}  "
                  f"{'-':>8}  {'no':<5}  not present")
            continue
        pwm = fan['duty']
        rpm = fan['rpm']
        floor = fan['min_duty']
        fault = fan['fault']
        pct = f'{round(pwm * 100 / PWM_MAX)}%' if pwm is not None else '?'
        state = {'stalled': 'NO-TACH (stall/dead?)', 'blind': f'BLIND (<min {floor})',
                 'read-error': 'READ-FAIL'}.get(fan['state'], fan['state'].upper())
        fault_display = 'yes' if fault == 1 else 'no' if fault == 0 else '?'
        rpm_display = 'n/a' if fan['state'] == 'blind' else rpm if rpm is not None else '?'
        print(f"  {label:<5}  {floor or '?':>4}  {pwm if pwm is not None else '?':>4}  "
              f"{PWM_MAX:>4}  {pct:>5}  {rpm_display:>8}  {fault_display:<5}  {state}")


def show(raw: bool):
    if not _is_ls1046a():
        print('This platform is not the NXP LS1046A Mono Gateway DK; '
              'no fan controller to report.')
        return 0

    zones_found = _find_thermal_zones()
    zones_found.update(_find_hwmon_zones())
    (floors, rpms, tach_source, presents, states, controller_device,
     rpm_unavailable, telemetry_error) = _read_floor_state()
    if telemetry_error is not None:
        states = {'0x30': 'unknown', '0x40': 'unknown'}
        tach_source = 'raw-m1'
        rpm_unavailable = set(FAN_REGS.values())
    hwmon = _find_emc2305(controller_device)
    config = configuration_status(
        state_path=Path(f'{SYS_ROOT}/run/vyos-fan-zones.json'),
        pending_path=Path(f'{SYS_ROOT}/run/vyos-fan-pending'),
        applied_path=Path(f'{SYS_ROOT}/run/fan-pid/applied.json'))
    if telemetry_error is not None:
        config['message'] += '; fan telemetry unavailable: ' + telemetry_error
        config['state'] = 'error'
    zones = _read_zones(zones_found, config['zones'])
    failed_sensors = [zone['zone'] for zone in zones
                      if zone['state'] in ('missing', 'unreadable', 'fault')]
    if failed_sensors:
        config['message'] += '; sensors unavailable: ' + ', '.join(failed_sensors)
        if config['state'] == 'ok':
            config['state'] = 'error'
    if hwmon is None or not (os.path.exists(f'{hwmon}/pwm1') and os.path.exists(f'{hwmon}/fan1_input')):
        config['message'] += '; EMC2305 unavailable, unbound, ambiguous, or missing required attributes'
        if config['state'] == 'ok':
            config['state'] = 'error'
    if hwmon is None:
        fans = _read_fans(None, {}, {}, None, {}, {'0x30': 'unknown', '0x40': 'unknown'}, set())
    else:
        fans = _read_fans(hwmon, floors, rpms, tach_source, presents, states, rpm_unavailable)
    failed_fans = [f"{fan['fan']} ({fan['state']})" for fan in fans
                   if fan['state'] in ('fault', 'stalled', 'read-error', 'no-tach')]
    if failed_fans:
        config['message'] += '; fan faults: ' + ', '.join(failed_fans)
        config['state'] = 'error'
    uncertain = [fan['fan'] for fan in fans if fan['state'] == 'recovering' or
                 (fan['fan'] == 'fan1' and fan['state'] in ('unknown', 'not present'))]
    if uncertain:
        config['message'] += '; fan status unconfirmed: ' + ', '.join(uncertain)
        if config['state'] == 'ok':
            config['state'] = 'error'

    if not raw:
        print('Fan and thermal status\n')
        print(f"  configuration: {config['state']} — {config['message']}\n")
        show_thermal_zones(zones)
        show_fans(hwmon, fans)
        return 0 if config['state'] == 'ok' else 1

    result = {'zones': zones, 'fans': fans,
              'configuration': {'state': config['state'], 'message': config['message']}}
    import json
    print(json.dumps(result, indent=2))
    return 0 if config['state'] == 'ok' else 1


def main():
    # op-mode scripts may receive arbitrary CLI arguments (e.g. '--help').
    # fan has no subcommands; accept 'raw' for machine-readable use.
    import sys
    raw = len(sys.argv) > 1 and sys.argv[1] == 'raw'
    raise SystemExit(show(raw=raw))


if __name__ == '__main__':
    main()
