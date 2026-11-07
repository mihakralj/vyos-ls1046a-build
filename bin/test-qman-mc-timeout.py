#!/usr/bin/env python3
"""Characterize QMan command timeouts and shutdown-ring ownership."""

import argparse
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "tests/dut/qman_mc_timeout.c"
INIT_HARNESS = ROOT / "tests/dut/qman_initfq_precommit.c"
SHUTDOWN_HARNESS = ROOT / "tests/dut/qman_shutdown_ring.c"


def function(source, prefix):
    match = re.search(rf"(?m)^{re.escape(prefix)}\([^;]*?\n\{{\n", source)
    if not match:
        raise ValueError(f"{prefix}: function not found")
    return source[match.start():source.index("\n}\n", match.end()) + 3]


def dpaa_checking_unset(kernel):
    config = kernel / ".config"
    if config.exists():
        return "# CONFIG_FSL_DPAA_CHECKING is not set" in config.read_text()
    # CI runs this inside the patch loop, before build-kernel.sh generates
    # .config: derive it from the Kconfig default and every config fragment.
    kconfig = (kernel / "drivers/soc/fsl/qbman/Kconfig").read_text()
    entry = kconfig.split("config FSL_DPAA_CHECKING\n", 1)[1].split("help", 1)[0]
    if re.search(r"(?m)^\s*default\b", entry):
        return False
    dirs = ("kernel/common/kernel-config", "kernel/common/vyos-base",
            "vyos-build/scripts/package-build/linux-kernel/config")
    return not any(
        "CONFIG_FSL_DPAA_CHECKING=y" in p.read_text(errors="replace")
        for d in dirs for p in (ROOT / d).rglob("*") if p.is_file()
    )


def run(kernel, controls, check_retire_snapshot, check_verb, check_shutdown_responses):
    qman = (kernel / "drivers/soc/fsl/qbman/qman.c").read_text()
    system = (kernel / "drivers/soc/fsl/qbman/dpaa_sys.h").read_text()
    if not dpaa_checking_unset(kernel):
        raise ValueError("model requires CONFIG_FSL_DPAA_CHECKING unset")
    if "#else\n#define DPAA_ASSERT(x)\n#endif" not in system:
        raise ValueError("DPAA_ASSERT semantics changed; review the model")

    init = function(qman, "int qman_init_fq")
    commit = init.find("qm_mc_commit(")
    timeout = init.find("qm_mc_result_timeout(", commit)
    if commit < 0 or timeout < commit or "ret = -ETIMEDOUT;" not in init[timeout:]:
        raise ValueError("INITFQ timeout ordering changed; review the model")
    mapping = init.find("dma_map_single(")
    map_error = init.find("dma_mapping_error(")
    start = init.find("qm_mc_start(")
    if not (0 <= mapping < map_error < start < commit):
        raise ValueError("F-299 must complete fallible DMA mapping before MC start")
    if "goto out;" in init[start:commit]:
        raise ValueError("fallible exit remains between MC start and commit")
    if len(re.findall(r"\bdma_addr_t\s+phys_fq\b", init)) != 1:
        raise ValueError("INITFQ DMA address must not be shadowed")

    body = "\n".join(
        function(qman, prefix) for prefix in (
            "static inline union qm_mc_command *qm_mc_start",
            "static inline void qm_mc_commit",
            "static inline union qm_mc_result *qm_mc_result",
            "static inline int qm_mc_result_timeout",
            "int qman_query_fq",
            "int qman_query_fq_np",
        )
    )
    macro_start = qman.index("#define qm_mr_drain(p, V)")
    macro_end = qman.index("int qman_shutdown_fq(", macro_start)
    shutdown = "\n".join((
        function(qman, "static int _qm_mr_consume_and_match_verb"),
        function(qman, "static int _qm_dqrr_consume_and_match"),
        qman[macro_start:macro_end],
        function(qman, "int qman_shutdown_fq"),
        function(qman, "int qman_release_fqid"),
    ))
    with tempfile.TemporaryDirectory(prefix="qman-mc-timeout-") as directory:
        path = Path(directory)
        (path / "qman_initfq_body.h").write_text(init)
        init_binary = path / "qman_initfq_precommit"
        subprocess.run(
            shlex.split(os.environ.get("HOSTCC", "cc")) + [
                "-std=gnu11", "-O2", "-Wall", "-Wextra", "-Werror",
                "-Wno-unused-parameter", "-I", str(path),
                str(INIT_HARNESS), "-o", str(init_binary),
            ],
            check=True,
        )
        subprocess.run([str(init_binary)], check=True, timeout=30)
        (path / "qman_mc_body.h").write_text(body)
        binary = path / "qman_mc_timeout"
        subprocess.run(
            shlex.split(os.environ.get("HOSTCC", "cc")) + [
                "-std=gnu11", "-Wall", "-Wextra", "-Werror",
                "-Wno-unused-parameter", "-I", str(path),
                str(HARNESS), "-o", str(binary),
            ],
            check=True,
        )
        (path / "qman_shutdown_body.h").write_text(shutdown)
        shutdown_binary = path / "qman_shutdown_ring"
        subprocess.run(
            shlex.split(os.environ.get("HOSTCC", "cc")) + [
                "-std=gnu11", "-Wall", "-Wextra", "-Werror",
                "-Wno-unused-parameter", "-I", str(path),
                str(SHUTDOWN_HARNESS), "-o", str(shutdown_binary),
            ],
            check=True,
        )
        failed = False
        if check_retire_snapshot or check_shutdown_responses:
            models = (shutdown_binary,)
        elif check_verb:
            models = (binary,)
        else:
            models = (binary, shutdown_binary)
        for model in models:
            command = [str(model)]
            if controls:
                command.append("--oracle-control")
            elif check_retire_snapshot:
                command.append("--check-retire-snapshot")
            elif check_shutdown_responses:
                command.append("--check-shutdown-responses")
            elif check_verb:
                command.append("--check-verb")
            result = subprocess.run(command, check=False, timeout=30)
            if result.returncode not in (0, 1):
                raise RuntimeError(f"{model.name} terminated with {result.returncode}")
            failed |= result.returncode != 0
        return int(failed)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kernel_source", type=Path)
    checks = parser.add_mutually_exclusive_group()
    checks.add_argument(
        "--characterize", action="store_true",
        help="report uncontained cases without treating them as a safety pass",
    )
    checks.add_argument(
        "--oracle-controls", action="store_true",
        help="exercise the oracles with simulated, non-production containment",
    )
    checks.add_argument(
        "--check-retire-snapshot", action="store_true",
        help="check the narrowly scoped retirement-result snapshot",
    )
    checks.add_argument(
        "--check-verb", action="store_true",
        help="check that both FQ queries reject a response with the wrong verb",
    )
    checks.add_argument(
        "--check-shutdown-responses", action="store_true",
        help="check shutdown rejects failed and mismatched management responses",
    )
    args = parser.parse_args()
    print(
        "Host model only: command timing and hardware responses are simulated.",
        flush=True,
    )
    if args.oracle_controls:
        print("Oracle controls simulate containment, not production code.", flush=True)
    try:
        failed = run(
            args.kernel_source, args.oracle_controls, args.check_retire_snapshot,
            args.check_verb, args.check_shutdown_responses,
        )
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"QMan MC timeout characterization failed: {error}", file=sys.stderr)
        return 2
    if failed and args.characterize and not args.oracle_controls:
        print("Uncontained cases remain; characterization is not a safety pass.")
        return 0
    return failed


if __name__ == "__main__":
    sys.exit(main())
