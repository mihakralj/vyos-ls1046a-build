"""Host regression coverage for the BMan free-buffer counter reader."""

import importlib.util
from pathlib import Path
import struct

import pytest

pytestmark = pytest.mark.host
PATH = Path(__file__).resolve().parents[1] / "bin/bman-pool-count.py"
SPEC = importlib.util.spec_from_file_location("bman_pool_count", PATH)
BMAN = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BMAN)


@pytest.fixture
def bman(tmp_path, monkeypatch):
    sys_net = tmp_path / "net"
    (sys_net / "eth3").mkdir(parents=True)
    devmem = tmp_path / "mem"
    devmem.write_bytes(bytes(0x1000))
    monkeypatch.setattr(BMAN, "SYS_CLASS_NET", sys_net)
    monkeypatch.setattr(BMAN, "DEVMEM", str(devmem))
    monkeypatch.setattr(BMAN, "BMAN_BASE", 0)
    return sys_net, devmem


@pytest.mark.parametrize(("bpid", "expected"), [(0, 0x12345678), (3, 2161), (63, 0x10203040)])
def test_reads_the_selected_pool_in_big_endian(bman, bpid, expected):
    sys_net, devmem = bman
    (sys_net / "eth3/bpids").write_text(f"{bpid}\n")
    with devmem.open("r+b") as regs:
        regs.seek(0x600 + 4 * bpid)
        regs.write(struct.pack(">I", expected))
    assert BMAN.read_pool("eth3") == (bpid, expected)


@pytest.mark.parametrize("bpids", ["", "-1", "64", "3 4", "not-a-number"])
def test_rejects_missing_or_ambiguous_bpid(bman, bpids):
    sys_net, _ = bman
    (sys_net / "eth3/bpids").write_text(bpids)
    with pytest.raises(ValueError):
        BMAN.read_pool("eth3")


def test_reports_unavailable_registers(bman):
    sys_net, devmem = bman
    (sys_net / "eth3/bpids").write_text("3")
    devmem.unlink()
    with pytest.raises(FileNotFoundError):
        BMAN.read_pool("eth3")


@pytest.mark.parametrize("interface", [".", "..", "../eth3"])
def test_rejects_non_interface_paths(bman, interface):
    with pytest.raises(ValueError):
        BMAN.read_pool(interface)
