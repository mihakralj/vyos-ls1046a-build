"""SSH management and data-plane profiles for the two independent lab DUTs."""

from __future__ import annotations

import json
import io
import os
import re
import shlex
import subprocess
import zipfile
from dataclasses import dataclass
from contextlib import contextmanager
from pathlib import Path

from .uart import RunResult


SUITE_ROOT = Path(__file__).resolve().parents[1]
NAME = re.compile(r"[A-Za-z0-9_.:-]+\Z")
DUT_ALIASES = {"dut1": "dut1", "185": "dut1", "dut2": "dut2", "106": "dut2"}


def source_archive(root, *, prefix=""):
    output = io.BytesIO()
    excluded = {".git", ".venv", ".pytest_cache", "__pycache__", "artifacts"}
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for directory, folders, files in os.walk(root):
            folders[:] = sorted(name for name in folders if name not in excluded)
            for name in sorted(files):
                path = Path(directory) / name
                if path.is_file() and not path.is_symlink() and path.suffix != ".pyc":
                    archive.write(path, Path(prefix) / path.relative_to(root))
    return output.getvalue()


@dataclass(frozen=True)
class Endpoint:
    role: str
    host: str
    user: str
    identity: str = ""
    namespace: str = ""

    def __post_init__(self):
        for name, value in (("host", self.host), ("user", self.user)):
            if not value or value.startswith("-") or not NAME.fullmatch(value):
                raise ValueError(f"invalid SSH {name}: {value!r}")
        if self.namespace and not re.fullmatch(r"[A-Za-z0-9_-]+", self.namespace):
            raise ValueError("invalid data-plane namespace")

    @classmethod
    def from_env(cls, role):
        prefix = f"ASK_{role.upper()}_SSH_"
        return cls(role, os.environ[prefix + "HOST"], os.environ[prefix + "USER"],
                   os.environ.get(prefix + "KEY", ""),
                   os.environ.get(f"ASK_{role.upper()}_NETNS", ""))

    def ssh(self, *, forward_agent=False):
        argv = ["ssh", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8",
                "-o", "ServerAliveInterval=5", "-o", "ServerAliveCountMax=2"]
        if forward_agent:
            argv.append("-A")
        if os.environ.get("ASK_SSH_KNOWN_HOSTS"):
            argv += ["-o", "UserKnownHostsFile=" + os.environ["ASK_SSH_KNOWN_HOSTS"],
                     "-o", "StrictHostKeyChecking=yes"]
        if self.identity:
            identity = Path(self.identity).expanduser()
            if identity.is_file():
                argv += ["-i", str(identity), "-o", "IdentitiesOnly=yes"]
            elif not os.environ.get("SSH_AUTH_SOCK"):
                raise FileNotFoundError(f"SSH identity missing: {identity}; configure a key or ssh-agent")
        if os.environ.get("ASK_RUNNER_NETNS"):
            argv = ["nsenter", "--net=/proc/1/ns/net", *argv]
        return argv

    def command(self, argv, *, privileged=True, management=False, forward_agent=False):
        remote = list(argv)
        namespace = "" if management else self.namespace
        if namespace:
            remote = ["ip", "netns", "exec", namespace, *remote]
        if (privileged or namespace) and self.user != "root":
            preserve = ["--preserve-env=SSH_AUTH_SOCK"] if forward_agent else []
            remote = ["sudo", "-n", *preserve, *remote]
        return [*self.ssh(forward_agent=forward_agent), f"{self.user}@{self.host}",
                shlex.join(remote)]

    def execute(self, argv, *, data=b"", timeout=30, privileged=True, management=False):
        bounded = ["timeout", "-k", "2", str(timeout), *argv]
        try:
            result = subprocess.run(self.command(bounded, privileged=privileged,
                                                 management=management), input=data,
                                    capture_output=True, timeout=timeout + 12)
        except subprocess.TimeoutExpired as error:
            raise TimeoutError(f"{self.role}: result missing; operation outcome unknown; not replayed") from error
        output = result.stdout.decode(errors="replace") + result.stderr.decode(errors="replace")
        return RunResult(shlex.join(argv), output, result.returncode)

    def run(self, command, timeout=30, *, management=False):
        return self.execute(["/bin/sh", "-c", command], timeout=timeout, management=management)

    def python(self, source, timeout=30, *, management=False):
        result = self.execute(["python3", "-"], data=source.encode(), timeout=timeout,
                              management=management)
        return {"rc": result.rc, "stdout": result.stdout}


class SSHConsole:
    def __init__(self, role):
        self.endpoint = Endpoint.from_env(role)

    def run(self, command, timeout=20):
        return self.endpoint.run(command, timeout)

    def python(self, source, timeout=20):
        return self.endpoint.python(source, timeout)

    def login(self, *args, **kwargs):
        return None

    def sync_prompt(self, *args, **kwargs):
        result = self.endpoint.execute(["true"])
        if result.rc:
            raise ConnectionError(result.stdout)

    def close(self):
        return None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


@contextmanager
def temporary_wan_vlan(bench, name, vid):
    if not NAME.fullmatch(name) or name == bench.dut["nic"] or not 1 <= vid <= 4094:
        raise ValueError("invalid temporary WAN VLAN")
    endpoint = bench.endpoint("wan")
    shown = endpoint.execute(["ip", "-j", "-d", "link", "show", "dev", name])
    if shown.rc == 0:
        links = json.loads(shown.stdout)
        parent = endpoint.execute(["ip", "-j", "link", "show", "dev", bench.dut["nic"]])
        if parent.rc or len(links) != 1:
            raise RuntimeError("cannot validate existing WAN VLAN: " + shown.stdout)
        link = links[0]
        correct_parent = (link.get("link_index") == json.loads(parent.stdout)[0]["ifindex"]
                  or link.get("link") == bench.dut["nic"])
        if (link.get("linkinfo", {}).get("info_kind") != "vlan"
                or link["linkinfo"].get("info_data", {}).get("id") != vid
                or not correct_parent
                or "UP" not in link["flags"]):
            raise RuntimeError("existing WAN VLAN does not match the selected test path: " + shown.stdout)
        yield name
        return
    result = endpoint.execute(["ip", "link", "add", "link", bench.dut["nic"],
                               "name", name, "type", "vlan", "id", str(vid)])
    if result.rc:
        raise RuntimeError("WAN VLAN creation failed: " + result.stdout)
    try:
        result = endpoint.execute(["ip", "link", "set", "dev", name, "up"])
        if result.rc:
            raise RuntimeError("WAN VLAN activation failed: " + result.stdout)
        yield name
    finally:
        result = endpoint.execute(["ip", "link", "del", "dev", name])
        if result.rc:
            absent = endpoint.execute(["test", "!", "-e", "/sys/class/net/" + name])
            if absent.rc:
                raise RuntimeError("WAN VLAN cleanup failed: " + result.stdout)


@dataclass(frozen=True)
class Bench:
    target: str
    dut: dict
    lan: dict
    wan: dict

    @classmethod
    def load(cls, target="dut1", path=None):
        config = json.loads(Path(path or SUITE_ROOT / "bench.json").read_text())
        name = DUT_ALIASES[str(target)]
        legacy = "185" if name == "dut1" else "106"
        dut = config["duts"].get(name) or config["duts"][legacy]
        if dut["lan_if"] == dut["wan_if"] or "eth0" in (dut["lan_if"], dut["wan_if"]):
            raise ValueError("test ports must be distinct and must not include management eth0")
        if config["lan"]["host"] == config["wan"]["host"]:
            raise ValueError("the lab requires separate Dell LAN and WAN endpoints")
        return cls(name, dut, config["lan"], config["wan"])

    def endpoint(self, role):
        settings = self.dut if role == "dut" else getattr(self, role)
        namespace = "" if role == "dut" else self.dut["namespace"]
        return Endpoint(role, settings["host"], settings["user"],
                        settings.get("identity", ""), namespace)

    def environment(self, environ):
        env = dict(environ)
        pppoe_vid = int(environ.get("ASK_FLOWTABLE_PPPOE_VID", "3900"))
        multicast_vid = int(environ.get("ASK_MROUTE_WAN_VID", "3900"))
        env.setdefault("ASK_PPPOE_SERVER_IF", f"askwan{pppoe_vid}")
        env.setdefault("ASK_MROUTE_WAN_IF", f"askwan{multicast_vid}")
        env.setdefault("ASK_DHCP_WAN_IF", "askwan3900")
        env.update({
            "ASK_TRANSPORT": "ssh", "ASK_LAB_DUT": self.target,
            "ASK_DUT_KIND": self.dut["kind"],
            "ASK_TARGET_IP": self.dut["lan_address"],
            "ASK_TARGET_LAN_IF": self.dut["lan_if"],
            "ASK_TARGET_WAN_IF": self.dut["wan_if"],
            "ASK_LAN_VM": self.lan.get("name", "dell1"), "ASK_LAN_NIC": self.dut["nic"],
            "ASK_LAN_ADDRESS": self.lan["address"], "ASK_LAN_ADDRESS6": self.lan["address6"],
            "ASK_WAN_IP": "127.0.0.1", "ASK_WAN_IPERF_IP": self.wan["address"],
            "ASK_WAN_IPV6": self.wan["address6"], "ASK_WAN_INJECT_IF": self.dut["nic"],
        })
        for role in ("dut", "lan", "wan"):
            endpoint = self.endpoint(role)
            prefix = f"ASK_{role.upper()}_SSH_"
            env.update({prefix + "HOST": endpoint.host, prefix + "USER": endpoint.user,
                        prefix + "KEY": environ.get(prefix + "KEY", endpoint.identity),
                        f"ASK_{role.upper()}_NETNS": endpoint.namespace})
        return env

    def preflight(self):
        report = {"dut": self.target, "kind": self.dut["kind"], "nodes": {}}
        for role in ("dut", "lan", "wan"):
            endpoint = self.endpoint(role)
            interface = self.dut["lan_if"] if role == "dut" else self.dut["nic"]
            source = f'''
import importlib.util, json, os, pathlib, shutil, subprocess
interface = {interface!r}
addresses = subprocess.run(["ip", "-j", "addr", "show", "dev", interface], capture_output=True, text=True)
ports = {{}}
for device in {([self.dut['lan_if'], self.dut['wan_if']] if role == 'dut' else [interface])!r}:
    result = subprocess.run(["ip", "-j", "addr", "show", "dev", device], capture_output=True, text=True)
    ports[device] = json.loads(result.stdout) if result.returncode == 0 else []
config = pathlib.Path("/proc/config.gz")
caps = {{"native_flowtable": pathlib.Path("/proc/cdx_flowtable").exists(),
        "legacy_cdx": pathlib.Path("/sys/module/cdx").exists(),
        "ask2": pathlib.Path("/sys/module/ask").exists(),
        "ehash_stats": pathlib.Path("/sys/kernel/debug/fman_pcd/0/fe_ehash_stats").exists(),
        "kasan": False, "kmemleak": pathlib.Path("/sys/kernel/debug/kmemleak").exists(),
        "failslab": pathlib.Path("/sys/kernel/debug/failslab").exists(),
        "wifi": any(pathlib.Path("/sys/class/net").glob("*/wireless"))}}
if config.exists():
    import gzip
    caps["kasan"] = "CONFIG_KASAN=y" in gzip.decompress(config.read_bytes()).decode().splitlines()
tools = {{name: shutil.which(name) for name in ("python3", "timeout", "ip", "ethtool", "iperf3", "nft", "conntrack")}}
modules = {{name: importlib.util.find_spec(name) is not None for name in ("aiohttp", "yaml", "scapy")}}
print(json.dumps({{"kernel": os.uname().release, "uid": os.geteuid(), "interface": interface,
                  "addresses": json.loads(addresses.stdout) if addresses.returncode == 0 else [],
                  "ports": ports,
                  "tools": tools, "python_modules": modules, "capabilities": caps,
                  "ok": all(ports.values()) and all(tools[name] for name in ("python3", "timeout", "ip"))}}))
'''
            try:
                result = endpoint.python(source, timeout=20)
                if result["rc"]:
                    raise RuntimeError(result["stdout"])
                node = json.loads(result["stdout"])
            except (OSError, RuntimeError, ValueError, TimeoutError) as error:
                node = {"ok": False, "error": str(error)}
            settings = self.dut if role == "dut" else getattr(self, role)
            report["nodes"][role] = {"name": settings.get("name", self.target if role == "dut" else role),
                                     "host": endpoint.host, "namespace": endpoint.namespace, **node}
        report["ok"] = all(node["ok"] for node in report["nodes"].values())
        return report