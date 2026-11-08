"""ask.ko adaptive RX alignment policy (ask_align.c): edit classes and the margin decision.

Extracts ask_align_class() and ask_align_decide() with the constants they use and runs
them under ASan/UBSan: +-4 B edits (POP xor PUSH, also with PPPoE) are class B and
length-neutral ones class A; a port switches only on a strict 2x majority in two windows above the
load floor (idle windows in between neither count nor reset), and switches back the same way.
"""
import re
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.host
ASK = Path(__file__).resolve().parents[1] / "kernel/ask/oot-modules/ask"

HARNESS = r"""
#include <stdint.h>
#include <stdio.h>
typedef uint8_t u8; typedef uint16_t u16; typedef uint64_t u64;
#define BIT(n) (1u << (n))
@DEFS@
@FUNCS@
static int fails;
#define CHECK(c) do { if (!(c)) { printf("FAIL line %d: %s\n", __LINE__, #c); fails++; } } while (0)
#define G 1000000000ULL
int main(void)
{
	u8 s = 0;
	u16 m;

	CHECK(ask_align_class(0) == 0);
	CHECK(ask_align_class(ASK_VLANF_POP) == 1);
	CHECK(ask_align_class(ASK_VLANF_PUSH) == 1);
	CHECK(ask_align_class(ASK_VLANF_POP | ASK_VLANF_PUSH) == 0);
	CHECK(ask_align_class(ASK_VLANF_PPPOE_STRIP) == 0);
	CHECK(ask_align_class(ASK_VLANF_PPPOE_INSERT) == 0);
	CHECK(ask_align_class(ASK_VLANF_POP | ASK_VLANF_PPPOE_STRIP) == 1);
	CHECK(ask_align_class(ASK_VLANF_PUSH | ASK_VLANF_PPPOE_INSERT) == 1);
	CHECK(ask_align_class(ASK_VLANF_POP | ASK_VLANF_PUSH | ASK_VLANF_PPPOE_STRIP) == 0);

	/* below the load floor nothing moves and the streak is kept */
	CHECK(ask_align_decide(0, ASK_ALIGN_MIN_BYTES - 1, 256, &s) == 256 && s == 0);
	s = 1;
	CHECK(ask_align_decide(0, ASK_ALIGN_MIN_BYTES - 1, 256, &s) == 256 && s == 1);
	/* so an idle window between two POP/PUSH windows still switches */
	s = 0;
	CHECK(ask_align_decide(0, 3 * G, 256, &s) == 256 && s == 1);
	CHECK(ask_align_decide(0, 0, 256, &s) == 256 && s == 1);
	CHECK(ask_align_decide(0, 3 * G, 256, &s) == 260 && s == 0);
	/* POP/PUSH-dominated: switch to 260 on the second consecutive window */
	CHECK(ask_align_decide(G / 10, 3 * G, 256, &s) == 256 && s == 1);
	CHECK(ask_align_decide(G / 10, 3 * G, 256, &s) == 260 && s == 0);
	/* an interrupted streak starts over */
	s = 0;
	CHECK(ask_align_decide(0, 3 * G, 256, &s) == 256 && s == 1);
	CHECK(ask_align_decide(2 * G, 2 * G, 256, &s) == 256 && s == 0);
	CHECK(ask_align_decide(0, 3 * G, 256, &s) == 256 && s == 1);
	/* a strict 2x majority is needed */
	s = 0;
	CHECK(ask_align_decide(G, 2 * G, 256, &s) == 256 && s == 0);
	/* and back to 256 when length-neutral traffic dominates */
	s = 0;
	m = ask_align_decide(3 * G, G, 260, &s);
	CHECK(m == 260 && s == 1);
	CHECK(ask_align_decide(3 * G, G, 260, &s) == 256);
	/* idle port keeps its margin */
	s = 0;
	CHECK(ask_align_decide(0, 0, 260, &s) == 260 && s == 0);
	/* a window above the floor that agrees with the margin resets a streak */
	s = 1;
	CHECK(ask_align_decide(3 * G, 0, 256, &s) == 256 && s == 0);

	/* the MTU gate: 260 only while every frame fits one RX buffer */
	CHECK(ask_align_gate(260, 1500) == 260);
	CHECK(ask_align_gate(260, ASK_ALIGN_B_MAX_MTU) == 260);
	CHECK(ask_align_gate(260, ASK_ALIGN_B_MAX_MTU + 1) == 256);
	CHECK(ask_align_gate(260, 9000) == 256);
	CHECK(ask_align_gate(256, 9000) == 256);
	CHECK(ask_align_gate(256, 1500) == 256);
	return fails != 0;
}
"""


def extract(src, head):
    i = src.find(head)
    assert i >= 0, head
    return src[i:src.index("\n}\n", i) + 3]


def test_align_policy(tmp_path):
    hdr = (ASK / "include/ask_internal.h").read_text()
    src = (ASK / "ask_align.c").read_text()
    defs = []
    for name in ("ASK_VLANF_POP", "ASK_VLANF_PUSH", "ASK_VLANF_PPPOE_STRIP", "ASK_VLANF_PPPOE_INSERT",
                 "ASK_ALIGN_MARGIN_A", "ASK_ALIGN_MARGIN_B"):
        defs.append(re.search(rf"^#define\s+{name}\s+\S+", hdr, re.M).group(0))
    for name in ("ASK_ALIGN_MIN_BYTES", "ASK_ALIGN_RATIO", "ASK_ALIGN_STREAK", "ASK_ALIGN_B_MAX_MTU"):
        defs.append(re.search(rf"^#define\s+{name}\s+[^\n/]+", src, re.M).group(0))
    funcs = extract(src, "static unsigned int ask_align_class(u8 vlan_edit_flags)\n{\n") + \
        extract(src, "static u16 ask_align_decide(u64 a, u64 b, u16 cur, u8 *streak)\n{\n") + \
        extract(src, "static u16 ask_align_gate(u16 want, unsigned int mtu)\n{\n")
    c = tmp_path / "align.c"
    c.write_text(HARNESS.replace("@DEFS@", "\n".join(defs)).replace("@FUNCS@", funcs))
    exe = tmp_path / "align"
    subprocess.run(["gcc", "-std=gnu11", "-Wall", "-Wextra", "-fsanitize=address,undefined",
                    "-fno-sanitize-recover=all", str(c), "-o", str(exe)], check=True)
    r = subprocess.run([str(exe)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout
