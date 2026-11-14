"""Host check for the S3 insert-path costs (plans/ASK2-FLOW-SCALING-PLAN.md S3).

The 65536-flow install profile put 3 % of CPU on irq-off time ending in REPLACE: ___ratelimit's
irqsave lock at the per-flow success-path prints, and the gen_by_cookie irqsave lock taken only to
read a value entry. Success-path prints must be pr_debug, and the read helpers must be lockless
(xa_load is RCU-safe and value entries carry no lifetime).
"""
from pathlib import Path
import re

import pytest

pytestmark = pytest.mark.host
ASK = Path(__file__).resolve().parents[1] / "kernel/ask/oot-modules/ask"

SUCCESS_PATH = [
    "PR14z19 IIF override",
    "META iif correction",
    "T-M6-1 v6 next-hop",
    "PR14z11 resolved next-hop",
    "REPLACE skip egress-side echo",
    "REPLACE dedup",
    "REPLACE staged",
]


def fn_body(src, name):
    start = re.search(r"^\S[^\n;]*\b%s\(" % name, src, re.M).start()
    return src[start:src.index("\n}\n", start) + 3]


def test_success_path_prints_are_debug():
    src = (ASK / "ask_flow_offload.c").read_text()
    for msg in SUCCESS_PATH:
        calls = re.findall(r"(\w+)\(\s*\"[^\"]*%s" % re.escape(msg), src)
        assert calls, msg
        assert set(calls) == {"ask_pr_dbg"}, (msg, calls)


@pytest.mark.parametrize("fn", ["ask_flow_gen_current", "ask_flow_gen_is_current"])
def test_gen_reads_are_lockless(fn):
    body = fn_body((ASK / "ask_flow.c").read_text(), fn)
    assert "xa_load(&t->gen_by_cookie" in body
    assert "xa_lock" not in body and "xa_unlock" not in body
