"""Host tests for the implicit WireGuard fast-path binding (patch 059): sysfs error handling and the device cap."""

from contextlib import contextmanager
import errno
import io
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.host
PATCH = Path(__file__).resolve().parents[1] / "data/vyos-1x-059-wireguard-offload-auto.patch"


class ConfigError(Exception):
    pass


class FakeParameter:
    def __init__(self, *states, error=None, exists=True, missing=(), missing_after_error=(),
                 lease="unowned", lease_exists=True, lease_after_error=None):
        self.states = list(states)
        self.error = error
        self.exists = exists
        self.missing = set(missing)
        self.missing_after_error = set(missing_after_error)
        self.lease = lease
        self.lease_exists = lease_exists
        self.lease_after_error = lease_after_error
        self.writes = []
        self.reads = 0

    def path_exists(self, path):
        if path == "/fake/wgfp_peer":
            return self.exists
        if path == "/fake/wgfp_peer_lease":
            return self.lease_exists
        assert path.startswith("/sys/class/net/")
        return path.rsplit("/", 1)[1] not in self.missing

    @contextmanager
    def open(self, path, mode="r"):
        if path == "/fake/wgfp_peer_lease":
            assert mode == "r"
            if isinstance(self.lease, OSError):
                raise self.lease
            with io.StringIO(self.lease) as source:
                yield source
            return
        assert path == "/fake/wgfp_peer"
        if mode == "w":
            yield self
            return
        state = self.states[min(self.reads, len(self.states) - 1)]
        self.reads += 1
        if isinstance(state, OSError):
            raise state
        with io.StringIO(state) as source:
            yield source

    def write(self, value):
        self.writes.append(value)
        if self.error:
            self.missing.update(self.missing_after_error)
            if self.lease_after_error is not None:
                self.lease = self.lease_after_error
            raise self.error


def cli_namespace(parameter):
    patch = PATCH.read_text()
    start = patch.index("+def wgfp_status():")
    end = patch.index("\n def apply(wireguard):", start)
    source = "\n".join(line[1:] for line in patch[start:end].splitlines()
                       if line.startswith("+"))
    namespace = {
        "ConfigError": ConfigError,
        "WGFP_PARAM": "/fake/wgfp_peer",
        "WGFP_LEASE_PARAM": "/fake/wgfp_peer_lease",
        "errno": errno,
        "open": parameter.open,
        "os": SimpleNamespace(path=SimpleNamespace(exists=parameter.path_exists)),
    }
    exec(compile(source, str(PATCH), "exec"), namespace)
    return namespace


def bind_function(parameter):
    return cli_namespace(parameter)["wgfp_bind"]


def preflight_apply(parameter, events):
    namespace = cli_namespace(parameter)
    namespace["WireGuardIf"] = lambda **kwargs: events.append(("interface", kwargs))
    patch = PATCH.read_text()
    apply = patch.index("\n def apply(wireguard):")
    start = patch.index("+    if os.path.exists(WGFP_PARAM):", apply)
    end = patch.index("\n@@", start)
    source = "def apply(wireguard):\n" + "\n".join(
        line[1:] for line in patch[start:end].splitlines()
        if line.startswith((" ", "+"))
    )
    exec(compile(source, str(PATCH), "exec"), namespace)
    return namespace["apply"]


@pytest.mark.parametrize("state", [
    "on stopping=1 sealed=0 dev=wg0",
    OSError(errno.EACCES, "status unavailable"),
])
def test_stopping_or_unreadable_status_blocks_interface_mutation(state):
    events = []
    with pytest.raises(ConfigError):
        preflight_apply(FakeParameter(state), events)(
            {"ifname": "wg0", "wgfp_ifaces": ["wg0"]}
        )
    assert events == []


def test_healthy_status_allows_interface_update():
    events = []
    preflight_apply(FakeParameter("on stopping=0 sealed=0 dev=wg0"), events)(
        {"ifname": "wg0", "wgfp_ifaces": ["wg0"]}
    )
    assert events == [("interface", {"ifname": "wg0", "wgfp_ifaces": ["wg0"]})]


def test_missing_replacement_blocks_disabling_old_binding_before_mutation():
    parameter = FakeParameter("on stopping=0 sealed=0 dev=wg0", missing=["wg1"])
    events = []
    with pytest.raises(ConfigError, match="replacement"):
        preflight_apply(parameter, events)({"ifname": "wg0", "wgfp_ifaces": ["wg1"]})
    assert events == []
    assert parameter.writes == []


@pytest.mark.parametrize(("state", "ifaces"), [
    ("off", ["wg0"]),
    ("on stopping=0 sealed=0 dev=wg0", ["wg0"]),
    ("on stopping=0 sealed=0 dev=wg0", []),
])
def test_reserved_fast_path_blocks_mutation_even_if_binding_matches(state, ifaces):
    parameter = FakeParameter(state, lease="owned=0123456789abcdef dev=wg0")
    events = []
    with pytest.raises(ConfigError, match="reserved") as error:
        preflight_apply(parameter, events)({"ifname": "wg0", "wgfp_ifaces": ifaces})
    assert "0123456789abcdef" not in str(error.value)
    assert events == []
    assert parameter.writes == []


def test_inactive_reservation_does_not_block_software_only_interface():
    parameter = FakeParameter("off", lease="owned=0123456789abcdef dev=off")
    events = []
    preflight_apply(parameter, events)({"ifname": "wg0", "wgfp_ifaces": []})
    bind_function(parameter)([])
    assert events == [("interface", {"ifname": "wg0", "wgfp_ifaces": []})]
    assert parameter.writes == []


@pytest.mark.parametrize("lease", [
    "unexpected",
    OSError(errno.EACCES, "reservation unavailable"),
])
def test_unknown_reservation_status_blocks_requested_offload(lease):
    parameter = FakeParameter("off", lease=lease)
    with pytest.raises(ConfigError, match="reservation"):
        bind_function(parameter)(["wg0"])
    assert parameter.writes == []


def test_older_kernel_without_reservation_parameter_still_binds():
    parameter = FakeParameter("off", lease_exists=False)
    bind_function(parameter)(["wg0"])
    assert parameter.writes == ["wg0"]


@pytest.mark.parametrize("ifaces", [["wg0"], [], ["wg1"]])
def test_stopping_binding_is_rejected_without_retry(ifaces):
    parameter = FakeParameter("on stopping=1 sealed=0 dev=wg0")
    with pytest.raises(ConfigError, match="teardown"):
        bind_function(parameter)(ifaces)
    assert parameter.writes == []


@pytest.mark.parametrize("code", [errno.EBUSY, errno.EACCES])
def test_real_sysfs_write_failures_fail_the_commit(code):
    parameter = FakeParameter("off", error=OSError(code, "injected"))
    with pytest.raises(ConfigError, match="injected"):
        bind_function(parameter)(["wg0"])
    assert parameter.writes == ["wg0"]


def test_failed_delete_enodev_cannot_be_mistaken_for_missing_new_device():
    parameter = FakeParameter(
        "on stopping=0 sealed=0 dev=wg0",
        "on stopping=1 sealed=0 dev=wg0",
        error=OSError(errno.ENODEV, "device unavailable"),
    )
    with pytest.raises(ConfigError, match="teardown"):
        bind_function(parameter)(["wg1"])
    assert parameter.writes == ["wg1"]


def test_unknown_state_after_enodev_does_not_report_success():
    parameter = FakeParameter(
        "off", OSError(errno.ENOENT, "status disappeared"),
        error=OSError(errno.ENODEV, "device unavailable"),
    )
    with pytest.raises(ConfigError, match="status"):
        bind_function(parameter)(["wg0"])


def test_missing_device_does_not_replace_binding_during_multi_interface_apply(capsys):
    parameter = FakeParameter("on stopping=0 sealed=0 dev=wg0", missing=["wg1"])
    bind = bind_function(parameter)
    bind(["wg0", "wg1"])
    assert "wg1" in capsys.readouterr().out
    assert parameter.writes == []
    parameter.missing.clear()
    bind(["wg0", "wg1"])
    assert parameter.writes == ["wg0 wg1"]


def test_missing_replacement_cannot_leave_an_unwanted_binding():
    parameter = FakeParameter("on stopping=0 sealed=0 dev=wg0", missing=["wg1"])
    with pytest.raises(ConfigError, match="replacement"):
        bind_function(parameter)(["wg1"])
    assert parameter.writes == []


def test_missing_device_after_enodev_is_reported(capsys):
    parameter = FakeParameter(
        "off", "off", error=OSError(errno.ENODEV, "device unavailable"),
        missing_after_error=["wg1"],
    )
    bind_function(parameter)(["wg0", "wg1"])
    assert "wg1" in capsys.readouterr().out
    assert parameter.writes == ["wg0 wg1"]


def test_enodev_is_an_error_when_all_devices_are_present():
    parameter = FakeParameter("off", "off", error=OSError(errno.ENODEV, "device unavailable"))
    with pytest.raises(ConfigError, match="device unavailable"):
        bind_function(parameter)(["wg0"])


def test_reservation_acquired_during_enodev_is_not_deferred(capsys):
    parameter = FakeParameter(
        "off", "off", error=OSError(errno.ENODEV, "device unavailable"),
        missing_after_error=["wg1"], lease_after_error="owned=token dev=off",
    )
    with pytest.raises(ConfigError, match="reserved"):
        bind_function(parameter)(["wg0", "wg1"])
    assert not capsys.readouterr().out


@pytest.mark.parametrize("state", ["", "on stopping=0", "on stopping=0 dev=off", "unrecognized"])
def test_invalid_status_is_not_treated_as_off(state):
    parameter = FakeParameter(state)
    with pytest.raises(ConfigError, match="status"):
        bind_function(parameter)([])
    assert parameter.writes == []


def test_healthy_binding_is_not_rewritten():
    parameter = FakeParameter("on stopping=0 sealed=0 dev=wg0")
    bind_function(parameter)(["wg0"])
    assert parameter.writes == []


def test_changed_binding_is_written():
    parameter = FakeParameter("off")
    bind_function(parameter)(["wg0", "wg1"])
    assert parameter.writes == ["wg0 wg1"]


def test_unsupported_kernel_is_silent_without_writing(capsys):
    # Offload is implicit, so a kernel without the fast path must not warn on every commit.
    parameter = FakeParameter(exists=False)
    bind_function(parameter)(["wg0"])
    assert capsys.readouterr().out == ""
    assert parameter.writes == []


def test_binding_is_capped_at_the_kernel_limit(capsys):
    parameter = FakeParameter("off")
    ifaces = [f"wg{i}" for i in range(10)]
    bind_function(parameter)(ifaces)
    assert parameter.writes == [" ".join(ifaces[:8])]
    out = capsys.readouterr().out
    assert "wg8, wg9 stay on the software path" in out


def test_patch_has_no_wireguard_offload_leaf():
    patch = PATCH.read_text()
    assert "interfaces_wireguard.xml.in" not in patch
    assert "'offload'" not in patch
