#!/usr/bin/env python3
"""Check generated FMan INV0 writers; optionally report unresolved cases."""

import argparse
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile


NON_CODE = re.compile(
    r'/\*.*?\*/|//[^\n]*|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'',
    re.DOTALL,
)
WRITER = re.compile(r"\bfman_set_fpm_extc\s*\(")
DECLARATION = re.compile(
    r"\bvoid\s+fman_set_fpm_extc\s*\([^;{}]*\)\s*(?=[;{])"
)
FE_LOCK = re.compile(r"\bmutex_lock\s*\(\s*&pcd->fe_lock\s*\)\s*;")
FE_UNLOCK = re.compile(r"\bmutex_unlock\s*\(\s*&pcd->fe_lock\s*\)\s*;")


def function(source, prefix):
    match = re.search(rf"(?m)^{re.escape(prefix)}\([^;]*?\n\{{\n", source)
    if not match:
        raise ValueError(f"{prefix}: function not found")
    return source[match.start():source.index("\n}\n", match.end()) + 3]


def check_publication_fence(add_key, predicate):
    """A latched INV0 fault must stop insertion before any table mutation."""
    if "READ_ONCE(fman->inv0_faulted)" not in predicate or "!fman ||" not in predicate:
        raise ValueError("fman_fpm_inv0_faulted must treat a missing FMan as faulted")
    fence = add_key.find("if (fman_fpm_inv0_faulted(")
    if fence < 0 or "return -ESHUTDOWN;" not in add_key[fence:fence + 240]:
        raise ValueError("ehash insert lacks the INV0 fault fence")
    for mutation in ("fman_pcd_ehash_recover(", "fman_pcd_ehash_del_key(",
                     "kzalloc(", "dma_pool_zalloc("):
        position = add_key.find(mutation)
        if position < 0 or position < fence:
            raise ValueError(f"INV0 fence must precede {mutation}")


def f177_blocks(source):
    start = "\tif (!err) {\n\t\t/* F-177"
    blocks = []
    offset = 0
    while True:
        offset = source.find(start, offset)
        if offset < 0:
            break
        end = source.index("\n\t}\n", offset) + len("\n\t}\n")
        blocks.append(source[offset:end])
        offset = end
    if len(blocks) != 2:
        raise ValueError(f"expected two F-177 blocks, found {len(blocks)}")
    return blocks


def check_f177_caller_lock(owner, block, name):
    def fail():
        raise ValueError(f"{name}: cannot verify FE ownership lock around F-177")

    if owner.count(block) != 1:
        fail()
    start = owner.index(block)
    code = NON_CODE.sub(lambda match: re.sub(r"[^\n]", " ", match.group()), owner)
    before = code[:start]
    locks = list(FE_LOCK.finditer(before))
    if len(locks) != 1 or re.search(r"\b(?:goto|case|default)\b|(?m:^\s*#)", before):
        fail()
    lock = locks[0]
    previous = before[:lock.start()].rstrip()
    if not previous or previous[-1] not in "{};":
        fail()

    def scopes(position):
        stack = []
        for offset, char in enumerate(code[:position]):
            if char == "{":
                stack.append(offset)
            elif char == "}":
                if not stack:
                    fail()
                stack.pop()
        return stack

    lock_scope = scopes(lock.start())
    block_scope = scopes(start)
    if block_scope[:len(lock_scope)] != lock_scope:
        fail()
    for unlock in FE_UNLOCK.finditer(before[lock.end():]):
        following = before[lock.end() + unlock.end():]
        if not re.match(r"\s*return\b[^;]*;", following):
            fail()
    if not FE_UNLOCK.search(code[start + len(block):]):
        fail()


def writer_count(source):
    code = NON_CODE.sub(" ", source)
    return len(WRITER.findall(code)) - len(DECLARATION.findall(code))


def check_writer_inventory(kernel_source, modeled, overrides):
    expected = {
        path.resolve(): sum(writer_count(body) for body in bodies)
        for path, bodies in modeled.items()
    }
    found = set()

    def walk_error(error):
        raise error

    for directory, subdirs, names in os.walk(kernel_source, onerror=walk_error):
        subdirs[:] = [name for name in subdirs if name != ".git"]
        for name in names:
            if not name.endswith((".c", ".h")):
                continue
            path = Path(directory) / name
            resolved = path.resolve()
            source = overrides.get(resolved, path).read_bytes()
            actual = writer_count(source.decode()) if b"fman_set_fpm_extc" in source else 0
            wanted = expected.get(resolved, 0)
            if actual != wanted:
                raise ValueError(
                    f"{path}: unmodeled or missing fman_set_fpm_extc calls "
                    f"(found {actual}, modeled {wanted})"
                )
            if resolved in expected:
                found.add(resolved)
    if found != expected.keys():
        raise ValueError(f"modeled source files missing from inventory: {expected.keys() - found}")
    print(
        f"INV0 inventory: {sum(expected.values())} modeled direct C/header calls; "
        "raw MMIO, indirect calls, symlinked directories and external modules are not covered",
        flush=True,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kernel_source", type=Path)
    parser.add_argument("--characterize", action="store_true",
                        help="report unresolved cases without treating them as a passing gate")
    args = parser.parse_args()
    harness = Path(__file__).resolve().parents[1] / "tests/dut/fman_global_sync.c"
    fixup = Path(__file__).resolve().parents[1] / "bin/kernel-fixups/F_293.py"
    compiler = shlex.split(os.environ.get("HOSTCC", "cc"))
    with tempfile.TemporaryDirectory(prefix="fman-global-sync-") as directory:
        path = Path(directory)
        kernel = path / "kernel"
        original_fman = args.kernel_source / "drivers/net/ethernet/freescale/fman"
        fman = kernel / "drivers/net/ethernet/freescale/fman"
        shutil.copytree(
            original_fman, fman,
            ignore=shutil.ignore_patterns(".*", "*.o", "*.cmd", "*.d"),
        )
        subprocess.run([sys.executable, str(fixup)], cwd=kernel, check=True)
        subprocess.run([sys.executable, str(fixup)], cwd=kernel, check=True)

        port = (fman / "fman_port.c").read_text()
        pcd = (fman / "fman_pcd.c").read_text()
        fman_c = (fman / "fman.c").read_text()
        debug, production = f177_blocks(pcd)
        for prefix, block in zip(
            ("static ssize_t fman_pcd_fe_flow_write",
             "int fman_pcd_fe_flow_add"),
            (debug, production),
        ):
            check_f177_caller_lock(function(pcd, prefix), block, prefix)
        print("F-177 FE lock: lexical checks only; indirect unlocks and complete "
              "control flow are not modeled", flush=True)
        check_publication_fence(
            function(pcd, "static int fman_pcd_ehash_add_key"),
            function(fman_c, "bool fman_fpm_inv0_faulted"))
        print("ehash publication: INV0 fault refused before recover, eviction "
              "and allocation (lexical order)", flush=True)
        rx = function(port, "int fman_port_set_cc_base")
        oh = function(port, "int fman_port_set_oh_cc_base")
        ehash = function(pcd, "static int fman_pcd_ehash_sync")
        extc = function(pcd, "static ssize_t fman_pcd_fe_extc_write")
        sync = function(fman_c, "int fman_fpm_extc_sync")
        poison = function(fman_c, "void fman_fpm_inv0_poison")
        check_writer_inventory(args.kernel_source, {
            original_fman / "fman_port.c": [rx, oh],
            original_fman / "fman_pcd.c": [ehash, debug, production, extc],
            original_fman / "fman.c": [sync, poison],
        }, {
            (original_fman / "fman_port.c").resolve(): fman / "fman_port.c",
            (original_fman / "fman_pcd.c").resolve(): fman / "fman_pcd.c",
            (original_fman / "fman.c").resolve(): fman / "fman.c",
        })
        bodies = [
            sync,
            poison,
            rx,
            oh,
            ehash,
            "static int f177_debug(struct fman_pcd *pcd)\n"
            "{\n\tint err = 0;\n" + debug + "\treturn err;\n}\n",
            "static int f177_production(struct fman_pcd *pcd)\n"
            "{\n\tint err = 0;\n" + production + "\treturn err;\n}\n",
            extc,
        ]
        (path / "fman_global_sync_body.h").write_text("\n".join(bodies))
        binary = path / "fman-global-sync"
        subprocess.run(
            [*compiler, "-std=gnu11", "-O2", "-Wall", "-Wextra", "-Werror",
             "-Wno-unused-parameter", "-pthread", "-I", str(path),
             str(harness), "-o", str(binary)],
            check=True,
        )
        command = [str(binary)]
        if args.characterize:
            command.append("--characterize")
        result = subprocess.run(command, check=False, timeout=30)
        if result.returncode < 0:
            raise RuntimeError(f"characterization terminated by signal {-result.returncode}")
        return result.returncode


if __name__ == "__main__":
    sys.exit(main())
