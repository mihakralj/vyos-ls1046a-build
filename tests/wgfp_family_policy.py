"""WireGuard fast path family policy: no WireGuard setting, the ports' `offload ipv4|ipv6` decide.

Runs dpaa_wgfp_family_ok() (dpaa_wgfp.c) and ask.ko's provider ask_hw_wgfp_family()
(ask_hw.c) under ASan/UBSan against mocks, and checks that fastpath.c applies the policy
at learning (ingress port, inner family), in both outer templates (port under the tunnel,
outer family) and in the aging pass (a family dropped from a port withdraws its flows).
"""
import re
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.host
ROOT = Path(__file__).resolve().parents[1]
WGFP = ROOT / "kernel/common/files/drivers/net/ethernet/freescale/dpaa/dpaa_wgfp.c"
FASTPATH = ROOT / "kernel/common/files/drivers/net/wireguard/fastpath.c"
ASKHW = ROOT / "kernel/ask/oot-modules/ask/ask_hw.c"
HDR = ROOT / "kernel/common/files/include/linux/fsl/dpaa_wgfp.h"

HARNESS = r"""
#include <stdint.h>
#include <stdio.h>
#include <stdbool.h>
#include <string.h>
typedef uint8_t u8;
#define AF_INET 2
#define AF_INET6 10
#define __rcu
#define EXPORT_SYMBOL_GPL(x)
#define rcu_read_lock() do { rcu_depth++; } while (0)
#define rcu_read_unlock() do { rcu_depth--; } while (0)
#define rcu_dereference(p) (p)
#define rcu_assign_pointer(p, v) ((p) = (v))
static int rcu_depth, syncs;
static void synchronize_rcu(void) { syncs++; }
#define ASK_FAM_V4 (1u << 0)
#define ASK_FAM_V6 (1u << 1)
struct net_device { int is_vlan; struct net_device *real; int dpaa; u8 port_id; };
struct fman_port { u8 id; };
static struct fman_port ports[64];
static bool is_vlan_dev(struct net_device *d) { return d->is_vlan; }
static struct net_device *vlan_dev_real_dev(struct net_device *d) { return d->real; }
static struct fman_port *dpaa_get_rx_fman_port(struct net_device *d)
{ if (!d->dpaa) return NULL; ports[d->port_id].id = d->port_id; return &ports[d->port_id]; }
static u8 fman_port_get_id(struct fman_port *p) { return p->id; }
static unsigned long ask_hw_port_l2[4];
static bool test_bit(unsigned int n, const unsigned long *b) { return b[n / 64] >> (n % 64) & 1; }
static u8 ask_hw_port_family[64];
static u8 ask_hw_port_family_get(u8 id) { return id < 64 ? ask_hw_port_family[id] : 0; }
@DEFS@
@WGFP@
@ASK@
static int fails;
#define CHECK(c) do { if (!(c)) { printf("FAIL line %d: %s\n", __LINE__, #c); fails++; } } while (0)
int main(void)
{
	struct net_device eth3 = { .dpaa = 1, .port_id = 0x10 }, eth4 = { .dpaa = 1, .port_id = 0x11 };
	struct net_device vif = { .is_vlan = 1, .real = &eth3 }, lo = { .dpaa = 0 };

	/* no provider (ask.ko not loaded): nothing admitted */
	CHECK(!dpaa_wgfp_family_ok(&eth3, AF_INET));
	dpaa_wgfp_set_family_policy(ask_hw_wgfp_family);
	ask_hw_port_family[0x10] = ASK_FAM_V4;
	ask_hw_port_family[0x11] = ASK_FAM_V4 | ASK_FAM_V6;
	CHECK(dpaa_wgfp_family_ok(&eth3, AF_INET) && !dpaa_wgfp_family_ok(&eth3, AF_INET6));
	CHECK(dpaa_wgfp_family_ok(&eth4, AF_INET) && dpaa_wgfp_family_ok(&eth4, AF_INET6));
	CHECK(dpaa_wgfp_family_ok(&vif, AF_INET) && !dpaa_wgfp_family_ok(&vif, AF_INET6));	/* vif -> eth3 */
	CHECK(!dpaa_wgfp_family_ok(&lo, AF_INET));	/* not a DPAA port */
	CHECK(!dpaa_wgfp_family_ok(NULL, AF_INET));
	CHECK(!dpaa_wgfp_family_ok(&eth4, 0));		/* unknown family */
	ask_hw_port_l2[0] |= 1ul << 0x11;		/* L2 (bridge) port: no routed WGFP */
	CHECK(!dpaa_wgfp_family_ok(&eth4, AF_INET));
	ask_hw_port_l2[0] = 0;
	ask_hw_port_family[0x10] = 0;			/* offload removed from the port */
	CHECK(!dpaa_wgfp_family_ok(&eth3, AF_INET));
	dpaa_wgfp_set_family_policy(NULL);		/* ask.ko unload waits for callers */
	CHECK(syncs == 1 && !dpaa_wgfp_family_ok(&eth4, AF_INET) && rcu_depth == 0);
	return fails != 0;
}
"""


def extract(src, head):
    i = src.find(head)
    assert i >= 0, head
    return src[i:src.index("\n}\n", i) + 3]


def test_family_policy_admission(tmp_path):
    wgfp = WGFP.read_text()
    ask = ASKHW.read_text()
    hdr = HDR.read_text()
    defs = "\n".join(re.findall(r"^#define DPAA_WGFP_FAM_V[46]\s+\S+$", hdr, re.M))
    defs += "\ntypedef u8 (*dpaa_wgfp_family_fn)(struct net_device *dev);\n"
    body = "static dpaa_wgfp_family_fn __rcu wgfp_family_fn;\n" + \
        extract(wgfp, "void dpaa_wgfp_set_family_policy(dpaa_wgfp_family_fn fn)\n{\n") + \
        extract(wgfp, "bool dpaa_wgfp_family_ok(struct net_device *dev, u8 family)\n{\n")
    provider = extract(ask, "static u8 ask_hw_wgfp_family(struct net_device *dev)\n{\n")
    c = tmp_path / "policy.c"
    c.write_text(HARNESS.replace("@DEFS@", defs).replace("@WGFP@", body).replace("@ASK@", provider))
    exe = tmp_path / "policy"
    subprocess.run(["gcc", "-std=gnu11", "-Wall", "-Wextra", "-Wno-unused-function",
                    "-fsanitize=address,undefined", "-fno-sanitize-recover=all", str(c), "-o", str(exe)],
                   check=True)
    r = subprocess.run([str(exe)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout


def test_policy_wired_everywhere():
    fp = FASTPATH.read_text()
    learn = extract(fp, "void wg_fastpath_learn(struct wg_peer *peer, struct sk_buff *skb)\n{\n")
    assert learn.index("dpaa_wgfp_family_ok(") < learn.index("thoff = nhoff")
    assert "!dpaa_wgfp_family_ok(rt->dst.dev, AF_INET)" in extract(fp, "static struct wgfp_tmpl *wgfp_tmpl4(")
    assert "!dpaa_wgfp_family_ok(dst->dev, AF_INET6)" in extract(fp, "static struct wgfp_tmpl *wgfp_tmpl6(")
    age = extract(fp, "static void wgfp_age_fn(struct work_struct *w)\n{\n")
    assert "family = dpaa_wgfp_family_ok(dev, job[i].t.family);" in age
    assert re.search(r"if \(!family\) \{\s*fl->del = true;", age)
    add = extract(WGFP.read_text(), "int dpaa_wgfp_flow_add(struct net_device *in, const struct dpaa_wgfp_tuple *t,")
    assert add.index("dpaa_wgfp_family_ok(in, t->family)") < add.index("wgfp_fm_port(")
    ready = extract(ASKHW.read_text(), "void ask_hw_set_ready(bool ready)\n{\n")
    assert ready.index("dpaa_wgfp_set_family_policy(NULL)") < ready.index("WRITE_ONCE(ask_hw_ready")
