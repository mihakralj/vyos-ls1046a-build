#!/usr/bin/env python3
"""Stateful ssh stand-in for tests/testrig_ask2_9k.py (DUT1 + two generators)."""

import json
import os
import re
import sys

STATE = os.environ["FAKE_STATE"]
LOG = os.environ["CALLS"]


def load():
    with open(STATE) as f:
        return json.load(f)


def save(state):
    with open(STATE, "w") as f:
        json.dump(state, f)


def log(entry):
    with open(LOG, "a") as f:
        f.write(entry + "\n")


args = sys.argv[1:]
while args and args[0].startswith("-"):
    opt = args.pop(0)
    if opt in ("-o", "-i"):
        args.pop(0)
target, command = args[0], " ".join(args[1:])
state = load()
host = {"test@dut": "dut", "test@gen1": "g1", "test@gen2": "g2"}[target]

if host == "dut":
    if command == "vbash -s":
        script = sys.stdin.read()
        for line in script.splitlines():
            m = re.match(r"(set|delete) interfaces ethernet (eth[34])( vif (\d+))? (mtu|offload) ?(\S*)", line)
            if not m:
                continue
            verb, eth, _, vif, kind, value = m.groups()
            log(f"dut {verb} {eth}{'.' + vif if vif else ''} {kind} {value}".rstrip())
            if kind == "offload":
                state["offload"][f"{eth} {value}"] = verb == "set"
            else:
                node = f"{eth}.{vif}" if vif else eth
                if verb == "set" and state["offload_active"] and os.environ.get("REQUIRE_DISARMED") == "1":
                    log("VIOLATION mtu change while armed")
                state["cfg"][node] = value if verb == "set" else None
                state["mtu"][node] = int(value) if verb == "set" else 1500
        state["offload_active"] = all(state["offload"].values())
        save(state)
        print("[edit]")
        sys.exit(0)
    if "boot_id" in command:
        print(state["boot"])
        sys.exit(0)
    if "fe_arm" in command:
        armed = "0x09 0x0d 0x10 0x11" if state["offload_active"] else "0x09 0x0d"
        print(f"Armed ports: {armed}\nFaulted ports: (none)\nFE fault/staging: no")
        sys.exit(0)
    m = re.match(r"cli-shell-api (returnActiveValue|existsActive) interfaces ethernet (eth[34])( vif (\d+))? (mtu|offload) ?(\S*)", command)
    if m:
        api, eth, _, vif, kind, fam = m.groups()
        if api == "returnActiveValue" and os.environ.get("FAIL_CONFIG_QUERY") == "1":
            sys.exit(255)
        if kind == "offload":
            sys.exit(0 if state["offload"][f"{eth} {fam}"] else 1)
        value = state["cfg"][f"{eth}.{vif}" if vif else eth]
        if value is None:
            sys.exit(1)
        if api == "returnActiveValue":
            print(value)
        sys.exit(0)
    if command.startswith("cat /sys/class/net/"):
        for dev in re.findall(r"/sys/class/net/([^/]+)/mtu", command):
            print(state["mtu"][dev])
        sys.exit(0)
    sys.exit(f"unexpected dut command: {command}")

links = state["gen"][host]
if "ping" in command:
    ndp = "-s 56" in command
    log(f"{host} {'ndp' if ndp else 'ping'}")
    sys.exit(1 if os.environ.get("FAIL_PING") == "1" and not ndp else 0)
if "/ifindex" in command:
    dev = re.search(r"d=(\S+);", command).group(1)
    link = links[dev]
    print(f"{link['index']} {link['link']} {link['mac']} {link['mtu']} {link['owner'] or '-'}")
    sys.exit(0)
m = re.match(r"sudo -n sh -s -- (\w+) (\S+) (\d+) (\d+) (\S+) (\S+) (\d+) (\d+)$", command)
if m:
    sys.stdin.read()
    op, dev, _, _, _, owner, before, after = m.groups()
    link = links[dev]
    log(f"{host} {op} {dev} {before}->{after}")
    if op == "claim":
        if link["owner"] or link["mtu"] != int(before):
            sys.exit(1)
        if os.environ.get("FAIL_CLAIM_ON_VLAN") == "1" and host == "g1" and dev.endswith(".10"):
            sys.exit(1)
        link["owner"] = owner
        if os.environ.get("CHANGE_CONFIG_ON_CLAIM") == "1" and host == "g2" and dev.endswith(".20"):
            state["cfg"]["eth4"] = "1600"
            state["mtu"]["eth4"] = 1600
    elif op == "set":
        if link["owner"] != owner:
            sys.exit(1)
        link["mtu"] = int(after)
        if os.environ.get("CHANGE_CONFIG_ON_RESTORE") == "1" and after == "1500":
            state["cfg"]["eth4"] = "1600"
            state["mtu"]["eth4"] = 1600
    elif op == "check":
        if link["owner"] != owner or link["mtu"] != int(before):
            sys.exit(1)
    elif op == "release":
        if link["mtu"] != 1500:
            sys.exit(1)
        if link["owner"] and link["owner"] != owner:
            sys.exit(1)
        link["owner"] = None
    save(state)
    sys.exit(0)
sys.exit(f"unexpected {host} command: {command}")
