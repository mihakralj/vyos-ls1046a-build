"""Host regression tests for deferred VLAN and PPPoE publication."""

from pathlib import Path
import subprocess

import pytest


pytestmark = pytest.mark.host
ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "kernel/ask/oot-modules/ask/ask_flow_offload.c"
HW_SOURCE = ROOT / "kernel/ask/oot-modules/ask/ask_hw.c"
HEADER = ROOT / "kernel/ask/oot-modules/ask/include/ask_internal.h"
HARNESS = ROOT / "tests/dut/ask_pending_capability.c"


def build_harness(tmp_path, *, omit_worker_guards=False,
                  omit_session_serialization=False, refresh_repark_epoch=False,
                  omit_session_fence=False, ignore_newer_session=False,
                  omit_family_event=False, omit_family_eligibility=False,
                  omit_repark_guard=False):
    source = SOURCE.read_text()
    protocols = [
        line for line in HEADER.read_text().splitlines()
        if line.startswith(("#define ASK_FLOW_L3_IPV4 ", "#define ASK_FLOW_L3_IPV6 "))
    ]
    assert len(protocols) == 2
    (tmp_path / "ask_flow_protocols.h").write_text("\n".join(protocols) + "\n")
    start = source.index("struct ask_flow_pending {")
    end = source.index("\n/*\n * PR14z10: match", start)
    definitions = source[start:end]
    if omit_family_eligibility:
        old = "if (!ask_hw_flow_policy_current(&p->key))\n\t\treturn false;"
        assert definitions.count(old) == 1
        definitions = definitions.replace(old, "if (false)\n\t\treturn false;")
    if omit_session_fence:
        assert definitions.count("!ask_pppoe_session_dirty") == 2
        definitions = definitions.replace("!ask_pppoe_session_dirty", "true")
    if ignore_newer_session:
        assert definitions.count("epoch != ask_pppoe_session_epoch") == 1
        definitions = definitions.replace("epoch != ask_pppoe_session_epoch", "false")
    (tmp_path / "pending_definitions.h").write_text(definitions)

    start = source.index("static struct ask_flow_pending *\nask_flow_pending_take_one")
    end = source.index("\n/*\n * ask_flow_neigh_resolved", start)
    (tmp_path / "pending_queue.h").write_text(source[start:end])

    start = source.index(" void ask_flow_neigh_resolved(")
    end = source.index("\nEXPORT_SYMBOL_GPL(ask_flow_neigh_resolved);", start)
    resolved = source[start:end]
    start = source.index("#define ASK_FLOW_PENDING_POLL_INTERVAL_MS")
    end = source.index("\n/* ------------------------------------------------------------------------- */", start)
    poll = source[start:end]
    if omit_worker_guards:
        guard = "if (!ask_flow_pending_eligible(p))"
        assert resolved.count(guard) == 2 and poll.count(guard) == 1
        resolved = resolved.replace(guard, "if (false && !ask_flow_pending_eligible(p))")
        poll = poll.replace(guard, "if (false && !ask_flow_pending_eligible(p))")
    if omit_repark_guard:
        guard = "if (!ask_flow_pending_eligible(p))\n\t\t\t\tgoto resolved_next;"
        assert resolved.count(guard) == 1
        resolved = resolved.replace(
            guard, "if (false && !ask_flow_pending_eligible(p))\n\t\t\t\tgoto resolved_next;"
        )
    if refresh_repark_epoch:
        old = "p->pppoe_session_epoch) == 0)"
        assert resolved.count(old) == 1
        resolved = resolved.replace(old, "ask_pppoe_session_snapshot()) == 0)")
    (tmp_path / "pending_workers.h").write_text(resolved + "\n" + poll)

    start = source.index("static int __ask_flow_capability_flush(")
    end = source.index("\nstatic void ask_pppoe_flush_fn(", start)
    (tmp_path / "pending_flush.h").write_text(source[start:end])

    start = end + 1
    end = source.index("\n/*\n * F-262:", start)
    session = source[start:end]
    if omit_session_serialization:
        end_worker = session.index("\nstatic DECLARE_WORK(")
        session = (
            'static void ask_pppoe_flush_fn(struct work_struct *w)\n'
            '{ ask_flow_flush_matching(ask_pppoe_collect, "PPPoE session down"); }\n'
            + session[end_worker:]
        )
    start = source.index("static int ask_pppoe_netdev_event(")
    end = source.index("\nstatic struct notifier_block ask_pppoe_netdev_nb", start)
    (tmp_path / "pending_session.h").write_text(session + "\n" + source[start:end])

    hw_source = HW_SOURCE.read_text()
    policy = []
    for prefix in ("static u8 ask_hw_port_family_get(",
                   "int ask_hw_flow_family_ok(",
                   "int ask_hw_flow_policy_snapshot("):
        start = hw_source.index(prefix)
        end = hw_source.index("\n}\n", start) + len("\n}\n")
        policy.append(hw_source[start:end])
    start = hw_source.index("int ask_hw_flow_publish_begin(")
    end = hw_source.index("\n/*\n * Control punt", start)
    policy.append(hw_source[start:end])
    (tmp_path / "pending_policy.h").write_text("\n".join(policy))

    start = hw_source.index("static void ask_hw_set_family_locked(")
    end = hw_source.index("\n/* Flow admission", start)
    family_body = hw_source[start:end]
    if omit_family_event:
        assert family_body.count("ask_flow_pppoe_flush();") == 1
        family_body = family_body.replace("ask_flow_pppoe_flush();", "(void)0;")
    (tmp_path / "pending_port_family.h").write_text(family_body)

    binary = tmp_path / "ask_pending_capability"
    subprocess.run(
        [
            "cc", "-std=gnu11", "-Wall", "-Wextra", "-Werror", "-pthread",
            "-Wno-unused-function", "-Wno-unused-parameter",
            "-I", str(tmp_path), str(HARNESS), "-o", str(binary),
        ],
        check=True,
    )
    return binary


def test_capability_disable_invalidates_queued_and_detached_pending(tmp_path):
    binary = build_harness(tmp_path)
    subprocess.run([str(binary)], check=True, timeout=30, cwd=tmp_path)

    mutant_dir = tmp_path / "mutant"
    mutant_dir.mkdir()
    mutant = build_harness(mutant_dir, omit_worker_guards=True)
    for probe in ("neigh-pppoe", "neigh-repark", "poll-pppoe", "poll-vlan"):
        result = subprocess.run([str(mutant), probe], timeout=30, cwd=mutant_dir)
        assert result.returncode == 1, probe


def test_session_down_invalidates_detached_pending(tmp_path):
    binary = build_harness(tmp_path)
    for probe in ("neigh-session", "poll-session", "session-publish",
                  "session-repark"):
        subprocess.run([str(binary), probe], check=True, timeout=30, cwd=tmp_path)

    mutant_dir = tmp_path / "async-mutant"
    mutant_dir.mkdir()
    mutant = build_harness(mutant_dir, omit_session_serialization=True)
    result = subprocess.run([str(mutant), "session-publish"], timeout=30, cwd=mutant_dir)
    assert result.returncode == 1

    mutant_dir = tmp_path / "repark-mutant"
    mutant_dir.mkdir()
    # Isolate the original epoch defense from the newer pending-event fence.
    mutant = build_harness(mutant_dir, refresh_repark_epoch=True,
                           omit_session_fence=True, omit_repark_guard=True)
    result = subprocess.run([str(mutant), "session-repark"], timeout=30, cwd=mutant_dir)
    assert result.returncode == 1


def test_session_withdrawal_fences_new_publication(tmp_path):
    binary = build_harness(tmp_path)
    for probe in ("session-dirty", "session-requeue", "session-sync-race"):
        subprocess.run([str(binary), probe], check=True, timeout=30, cwd=tmp_path)

    mutant_dir = tmp_path / "admission-mutant"
    mutant_dir.mkdir()
    mutant = build_harness(mutant_dir, omit_session_fence=True)
    result = subprocess.run([str(mutant), "session-dirty"], timeout=30, cwd=mutant_dir)
    assert result.returncode == 1

    mutant_dir = tmp_path / "new-event-mutant"
    mutant_dir.mkdir()
    mutant = build_harness(mutant_dir, ignore_newer_session=True)
    for probe in ("session-requeue", "session-sync-race"):
        result = subprocess.run([str(mutant), probe], timeout=30, cwd=mutant_dir)
        assert result.returncode == 1


def test_pppoe_session_port_family_change_invalidates_pending(tmp_path):
    binary = build_harness(tmp_path)
    subprocess.run([str(binary), "session-family"], check=True, timeout=30, cwd=tmp_path)

    mutant_dir = tmp_path / "family-mutant"
    mutant_dir.mkdir()
    mutant = build_harness(mutant_dir, omit_family_event=True)
    result = subprocess.run([str(mutant), "session-family"], timeout=30, cwd=mutant_dir)
    assert result.returncode == 1


def test_family_change_invalidates_detached_and_reparking_flows(tmp_path):
    binary = build_harness(tmp_path)
    for probe in ("neigh-family", "poll-family", "family-repark"):
        subprocess.run([str(binary), probe], check=True, timeout=30, cwd=tmp_path)

    mutant_dir = tmp_path / "family-eligibility-mutant"
    mutant_dir.mkdir()
    mutant = build_harness(mutant_dir, omit_family_eligibility=True)
    for probe in ("neigh-family", "poll-family"):
        result = subprocess.run([str(mutant), probe], timeout=30, cwd=mutant_dir)
        assert result.returncode == 1, probe

    mutant_dir = tmp_path / "family-repark-mutant"
    mutant_dir.mkdir()
    mutant = build_harness(mutant_dir, omit_repark_guard=True)
    result = subprocess.run([str(mutant), "family-repark"], timeout=30, cwd=mutant_dir)
    assert result.returncode == 1
