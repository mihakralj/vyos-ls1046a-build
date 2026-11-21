"""Host regression tests for S5(1) same-MAC neighbour-event cache (ask_neigh.c)."""

from pathlib import Path
import os
import shlex
import subprocess

import pytest


pytestmark = pytest.mark.host
ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "kernel/ask/oot-modules/ask/ask_neigh.c"
HARNESS = ROOT / "tests/dut/ask_neigh_cache.c"


def test_neigh_same_mac_cache(tmp_path):
    src = SOURCE.read_text()
    start = src.index("struct ask_neigh_event {")
    end = src.index("\nstatic struct notifier_block ask_neigh_netdev_nb", start)
    body = src[start:end]
    assert "ask_neigh_cache_hit" in body
    assert "ask_neigh_cache_commit" in body
    assert "ask_neigh_cache_invalidate" in body
    assert "ask_neigh_cache_purge_dev" in body
    assert "ask_neigh_cache_clear" in body
    assert "net_eq(dev_net(dev), &init_net)" in body
    assert "register_netdevice_notifier" in src
    (tmp_path / "neigh_cache_body.h").write_text(body)
    exe = tmp_path / "ask-neigh-cache"
    subprocess.run(
        [*shlex.split(os.environ.get("HOSTCC", "cc")), "-std=gnu11",
         "-Wall", "-Wextra", "-Werror",
         "-Wno-unused-parameter", "-Wno-unused-function", "-Wno-unused-variable",
         *shlex.split(os.environ.get("HOSTCFLAGS", "")),
         "-I", str(tmp_path), str(HARNESS), "-o", str(exe)],
        check=True,
    )
    out = subprocess.run([str(exe)], capture_output=True, text=True, check=True, timeout=30)
    assert "ask_neigh_cache: OK" in out.stdout
