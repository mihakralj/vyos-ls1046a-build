---
name: vyos-scripting
description: Configure and operate VyOS programmatically via command scripting (vbash + script-template) and the PyVyOS Python library. Triggers on "vyos scripting", "vyos command scripting", "configure vyos via script", "pyvyos", "vyos api", "automate vyos", or any request to drive VyOS config non-interactively from a shell or Python script.
---

# vyos-scripting

Two supported ways to drive VyOS non-interactively:

1. **Command scripting** — bash scripts (`#!/bin/vbash`) that source VyOS's shell
   functions and run `configure`/`set`/`commit`/`run` directly on the box.
2. **PyVyOS** — a Python library that talks to the VyOS HTTP API from a remote
   host.

Use command scripting for on-box automation (commit hooks, boot scripts, cron,
VRRP transitions). Use PyVyOS for remote/orchestrated management (Ansible-style,
CI, fleet config).

---

## 1. Command scripting (on-box)

### 1.1 Boilerplate

Every script MUST source the VyOS function library first:

```bash
#!/bin/vbash
source /opt/vyatta/etc/functions/script-template
exit
```

### 1.2 Run configuration commands

Present config commands exactly as in an interactive `configure` session:

```bash
#!/bin/vbash
source /opt/vyatta/etc/functions/script-template
configure
set protocols bgp system-as 65536
set protocols bgp neighbor 192.168.2.1 shutdown
commit
exit
```

### 1.3 Run operational commands

**Always** prefix operational commands with `run`:

```bash
#!/bin/vbash
source /opt/vyatta/etc/functions/script-template
run show interfaces
exit
```

### 1.4 Run commands remotely over SSH

Pass a script block to a remote VyOS system via `vbash -s`:

```bash
ssh 192.0.2.1 'vbash -s' <<EOF
source /opt/vyatta/etc/functions/script-template
run show interfaces
exit
EOF
```

### 1.5 Non-bash script languages

Have the script **print** VyOS commands, then `source` that output from a bash
script:

```python
#!/usr/bin/env python3
print("delete firewall group address-group somehosts")
print("set firewall group address-group somehosts address '192.0.2.3'")
print("set firewall group address-group somehosts address '203.0.113.55'")
```

```bash
#!/bin/vbash
source /opt/vyatta/etc/functions/script-template
configure
source <(/config/scripts/setfirewallgroup.py)
commit
```

### 1.6 Permissions / group guard (critical)

- Scripts in `/config/scripts/` are **not** executable by default — run
  `chmod +x /config/scripts/script-name.sh`.
- Do **not** prefix a config-modifying script with `sudo` — subsequent manual
  config changes fail with `Set failed` and require a reboot. Run under the
  `vyattacfg` group with `sg`:

```bash
sg vyattacfg -c ./myscript.sh
```

Safeguard the script itself:

```bash
if [ "$(id -g -n)" != 'vyattacfg' ] ; then
    exec sg vyattacfg -c "/bin/vbash $(readlink -f $0) $@"
fi
```

### 1.7 Commit pre/post hooks

Scripts run **before** and **after** each commit, in alphabetical order:

- `/config/scripts/commit/pre-hooks.d/`
- `/config/scripts/commit/post-hooks.d/`

- Filenames: ASCII letters, digits, `_`, `-` only.
- Hooks run **without** root — prefix specific commands with `sudo` when needed.

### 1.8 Boot-time scripts

- `/config/scripts/vyos-preconfig-bootup.script` — runs **before** config is
  applied (pre-configuration workarounds).
- `/config/scripts/vyos-postconfig-bootup.script` — runs **after** config is
  applied (post-configuration workarounds).

Use these only as a last resort; prefer CLI-based solutions.

---

## 2. PyVyOS (remote, Python)

PyVyOS configures VyOS through its HTTP API.

- Docs: https://pyvyos.readthedocs.io/en/latest/
- Repo: https://github.com/robertoberto/pyvyos
- PyPI: https://pypi.org/project/pyvyos/

### 2.1 Install

```bash
pip install pyvyos
```

### 2.2 Initialize a device

```python
import os, urllib3
from dataclasses import dataclass
from dotenv import load_dotenv
from pyvyos import VyDevice

urllib3.disable_warnings()
load_dotenv()

@dataclass
class ApiResponse:
    status: int
    request: dict
    result: dict
    error: str

verify_ssl = os.getenv('VYDEVICE_VERIFY_SSL')
verify = verify_ssl.lower() == "true" if verify_ssl else True

device = VyDevice(
    hostname=os.getenv('VYDEVICE_HOSTNAME'),
    apikey=os.getenv('VYDEVICE_APIKEY'),
    port=os.getenv('VYDEVICE_PORT'),
    protocol=os.getenv('VYDEVICE_PROTOCOL'),
    verify=verify,
)
```

### 2.3 Common operations

Paths are lists of config-path components (e.g.
`["interfaces", "ethernet", "eth0", "address", "192.168.1.1/24"]`).

```python
# Set a value
r = device.configure_set(path=["interfaces", "ethernet", "eth0", "address", "192.168.1.1/24"])

# Read a single value
r = device.retrieve_return_values(path=["interfaces", "dummy", "dum1", "address"])

# Show full config
r = device.retrieve_show_config(path=[])

# Delete an object
r = device.configure_delete(path=["interfaces", "dummy", "dum1"])

# Save config (running -> startup)
r = device.config_file_save()

# Save to a specific file
r = device.config_file_save(file="/config/test300.config")

# Load config from file
r = device.config_file_load(file="/config/test300.config")

# Show a path
r = device.show(path=["system", "image"])

# Generate an object (e.g. SSH client key)
r = device.generate(path=["ssh", "client-key", "/tmp/key_abc123"])

# Reset an object
r = device.reset(path=["conntrack-sync", "internal-cache"])
```

Check `response.error` before using `response.result`:

```python
if not r.error:
    print(r.result)
```

---

## 3. Choosing an approach

| Need | Use |
|---|---|
| Commit hook, boot script, VRRP transition, cron on-box | Command scripting (§1) |
| Remote/orchestrated config, fleet, CI | PyVyOS (§2) |
| One-off remote op command | `ssh host 'vbash -s'` (§1.4) |

## 4. Gotchas

- Operational commands need the `run` prefix in scripts (§1.3).
- `sudo` on a config script breaks later interactive config — use `sg vyattacfg` (§1.6).
- Hooks and boot scripts run without root (§1.7, §1.8).
- PyVyOS needs an API key and the `verify` flag set correctly for self-signed certs (§2.2).
