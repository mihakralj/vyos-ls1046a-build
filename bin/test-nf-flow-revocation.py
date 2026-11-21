#!/usr/bin/env python3
"""Host gate for Priority 1 nf_flow_table DIRECT dst_check, fencing, and revocation."""

import argparse
import os
from pathlib import Path
import shlex
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "tests/dut/nf_flow_revocation.c"


def section(source, start, end):
    first = source.index(start)
    last = source.index(end, first)
    return source[first:last]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kernel_source", type=Path)
    parser.add_argument("--sanitize", action="store_true",
                        help="compile with AddressSanitizer + UBSan")
    args = parser.parse_args()

    netfilter = args.kernel_source / "net/netfilter"
    hdr = (args.kernel_source / "include/net/netfilter/nf_flow_table.h").read_text()
    core = (netfilter / "nf_flow_table_core.c").read_text()
    ip = (netfilter / "nf_flow_table_ip.c").read_text()
    offload = (netfilter / "nf_flow_table_offload.c").read_text()
    nft = (netfilter / "nft_flow_offload.c").read_text()

    # Structural assertions on the kernel source itself (fail on unpatched 6.18.62).
    tuple_def = section(hdr, "struct flow_offload_tuple {", "\n};")
    union_start = tuple_def.rindex("\tunion {")
    if "*dst_cache;" not in tuple_def[:union_start]:
        raise SystemExit("FAIL: struct flow_offload_tuple still hides dst_cache inside union")
    if "dst_cookie;" not in tuple_def[:union_start]:
        raise SystemExit("FAIL: struct flow_offload_tuple still hides dst_cookie inside union")
    if "static inline bool nf_flow_dst_check(" not in hdr:
        raise SystemExit("FAIL: nf_flow_dst_check() not shared in nf_flow_table.h")
    if "bool nf_flow_admission_fenced(" not in hdr:
        raise SystemExit("FAIL: nf_flow_admission_fenced() missing from nf_flow_table.h")
    if "void nf_flow_table_fence_begin(" not in hdr or \
       "void nf_flow_table_fence_end(" not in hdr:
        raise SystemExit("FAIL: nf_flow_table_fence_begin/end missing from nf_flow_table.h")
    if "int nf_flow_table_revoke_match(" not in hdr or \
       "int nf_flow_table_revoke_all(" not in hdr:
        raise SystemExit("FAIL: nf_flow_table_revoke_match/all missing from nf_flow_table.h")
    if "static inline bool nf_flow_dst_check(" in ip:
        raise SystemExit("FAIL: nf_flow_table_ip.c still defines its own XMIT_DIRECT-skipping nf_flow_dst_check()")

    nft_eval = section(nft, "static void nft_flow_offload_eval(", "\nstatic int nft_flow_offload_validate(")
    if "nf_flow_admission_fenced()" not in nft_eval:
        raise SystemExit("FAIL: nft_flow_offload_eval() missing fast-path nf_flow_admission_fenced() check")
    if nft_eval.index("nf_flow_admission_fenced()") > nft_eval.index("nft_flow_route("):
        raise SystemExit("FAIL: nft_flow_offload_eval() must check nf_flow_admission_fenced() before nft_flow_route()")

    dst_check_fn = section(hdr, "static inline bool nf_flow_dst_check(", "\n}") + "\n}\n"
    fill_route = section(core, "static struct dst_entry *nft_route_dst_fetch(", "\nvoid flow_offload_route_init(")
    free_fns = section(core, "static void flow_offload_route_release(", "\nstatic u32 flow_offload_hash(")
    add_del_fns = section(core, "int flow_offload_add(", "\nvoid flow_offload_teardown(")
    gc_fn = section(core, "static void nf_flow_offload_gc_step(", "\nvoid nf_flow_table_gc_run(")
    cleanup_fn = section(core, "static bool nf_flow_tuple_uses_dev(", "\nvoid nf_flow_table_gc_cleanup(")
    revoke_fns = section(core, "void nf_flow_table_fence_begin(", "\nstatic void flow_offload_quarantine_rcu(")
    quarantine_fn = section(core, "static void flow_offload_quarantine_rcu(", "\nstatic void nf_flow_table_free_step(")
    work_fns = (
        section(offload, "static void flow_offload_work_add(", "\nstatic void flow_offload_hw_close_fixup(")
        + "\n"
        + section(offload, "static void flow_offload_work_handler(", "\nstatic void flow_offload_queue_work(")
        + "\n"
        + section(offload, "static struct flow_offload_work *\nnf_flow_offload_work_alloc(", "\nvoid nf_flow_offload_add(")
    )

    with tempfile.TemporaryDirectory(prefix="nf-flow-revocation-") as directory:
        work = Path(directory)
        (work / "nf_dst_check.h").write_text(dst_check_fn)
        (work / "nf_fill_route.h").write_text(fill_route)
        (work / "nf_free_fns.h").write_text(free_fns)
        (work / "nf_add_del.h").write_text(add_del_fns)
        (work / "nf_gc_step.h").write_text(gc_fn)
        (work / "nf_do_cleanup.h").write_text(cleanup_fn)
        (work / "nf_revoke_fns.h").write_text(revoke_fns)
        (work / "nf_quarantine_fn.h").write_text(quarantine_fn)
        (work / "nf_work_fns.h").write_text(work_fns)

        binary = work / "nf-flow-revocation"
        cflags = [
            "-std=gnu11", "-O2", "-Wall", "-Wextra", "-Werror",
            "-Wno-unused-parameter", "-Wno-unused-function",
        ]
        if args.sanitize:
            cflags += ["-fsanitize=address,undefined", "-fno-omit-frame-pointer"]
        subprocess.run(
            [*shlex.split(os.environ.get("HOSTCC", "cc")),
             *cflags, "-I", str(work), str(HARNESS), "-o", str(binary)],
            check=True,
        )
        return subprocess.run([str(binary)], check=False, timeout=30).returncode


if __name__ == "__main__":
    raise SystemExit(main())
