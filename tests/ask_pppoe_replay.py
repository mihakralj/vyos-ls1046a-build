"""Host regressions for PPPoE publication and session-port admission."""

from pathlib import Path
import subprocess

import pytest


pytestmark = pytest.mark.host
ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "kernel/ask/oot-modules/ask/ask_flow_offload.c"
HW_SOURCE = ROOT / "kernel/ask/oot-modules/ask/ask_hw.c"
HEADER = ROOT / "kernel/ask/oot-modules/ask/include/ask_internal.h"
HARNESS = ROOT / "tests/dut/ask_pppoe_replay.c"
LIVE_HARNESS = ROOT / "tests/dut/ask_pppoe_live_path.c"


def test_pppoe_replay_respects_the_global_gate(tmp_path):
    source = SOURCE.read_text()
    hw = HW_SOURCE.read_text()
    protocols = [
        line for line in HEADER.read_text().splitlines()
        if line.startswith(("#define ASK_FLOW_L3_IPV4 ", "#define ASK_FLOW_L3_IPV6 "))
    ]
    assert len(protocols) == 2
    (tmp_path / "ask_flow_protocols.h").write_text("\n".join(protocols) + "\n")
    pieces = []
    for prefix in ("static u8 ask_hw_port_family_get(",
                   "static void ask_hw_set_family_locked(",
                   "int ask_hw_flow_family_ok(",
                   "void ask_hw_offload_set_family(",
                   "int ask_hw_flow_policy_snapshot("):
        start = hw.index(prefix)
        end = hw.index("\n}\n", start) + len("\n}\n")
        pieces.append(hw[start:end])
    (tmp_path / "ask_hw_flow_family_body.h").write_text("\n".join(pieces))
    start = hw.index("int ask_hw_flow_publish_begin(")
    end = hw.index("\n/*\n * Control punt", start)
    guard = hw[start:end]
    guard_header = tmp_path / "ask_hw_publish_body.h"
    guard_header.write_text(guard)
    start = source.index("static int ask_fe_flow_insert(",
                         source.index("/* F-109: Direct kernel API"))
    end = source.index("\n/*\n * Fix B:", start)
    (tmp_path / "ask_fe_insert_body.h").write_text(source[start:end])
    start = source.index("static bool ask_pppoe_port_armed(int ifindex, u8 l3_proto)\n{")
    end = source.index("\n/*\n * G3", start)
    (tmp_path / "ask_pppoe_port_body.h").write_text(source[start:end])
    binary = tmp_path / "ask_pppoe_replay"
    command = [
        "cc", "-std=gnu11", "-Wall", "-Wextra", "-Werror", "-pthread",
        "-Wno-unused-function", "-Wno-unused-parameter",
        "-I", str(tmp_path), str(HARNESS), "-o", str(binary),
    ]
    subprocess.run(command, check=True)
    subprocess.run([str(binary)], check=True, timeout=30)

    family = "rc = ask_hw_flow_family_ok(key->port_id, key);"
    epoch = ("ask_hw_family_changed[key->port_id]\n"
             "\t\t[key->l3_proto == ASK_FLOW_L3_IPV6] > key->family_policy_seq")
    exhausted = "if (ask_hw_family_seq_exhausted) {"
    enq = "if (ask_hw_get_enq_fe_off() != enq_off) {"
    acquired = "*fm = h->fman;\n\treturn 0;"
    release = "mutex_unlock(&guard->lock);\n\tmodule_put(THIS_MODULE);"
    for text in (family, epoch, exhausted, enq, acquired, release):
        assert guard.count(text) == 1
    mutants = (
        guard.replace(family, "rc = 0;"),
        guard.replace(epoch, "false"),
        guard.replace(exhausted, "if (false) {"),
        guard.replace(enq, "if (false) {"),
        guard.replace(acquired, "*fm = h->fman;\n\tmutex_unlock(&h->lock);\n\treturn 0;")
             .replace(release, "module_put(THIS_MODULE);"),
    )
    for mutant in mutants:
        guard_header.write_text(mutant)
        subprocess.run(command, check=True)
        result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30)
        assert result.returncode == 1, result.stdout + result.stderr


def build_live_path_harness(tmp_path, mutation=None):
    source = SOURCE.read_text()
    header = HEADER.read_text()

    start = header.index("struct ask_pppoe_info {")
    end = header.index("\n};", start) + len("\n};")
    (tmp_path / "ask_pppoe_info.h").write_text(header[start:end])

    start = source.index("static bool ask_tuple_has_pppoe(")
    end = source.index("\nEXPORT_SYMBOL_GPL(ask_flow_cookie_pppoe);", start)
    (tmp_path / "ask_pppoe_cookie_body.h").write_text(source[start:end])

    start = source.index("static bool ask_pppoe_port_armed(int ifindex, u8 l3_proto)\n{")
    end = source.index("\n/*\n * G3", start)
    (tmp_path / "ask_pppoe_port_body.h").write_text(source[start:end])

    start = source.index("static bool ask_pppoe_path_current(")
    end = source.index("\n/* ------------------------------------------------------------------------- */", start)
    path = source[start:end]
    if mutation == "epoch":
        old = "!ask_pppoe_session_is_current(key->pppoe_session_epoch)"
        assert path.count(old) == 1
        path = path.replace(old, "false")
    elif mutation == "sid":
        old = "path->encap.id != info.sid"
        assert path.count(old) == 1
        path = path.replace(old, "false")
    (tmp_path / "ask_pppoe_path_body.h").write_text(path)

    start = source.index("static int ask_fe_flow_insert(",
                         source.index("/* F-109: Direct kernel API"))
    end = source.index("\n/*\n * Fix B:", start)
    insert = source[start:end]
    if mutation == "publication":
        old = "!ask_pppoe_path_current(key)"
        assert insert.count(old) == 1
        insert = insert.replace(old, "false")
    (tmp_path / "ask_fe_insert_body.h").write_text(insert)

    start = source.index("static int ask_flow_fe_install(")
    end = source.index("\nstatic bool ask_flow_token_is_current(", start)
    (tmp_path / "ask_flow_fe_install_body.h").write_text(source[start:end])
    start = source.index("static int ask_neigh_republish(",
                         source.index(" * A14: publish one cookie"))
    end = source.index("\n/*\n * ask_flow_neigh_mac_changed", start)
    (tmp_path / "ask_neigh_republish_body.h").write_text(source[start:end])

    binary = tmp_path / "ask_pppoe_live_path"
    subprocess.run(
        [
            "cc", "-std=gnu11", "-Wall", "-Wextra", "-Werror",
            "-Wno-unused-function", "-Wno-unused-parameter",
            "-I", str(tmp_path), str(LIVE_HARNESS), "-o", str(binary),
        ],
        check=True,
    )
    return binary


def test_pppoe_live_session_snapshot_and_replay(tmp_path):
    binary = build_live_path_harness(tmp_path)
    subprocess.run([str(binary)], check=True, timeout=30)

    for mutation in ("epoch", "sid", "publication"):
        mutant_dir = tmp_path / mutation
        mutant_dir.mkdir()
        mutant = build_live_path_harness(mutant_dir, mutation=mutation)
        result = subprocess.run([str(mutant)], timeout=30, cwd=mutant_dir)
        assert result.returncode == 1, mutation
