"""Exercise owned iperf servers without touching shared benchmark processes."""

import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys

import pytest

pytestmark = pytest.mark.host
ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "bin/testrig-wg-server.py"
STOP = ROOT / "bin/testrig-wgfp-stop.py"


@pytest.fixture
def ownership_directory():
    if not shutil.which("iperf3") or not hasattr(os, "pidfd_open"):
        pytest.skip("iperf3 and pidfd support are required")
    directory = Path("/tmp") / f"wgrig-srv-{secrets.token_hex(16)}-s0"
    yield directory
    identity = directory / "identity"
    if identity.is_file():
        pid, started, port = identity.read_text().strip().split(":")
        subprocess.run(
            [sys.executable, str(STOP), pid, started, "server", port],
            text=True, capture_output=True, check=False, timeout=10,
        )
        helper("remove", directory, port)
    assert not directory.exists(), f"owned test server requires cleanup: {directory}"


def helper(operation, directory, port):
    return subprocess.run(
        [sys.executable, str(SERVER), operation, str(directory), str(port)],
        text=True, capture_output=True, check=False, timeout=15,
    )


def unused_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_owned_server_starts_and_stops_by_recorded_process_identity(ownership_directory):
    port = unused_port()
    result = helper("start", ownership_directory, port)
    assert result.returncode == 0, result.stderr
    result = helper("identity", ownership_directory, port)
    assert result.returncode == 0, result.stderr
    pid, started, recorded_port = result.stdout.strip().split(":")
    assert int(recorded_port) == port
    assert helper("remove", ownership_directory, port).returncode != 0
    stopped = subprocess.run(
        [sys.executable, str(STOP), pid, started, "server", str(port)],
        text=True, capture_output=True, check=False, timeout=10,
    )
    assert stopped.returncode == 0, stopped.stderr
    result = helper("remove", ownership_directory, port)
    assert result.returncode == 0, result.stderr
    assert helper("remove", ownership_directory, port).returncode == 0


def test_server_port_collision_does_not_stop_existing_listener(ownership_directory):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        result = helper("start", ownership_directory, port)
        assert result.returncode == 11, result.stderr
        assert not ownership_directory.exists()
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            pass


def test_existing_ownership_directory_is_not_adopted(ownership_directory):
    ownership_directory.mkdir(mode=0o700)
    sentinel = ownership_directory / "foreign"
    sentinel.write_text("unchanged")
    try:
        result = helper("start", ownership_directory, unused_port())
        assert result.returncode == 10, result.stderr
        assert sentinel.read_text() == "unchanged"
    finally:
        sentinel.unlink()
        ownership_directory.rmdir()
