#!/usr/bin/env python3
"""Exercise source-extracted CAAM QI queue and DMA ownership paths."""

import argparse
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "tests/dut/caam_qi_ownership.c"
CRYPTO_HARNESS = ROOT / "tests/dut/caam_qi_crypto.c"
INIT_HARNESS = ROOT / "tests/dut/caam_qi_init.c"


def function(source, prefix):
    match = re.search(rf"(?m)^{re.escape(prefix)}\([^;]*?\n\{{\n", source)
    if not match:
        raise ValueError(f"{prefix}: function not found")
    return source[match.start():source.index("\n}\n", match.end()) + 3]


def run(kernel, sanitize, check_pending, check_release, check_eqcr, check_crypto,
        check_init):
    source = (kernel / "drivers/crypto/caam/qi.c").read_text()
    body = "\n".join(
        function(source, prefix) for prefix in (
            "static int kill_fq",
            "int caam_qi_enqueue",
            "static int empty_caam_fq",
            "void caam_drv_ctx_rel",
        )
    )
    with tempfile.TemporaryDirectory(prefix="caam-qi-ownership-") as directory:
        path = Path(directory)
        (path / "caam_qi_ownership_body.h").write_text(body)
        binary = path / "caam_qi_ownership"
        flags = ["-fsanitize=address,undefined", "-fno-omit-frame-pointer"] if sanitize else []
        if check_init:
            devres = (kernel / "include/linux/device/devres.h").read_text()
            init_body = "\n".join((
                function(source, "static void caam_qi_shutdown"),
                function(source, "static void free_rsp_fqs"),
                function(source, "static void free_caam_qi_pcpu_netdev"),
                function(devres, "static inline int __devm_add_action_or_reset"),
                function(source, "int caam_qi_init"),
            ))
            (path / "caam_qi_init_body.h").write_text(init_body)
            subprocess.run(
                shlex.split(os.environ.get("HOSTCC", "cc")) + [
                    "-std=gnu11", "-Wall", "-Wextra", "-Werror",
                    "-Wno-unused-parameter", *flags, "-I", str(path),
                    str(INIT_HARNESS), "-o", str(binary),
                ],
                check=True,
            )
            subprocess.run([str(binary)], check=True, timeout=30)
            return
        if check_crypto:
            enqueue = function(source, "int caam_qi_enqueue")
            if "if (ret == -EBUSY)\n\t\tret = -EAGAIN;" not in enqueue:
                raise ValueError("CAAM synchronous full-ring return changed")
            alg = (kernel / "drivers/crypto/caam/caamalg_qi.c").read_text()
            crypto = (kernel / "include/linux/crypto.h").read_text()
            crypto_body = "\n".join((
                function(alg, "static inline int aead_crypt"),
                function(alg, "static inline int skcipher_crypt"),
                function(crypto, "static inline int crypto_wait_req"),
            ))
            (path / "caam_qi_crypto_body.h").write_text(crypto_body)
            subprocess.run(
                shlex.split(os.environ.get("HOSTCC", "cc")) + [
                    "-std=gnu11", "-Wall", "-Wextra", "-Werror",
                    "-Wno-unused-parameter", *flags, "-I", str(path),
                    str(CRYPTO_HARNESS), "-o", str(binary),
                ],
                check=True,
            )
            subprocess.run([str(binary)], check=True, timeout=30)
            return
        if "qman_enqueue_fqid(" in function(source, "int caam_qi_enqueue"):
            flags.append("-DTEST_CAAM_FQID")
        if check_eqcr:
            qman = (kernel / "drivers/soc/fsl/qbman/qman.c").read_text()
            qman_body = "\n".join(
                function(qman, prefix) for prefix in (
                    "int qman_enqueue",
                    "int qman_enqueue_fqid",
                )
            )
            (path / "caam_qi_qman_body.h").write_text(qman_body)
            flags.append("-DTEST_CAAM_REAL_QMAN")
        if check_release:
            alg = (kernel / "drivers/crypto/caam/caamalg_qi.c").read_text()
            api = (kernel / "crypto/api.c").read_text()
            exit_body = function(alg, "static void caam_exit_common")
            if (
                "u8 key[CAAM_MAX_KEY_SIZE];" not in alg
                or "dma_map_single(dev, ctx->key, sizeof(ctx->key)," not in alg
                or "ctx->cdata.key_dma = ctx->key_dma" not in alg
                or alg.count("cra_ctxsize = sizeof(struct caam_ctx)") != 2
                or exit_body.count("caam_drv_ctx_rel(") != 2
                or "caam_exit_common(" not in function(alg, "static void caam_cra_exit")
                or "caam_exit_common(" not in function(alg, "static void caam_aead_exit")
            ):
                raise ValueError("CAAM transform ownership changed; review the model")
            destroy = function(api, "void crypto_destroy_tfm")
            exit_ops = function(api, "static void crypto_exit_ops")
            aead = (kernel / "crypto/aead.c").read_text()
            skcipher = (kernel / "crypto/skcipher.c").read_text()
            if (
                not (0 <= destroy.find("refcount_dec_and_test(&tfm->refcnt)") <
                     destroy.find("crypto_exit_ops(tfm)") <
                     destroy.find("kfree_sensitive(mem)"))
                or "tfm->exit(tfm)" not in exit_ops
                or "alg->exit = caam_cra_exit" not in alg
                or "alg->exit = caam_aead_exit" not in alg
                or "alg->exit(aead)" not in function(
                    aead, "static void crypto_aead_exit_tfm",
                )
                or "alg->exit(skcipher)" not in function(
                    skcipher, "static void crypto_skcipher_exit_tfm",
                )
            ):
                raise ValueError("crypto transform release ordering changed")
            control = exit_body.replace(
                "static void caam_exit_common(",
                "static void caam_exit_common_leaf_control(", 1,
            ).replace("caam_drv_ctx_rel(", "leaf_control_drv_ctx_rel(")
            (path / "caam_qi_exit_body.h").write_text(exit_body + "\n" + control)
            flags.append("-DTEST_CAAM_RELEASE")
        subprocess.run(
            shlex.split(os.environ.get("HOSTCC", "cc")) + [
                "-std=gnu11", "-Wall", "-Wextra", "-Werror",
                "-Wno-unused-parameter", *flags, "-I", str(path),
                str(HARNESS), "-o", str(binary),
            ],
            check=True,
        )
        command = [str(binary)]
        if check_pending:
            command.append("--check-pending")
        if check_release:
            command.append("--check-release")
        if check_eqcr:
            command.append("--check-eqcr")
        subprocess.run(command, check=True, timeout=30)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kernel_source", type=Path)
    parser.add_argument("--sanitize", action="store_true")
    checks = parser.add_mutually_exclusive_group()
    checks.add_argument(
        "--check-pending", action="store_true",
        help="fail if an old job can remain pending after the drain returns",
    )
    checks.add_argument(
        "--check-release", action="store_true",
        help="fail if queue or crypto-transform owners are released after a teardown error",
    )
    checks.add_argument(
        "--check-eqcr", action="store_true",
        help="exercise the extracted QMan enqueue path with simulated full rings",
    )
    checks.add_argument(
        "--check-crypto", action="store_true",
        help="exercise extracted crypto callers and synchronous wait handling",
    )
    checks.add_argument(
        "--check-init", action="store_true",
        help="exercise successful and failed devres-action registration cleanup",
    )
    args = parser.parse_args()
    print("Host model only: no CAAM or QMan hardware was exercised.", flush=True)
    try:
        run(
            args.kernel_source, args.sanitize, args.check_pending,
            args.check_release, args.check_eqcr, args.check_crypto, args.check_init,
        )
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"CAAM QI ownership test failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
