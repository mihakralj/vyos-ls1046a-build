#!/usr/bin/env python3
"""
inject-ask-hooks.py — Inject ASK fast-path hooks into a mainline 6.6 kernel tree.

This script ports the ASK (Gillmor/Comcerto) kernel hooks from the NXP 6.12
patch to mainline 6.6. It:
  1. Copies new standalone source files into the kernel tree
  2. Patches Kconfig and Makefile files to add ASK build options
  3. Inserts #ifdef CONFIG_CPE_FAST_PATH hook blocks into generic kernel
     headers and source files (netdevice.h, skbuff.h, nf_conntrack.h,
     net/core/dev.c, net/netfilter/*, etc.)

Usage:
    python3 inject-ask-hooks.py /path/to/linux-6.6-kernel-tree

The script searches for anchor patterns in each file and inserts the hook
code at the correct location. If a pattern is not found, it reports an
error but continues (partial patching is OK for incremental development).

Source: ASK/patches/kernel/002-mono-gateway-ask-kernel_linux_6_12.patch
        ASK/patches/kernel/999-layerscape-ask-kernel_linux_5_4_3_00_0.patch
"""

import os
import sys
import shutil
import re

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# Track results
results = {"ok": 0, "skip": 0, "fail": 0}


def insert_after(filepath, anchor, code, label=""):
    """Insert code block after the first line matching anchor pattern."""
    if not os.path.exists(filepath):
        print(f"  SKIP {label}: {filepath} not found")
        results["skip"] += 1
        return False
    with open(filepath, "r") as f:
        lines = f.readlines()

    for i, line in enumerate(lines):
        if re.search(anchor, line):
            # Check if already patched
            if i + 1 < len(lines) and "CONFIG_CPE_FAST_PATH" in lines[i + 1]:
                print(f"  SKIP {label}: already patched")
                results["skip"] += 1
                return True
            lines.insert(i + 1, code)
            with open(filepath, "w") as f:
                f.writelines(lines)
            print(f"  OK   {label}")
            results["ok"] += 1
            return True

    print(f"  FAIL {label}: anchor not found: {anchor}")
    results["fail"] += 1
    return False


def insert_before(filepath, anchor, code, label=""):
    """Insert code block before the first line matching anchor pattern."""
    if not os.path.exists(filepath):
        print(f"  SKIP {label}: {filepath} not found")
        results["skip"] += 1
        return False
    with open(filepath, "r") as f:
        lines = f.readlines()

    for i, line in enumerate(lines):
        if re.search(anchor, line):
            if i > 0 and "CONFIG_CPE_FAST_PATH" in lines[i - 1]:
                print(f"  SKIP {label}: already patched")
                results["skip"] += 1
                return True
            lines.insert(i, code)
            with open(filepath, "w") as f:
                f.writelines(lines)
            print(f"  OK   {label}")
            results["ok"] += 1
            return True

    print(f"  FAIL {label}: anchor not found: {anchor}")
    results["fail"] += 1
    return False


def append_to_file(filepath, code, label=""):
    """Append code to end of file."""
    if not os.path.exists(filepath):
        print(f"  SKIP {label}: {filepath} not found")
        results["skip"] += 1
        return False
    with open(filepath, "r") as f:
        content = f.read()
    if "CONFIG_CPE_FAST_PATH" in content or "QOSMARK" in content:
        print(f"  SKIP {label}: already patched")
        results["skip"] += 1
        return True
    with open(filepath, "a") as f:
        f.write(code)
    print(f"  OK   {label}")
    results["ok"] += 1
    return True


def replace_line(filepath, anchor, old_pattern, new_text, label=""):
    """Replace a line matching old_pattern near anchor with new_text."""
    if not os.path.exists(filepath):
        print(f"  SKIP {label}: {filepath} not found")
        results["skip"] += 1
        return False
    with open(filepath, "r") as f:
        content = f.read()
    if new_text.strip() in content:
        print(f"  SKIP {label}: already patched")
        results["skip"] += 1
        return True
    new_content = re.sub(old_pattern, new_text, content, count=1)
    if new_content == content:
        print(f"  FAIL {label}: pattern not found")
        results["fail"] += 1
        return False
    with open(filepath, "w") as f:
        f.write(new_content)
    print(f"  OK   {label}")
    results["ok"] += 1
    return True


def copy_new_files(kernel_dir):
    """Copy new ASK source files into kernel tree."""
    print("\n=== Phase 1: Copy new source files ===")

    copies = [
        ("comcerto_fp_netfilter.c", "net/netfilter/comcerto_fp_netfilter.c"),
        ("xt_qosconnmark.c", "net/netfilter/xt_qosconnmark.c"),
        ("xt_qosmark.c", "net/netfilter/xt_qosmark.c"),
        ("ipsec_flow.c", "net/xfrm/ipsec_flow.c"),
        ("ipsec_flow.h", "net/xfrm/ipsec_flow.h"),
        ("fsl_oh_port.h", "include/linux/fsl_oh_port.h"),
        ("xt_QOSCONNMARK.h", "include/uapi/linux/netfilter/xt_QOSCONNMARK.h"),
        ("xt_QOSMARK.h", "include/uapi/linux/netfilter/xt_QOSMARK.h"),
        ("xt_qosconnmark.h", "include/uapi/linux/netfilter/xt_qosconnmark.h"),
        ("xt_qosmark.h", "include/uapi/linux/netfilter/xt_qosmark.h"),
    ]

    for src_name, dst_rel in copies:
        src = os.path.join(SCRIPT_DIR, src_name)
        dst = os.path.join(kernel_dir, dst_rel)
        if not os.path.exists(src):
            print(f"  FAIL copy {src_name}: source not found")
            results["fail"] += 1
            continue
        if os.path.exists(dst):
            print(f"  SKIP copy {dst_rel}: already exists")
            results["skip"] += 1
            continue
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
        print(f"  OK   copy {dst_rel}")
        results["ok"] += 1


def patch_kconfig_makefiles(kernel_dir):
    """Add ASK config options and build rules."""
    print("\n=== Phase 2: Kconfig and Makefile additions ===")

    # --- net/Kconfig: add CONFIG_CPE_FAST_PATH ---
    insert_after(
        os.path.join(kernel_dir, "net/Kconfig"),
        r"^if INET$",
        """
config CPE_FAST_PATH
\tbool "Fast Path Processing offload"
\tdepends on ARCH_LAYERSCAPE
\tdepends on NETFILTER
\tselect NF_CONNTRACK
\thelp
\t  Support for Fast Path offload.

""",
        "net/Kconfig: CONFIG_CPE_FAST_PATH",
    )

    # --- net/netfilter/Kconfig: add QOSMARK and QOSCONNMARK ---
    append_to_file(
        os.path.join(kernel_dir, "net/netfilter/Kconfig"),
        """
config NETFILTER_XT_QOSMARK
\ttristate 'qosmark target and match support'
\tdefault m if NETFILTER_ADVANCED=n
\thelp
\t  This option adds the "QOSMARK" target and "qosmark" match.

\t  Netfilter qosmark matching allows you to match packets based on the
\t  "qosmark" value in the packet metadata.

config NETFILTER_XT_QOSCONNMARK
\ttristate 'qosconnmark target and match support'
\tdepends on NF_CONNTRACK
\tdefault m if NETFILTER_ADVANCED=n
\thelp
\t  This option adds the "QOSCONNMARK" target and "qosconnmark" match.

\t  Netfilter allows you to mark connections based on QoS characteristics
\t  and use those marks for further classification.

config COMCERTO_FP
\ttristate "Comcerto fast path netfilter module"
\tdepends on CPE_FAST_PATH
\tdepends on NF_CONNTRACK
\tdefault y if CPE_FAST_PATH
\thelp
\t  Comcerto fast path conntrack information collector.
\t  Gathers ifindex, mark, and XFRM state per conntrack entry
\t  for offload to the FMan hardware classifier.
""",
        "net/netfilter/Kconfig: QOSMARK + QOSCONNMARK + COMCERTO_FP",
    )

    # --- net/netfilter/Makefile: add new modules ---
    append_to_file(
        os.path.join(kernel_dir, "net/netfilter/Makefile"),
        """
# ASK fast-path modules
obj-$(CONFIG_NETFILTER_XT_QOSMARK) += xt_qosmark.o
obj-$(CONFIG_NETFILTER_XT_QOSCONNMARK) += xt_qosconnmark.o
obj-$(CONFIG_COMCERTO_FP) += comcerto_fp_netfilter.o
""",
        "net/netfilter/Makefile: ASK modules",
    )

    # --- net/ipv4/Kconfig: add INET_IPSEC_OFFLOAD ---
    append_to_file(
        os.path.join(kernel_dir, "net/ipv4/Kconfig"),
        """
config INET_IPSEC_OFFLOAD
\ttristate "IP: IPSec hardware offload support"
\tdepends on CPE_FAST_PATH
\tdepends on XFRM
\tdepends on INET_ESP
\thelp
\t  Support for IPSec SA hardware offload for IPv4.
\t  Maintains a flow cache for fast-path IPSec packet forwarding
\t  via FMan PCD classifier.
""",
        "net/ipv4/Kconfig: INET_IPSEC_OFFLOAD",
    )

    # --- net/ipv6/Kconfig: add INET6_IPSEC_OFFLOAD ---
    append_to_file(
        os.path.join(kernel_dir, "net/ipv6/Kconfig"),
        """
config INET6_IPSEC_OFFLOAD
\ttristate "IPv6: IPSec hardware offload support"
\tdepends on CPE_FAST_PATH
\tdepends on XFRM
\tdepends on INET6_ESP
\thelp
\t  Support for IPSec SA hardware offload for IPv6.
\t  Maintains a flow cache for fast-path IPSec packet forwarding
\t  via FMan PCD classifier.
""",
        "net/ipv6/Kconfig: INET6_IPSEC_OFFLOAD",
    )

    # --- net/xfrm/Makefile: add ipsec_flow ---
    append_to_file(
        os.path.join(kernel_dir, "net/xfrm/Makefile"),
        """
# ASK IPSec flow cache
obj-$(CONFIG_INET_IPSEC_OFFLOAD) += ipsec_flow.o
obj-$(CONFIG_INET6_IPSEC_OFFLOAD) += ipsec_flow.o
""",
        "net/xfrm/Makefile: ipsec_flow",
    )


def patch_headers(kernel_dir):
    """Insert ASK data structures and declarations into kernel headers."""
    print("\n=== Phase 3: Header modifications ===")

    # --- include/linux/netdevice.h ---
    # Add wifi_offload_dev field to struct net_device
    insert_before(
        os.path.join(kernel_dir, "include/linux/netdevice.h"),
        r"^\s*int\s+ifindex;",
        """#if defined(CONFIG_CPE_FAST_PATH)
\t/* ASK: network device that offloads WiFi data to PFE */
\tstruct net_device\t\t*wifi_offload_dev;
#endif
""",
        "netdevice.h: wifi_offload_dev field",
    )

    # Add ASK function declarations near dev_queue_xmit
    insert_after(
        os.path.join(kernel_dir, "include/linux/netdevice.h"),
        r"int __dev_queue_xmit\(struct sk_buff \*skb.*struct net_device \*sb_dev\);",
        """
#if defined(CONFIG_CPE_FAST_PATH)
int original_dev_queue_xmit(struct sk_buff *skb);
typedef int (*dpaa_wifi_xmit_local_hook_t)(struct sk_buff *skb);
int dpa_register_wifi_xmit_local_hook(dpaa_wifi_xmit_local_hook_t hookfn);
void dpa_unregister_wifi_xmit_local_hook(void);
int dpa_add_dummy_eth_hdr(struct sk_buff** skb_in, int priv_headroom, unsigned char *hdroom_realloc);
#endif

""",
        "netdevice.h: ASK xmit hook declarations",
    )

    # --- include/linux/skbuff.h ---
    # Add qosmark and iif_index fields to sk_buff
    # In 6.6, look for the mark field and add after it
    insert_after(
        os.path.join(kernel_dir, "include/linux/skbuff.h"),
        r"__u32\s+mark;",  # first occurrence in sk_buff struct
        """#if defined(CONFIG_CPE_FAST_PATH)
\t__u32\t\t\t\tqosmark;
\tint\t\t\t\tiif_index;
\t__u8\t\t\t\tipsec_offload;
#endif
""",
        "skbuff.h: qosmark/iif_index/ipsec_offload fields",
    )

    # --- include/net/netfilter/nf_conntrack.h ---
    # Add comcerto_fp_info struct and fields to nf_conn

    # First add the comcerto_fp_info structure definition before nf_conn
    insert_before(
        os.path.join(kernel_dir, "include/net/netfilter/nf_conntrack.h"),
        r"struct nf_conn\s*\{",
        """#if defined(CONFIG_CPE_FAST_PATH)
#define MAX_SUPPORTED_XFRMS_PER_DIR 2
struct comcerto_fp_info {
\tint ifindex;
\t__u32 mark;
\t__u16 iif_index;
\t__u32 xfrm_handle[MAX_SUPPORTED_XFRMS_PER_DIR * 2];
};
#endif

""",
        "nf_conntrack.h: comcerto_fp_info struct",
    )

    # Add fp_info and qosconnmark fields to nf_conn struct
    # Look for the mark field inside nf_conn
    insert_after(
        os.path.join(kernel_dir, "include/net/netfilter/nf_conntrack.h"),
        r"#if defined\(CONFIG_NF_CONNTRACK_MARK\)",
        """#endif
#if defined(CONFIG_CPE_FAST_PATH)
\tstruct comcerto_fp_info fp_info[IP_CT_DIR_MAX];
\t__u64 qosconnmark;
""",
        "nf_conntrack.h: fp_info + qosconnmark in nf_conn",
    )

    # --- include/net/ip.h ---
    # Add fast-path defrag user enum
    insert_after(
        os.path.join(kernel_dir, "include/net/ip.h"),
        r"IP_DEFRAG_VS_FWD",
        """#if defined(CONFIG_CPE_FAST_PATH)
\tIP_DEFRAG_CPE_FP,
#endif
""",
        "ip.h: IP_DEFRAG_CPE_FP enum",
    )

    # --- include/linux/if_bridge.h ---
    # Add bridge fast-path notify hooks (before final bare #endif)
    insert_before(
        os.path.join(kernel_dir, "include/linux/if_bridge.h"),
        r"^#endif\s*$",
        """
#if defined(CONFIG_CPE_FAST_PATH)
/* ASK bridge fast-path hooks */
typedef void (*br_fp_fdb_update_hook_t)(struct net_bridge_fdb_entry *fdb, const char *src_addr);
typedef void (*br_fp_fdb_delete_hook_t)(struct net_bridge_fdb_entry *fdb);
extern br_fp_fdb_update_hook_t br_fp_fdb_update_hook;
extern br_fp_fdb_delete_hook_t br_fp_fdb_delete_hook;
#endif

""",
        "if_bridge.h: ASK FDB notify hooks",
    )

    # --- include/net/xfrm.h ---
    # Add handle field to xfrm_state
    insert_after(
        os.path.join(kernel_dir, "include/net/xfrm.h"),
        r"struct xfrm_state\s*\{",
        """\t/* ASK fast-path: unique handle for SA identification */
#if defined(CONFIG_CPE_FAST_PATH)
\tunsigned int\t\thandle;
#endif
""",
        "xfrm.h: handle field in xfrm_state",
    )

    # --- include/net/netns/xfrm.h ---
    # Add handle sequence counter to netns_xfrm
    insert_after(
        os.path.join(kernel_dir, "include/net/netns/xfrm.h"),
        r"struct netns_xfrm\s*\{",
        """#if defined(CONFIG_CPE_FAST_PATH)
\tunsigned int\t\thandle_sequence;
#endif
""",
        "netns/xfrm.h: handle_sequence in netns_xfrm",
    )

    # --- include/net/tcp.h ---
    # Add RST info to tcp_notify hook
    insert_after(
        os.path.join(kernel_dir, "include/net/tcp.h"),
        r"void tcp_send_active_reset\(",
        """
#if defined(CONFIG_CPE_FAST_PATH)
/* ASK: notify fast-path of TCP RST events */
typedef void (*tcp_fp_rst_hook_t)(struct sock *sk);
extern tcp_fp_rst_hook_t tcp_fp_rst_hook;
#endif
""",
        "tcp.h: ASK RST notify hook",
    )

    # --- include/uapi/linux/netfilter/nf_conntrack_common.h ---
    # Add IPCT_QOSCONNMARK event type for conntrack QoS mark changes
    insert_after(
        os.path.join(
            kernel_dir,
            "include/uapi/linux/netfilter/nf_conntrack_common.h",
        ),
        r"IPCT_MARK,",
        """#if defined(CONFIG_CPE_FAST_PATH)
\tIPCT_QOSCONNMARK,
#endif
""",
        "nf_conntrack_common.h: IPCT_QOSCONNMARK event",
    )

    # Add IPS_PERMANENT_BIT for fast-path permanent flows
    insert_after(
        os.path.join(
            kernel_dir,
            "include/uapi/linux/netfilter/nf_conntrack_common.h",
        ),
        r"IPS_OFFLOAD_BIT\s*=",
        """
#if defined(CONFIG_CPE_FAST_PATH)
/* ASK: flow is permanently offloaded to hardware */
\tIPS_PERMANENT_BIT = 15,
\tIPS_PERMANENT = (1 << IPS_PERMANENT_BIT),
#endif
""",
        "nf_conntrack_common.h: IPS_PERMANENT",
    )


def patch_net_core(kernel_dir):
    """Insert ASK hooks into net/core/ files."""
    print("\n=== Phase 4: net/core/ hooks ===")

    # --- net/core/dev.c ---
    # Add ASK wifi offload hook and gillmor fastpath at top of file
    insert_after(
        os.path.join(kernel_dir, "net/core/dev.c"),
        r'^#include "dev\.h"',
        """
#if defined(CONFIG_CPE_FAST_PATH)
/* ASK fast-path: WiFi offload and gillmor transmit hooks */
static dpaa_wifi_xmit_local_hook_t dpaa_wifi_xmit_local_hook_fn = NULL;
static DEFINE_MUTEX(dpaa_wifi_hook_mutex);

int dpa_register_wifi_xmit_local_hook(dpaa_wifi_xmit_local_hook_t hookfn)
{
\tmutex_lock(&dpaa_wifi_hook_mutex);
\tdpaa_wifi_xmit_local_hook_fn = hookfn;
\tmutex_unlock(&dpaa_wifi_hook_mutex);
\treturn 0;
}
EXPORT_SYMBOL(dpa_register_wifi_xmit_local_hook);

void dpa_unregister_wifi_xmit_local_hook(void)
{
\tmutex_lock(&dpaa_wifi_hook_mutex);
\tdpaa_wifi_xmit_local_hook_fn = NULL;
\tmutex_unlock(&dpaa_wifi_hook_mutex);
}
EXPORT_SYMBOL(dpa_unregister_wifi_xmit_local_hook);

/* NOTE: dpa_add_dummy_eth_hdr implementation is in sdk_dpaa/dpaa_eth_sg.c
 * (3-param version: skb_in, priv_headroom, hdroom_realloc).
 * Do NOT duplicate it here — it causes conflicting types. */

int original_dev_queue_xmit(struct sk_buff *skb)
{
\treturn __dev_queue_xmit(skb, NULL);
}
EXPORT_SYMBOL(original_dev_queue_xmit);
#endif /* CONFIG_CPE_FAST_PATH */

""",
        "net/core/dev.c: ASK hook definitions",
    )

    # --- net/core/skbuff.c ---
    # Add skb field initialization in __alloc_skb
    insert_after(
        os.path.join(kernel_dir, "net/core/skbuff.c"),
        r"skb->mac_header\s*=\s*\(typeof",
        """#if defined(CONFIG_CPE_FAST_PATH)
\tskb->qosmark = 0;
\tskb->iif_index = 0;
\tskb->ipsec_offload = 0;
#endif
""",
        "net/core/skbuff.c: ASK field init",
    )


def patch_netfilter(kernel_dir):
    """Insert ASK hooks into net/netfilter/ files."""
    print("\n=== Phase 5: net/netfilter/ hooks ===")

    # --- net/netfilter/nf_conntrack_core.c ---
    # Add ifindex/mark tracking in conntrack confirmation path
    # Must go INSIDE the function body — anchor on the opening { after the signature
    # The function is: int __nf_conntrack_confirm(struct sk_buff *skb)\n{
    # We anchor on a unique early line inside the function body.
    insert_after(
        os.path.join(kernel_dir, "net/netfilter/nf_conntrack_core.c"),
        r"unsigned int zone_id;",
        """
#if defined(CONFIG_CPE_FAST_PATH)
\t/* ASK: track interface and mark for fast-path offload */
\t{
\t\tstruct nf_conn *ct;
\t\tenum ip_conntrack_info ctinfo;
\t\tct = nf_ct_get(skb, &ctinfo);
\t\tif (ct) {
\t\t\tint dir = CTINFO2DIR(ctinfo);
\t\t\tct->fp_info[dir].ifindex = skb->dev ? skb->dev->ifindex : 0;
\t\t\tct->fp_info[dir].mark = skb->mark;
\t\t\tct->fp_info[dir].iif_index = skb->iif_index;
\t\t}
\t}
#endif
""",
        "nf_conntrack_core.c: ASK ifindex/mark tracking",
    )

    # --- net/netfilter/nf_conntrack_proto_tcp.c ---
    # Emit IPCT_PROTOINFO on ESTABLISHED→ESTABLISHED transitions
    insert_after(
        os.path.join(kernel_dir, "net/netfilter/nf_conntrack_proto_tcp.c"),
        r"nf_conntrack_event_cache\(IPCT_ASSURED,\s*ct\);",
        """#ifdef CONFIG_CPE_FAST_PATH
\t\t\tif (old_state == TCP_CONNTRACK_ESTABLISHED && new_state == TCP_CONNTRACK_ESTABLISHED)
\t\t\t\tnf_conntrack_event_cache(IPCT_PROTOINFO, ct);
#endif
""",
        "nf_conntrack_proto_tcp.c: ASK ESTABLISHED event",
    )

    # --- net/netfilter/nf_conntrack_standalone.c ---
    # Display fp_info, qosconnmark, and PERMANENT in /proc/net/nf_conntrack
    # Insert AFTER the "mark=%u " seq_printf — this is safe because the mark
    # line is a complete statement. We add all ASK fields in one block.
    # NOTE: Do NOT insert after seq_has_overflowed() — that splits the
    # "if (seq_has_overflowed(s)) goto release;" into two statements,
    # making the goto unconditional and breaking the entire proc display.
    insert_after(
        os.path.join(kernel_dir, "net/netfilter/nf_conntrack_standalone.c"),
        r'seq_printf\(s,\s*"mark=%u\s*"',
        """#if defined(CONFIG_CPE_FAST_PATH)
\tif (test_bit(IPS_PERMANENT_BIT, &ct->status))
\t\tseq_printf(s, "[PERMANENT] ");
\tif (ct->qosconnmark != 0)
\t\tseq_printf(s, "qosconnmark=0x%llx ", (unsigned long long)ct->qosconnmark);
\t{
\t\tint _d;
\t\tfor (_d = 0; _d < IP_CT_DIR_MAX; _d++) {
\t\t\tconst struct comcerto_fp_info *fpi = &ct->fp_info[_d];
\t\t\tif (fpi->ifindex || fpi->mark || fpi->iif_index)
\t\t\t\tseq_printf(s, "fp[%d]={if=%d mark=0x%x iif=%d} ",
\t\t\t\t\t_d, fpi->ifindex, fpi->mark, fpi->iif_index);
\t\t}
\t}
\tif (seq_has_overflowed(s))
\t\tgoto release;
#endif
""",
        "nf_conntrack_standalone.c: ASK qosconnmark display",
    )


def patch_ip_output(kernel_dir):
    """Insert ASK hooks into IP output path."""
    print("\n=== Phase 6: IP output hooks ===")

    # --- net/ipv4/ip_output.c ---
    # Skip netfilter POST_ROUTING for ipsec_offload packets
    # In 6.6, the unicast path uses NF_HOOK_COND with ip_finish_output
    insert_before(
        os.path.join(kernel_dir, "net/ipv4/ip_output.c"),
        r"return NF_HOOK_COND\(NFPROTO_IPV4,\s*NF_INET_POST_ROUTING",
        """#if defined(CONFIG_CPE_FAST_PATH)
\tif (unlikely(skb->ipsec_offload))
\t\treturn ip_finish_output(net, sk, skb);
#endif
""",
        "ip_output.c: ASK POST_ROUTING bypass",
    )

    # --- net/ipv6/ip6_output.c ---
    # Same for IPv6 — uses NF_HOOK_COND
    insert_before(
        os.path.join(kernel_dir, "net/ipv6/ip6_output.c"),
        r"return NF_HOOK_COND\(NFPROTO_IPV6,\s*NF_INET_POST_ROUTING",
        """#if defined(CONFIG_CPE_FAST_PATH)
\tif (unlikely(skb->ipsec_offload))
\t\treturn ip6_finish_output(net, sk, skb);
#endif
""",
        "ip6_output.c: ASK POST_ROUTING bypass",
    )


def patch_xfrm(kernel_dir):
    """Insert ASK hooks into XFRM subsystem."""
    print("\n=== Phase 7: XFRM hooks ===")

    # --- net/xfrm/xfrm_state.c ---
    # Assign unique handle to each xfrm_state
    # Must go AFTER x is allocated: "write_pnet(&x->xs_net, net);" is the
    # first statement after "if (x) {" in xfrm_state_alloc.
    insert_after(
        os.path.join(kernel_dir, "net/xfrm/xfrm_state.c"),
        r"write_pnet\(&x->xs_net, net\);",
        """#if defined(CONFIG_CPE_FAST_PATH)
\t\tx->handle = ++net->xfrm.handle_sequence;
#endif
""",
        "xfrm_state.c: ASK handle assignment",
    )

    # --- net/xfrm/xfrm_policy.c ---
    # Add ipsec_flow hooks
    insert_after(
        os.path.join(kernel_dir, "net/xfrm/xfrm_policy.c"),
        r'#include "xfrm_hash\.h"',
        """
#if defined(CONFIG_INET_IPSEC_OFFLOAD) || defined(CONFIG_INET6_IPSEC_OFFLOAD)
#include "ipsec_flow.h"
#endif
""",
        "xfrm_policy.c: ASK ipsec_flow include",
    )


def patch_bridge(kernel_dir):
    """Insert ASK hooks into bridge code."""
    print("\n=== Phase 8: Bridge hooks ===")

    # --- net/bridge/br_input.c ---
    # Add fast-path hook at bridge input
    insert_after(
        os.path.join(kernel_dir, "net/bridge/br_input.c"),
        r"#include.*br_private\.h",
        """
#if defined(CONFIG_CPE_FAST_PATH)
#include <linux/if_bridge.h>
#endif
""",
        "br_input.c: ASK include",
    )

    # --- net/bridge/br_fdb.c ---
    # Add FDB update notification hooks
    insert_after(
        os.path.join(kernel_dir, "net/bridge/br_fdb.c"),
        r"#include.*br_private\.h",
        """
#if defined(CONFIG_CPE_FAST_PATH)
br_fp_fdb_update_hook_t br_fp_fdb_update_hook __read_mostly;
br_fp_fdb_delete_hook_t br_fp_fdb_delete_hook __read_mostly;
EXPORT_SYMBOL(br_fp_fdb_update_hook);
EXPORT_SYMBOL(br_fp_fdb_delete_hook);
#endif
""",
        "br_fdb.c: ASK FDB hook variables",
    )


def patch_sdk_drivers(kernel_dir):
    """Apply ASK modifications to SDK DPAA/FMan drivers."""
    print("\n=== Phase 9: SDK driver ASK modifications ===")
    print("  NOTE: SDK driver ASK mods are extracted directly from the 6.12 patch.")
    print("  These should be applied as a separate patch on top of the SDK tree.")
    print("  Skipping automated injection — use the 6.12 patch hunks for SDK files.")
    # The SDK driver modifications are extensive and tightly coupled to the
    # SDK codebase. They should be applied as a traditional patch file
    # extracted from the 6.12 ASK patch (the sdk_dpaa/* and sdk_fman/* hunks).
    # This is Phase 2 work.


def main():
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <kernel-tree-path>")
        print(f"  e.g.: {sys.argv[0]} /opt/vyos-dev/linux")
        sys.exit(1)

    kernel_dir = sys.argv[1]

    if not os.path.isdir(os.path.join(kernel_dir, "net/core")):
        print(f"ERROR: {kernel_dir} doesn't look like a kernel tree")
        sys.exit(1)

    print(f"Injecting ASK hooks into: {kernel_dir}")

    copy_new_files(kernel_dir)
    patch_kconfig_makefiles(kernel_dir)
    patch_headers(kernel_dir)
    patch_net_core(kernel_dir)
    patch_netfilter(kernel_dir)
    patch_ip_output(kernel_dir)
    patch_xfrm(kernel_dir)
    patch_bridge(kernel_dir)
    patch_sdk_drivers(kernel_dir)

    print(f"\n=== Summary ===")
    print(f"  OK:   {results['ok']}")
    print(f"  SKIP: {results['skip']}")
    print(f"  FAIL: {results['fail']}")

    if results["fail"] > 0:
        print("\nSome hooks failed to apply. Check the FAIL messages above.")
        print("Failed hooks may need manual insertion due to 6.6 API differences.")
        sys.exit(1)
    else:
        print("\nAll hooks applied successfully!")
        sys.exit(0)


if __name__ == "__main__":
    main()