"""Host contract tests for ASK2 Phase 1.1 YNL telemetry (NAT, first-seen, fallbacks)."""

from pathlib import Path
import re

import pytest
import yaml


pytestmark = pytest.mark.host
ROOT = Path(__file__).resolve().parents[1]
ASK_DIR = ROOT / "kernel/ask/oot-modules/ask"
YAML_SPEC = ROOT / "kernel/ask/uapi/ask.yaml"
UAPI_H = ASK_DIR / "include/uapi/linux/ask/ask.h"
INTERNAL_H = ASK_DIR / "include/ask_internal.h"
GENL_C = ASK_DIR / "ask_genl.c"
ATTR_C = ASK_DIR / "ask_genl_attr.c"
FLOW_C = ASK_DIR / "ask_flow.c"
OFFLOAD_C = ASK_DIR / "ask_flow_offload.c"
STATS_C = ASK_DIR / "ask_stats.c"
SHOW_OFFLOAD = ROOT / "data/vyos-1x-files/src/op_mode/show_offload.py"
SHOW_ASK = ROOT / "data/vyos-1x-files/src/op_mode/show_ask_offload.py"

EXPECTED_FLOW_NEW_ATTRS = [
    ("first-seen-ns", "ASK_FLOW_ATTR_FIRST_SEEN_NS", "u64", "NLA_U64"),
    ("nat-flags", "ASK_FLOW_ATTR_NAT_FLAGS", "u8", "NLA_U8"),
    ("nat-src-ip", "ASK_FLOW_ATTR_NAT_SRC_IP", "binary", "NLA_BINARY"),
    ("nat-dst-ip", "ASK_FLOW_ATTR_NAT_DST_IP", "binary", "NLA_BINARY"),
    ("nat-sport", "ASK_FLOW_ATTR_NAT_SPORT", "u16", "NLA_U16"),
    ("nat-dport", "ASK_FLOW_ATTR_NAT_DPORT", "u16", "NLA_U16"),
]

EXPECTED_FALLBACK_ATTRS = [
    ("admission-gate", "ASK_FALLBACK_ATTR_ADMISSION_GATE"),
    ("policy-fenced", "ASK_FALLBACK_ATTR_POLICY_FENCED"),
    ("unsupported-proto", "ASK_FALLBACK_ATTR_UNSUPPORTED_PROTO"),
    ("unsupported-action", "ASK_FALLBACK_ATTR_UNSUPPORTED_ACTION"),
    ("neigh-unresolved", "ASK_FALLBACK_ATTR_NEIGH_UNRESOLVED"),
    ("port-unengaged", "ASK_FALLBACK_ATTR_PORT_UNENGAGED"),
    ("max-flows", "ASK_FALLBACK_ATTR_MAX_FLOWS"),
    ("hw-error", "ASK_FALLBACK_ATTR_HW_ERROR"),
]


def _extract_enum_members(src: str, enum_name: str) -> list[str]:
    m = re.search(rf"enum\s+{enum_name}\s*\{{(.*?)\}};", src, re.S)
    assert m, f"enum {enum_name} not found"
    body = re.sub(r"/\*.*?\*/", "", m.group(1), flags=re.S)
    body = re.sub(r"//.*", "", body)
    members = []
    for tok in body.split(","):
        tok = tok.strip()
        if not tok:
            continue
        name = tok.split("=")[0].strip()
        if name and not name.startswith("__"):
            members.append(name)
    return members


def test_yaml_and_uapi_lockstep():
    spec = yaml.safe_load(YAML_SPEC.read_text())
    uapi = UAPI_H.read_text()

    sets = {s["name"]: s for s in spec["attribute-sets"]}
    assert "fallback" in sets
    assert "flow" in sets
    assert "info" in sets

    # Verify fallback attribute-set order matches enum ask_fallback_attr.
    fb_yaml = [a["name"] for a in sets["fallback"]["attributes"]]
    assert fb_yaml == [name for name, _ in EXPECTED_FALLBACK_ATTRS]
    for a in sets["fallback"]["attributes"]:
        assert a["type"] == "u64", a

    fb_enum = _extract_enum_members(uapi, "ask_fallback_attr")
    assert fb_enum[0] == "ASK_FALLBACK_ATTR_UNSPEC"
    assert fb_enum[1:] == [macro for _, macro in EXPECTED_FALLBACK_ATTRS]

    # Verify ASK_INFO_ATTR_FALLBACK in both ask.yaml and ask.h at the same index.
    info_yaml = [a["name"] for a in sets["info"]["attributes"]]
    info_enum = _extract_enum_members(uapi, "ask_info_attr")
    assert info_enum[0] == "ASK_INFO_ATTR_UNSPEC"
    assert len(info_yaml) == len(info_enum) - 1
    assert info_yaml[-1] == "fallback"
    assert info_enum[-1] == "ASK_INFO_ATTR_FALLBACK"
    fb_info_attr = sets["info"]["attributes"][-1]
    assert fb_info_attr["type"] == "nest"
    assert fb_info_attr["nested-attributes"] == "fallback"

    # Verify flow attribute-set order matches enum ask_flow_attr 1:1.
    flow_yaml_attrs = {a["name"]: a for a in sets["flow"]["attributes"]}
    flow_yaml_names = [a["name"] for a in sets["flow"]["attributes"]]
    flow_enum = _extract_enum_members(uapi, "ask_flow_attr")
    assert flow_enum[0] == "ASK_FLOW_ATTR_UNSPEC"
    assert len(flow_yaml_names) == len(flow_enum) - 1

    for yname, macro, ytype, _ in EXPECTED_FLOW_NEW_ATTRS:
        assert yname in flow_yaml_attrs
        assert flow_yaml_attrs[yname]["type"] == ytype
        if yname in ("nat-sport", "nat-dport"):
            assert flow_yaml_attrs[yname].get("byte-order") == "big-endian"
        y_idx = flow_yaml_names.index(yname) + 1
        assert flow_enum[y_idx] == macro

    # Verify dump-flows and get-flow ops expose all new flow attributes.
    ops = {o["name"]: o for o in spec["operations"]["list"]}
    for op_name, section in (("dump-flows", "dump"), ("get-flow", "do")):
        reply_attrs = ops[op_name][section]["reply"]["attributes"]
        for yname, _, _, _ in EXPECTED_FLOW_NEW_ATTRS:
            assert yname in reply_attrs, f"{yname} missing from {op_name} reply"

    # UAPI header defines ASK_NATF_* without kernel-only BIT().
    for bit_idx, name in enumerate(("ASK_NATF_SNAT", "ASK_NATF_DNAT", "ASK_NATF_SPAT", "ASK_NATF_DPAT")):
        assert re.search(rf"#define\s+{name}\s+\(1u\s*<<\s*{bit_idx}\)", uapi), name
    assert "BIT(" not in uapi


def test_genl_policies_and_emitters():
    attr = ATTR_C.read_text()
    genl = GENL_C.read_text()

    # ask_fallback_policy must be defined, exported, and referenced from ask_info_policy.
    assert "const struct nla_policy ask_fallback_policy[ASK_FALLBACK_ATTR_MAX + 1]" in attr
    assert "EXPORT_SYMBOL_GPL(ask_fallback_policy);" in attr
    for _, macro in EXPECTED_FALLBACK_ATTRS:
        assert f"[{macro}]" in attr
    assert "[ASK_INFO_ATTR_FALLBACK]" in attr
    assert "NLA_POLICY_NESTED(ask_fallback_policy)" in attr

    # ask_flow_policy covers all new flow attrs so strict nla_parse() succeeds.
    fp_start = attr.index("const struct nla_policy ask_flow_policy[")
    fp = attr[fp_start:attr.index("\n};", fp_start) + 3]
    for _, macro, _, nla_type in EXPECTED_FLOW_NEW_ATTRS:
        assert f"[{macro}]" in fp and nla_type in fp

    # ask_genl_fill_one_flow emits first_seen_ns and conditional NAT attrs.
    ff_start = genl.index("int ask_genl_fill_one_flow(")
    ff = genl[ff_start:genl.index("EXPORT_SYMBOL_GPL(ask_genl_fill_one_flow);", ff_start)]
    assert "ASK_FLOW_ATTR_FIRST_SEEN_NS" in ff
    assert "f->first_seen_ns" in ff
    assert "ASK_FLOW_ATTR_NAT_FLAGS" in ff
    assert "ASK_FLOW_ATTR_NAT_SRC_IP" in ff
    assert "ASK_FLOW_ATTR_NAT_DST_IP" in ff
    assert "ASK_FLOW_ATTR_NAT_SPORT" in ff
    assert "ASK_FLOW_ATTR_NAT_DPORT" in ff

    # ask_genl_get_info_fill emits nested ASK_INFO_ATTR_FALLBACK.
    gi_start = genl.index("int ask_genl_get_info_fill(")
    gi = genl[gi_start:genl.index("EXPORT_SYMBOL_GPL(ask_genl_get_info_fill);", gi_start)]
    assert "ASK_INFO_ATTR_FALLBACK" in gi
    assert "ask_fallback_snapshot" in gi
    for _, macro in EXPECTED_FALLBACK_ATTRS:
        assert macro in gi


def test_flow_first_seen_and_fallback_instrumentation():
    flow_c = FLOW_C.read_text()
    offload_c = OFFLOAD_C.read_text()
    stats_c = STATS_C.read_text()
    internal_h = INTERNAL_H.read_text()

    assert "u64 first_seen_ns;" in internal_h
    assert "int hw_fallback_rc;" in internal_h
    assert "f->first_seen_ns = ktime_get_ns();" in flow_c
    assert "f->hw_fallback_rc = rc;" in flow_c
    # S7 unlocked admission-gate fast path must also increment admission-gate.
    assert re.search(
        r"if \(ask_flow_replace_deferred_unlocked\(cb_priv, f\)\)\s*\{\s*"
        r"ask_fallback_inc\(ASK_FALLBACK_ADMISSION_GATE\);",
        offload_c,
    )

    # Fallback counter storage lives in ask_stats.c with exported helpers.
    assert "void ask_fallback_inc(" in stats_c
    assert "void ask_fallback_snapshot(" in stats_c

    # Every fallback reason is actually incremented somewhere in ask_flow*.c.
    combined = flow_c + "\n" + offload_c
    for reason in (
        "ASK_FALLBACK_ADMISSION_GATE",
        "ASK_FALLBACK_POLICY_FENCED",
        "ASK_FALLBACK_UNSUPPORTED_PROTO",
        "ASK_FALLBACK_UNSUPPORTED_ACTION",
        "ASK_FALLBACK_NEIGH_UNRESOLVED",
        "ASK_FALLBACK_PORT_UNENGAGED",
        "ASK_FALLBACK_MAX_FLOWS",
        "ASK_FALLBACK_HW_ERROR",
    ):
        assert f"ask_fallback_inc({reason})" in combined, reason


def test_vyos_cli_renders_nat_age_and_fallbacks():
    so = SHOW_OFFLOAD.read_text()
    sa = SHOW_ASK.read_text()

    for txt in (so, sa):
        assert "nat-flags" in txt
        assert "nat-src-ip" in txt
        assert "nat-dst-ip" in txt
        assert "nat-sport" in txt
        assert "nat-dport" in txt
        assert "first-seen-ns" in txt

    assert "fallback" in so
    assert "get-info" in so


def test_vyos_cli_formatting_helpers(monkeypatch):
    import importlib.util
    import sys
    import types

    # Stub VyOS runtime modules not installed on the build host.
    vyos_mod = types.ModuleType("vyos")
    cfg_mod = types.ModuleType("vyos.configquery")
    cfg_mod.ConfigTreeQuery = object
    utils_mod = types.ModuleType("vyos.utils")
    proc_mod = types.ModuleType("vyos.utils.process")
    proc_mod.rc_cmd = lambda cmd: (0, "")
    tab_mod = types.ModuleType("tabulate")
    tab_mod.tabulate = lambda rows, headers=(), tablefmt="simple": str(rows)

    for name, mod in (
        ("vyos", vyos_mod),
        ("vyos.configquery", cfg_mod),
        ("vyos.utils", utils_mod),
        ("vyos.utils.process", proc_mod),
        ("tabulate", tab_mod),
    ):
        monkeypatch.setitem(sys.modules, name, mod)

    def _load(path: Path, modname: str):
        spec = importlib.util.spec_from_file_location(modname, path)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        return m

    so = _load(SHOW_OFFLOAD, "show_offload_test")
    sa = _load(SHOW_ASK, "show_ask_offload_test")

    for mod in (so, sa):
        assert mod._fmt_nat({}) == "-"
        assert mod._fmt_nat({
            "nat-flags": 1 | 4,
            "nat-src-ip": "0a6302b9",
            "nat-sport": 41234,
        }) == "snat 10.99.2.185:41234"
        assert mod._fmt_nat({
            "nat-flags": 2 | 8,
            "nat-dst-ip": "0a63010a",
            "nat-dport": 8080,
        }) == "dnat 10.99.1.10:8080"
        assert mod._fmt_nat({
            "nat-flags": 1,
            "nat-src-ip": "fd990002000000000000000000000185",
        }) == "snat fd99:2::185"
        # 16-byte IPv6 addresses whose lower 12 bytes are zero must stay IPv6.
        assert mod._fmt_ip("fe800000000000000000000000000000") == "fe80::"
        assert mod._fmt_ip("fd990002000000000000000000000000") == "fd99:2::"
        assert mod._fmt_ip("00000000000000000000000000000000") == "::"
        assert mod._fmt_ip("0a6302b9") == "10.99.2.185"
        assert mod._fmt_age(None, 10_000_000_000) == "-"
        assert mod._fmt_age(0, 10_000_000_000) == "-"
        assert mod._fmt_age(5_000_000_000, 17_500_000_000) == "12s"
        assert mod._fmt_age(0 + 1_000_000_000, 126_000_000_000) == "2m05s"
        assert mod._fmt_age(1_000_000_000, 3_721_000_000_000) == "1h02m"

    assert so._fmt_fallbacks({}) is None
    assert so._fmt_fallbacks({"fallback": {"max-flows": 0, "hw-error": 0}}) == "none"
    assert so._fmt_fallbacks({
        "fallback": {
            "admission-gate": 12,
            "unsupported-action": 3,
            "max-flows": 0,
        }
    }) == "admission-gate=12, unsupported-action=3"
