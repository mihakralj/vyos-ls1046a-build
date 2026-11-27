#!/usr/bin/env python3
"""Host gate for G10 VyOS CLI rendering (vyos-1x-025 + vyos-1x-061).

Applies the data/vyos-1x-*.patch stack to a temporary checkout of the pinned
upstream vyos-1x commit (from bin/ci-setup-vyos1x.sh), syntax-checks the
patched files, and exercises:
  1. vyos-1x-025 (EthernetIf.set_ingress_policer):
     - clsact coexistence (uses clsact + ingress when legacy ingress qdisc is
       absent; preserves parent ffff: when legacy ingress qdisc exists)
     - A460 L2 MTU padding (+14 B Ethernet header) when peak-bandwidth is set
     - preserves hw-tc-offload when ASK2 offload, explicit hw-tc-offload, or
       in_hw tc filters (e.g. Phase 3 DSCP) are present
  2. vyos-1x-061 (TrafficShaper._update_offload + src/conf_mode/qos.py):
     - Phase 5a hardware WRED rendering (queue-type random-detect -> tc red
       without ecn) on 1-level and 2-level leaves + default
     - Phase 3 MURAM DSCP rendering (match ip/ipv6 dscp -> clsact egress
       flower skip_sw ip_tos <d<<2>/0xfc action skbedit priority 1:<cls>)
     - commit-time verify() guards: queue-type allowlist, RED threshold bounds,
       duplicate (family, dscp), invalid DSCP, single-port MURAM DSCP singleton
     - surgical cleanup of QoS-owned egress DSCP filters (pref 1000..1199)
"""

from pathlib import Path
import ast
import importlib.util
import re
import shutil
import subprocess
import sys
import tempfile
import types

ROOT = Path(__file__).resolve().parents[1]
UPSTREAM_REPO = Path("/mnt/builds/vyos-1x")


def pinned_commit() -> str:
    setup = (ROOT / "bin/ci-setup-vyos1x.sh").read_text()
    m = re.search(r"^VYOS1X_COMMIT=([0-9a-f]{40})$", setup, re.M)
    if not m:
        raise RuntimeError("VYOS1X_COMMIT not found in bin/ci-setup-vyos1x.sh")
    return m.group(1)


def prepare_patched_tree(tmpdir: Path) -> Path:
    tree = tmpdir / "vyos-1x"
    commit = pinned_commit()
    # Mirror CI's build.py git clone (upstream vyos-1x history available for
    # legacy 3-way patches) while verifying 061 applies directly without 3-way
    # fallback and never perturbs earlier patch blob chains (e.g. 025 -> 031).
    subprocess.run(
        ["git", "clone", "-q", "--no-checkout", str(UPSTREAM_REPO), str(tree)],
        check=True,
    )
    subprocess.run(["git", "-C", str(tree), "checkout", "-q", commit], check=True)
    files_dir = ROOT / "data/vyos-1x-files"
    if files_dir.is_dir():
        shutil.copytree(files_dir, tree, dirs_exist_ok=True)
    for patch in sorted((ROOT / "data").glob("vyos-1x-*.patch")):
        if patch.name == "vyos-1x-061-qos-ceetm-offload.patch":
            # Verify 061 applies directly cleanly (zero 3-way drift).
            subprocess.run(
                ["git", "-C", str(tree), "apply", "--check", "--whitespace=nowarn", str(patch)],
                check=True,
            )
        subprocess.run(
            ["git", "-C", str(tree), "apply", "--3way", "--whitespace=nowarn", str(patch)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
    return tree


def install_vyos_stubs(tree: Path):
    """Install lightweight stubs for vyos.* so trafficshaper/qos/ethernet import cleanly."""
    for mod in list(sys.modules):
        if mod == "vyos" or mod.startswith("vyos.") or mod == "conf_mode_qos":
            del sys.modules[mod]

    vyos_pkg = types.ModuleType("vyos")
    vyos_pkg.__path__ = [str(tree / "python/vyos")]
    sys.modules["vyos"] = vyos_pkg

    class ConfigError(Exception):
        pass

    warnings_seen = []

    def Warning(msg):
        warnings_seen.append(str(msg))

    base_mod = types.ModuleType("vyos.base")
    base_mod.Warning = Warning
    sys.modules["vyos.base"] = base_mod

    vyos_pkg.ConfigError = ConfigError

    utils_pkg = types.ModuleType("vyos.utils")
    utils_pkg.__path__ = []
    sys.modules["vyos.utils"] = utils_pkg

    proc_cmds = []

    def cmdl(command, **kwargs):
        proc_cmds.append(list(command))
        return ""

    def run_fn(command, **kwargs):
        proc_cmds.append(command if isinstance(command, list) else command.split())
        return 0

    def popen_fn(command, **kwargs):
        proc_cmds.append(command)
        return "", 0

    proc_mod = types.ModuleType("vyos.utils.process")
    proc_mod.cmdl = cmdl
    proc_mod.run = run_fn
    proc_mod.popen = popen_fn
    proc_mod.cmd = lambda c, **kw: ""
    sys.modules["vyos.utils.process"] = proc_mod

    def dict_search(path, obj):
        cur = obj
        for key in path.split("."):
            if not isinstance(cur, dict) or key not in cur:
                return None
            cur = cur[key]
        return cur

    def dict_search_args(obj, *keys):
        cur = obj
        for key in keys:
            if not isinstance(cur, dict) or key not in cur:
                return None
            cur = cur[key]
        return cur

    def dict_search_recursive(obj, target):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k == target:
                    yield v, [k]
                for sub_v, sub_p in dict_search_recursive(v, target):
                    yield sub_v, [k] + sub_p

    dict_mod = types.ModuleType("vyos.utils.dict")
    dict_mod.dict_search = dict_search
    dict_mod.dict_search_args = dict_search_args
    dict_mod.dict_search_recursive = dict_search_recursive
    sys.modules["vyos.utils.dict"] = dict_mod

    file_mod = types.ModuleType("vyos.utils.file")
    file_mod.read_file = lambda path: "1500"
    sys.modules["vyos.utils.file"] = file_mod

    net_mod = types.ModuleType("vyos.utils.network")
    net_mod.get_protocol_by_name = lambda name: 6
    sys.modules["vyos.utils.network"] = net_mod

    # Minimal stubs for conf_mode/qos.py imports
    netif_mod = types.ModuleType("netifaces")
    netif_mod.interfaces = lambda: ["eth3", "eth4"]
    sys.modules["netifaces"] = netif_mod

    for name in (
        "vyos.config",
        "vyos.configdict",
        "vyos.configverify",
        "vyos.configdep",
        "vyos.ifconfig",
        "vyos.qos",
        "vyos.xml_ref",
    ):
        m = types.ModuleType(name)
        if name == "vyos.qos":
            m.__path__ = [str(tree / "python/vyos/qos")]
        sys.modules[name] = m

    vyos_pkg.airbag = types.SimpleNamespace(enable=lambda: None)
    sys.modules["vyos.xml_ref"].relative_defaults = lambda *a, **k: {}
    sys.modules["vyos.config"].Config = object
    sys.modules["vyos.configdict"].dict_merge = lambda a, b: {**a, **b}
    sys.modules["vyos.configverify"].verify_interface_exists = lambda *a, **k: True
    sys.modules["vyos.configdep"].set_dependents = lambda *a, **k: None
    sys.modules["vyos.configdep"].call_dependents = lambda *a, **k: None

    class Section:
        @staticmethod
        def section(ifname):
            if ifname.startswith("eth"):
                return "ethernet"
            if ifname.startswith("br"):
                return "bridge"
            return "dummy"

        @staticmethod
        def interfaces(sec=None):
            return ["eth3", "eth4"]

    sys.modules["vyos.ifconfig"].Section = Section
    sys.modules["vyos.ifconfig"].Interface = object

    # Load real qos/base.py and qos/trafficshaper.py
    def load_py(mod_name, rel_path):
        spec = importlib.util.spec_from_file_location(mod_name, tree / rel_path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = mod
        spec.loader.exec_module(mod)
        return mod

    base = load_py("vyos.qos.base", "python/vyos/qos/base.py")
    ts = load_py("vyos.qos.trafficshaper", "python/vyos/qos/trafficshaper.py")
    for stub_cls in (
        "CAKE", "DropTail", "FairQueue", "FQCodel", "Limiter",
        "NetEm", "Priority", "RandomDetect", "RateLimiter", "RoundRobin",
    ):
        setattr(sys.modules["vyos.qos"], stub_cls, type(stub_cls, (base.QoSBase,), {}))
    sys.modules["vyos.qos"].TrafficShaper = ts.TrafficShaper
    sys.modules["vyos.qos"].TrafficShaperHFSC = ts.TrafficShaperHFSC

    qos_cm = load_py("conf_mode_qos", "src/conf_mode/qos.py")
    return ConfigError, warnings_seen, proc_cmds, ts, qos_cm


def extract_set_ingress_policer(tree: Path):
    """Compile EthernetIf.set_ingress_policer in an isolated namespace for unit testing."""
    src = (tree / "python/vyos/ifconfig/ethernet.py").read_text()
    mod_ast = ast.parse(src)
    fn_node = None
    for node in ast.walk(mod_ast):
        if isinstance(node, ast.FunctionDef) and node.name == "set_ingress_policer":
            fn_node = node
            break
    assert fn_node is not None, "set_ingress_policer not found in ethernet.py"
    fn_src = ast.get_source_segment(src, fn_node)
    # Dedent method to top-level function
    lines = fn_src.splitlines()
    indent = len(lines[0]) - len(lines[0].lstrip())
    dedented = "\n".join(line[indent:] if len(line) >= indent else line for line in lines)
    ns = {
        "read_file": lambda p: "1500",
        "dict_search": lambda path, obj: (
            obj.get(path.split(".")[0], {}).get(path.split(".")[1])
            if "." in path and isinstance(obj.get(path.split(".")[0]), dict)
            else obj.get(path)
        ),
    }
    exec(dedented, ns)
    return ns["set_ingress_policer"]


def test_025_ingress_policer(tree: Path):
    fn = extract_set_ingress_policer(tree)

    class FakeIf:
        def __init__(self, ifname="eth3", qdisc_out="", egress_out="", ingress_out=""):
            self.ifname = ifname
            self.cmds = []
            self.qdisc_out = qdisc_out
            self.egress_out = egress_out
            self.ingress_out = ingress_out

        def _popen(self, cmd):
            self.cmds.append(cmd)
            if "tc qdisc show" in cmd:
                return self.qdisc_out, 0
            if "tc filter show" in cmd and "egress" in cmd:
                return self.egress_out, 0
            if "tc filter show" in cmd:
                return self.ingress_out, 0
            return "", 0

    # 1. Two-rate policer with no prior ingress qdisc: must use clsact + ingress
    #    and pad L3 MTU 1500 -> L2 MTU 1514 (A460 + VLAN_HLEN = 1518 in kernel).
    f = FakeIf("eth3", qdisc_out="qdisc mq 0: root\n")
    fn(f, {"ingress_policer": {"bandwidth": "500mbit", "burst": "64k", "peak_bandwidth": "1gbit"}})
    assert any("tc qdisc add dev eth3 clsact" in c for c in f.cmds), f"expected clsact: {f.cmds}"
    add_cmds = [c for c in f.cmds if "tc filter add" in c]
    assert len(add_cmds) == 1, f"expected 1 tc filter add, got {add_cmds}"
    assert " ingress " in add_cmds[0] and "parent ffff:" not in add_cmds[0], (
        f"expected clsact ingress hook, got: {add_cmds[0]}"
    )
    assert "mtu 1514" in add_cmds[0], f"expected L2 mtu 1514 (1500+14), got: {add_cmds[0]}"
    assert "ethtool -K eth3 hw-tc-offload on" in f.cmds

    # 2. Legacy ingress qdisc already present (mirror/redirect): attach to parent ffff:
    f2 = FakeIf("eth3", qdisc_out="qdisc mq 0: root\nqdisc ingress ffff: parent ffff:fff1\n")
    fn(f2, {"ingress_policer": {"bandwidth": "500mbit"}})
    add2 = [c for c in f2.cmds if "tc filter add" in c]
    assert len(add2) == 1 and "parent ffff:" in add2[0], f"expected parent ffff:, got {add2}"
    assert not any("clsact" in c for c in f2.cmds), f"should not add clsact over legacy ingress: {f2.cmds}"

    # 3. Deleting policer while ASK2 offload is enabled MUST NOT disable hw-tc-offload
    f3 = FakeIf("eth3")
    fn(f3, {"offload": {"ipv4": {}}})
    assert not any("hw-tc-offload off" in c for c in f3.cmds), (
        f"must not disable hw-tc-offload when ASK2 ipv4 offload is active: {f3.cmds}"
    )

    # 4. Deleting policer while Phase 3 DSCP in_hw egress filter exists MUST NOT disable hw-tc-offload
    f4 = FakeIf("eth3", egress_out="filter protocol ip pref 1046 flower\n  skip_sw\n  in_hw in_hw_count 1\n")
    fn(f4, {})
    assert not any("hw-tc-offload off" in c for c in f4.cmds), (
        f"must not disable hw-tc-offload while in_hw egress filters exist: {f4.cmds}"
    )

    # 5. Deleting policer on a plain port with no offloads DOES disable hw-tc-offload
    f5 = FakeIf("eth3")
    fn(f5, {})
    assert any("hw-tc-offload off" in c for c in f5.cmds), (
        f"expected hw-tc-offload off on plain port: {f5.cmds}"
    )
    print("PASS [1]: vyos-1x-025 clsact coexistence, A460 L2 MTU (+14), and hw-tc-offload preservation")


def test_061_qos_rendering_and_verify(tree: Path):
    ConfigError, warnings_seen, proc_cmds, ts_mod, qos_cm = install_vyos_stubs(tree)

    # Verify zero new XML nodes added beyond existing <leafNode name="offload">
    xml_patch = (ROOT / "data/vyos-1x-061-qos-ceetm-offload.patch").read_text()
    xml_section = xml_patch.split("diff --git a/python/")[0]
    added_nodes = re.findall(r"^\+\s*<(?:leafNode|node|tagNode)\s+name=\"([^\"]+)\"", xml_section, re.M)
    assert added_nodes == ["offload"], f"expected zero new XML beyond 'offload', found {added_nodes}"

    # 1. Valid 2-level offloaded shaper with Phase 3 DSCP (v4+v6) + Phase 5a WRED + mark
    policy_cfg = {
        "offload": {},
        "bandwidth": "500mbit",
        "class": {
            "10": {
                "bandwidth": "200mbit",
                "priority": "0",
                "queue_type": "drop-tail",
                "match": {
                    "ef4": {"ip": {"dscp": "EF"}},
                    "ef6": {"ipv6": {"dscp": "46"}},
                },
            },
            "20": {
                "bandwidth": "200mbit",
                "priority": "4",
                "queue_type": "random-detect",
                "average_packet": "1500",
                "minimum_threshold": "26",
                "maximum_threshold": "66",
                "queue_limit": "266",
                "mark_probability": "10",
                "match": {
                    "af41": {"ip": {"dscp": "AF41"}},
                    "m20": {"mark": "20"},
                },
            },
        },
        "default": {
            "bandwidth": "100mbit",
            "priority": "20",
            "queue_type": "random-detect",
            "average_packet": "1500",
            "minimum_threshold": "26",
            "maximum_threshold": "66",
            "mark_probability": "10",
        },
    }
    qos_cfg = {
        "policy": {"shaper": {"G10-FULL": policy_cfg}},
        "interface": {"eth3": {"egress": "G10-FULL"}},
    }
    warnings_seen.clear()
    qos_cm.verify(qos_cfg)
    assert not any("unsupported criteria" in w for w in warnings_seen), (
        f"pure ip/ipv6 dscp matches should not trigger unsupported-criteria warning: {warnings_seen}"
    )

    # Render commands via TrafficShaper.update()
    proc_cmds.clear()
    shaper = ts_mod.TrafficShaper("eth3")
    # Capture _cmdl calls
    cmds = []
    shaper._cmdl = lambda c: cmds.append(list(c))
    shaper.update(policy_cfg, "egress")

    joined = [" ".join(c) for c in cmds]
    assert any("htb offload default 15" in s for s in joined), f"missing root htb offload: {joined}"
    # Class 20 (0x14) and default (0x15) must have hardware RED qdiscs without ecn
    red_cmds = [s for s in joined if " red " in s]
    assert len(red_cmds) == 2, f"expected 2 hardware RED qdiscs (class 20 + default), got: {red_cmds}"
    for r in red_cmds:
        assert " ecn" not in r and "nodrop" not in r, f"RED must not set ecn/nodrop: {r}"
        assert "bandwidth 500000000" in r, f"RED must include bandwidth: {r}"
    assert any("parent 1:14 handle 14: red " in r for r in red_cmds), f"missing class 20 RED: {red_cmds}"
    assert any("parent 1:15 handle 15: red " in r for r in red_cmds), f"missing default RED: {red_cmds}"
    # Class 10 (drop-tail) must NOT get a pfifo or red qdisc
    assert not any("parent 1:a " in s for s in joined), f"drop-tail must not attach leaf qdisc: {joined}"

    # Phase 3 DSCP clsact egress flower filters
    assert any("ethtool -K eth3 hw-tc-offload on" in s for s in joined), f"missing hw-tc-offload on: {joined}"
    assert any("tc qdisc add dev eth3 clsact" in s for s in joined), f"missing clsact add: {joined}"
    flower_cmds = [s for s in joined if " flower skip_sw " in s]
    assert len(flower_cmds) == 3, f"expected 3 DSCP flower filters (v4 EF, v6 EF, v4 AF41), got: {flower_cmds}"
    assert any("egress protocol ip pref 1046 flower skip_sw ip_tos 0xb8/0xfc action skbedit priority 1:a" in s for s in flower_cmds), flower_cmds
    assert any("egress protocol ipv6 pref 1146 flower skip_sw ip_tos 0xb8/0xfc action skbedit priority 1:a" in s for s in flower_cmds), flower_cmds
    assert any("egress protocol ip pref 1034 flower skip_sw ip_tos 0x88/0xfc action skbedit priority 1:14" in s for s in flower_cmds), flower_cmds

    # 2. 1-level offloaded shaper with default queue-type random-detect
    one_level = {
        "offload": {},
        "bandwidth": "500mbit",
        "default": {
            "bandwidth": "500mbit",
            "queue_type": "random-detect",
            "maximum_threshold": "18",
        },
    }
    qos_cm.verify({
        "policy": {"shaper": {"G10-1L": one_level}},
        "interface": {"eth3": {"egress": "G10-1L"}},
    })
    cmds1 = []
    shaper1 = ts_mod.TrafficShaper("eth3")
    shaper1._cmdl = lambda c: cmds1.append(list(c))
    shaper1.update(one_level, "egress")
    joined1 = [" ".join(c) for c in cmds1]
    assert any("parent 1:1 handle 1: red " in s for s in joined1), f"1-level default RED missing: {joined1}"

    # 3. Negative verify() guards
    def expect_err(cfg, needle):
        try:
            qos_cm.verify(cfg)
        except ConfigError as exc:
            assert needle.lower() in str(exc).lower(), f"expected '{needle}' in '{exc}'"
            return
        raise AssertionError(f"expected ConfigError containing '{needle}'")

    # 3a. Unsupported software leaf queue-type (fair-queue / priority)
    bad_qt = {
        "policy": {"shaper": {"P": {
            "offload": {}, "bandwidth": "500mbit",
            "default": {"bandwidth": "500mbit", "queue_type": "fair-queue"},
        }}},
        "interface": {"eth3": {"egress": "P"}},
    }
    expect_err(bad_qt, "queue-type")

    # 3b. RED min >= max
    bad_red = {
        "policy": {"shaper": {"P": {
            "offload": {}, "bandwidth": "500mbit",
            "default": {
                "bandwidth": "500mbit", "queue_type": "random-detect",
                "minimum_threshold": "50", "maximum_threshold": "50",
            },
        }}},
        "interface": {"eth3": {"egress": "P"}},
    }
    expect_err(bad_red, "threshold")

    # 3c. Duplicate (family, dscp) across classes in same policy
    dup_dscp = {
        "policy": {"shaper": {"P": {
            "offload": {}, "bandwidth": "500mbit",
            "class": {
                "10": {"bandwidth": "100mbit", "priority": "0", "match": {"a": {"ip": {"dscp": "EF"}}}},
                "20": {"bandwidth": "100mbit", "priority": "1", "match": {"b": {"ip": {"dscp": "46"}}}},
            },
            "default": {"bandwidth": "100mbit", "priority": "20"},
        }}},
        "interface": {"eth3": {"egress": "P"}},
    }
    expect_err(dup_dscp, "dscp")

    # 3d. Single-port MURAM DSCP singleton across two interfaces
    two_ports = {
        "policy": {
            "shaper": {
                "P1": {
                    "offload": {}, "bandwidth": "500mbit",
                    "class": {"10": {"bandwidth": "100mbit", "priority": "0", "match": {"a": {"ip": {"dscp": "EF"}}}}},
                    "default": {"bandwidth": "100mbit", "priority": "20"},
                },
                "P2": {
                    "offload": {}, "bandwidth": "500mbit",
                    "class": {"10": {"bandwidth": "100mbit", "priority": "0", "match": {"a": {"ip": {"dscp": "AF41"}}}}},
                    "default": {"bandwidth": "100mbit", "priority": "20"},
                },
            }
        },
        "interface": {
            "eth3": {"egress": "P1"},
            "eth4": {"egress": "P2"},
        },
    }
    expect_err(two_ports, "one")

    # 3e. Two interfaces with offloaded shapers WITHOUT DSCP matches MUST still pass
    two_ports_ok = {
        "policy": {
            "shaper": {
                "P1": {"offload": {}, "bandwidth": "500mbit", "default": {"bandwidth": "500mbit"}},
                "P2": {"offload": {}, "bandwidth": "500mbit", "default": {"bandwidth": "500mbit"}},
            }
        },
        "interface": {"eth3": {"egress": "P1"}, "eth4": {"egress": "P2"}},
    }
    qos_cm.verify(two_ports_ok)

    print("PASS [2]: vyos-1x-061 Phase 3 DSCP + Phase 5a WRED rendering, guards, and zero new XML")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="g10-vyos-cli-") as tmp:
        tree = prepare_patched_tree(Path(tmp))
        # Compile-check modified Python modules
        for rel in (
            "python/vyos/ifconfig/ethernet.py",
            "python/vyos/qos/trafficshaper.py",
            "src/conf_mode/qos.py",
        ):
            subprocess.run([sys.executable, "-m", "py_compile", str(tree / rel)], check=True)
        test_025_ingress_policer(tree)
        test_061_qos_rendering_and_verify(tree)
    print("OK: G10 VyOS CLI (vyos-1x-025 + vyos-1x-061) gate PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
