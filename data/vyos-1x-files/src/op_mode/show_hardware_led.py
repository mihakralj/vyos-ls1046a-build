#!/usr/bin/env python3

from vyos import ConfigError
from vyos.led_control import CHANNELS, LEDS, PENDING, read_state


def main():
    try:
        config = read_state(require_exists=True)
    except (ConfigError, OSError, ValueError):
        config = None
    mode = config.get('mode', 'auto') if config is not None else 'unavailable'
    print(f"Mode: {mode}{' (pending)' if PENDING.exists() else ''}")
    if config and 'color' in config:
        print(f"Configured color: {config['color'].upper()}")
    try:
        values = [int((LEDS / f'status:{channel}' / 'brightness').read_text())
                  for channel in CHANNELS]
        if any(value < 0 or value > 255 for value in values):
            raise ValueError('invalid brightness')
        print('Current color: ' + ''.join(f'{value:02X}' for value in values))
    except (OSError, ValueError):
        print('Current color: unavailable')


if __name__ == '__main__':
    main()
