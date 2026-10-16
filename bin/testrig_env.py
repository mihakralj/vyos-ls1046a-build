"""Rig settings for the Python testrig drivers; same sources and rules as bin/testrig-env.sh."""
import os
import re
import sys

DEFAULTS = {
    "RIG_KEY": "", "RIG_OUT": "~/testrig/out",
    "GEN_IF": "enp1s0f0np0", "GEN_IF_NS": "enp1s0f1np1", "VENDOR_NS": "n106",
    "PPPOE_USER": "pppoe-test", "PPPOE_PLUGIN": "/usr/lib/pppd/2.5.2/rp-pppoe.so",
}


def _expand(value):
    value = value.strip('"').strip("'")
    if value.startswith("~/"):
        value = os.path.join(os.path.expanduser("~"), value[2:])
    return value.replace("$HOME", os.path.expanduser("~"))


def load():
    """Environment first, then ${TESTRIG_ENV:-~/.config/testrig.env}, then DEFAULTS."""
    path = os.environ.get("TESTRIG_ENV") or os.path.expanduser("~/.config/testrig.env")
    settings = {}
    if os.access(path, os.R_OK):
        with open(path) as f:
            for line in f:
                line = line.rstrip("\r\n")
                if not line or line.startswith("#"):
                    continue
                key, _, value = line.partition("=")
                if re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
                    settings[key] = _expand(value)
    for key, value in DEFAULTS.items():
        settings.setdefault(key, _expand(value))
    for key, value in os.environ.items():
        if value:
            settings[key] = value
    settings["_FILE"] = path
    return settings


def require(settings, *keys):
    missing = [k for k in keys if not settings.get(k)]
    if missing:
        sys.exit(f"ERROR: set {' '.join(missing)} in the environment or in {settings['_FILE']} "
                 "(template: bin/testrig.env.example)")
    return [settings[k] for k in keys]


def key_args(settings):
    return ["-i", settings["RIG_KEY"]] if settings.get("RIG_KEY") else []
