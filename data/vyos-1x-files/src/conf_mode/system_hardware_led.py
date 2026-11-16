#!/usr/bin/env python3

import subprocess
import sys
import time

from vyos.utils.boot import boot_configuration_complete, boot_configuration_success
from vyos import ConfigError
from vyos.config import Config
from vyos.led_control import apply_state, auto_allowed, reconcile_state, validate


class LedSettings(dict):
    def __init__(self, settings, post_boot_delete=False, running_default=False):
        super().__init__(settings)
        self.post_boot_delete = post_boot_delete
        self.running_default = running_default


def get_config(config=None):
    conf = config if config is not None else Config()
    path = ['system', 'hardware', 'led']
    settings = conf.get_config_dict(path, get_first_key=True)
    post_boot_delete = False
    running_default = False
    if boot_configuration_complete() and conf.get_config_tree(effective=True) is not None:
        running = conf.get_config_dict(path, effective=True, get_first_key=True)
        post_boot_delete = not settings and conf.exists_effective(path)
        running_default = not running
    return LedSettings(settings, post_boot_delete, running_default)


def verify(config):
    validate(config)


def generate(config):
    pass


def apply(config):
    apply_state(config, pending=True,
                post_boot_delete=getattr(config, 'post_boot_delete', False),
                running_default=getattr(config, 'running_default', False))


if __name__ == '__main__':
    try:
        if sys.argv[1:] == ['--reconcile']:
            deadline = time.monotonic() + 1200
            while not boot_configuration_complete():
                if time.monotonic() >= deadline:
                    raise ConfigError('VyOS boot configuration did not complete')
                time.sleep(1)
            reconcile_state(allow_default=boot_configuration_success())
        elif sys.argv[1:] == ['--auto-allowed']:
            sys.exit(0 if auto_allowed() else 1)
        else:
            config = get_config()
            verify(config)
            generate(config)
            apply(config)
    except (ConfigError, OSError, ValueError, subprocess.SubprocessError) as error:
        print(error, file=sys.stderr)
        sys.exit(1)
