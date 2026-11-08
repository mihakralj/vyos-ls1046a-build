#!/usr/bin/env python3
"""Characterize flowtable handling of unsettled hardware ownership."""

import argparse
import os
from pathlib import Path
import shlex
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "tests/dut/nf_flow_withdrawal.c"


def section(source, start, end):
    first = source.index(start)
    last = source.index(end, first)
    return source[first:last]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kernel_source", type=Path)
    parser.add_argument("--characterize", action="store_true",
                        help="report an open gate without returning failure")
    args = parser.parse_args()

    netfilter = args.kernel_source / "net/netfilter"
    offload = (netfilter / "nf_flow_table_offload.c").read_text()
    ip = (netfilter / "nf_flow_table_ip.c").read_text()
    core = (netfilter / "nf_flow_table_core.c").read_text()
    nft = (netfilter / "nf_tables_api.c").read_text()
    rhashtable = (args.kernel_source / "lib/rhashtable.c").read_text()
    ask = (ROOT / "kernel/ask/oot-modules/ask/ask_flow_offload.c").read_text()
    parts = (
        section(offload, "static void nf_flow_offload_init(",
                "static void flow_offload_hw_close_fixup("),
        section(offload, "static void flow_offload_hw_close_fixup(",
                "static void flow_offload_tuple_stats("),
        section(offload, "static void flow_offload_work_handler(",
                "static void flow_offload_queue_work("),
        section(ip, "static bool nf_flow_hw_record_live(",
                "static void nf_flow_nat_ip_tcp("),
        section(core, "static void nf_flow_offload_gc_step(",
                "void nf_flow_table_gc_run("),
        section(core, "void nf_flow_table_gc_run(",
                "static void nf_flow_offload_work_gc("),
    )
    destroy_parts = (
        section(offload, "void nf_flow_table_offload_flush_cleanup(",
                "static int nf_flow_table_block_setup("),
        section(rhashtable, "void rhashtable_destroy(",
                "EXPORT_SYMBOL_GPL(rhashtable_destroy);"),
        section(core, "static void nf_flow_table_do_cleanup(",
                "void nf_flow_table_gc_cleanup("),
        section(core, "static void flow_offload_quarantine_rcu(",
                "EXPORT_SYMBOL_GPL(nf_flow_table_free);"),
        section(nft, "static void nf_tables_flowtable_destroy(",
                "static int nf_tables_fill_gen_info("),
    )

    with tempfile.TemporaryDirectory(prefix="nf-flow-withdrawal-") as directory:
        work = Path(directory)
        (work / "nf_flow_withdrawal_body.h").write_text("\n".join(parts))
        (work / "nf_flow_destroy_body.h").write_text("\n".join(destroy_parts))
        echo_start = ask.index(" * T-M6-8 FIX:")
        echo = section(ask[echo_start:], "\n\tif (have_meta_iif) {",
                       "\n\t/*\n\t * T-M6-8b: re-resolve egress_dev")
        (work / "ask_echo_filter_body.h").write_text(echo)
        dedup = section(ask, "\n\t{\n\t\tstruct ask_flow *existing;",
                        "\n\t/*\n\t * T-M6-A3: claim ownership")
        (work / "ask_dedup_body.h").write_text(dedup)
        delay = section(ask, "static bool ask_offload_too_young(",
                        "\n/* KUnit drives REPLACE")
        (work / "ask_delay_body.h").write_text(delay)
        replace = section(ask, "static int ask_flow_offload_replace(",
                          "\nstatic int ask_flow_offload_destroy(")
        retained = section(ask, "static int ask_flow_retained_owner(",
                           "\nstatic int ask_flow_offload_replace(")
        (work / "ask_retained_body.h").write_text(retained)
        early_delay = section(replace, "\n\trc = ask_flow_retained_owner(t, f);",
                              "\n\trc = ask_hw_flow_policy_snapshot(")
        if not (early_delay.index("ask_flow_retained_owner(") <
                early_delay.index("ask_offload_too_young(")):
            raise RuntimeError("ASK retained-owner check must precede the delay")
        if not replace.index(early_delay) < replace.index(echo) < replace.index(dedup):
            raise RuntimeError("ASK REPLACE ordering changed; update the harness")
        destroy = section(ask, "static int ask_flow_offload_destroy(",
                          "\n/*\n * T-M8-3:")
        if destroy.index("xa_erase(&ask_offload_first_seen") > destroy.index(
                "ask_flow_gen_tombstone("):
            raise RuntimeError("ASK DESTROY ordering changed; update the harness")
        (work / "ask_early_delay_body.h").write_text(early_delay)
        binary = work / "nf-flow-withdrawal"
        subprocess.run(
            [*shlex.split(os.environ.get("HOSTCC", "cc")),
             "-std=gnu11", "-O2", "-Wall", "-Wextra", "-Werror",
             "-Wno-unused-parameter", "-I", str(work), str(HARNESS),
             "-o", str(binary)],
            check=True,
        )
        command = [str(binary)]
        if args.characterize:
            command.append("--characterize")
        return subprocess.run(command, check=False, timeout=30).returncode


if __name__ == "__main__":
    raise SystemExit(main())
