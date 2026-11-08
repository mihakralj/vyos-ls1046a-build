#!/usr/bin/env python3
"""Fault-test generated FE disarm and parser readback code on the host."""

import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile


def function(source, name):
    match = re.search(
        rf"(?m)^(?:static )?(?:__maybe_unused )?(?:bool|int|void|ssize_t) {re.escape(name)}\([^;]*?\n\{{\n",
        source,
    )
    if not match:
        raise ValueError(f"function {name} not found")
    return source[match.start():source.index("\n}\n", match.end()) + 3]


def main():
    if len(sys.argv) != 2:
        sys.exit("usage: test-fman-disarm.py <kernel-source-dir>")
    directory = Path(sys.argv[1]) / "drivers/net/ethernet/freescale/fman"
    kg = (directory / "fman_pcd_kg.c").read_text()
    pcd = (directory / "fman_pcd.c").read_text()
    port = (directory / "fman_port.c").read_text()
    disarm = "\n".join(
        function(kg, name)
        for name in ("fman_pcd_kg_port_detach_cc", "fman_pcd_kg_port_disarm_fe")
    )
    disarm += function(pcd, "fman_pcd_fe_fault_arm")
    disarm += function(pcd, "fman_pcd_fe_disarm_preflight_locked")
    disarm += function(pcd, "__fman_pcd_fe_arm_disengage")
    parser = "\n".join(
        function(port, name)
        for name in ("stop_port_hwp", "start_port_hwp",
                     "fman_port_clear_lcv_split", "fman_port_verify_fe_detached")
    )
    engage = function(pcd, "fman_pcd_fe_engage_profile")
    engage_locked = function(pcd, "__fman_pcd_fe_engage_profile_locked")
    arm = function(pcd, "__fman_pcd_fe_arm_engage")
    workspace_pre_kg = "err = fman_pcd_fe_port_set" in arm
    chain_start = pcd.index("static __maybe_unused int __fman_pcd_fe_build_vm_chain(")
    chain_end = pcd.index("static int fman_pcd_fe_retained_check(", chain_start)
    if "fman_pcd_fe_pool_get_locked(pcd)" not in pcd[chain_start:chain_end]:
        sys.exit("FAIL: FE chain build would recursively acquire fe_lock")
    if workspace_pre_kg:
        workspace = arm.index("err = fman_pcd_fe_port_set")
        kg_dispatch = arm.index("err = fman_pcd_kg_port_arm_fe", workspace)
        if not (arm.index("fman_pcd_port_ensure_params_page") < workspace < kg_dispatch):
            sys.exit("FAIL: FE workspace must be published after page setup and before KG dispatch")
        workspace_failure = arm[workspace:kg_dispatch]
        if ("fman_pcd_fe_fault_arm(pcd, (u8)port_id, false)" not in workspace_failure or
                "__fman_pcd_fe_arm_disengage" in workspace_failure):
            sys.exit("FAIL: failed workspace publication must retain partial FE state")
    else:
        workspace = engage_locked.index("err = fman_pcd_fe_port_set")
        advanced = engage_locked.index("err = fman_pcd_fe_adv_offload", workspace)
        workspace_failure = engage_locked[workspace:advanced]
        if ("fman_pcd_fe_fault_arm(pcd, hw_port_id, true)" not in workspace_failure or
                "__fman_pcd_fe_arm_disengage" in workspace_failure):
            sys.exit("FAIL: failed workspace setup must stop RX and retain state")

    advanced = engage_locked.index("err = fman_pcd_fe_adv_offload")
    success = engage_locked.index('pr_info("fman_pcd: FE engage', advanced)
    advanced_failure = engage_locked[advanced:success]
    if ("fman_pcd_fe_fault_arm(pcd, hw_port_id, true)" not in advanced_failure or
            "__fman_pcd_fe_arm_disengage" in advanced_failure):
        sys.exit("FAIL: advanced-offload failure must stop RX and retain state")
    if ("test_bit(port_id, pcd->fe_port_faulted)" not in arm or
            "fman_pcd_fe_fault_arm(pcd, (u8)port_id, kg_programming_started)" not in arm):
        sys.exit("FAIL: typed FE arm must stop RX on a failed KG arm")
    muram_guard = arm.index("if (!muram)")
    scaffold_alloc = arm.index("gro = fman_pcd_muram_alloc", muram_guard)
    if "return -ENXIO;" not in arm[muram_guard:scaffold_alloc]:
        sys.exit("FAIL: FE arm must refuse missing MURAM before scaffold allocation")
    table_start = arm.index("struct fman_pcd_ehash_table *et")
    table_end = arm.index("if (et && pcd->fe_int_buf_off)", table_start)
    table_fragment = arm[table_start:table_end]
    kg_arm = function(kg, "fman_pcd_kg_port_arm_fe")
    mutation = kg_arm.index("if (ekfc)")
    started = kg_arm.index("*programming_started = true;")
    if not (kg_arm.index("if (!slot)") < started < mutation):
        sys.exit("FAIL: KG arm must track state before its first mutation")
    kg_arm_prefix = kg_arm[:mutation] + "(void)rxport;\n\t(void)err;\n\treturn 0;\n}\n"
    fe_defs_start = pcd.index("struct fman_pcd_fe_context_params {")
    fe_defs_end = pcd.index("/*\n * Encode one FE descriptor", fe_defs_start)

    root = Path(__file__).resolve().parents[1]
    compiler = shlex.split(os.environ.get("HOSTCC", "cc"))
    compile_flags = ["-DFMAN_WORKSPACE_PRE_KG=1"] if workspace_pre_kg else []
    with tempfile.TemporaryDirectory(prefix="fman-disarm-") as tmp:
        tmp = Path(tmp)
        (tmp / "disarm_body.h").write_text(disarm)
        (tmp / "prepared_disarm_body.h").write_text(
            function(pcd, "fman_pcd_fe_disarm_preflight_locked") +
            function(pcd, "fman_pcd_fe_disengage_prepared")
        )
        (tmp / "parser_body.h").write_text(parser)
        (tmp / "workspace_disarm_body.h").write_text(
            function(pcd, "fman_pcd_fe_port_del")
        )
        (tmp / "arm_failure_body.h").write_text(
            function(pcd, "fman_pcd_fe_fault_arm") + engage_locked + engage
        )
        (tmp / "arm_table_fragment.h").write_text(
            "static int prepare_port_table(struct fman_pcd *pcd, u8 port_id,\n"
            "    unsigned long gro, unsigned long mto, unsigned long ato,\n"
            "    struct fman_pcd_ehash_table **chosen)\n{\n"
            "int err __attribute__((unused));\n" + table_fragment +
            "\n*chosen = et;\nreturn 0;\n}\n"
        )
        (tmp / "kg_arm_prefix.h").write_text(kg_arm_prefix)
        (tmp / "fe_context_body.h").write_text(
            pcd[fe_defs_start:fe_defs_end] + "\n" +
            "\n".join(function(pcd, name) for name in
                      ("fman_pcd_fe_build", "fman_pcd_fe_context_build",
                       "fman_pcd_fe_build_contexts"))
        )
        (tmp / "debugfs_lifecycle_body.h").write_text(
            function(pcd, "fman_pcd_fe_port_write") +
            function(pcd, "fman_pcd_fe_arm_write") +
            function(pcd, "fman_pcd_fe_arm_show") +
            function(pcd, "fman_pcd_fe_extc_show") +
            function(pcd, "fman_pcd_fe_extc_write")
        )
        debug_chain = ""
        if "static bool fman_pcd_fe_debug_mutation_blocked_locked(" in pcd:
            debug_chain += function(pcd, "fman_pcd_fe_debug_mutation_blocked_locked")
        debug_chain += function(pcd, "fman_pcd_fe_obs_disarm")
        debug_chain += "\n".join(
            function(pcd, name) for name in
            ("fman_pcd_fe_pool_get_locked", "fman_pcd_fe_pool_get",
             "fman_pcd_fe_pool_put", "fman_pcd_fe_pool_write",
             "fman_pcd_fe_singletons_write", "fman_pcd_fe_enq_write",
             "fman_pcd_fe_enter_write", "fman_pcd_fe_hashfe_write",
             "fman_pcd_fe_obs_write", "fman_pcd_fe_group_write",
             "fman_pcd_ehash_write", "fman_pcd_fe_kg_ekfc_write")
        )
        (tmp / "debugfs_chain_body.h").write_text(debug_chain)
        for name in ("fman_disarm", "fman_prepared_disarm",
                     "fman_port_disarm", "fman_arm_fault",
                     "fman_kg_arm_started", "fman_arm_table",
                     "fman_debugfs_lifecycle", "fman_workspace_disarm",
                     "fman_fe_context", "fman_debugfs_chain"):
            binary = tmp / name
            subprocess.run(
                [*compiler, *compile_flags, "-std=gnu11", "-O2", "-Wall", "-Wextra", "-Werror",
                 "-pthread",
                 "-Wno-unused-parameter", "-I", str(tmp),
                 str(root / "tests/dut" / f"{name}.c"), "-o", str(binary)],
                check=True,
            )
            subprocess.run([str(binary)], check=True, timeout=30)


if __name__ == "__main__":
    main()
