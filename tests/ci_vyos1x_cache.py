from concurrent.futures import ThreadPoolExecutor
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import time

import pytest


pytestmark = pytest.mark.host
SCRIPT = Path(__file__).resolve().parents[1] / "bin/ci-build-packages.sh"
SETUP = Path(__file__).resolve().parents[1] / "bin/ci-setup-vyos1x.sh"
COMMIT = "a" * 40
PACKAGES = [
    ("vyos-1x", "arm64"), ("vyos-1x-aws", "all"),
    ("vyos-1x-smoketest", "all"), ("vyos-1x-vmware", "all"),
    ("vyos-user-utils", "all"), ("libvyosconfig0", "arm64"),
]


class PackageBuild:
    def __init__(self, workspace, cache, *, reverse=False):
        self.workspace = workspace
        self.cache = cache
        self.bin = workspace / "bin"
        self.overlay = workspace / "data/vyos-1x-files"
        self.package = workspace / "vyos-build/scripts/package-build/vyos-1x"
        self.staged = self.package / "ls1046a-patches/vyos-1x-files"
        self.log = workspace / "build.log"
        self.output_dir = "."
        self.skip_package = ""
        self.bin.mkdir(parents=True)
        self.package.mkdir(parents=True)
        (self.overlay / "src").mkdir(parents=True)
        entries = [
            ("alpha", b"ab"),
            ("beta", b"c"),
            ("empty", b""),
            ("space and\nnewline", b"file"),
        ]
        for name, contents in reversed(entries) if reverse else entries:
            (self.overlay / "src" / name).write_bytes(contents)
        (self.overlay / "src/link").symlink_to("alpha")
        (workspace / "data/vyos-1x-001-test.patch").write_bytes(b"patch\n")
        (self.bin / "ci-setup-vyos1x.sh").write_bytes(b"setup\n")
        (self.bin / "common.sh").write_text(":\n")
        for name in ("apt-get", "ci-build-http-api-tools.sh"):
            path = self.bin / name
            path.write_text("#!/bin/sh\nexit 0\n")
            path.chmod(0o755)
        (self.package / "package.toml").write_text(f'commit_id = "{COMMIT}"\n')
        builder = self.package / "build.py"
        builder.write_text(
            """#!/usr/bin/env python3
import os
from pathlib import Path
import shutil
import subprocess

root = Path(os.environ["FAKE_PACKAGE_ROOT"])
shutil.rmtree(root, ignore_errors=True)
(root / "DEBIAN").mkdir(parents=True)
shutil.copytree(
    "ls1046a-patches/vyos-1x-files", root / "usr/share/overlay", symlinks=True,
    ignore=lambda path, names: [name for name in names if "\\n" in name],
)
for name, arch in @PACKAGES@:
    if name == os.environ["FAKE_SKIP_PACKAGE"]:
        continue
    (root / "DEBIAN/control").write_text(
        f"Package: {name}\\nVersion: 1\\nArchitecture: {arch}\\n"
        "Maintainer: Cache test <test@example.invalid>\\nDescription: cache test\\n"
    )
    output = Path(os.environ["FAKE_OUTPUT_DIR"]) / f"{name}_1_{arch}.deb"
    subprocess.run(
        ["dpkg-deb", "--build", "--root-owner-group", "-Znone", str(root),
         str(output)], check=True,
    )
with open(os.environ["FAKE_BUILD_LOG"], "a") as log:
    log.write("build\\n")
""".replace("@PACKAGES@", repr(PACKAGES))
        )
        builder.chmod(0o755)
        self.stage()

    def stage(self):
        shutil.rmtree(self.staged, ignore_errors=True)
        shutil.copytree(self.overlay, self.staged, symlinks=True)

    def env(self):
        return {
            **os.environ,
            "GITHUB_WORKSPACE": str(self.workspace),
            "RUNNER_TOOL_CACHE": str(self.cache),
            "HOME": str(self.workspace / "home"),
            "PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}",
            "ASK_KERNEL_TAG": "test",
            "FAKE_PACKAGE_ROOT": str(self.workspace / "fake-package"),
            "FAKE_BUILD_LOG": str(self.log),
            "FAKE_OUTPUT_DIR": self.output_dir,
            "FAKE_SKIP_PACKAGE": self.skip_package,
        }

    def run(self, *, check=True):
        result = subprocess.run(
            ["bash", str(SCRIPT)], cwd=self.workspace, env=self.env(),
            capture_output=True, text=True, check=False, timeout=30,
        )
        if not check:
            return result
        assert result.returncode == 0, result.stdout + result.stderr
        match = re.search(
            r"^### vyos-1x cache (HIT|MISS) \(key=([^),]+)",
            result.stdout, re.MULTILINE,
        )
        assert match, result.stdout + result.stderr
        return match.groups()

    def builds(self):
        return self.log.read_text().count("build\n") if self.log.exists() else 0

    def cached_debs(self, key):
        cache = self.cache / "vyos-1x-cache"
        entry = cache / key
        if entry.is_dir():
            return sorted(entry.glob("*.deb"))
        return sorted(cache.glob(f"{key}__*.deb"))

    def cached_alpha(self, key):
        debs = [deb for deb in self.cached_debs(key)
                if deb.name.split("__")[-1].startswith("vyos-1x_")]
        assert len(debs) == 1
        destination = self.workspace / "extracted"
        shutil.rmtree(destination, ignore_errors=True)
        subprocess.run(
            ["dpkg-deb", "-x", str(debs[0]), str(destination)],
            check=True, capture_output=True,
        )
        return (destination / "usr/share/overlay/src/alpha").read_bytes()


@pytest.fixture
def build(tmp_path):
    return PackageBuild(tmp_path / "workspace", tmp_path / "cache")


def test_unchanged_overlay_hits_cache(build):
    first = build.run()
    second = build.run()
    assert first[0] == "MISS"
    assert second == ("HIT", first[1])
    assert build.builds() == 1


@pytest.mark.parametrize("mutation", [
    "content", "rename", "add_empty", "remove_empty", "empty_directory",
    "mode", "symlink_target", "symlink_rename", "newline_rename", "split_content",
])
def test_overlay_mutations_change_cache_key(build, mutation):
    first = build.run()
    overlay = build.overlay / "src"
    if mutation == "content":
        (overlay / "alpha").write_bytes(b"changed")
    elif mutation == "rename":
        (overlay / "alpha").rename(overlay / "renamed")
    elif mutation == "add_empty":
        (overlay / "new-empty").touch()
    elif mutation == "remove_empty":
        (overlay / "empty").unlink()
    elif mutation == "empty_directory":
        (overlay / "new-directory").mkdir()
    elif mutation == "mode":
        (overlay / "alpha").chmod(0o755)
    elif mutation == "symlink_target":
        (overlay / "link").unlink()
        (overlay / "link").symlink_to("beta")
    elif mutation == "symlink_rename":
        (overlay / "link").rename(overlay / "renamed-link")
    elif mutation == "newline_rename":
        (overlay / "space and\nnewline").rename(overlay / "different\nname")
    elif mutation == "split_content":
        (overlay / "alpha").write_bytes(b"a")
        (overlay / "beta").write_bytes(b"bc")
    build.stage()
    second = build.run()
    assert first[0] == "MISS"
    assert second[0] == "MISS"
    assert second[1] != first[1]
    assert build.run() == ("HIT", second[1])
    assert build.builds() == 2


def test_source_change_after_staging_fails_closed(build):
    first = build.run()
    assert build.cached_alpha(first[1]) == b"ab"
    (build.overlay / "src/alpha").write_bytes(b"new content")
    result = build.run(check=False)
    assert result.returncode != 0
    assert "overlay changed after staging" in result.stderr
    assert build.builds() == 1
    build.stage()
    after_restage = build.run()
    assert after_restage[0] == "MISS"
    assert after_restage[1] != first[1]
    assert build.cached_alpha(after_restage[1]) == b"new content"
    assert build.run() == ("HIT", after_restage[1])


def test_staged_change_without_source_fails_closed(build):
    assert build.run()[0] == "MISS"
    (build.staged / "src/alpha").write_bytes(b"staged content")
    result = build.run(check=False)
    assert result.returncode != 0
    assert "overlay changed after staging" in result.stderr
    assert build.builds() == 1


def test_actual_setup_stages_overlay_for_cache(build):
    shutil.copyfile(SETUP, build.bin / "ci-setup-vyos1x.sh")
    (build.workspace / "data/reftree.cache").write_bytes(b"reference tree\n")
    env = {**build.env(), "MOK_KEY": "", "MINISIGN_PRIVATE_KEY": ""}

    def setup():
        return subprocess.run(
            ["bash", str(SETUP)], cwd=build.workspace, env=env,
            capture_output=True, text=True, check=True, timeout=30,
        )

    setup()
    first = build.run()
    assert first[0] == "MISS"
    assert build.run() == ("HIT", first[1])
    (build.overlay / "src/alpha").write_bytes(b"staged by setup")
    setup()
    second = build.run()
    assert second[0] == "MISS"
    assert second[1] != first[1]
    assert build.cached_alpha(second[1]) == b"staged by setup"


def test_cache_hit_removes_stale_local_packages(build):
    first = build.run()
    (build.overlay / "src/alpha").write_bytes(b"other content")
    build.stage()
    assert build.run()[0] == "MISS"
    (build.overlay / "src/alpha").write_bytes(b"ab")
    build.stage()
    assert build.run() == ("HIT", first[1])
    assert not (build.package / "vyos-1x_1_arm64.deb").exists()
    replay = build.package.parent / "vyos-1x_1_arm64.deb"
    extracted = build.workspace / "replayed"
    subprocess.run(["dpkg-deb", "-x", str(replay), str(extracted)], check=True)
    assert (extracted / "usr/share/overlay/src/alpha").read_bytes() == b"ab"


def test_cache_miss_removes_only_previous_vyos1x_packages(build):
    assert build.run()[0] == "MISS"
    names = (
        "vyos-1x", "vyos-1x-aws", "vyos-1x-smoketest", "vyos-1x-vmware",
        "vyos-user-utils", "libvyosconfig0",
    )
    for directory in (build.package, build.package.parent):
        for name in names:
            (directory / f"{name}_old_arm64.deb").write_bytes(b"stale")
        (directory / "other_1_arm64.deb").write_bytes(b"unrelated")
    (build.overlay / "src/alpha").write_bytes(b"new content")
    build.stage()
    status, key = build.run()
    assert status == "MISS"
    for directory in (build.package, build.package.parent):
        assert all(not (directory / f"{name}_old_arm64.deb").exists() for name in names)
        assert (directory / "other_1_arm64.deb").read_bytes() == b"unrelated"
    assert build.cached_alpha(key) == b"new content"


def test_complete_deb_bundle_is_cached_and_replayed(build):
    first = build.run()
    assert first[0] == "MISS"
    assert len(build.cached_debs(first[1])) == len(PACKAGES)
    assert build.run() == ("HIT", first[1])
    for name, arch in PACKAGES:
        deb = build.package.parent / f"{name}_1_{arch}.deb"
        result = subprocess.run(
            ["dpkg-deb", "-f", str(deb), "Package"], check=True,
            capture_output=True, text=True,
        )
        assert result.stdout.strip() == name
    assert build.builds() == 1


def test_interrupted_bundle_write_is_not_replayed(build):
    wrapper = build.bin / "cp"
    wrapper.write_text(
        """#!/usr/bin/env python3
import os
from pathlib import Path
import signal
import sys

if "/vyos-1x-cache/.staging." in sys.argv[-1]:
    count = Path(os.environ["GITHUB_WORKSPACE"]) / "copy-count"
    attempts = int(count.read_text()) if count.exists() else 0
    count.write_text(str(attempts + 1))
    if attempts == 1:
        os.kill(os.getppid(), signal.SIGKILL)
        sys.exit(1)
os.execv("/bin/cp", ["cp", *sys.argv[1:]])
"""
    )
    wrapper.chmod(0o755)
    interrupted = build.run(check=False)
    assert interrupted.returncode != 0
    assert (build.workspace / "copy-count").read_text() == "2"
    assert not list((build.cache / "vyos-1x-cache").glob("vyos-1x_v4_*"))
    wrapper.unlink()
    status, key = build.run()
    assert status == "MISS"
    assert len(build.cached_debs(key)) == len(PACKAGES)
    assert build.run() == ("HIT", key)
    assert build.builds() == 2


@pytest.mark.parametrize("when", ["before", "after"])
def test_interruption_at_publish_boundary(build, when):
    wrapper = build.bin / "mv"
    wrapper.write_text(
        """#!/usr/bin/env python3
import os
from pathlib import Path
import signal
import subprocess
import sys

source, destination = map(Path, sys.argv[-2:])
if source.name.startswith(".staging.") and destination.name.startswith("vyos-1x_v4_"):
    if @WHEN@ == "after":
        subprocess.run(["/bin/mv", *sys.argv[1:]], check=True)
    os.kill(os.getppid(), signal.SIGKILL)
    sys.exit(1)
os.execv("/bin/mv", ["mv", *sys.argv[1:]])
""".replace("@WHEN@", repr(when))
    )
    wrapper.chmod(0o755)
    interrupted = build.run(check=False)
    assert interrupted.returncode != 0
    wrapper.unlink()
    status, key = build.run()
    assert status == ("MISS" if when == "before" else "HIT")
    assert len(build.cached_debs(key)) == len(PACKAGES)
    assert build.run() == ("HIT", key)
    assert build.builds() == (2 if when == "before" else 1)


@pytest.mark.parametrize("missing", ["vyos-1x", "libvyosconfig0", "vyos-1x-smoketest"])
def test_missing_cached_package_forces_rebuild(build, missing):
    status, key = build.run()
    assert status == "MISS"
    candidates = [deb for deb in build.cached_debs(key)
                  if deb.name.split("__")[-1].startswith(f"{missing}_")]
    assert len(candidates) == 1
    candidates[0].unlink()
    assert build.run() == ("MISS", key)
    assert len(build.cached_debs(key)) == len(PACKAGES)
    assert build.run() == ("HIT", key)
    assert build.builds() == 2


def test_wrong_cached_architecture_forces_rebuild(build):
    status, key = build.run()
    assert status == "MISS"
    target = next(deb for deb in build.cached_debs(key)
                  if deb.name.startswith("libvyosconfig0_"))
    target.rename(target.with_name("libvyosconfig0_1_all.deb"))
    assert build.run() == ("MISS", key)
    assert build.run() == ("HIT", key)


def test_cached_payload_corruption_forces_rebuild(build):
    status, key = build.run()
    assert status == "MISS"
    target = next(deb for deb in build.cached_debs(key)
                  if deb.name.split("__")[-1].startswith("libvyosconfig0_"))
    with target.open("ab") as stream:
        stream.write(b"trailing corruption")
    assert subprocess.run(["dpkg-deb", "--info", str(target)],
                          capture_output=True, check=False).returncode == 0
    assert build.run() == ("MISS", key)
    assert build.run() == ("HIT", key)


@pytest.mark.parametrize("damage", ["missing", "truncated", "extra_deb"])
def test_invalid_manifest_or_extra_package_forces_rebuild(build, damage):
    status, key = build.run()
    assert status == "MISS"
    entry = build.cache / "vyos-1x-cache" / key
    manifest = entry / "SHA256SUMS"
    if damage == "missing":
        manifest.unlink()
    elif damage == "truncated":
        manifest.write_bytes(manifest.read_bytes().splitlines(keepends=True)[0])
    else:
        shutil.copyfile(build.cached_debs(key)[0], entry / "unexpected_1_all.deb")
    assert build.run() == ("MISS", key)
    assert len(build.cached_debs(key)) == len(PACKAGES)
    assert build.run() == ("HIT", key)
    assert build.builds() == 2


@pytest.mark.parametrize("missing", ["vyos-1x", "libvyosconfig0"])
def test_incomplete_build_is_not_published(build, missing):
    build.skip_package = missing
    result = build.run(check=False)
    assert result.returncode != 0
    expected = "produced no vyos-1x_*_arm64.deb" if missing == "vyos-1x" else "incomplete or invalid vyos-1x .deb bundle"
    assert expected in result.stderr
    cache = build.cache / "vyos-1x-cache"
    assert not list(cache.glob("vyos-1x_v4_*"))
    assert not list(cache.glob(".staging.*"))
    build.skip_package = ""
    status, key = build.run()
    assert status == "MISS"
    assert build.run() == ("HIT", key)


def test_concurrent_complete_publisher_is_not_overwritten(build):
    wrapper = build.bin / "mv"
    wrapper.write_text(
        """#!/usr/bin/env python3
import os
from pathlib import Path
import shutil
import sys

source, destination = map(Path, sys.argv[-2:])
if source.name.startswith(".staging.") and destination.name.startswith("vyos-1x_v4_"):
    shutil.copytree(source, destination)
os.execv("/bin/mv", ["mv", *sys.argv[1:]])
"""
    )
    wrapper.chmod(0o755)
    status, key = build.run()
    assert status == "MISS"
    assert len(build.cached_debs(key)) == len(PACKAGES)
    assert not list((build.cache / "vyos-1x-cache").glob(".staging.*"))
    wrapper.unlink()
    assert build.run() == ("HIT", key)
    assert build.builds() == 1


def test_parallel_publishers_share_a_complete_entry(tmp_path):
    cache = tmp_path / "shared-cache"
    builds = [PackageBuild(tmp_path / name, cache) for name in ("one", "two")]
    for build in builds:
        builder = build.package / "build.py"
        builder.write_text(builder.read_text() + """
import time
name = Path(os.environ["GITHUB_WORKSPACE"]).name
other = "two" if name == "one" else "one"
cache = Path(os.environ["RUNNER_TOOL_CACHE"])
(cache / f"{name}.ready").touch()
for _ in range(1000):
    if (cache / f"{other}.ready").exists():
        break
    time.sleep(0.01)
else:
    raise SystemExit("parallel builder timed out")
""")
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda item: item.run(), builds))
    assert [status for status, _ in results] == ["MISS", "MISS"]
    assert results[0][1] == results[1][1]
    key = results[0][1]
    assert len(builds[0].cached_debs(key)) == len(PACKAGES)
    assert not list((cache / "vyos-1x-cache").glob(".staging.*"))
    assert builds[0].run() == ("HIT", key)
    assert builds[0].builds() == builds[1].builds() == 1


def test_incomplete_concurrent_publisher_is_rejected(build):
    wrapper = build.bin / "mv"
    wrapper.write_text(
        """#!/usr/bin/env python3
import os
from pathlib import Path
import shutil
import sys

source, destination = map(Path, sys.argv[-2:])
if source.name.startswith(".staging.") and destination.name.startswith("vyos-1x_v4_"):
    destination.mkdir()
    first = next(source.glob("*.deb"))
    shutil.copyfile(first, destination / first.name)
os.execv("/bin/mv", ["mv", *sys.argv[1:]])
"""
    )
    wrapper.chmod(0o755)
    result = build.run(check=False)
    assert result.returncode != 0
    assert "could not publish complete vyos-1x cache entry" in result.stderr
    wrapper.unlink()
    status, key = build.run()
    assert status == "MISS"
    assert len(build.cached_debs(key)) == len(PACKAGES)
    assert build.run() == ("HIT", key)


def test_legacy_partial_v3_entry_is_not_replayed(build):
    status, key = build.run()
    assert status == "MISS"
    assert "vyos-1x_v4_" in key
    cache = build.cache / "vyos-1x-cache"
    legacy = key.replace("vyos-1x_v4_", "vyos-1x_v3_")
    main = next(deb for deb in build.cached_debs(key) if deb.name.startswith("vyos-1x_"))
    shutil.copyfile(main, cache / f"{legacy}__{main.name}")
    shutil.rmtree(cache / key)
    assert build.run() == ("MISS", key)
    assert len(build.cached_debs(key)) == len(PACKAGES)
    assert build.builds() == 2


def test_cache_gc_removes_only_old_entries_and_staging(build):
    cache = build.cache / "vyos-1x-cache"
    old_entry = cache / "vyos-1x_v4_old"
    old_stage = cache / ".staging.old"
    unrelated = cache / "unrelated"
    for path in (old_entry, old_stage, unrelated):
        path.mkdir(parents=True)
        (path / "data").write_text("old")
        timestamp = time.time() - 30 * 86400
        os.utime(path, (timestamp, timestamp))
    assert build.run()[0] == "MISS"
    assert not old_entry.exists()
    assert not old_stage.exists()
    assert unrelated.exists()


def test_cache_hit_refreshes_entry_age(build):
    status, key = build.run()
    assert status == "MISS"
    entry = build.cache / "vyos-1x-cache" / key
    aged = time.time() - 10 * 86400
    os.utime(entry, (aged, aged))
    assert build.run() == ("HIT", key)
    assert entry.stat().st_mtime > aged


def test_cache_directory_with_spaces(tmp_path):
    build = PackageBuild(tmp_path / "workspace", tmp_path / "cache with spaces")
    status, key = build.run()
    assert status == "MISS"
    assert build.run() == ("HIT", key)
    assert build.builds() == 1


@pytest.mark.parametrize("conflicting", [False, True])
def test_duplicate_build_artifacts_must_match(build, conflicting):
    builder = build.package / "build.py"
    builder.write_text(builder.read_text() + """
source = Path("vyos-1x_1_arm64.deb")
destination = Path("../vyos-1x_1_arm64.deb")
shutil.copyfile(source, destination)
if @CONFLICT@:
    with destination.open("ab") as stream:
        stream.write(b"different")
""".replace("@CONFLICT@", repr(conflicting)))
    result = build.run(check=False)
    if conflicting:
        assert result.returncode != 0
        assert "conflicting vyos-1x build artifacts" in result.stderr
        assert not list((build.cache / "vyos-1x-cache").glob("vyos-1x_v4_*"))
    else:
        assert result.returncode == 0, result.stdout + result.stderr
        entry = next((build.cache / "vyos-1x-cache").glob("vyos-1x_v4_*"))
        assert len(list(entry.glob("*.deb"))) == len(PACKAGES)
        assert build.run() == ("HIT", entry.name)


def test_cache_populates_when_builder_outputs_only_parent(build):
    build.output_dir = ".."
    first = build.run()
    assert first[0] == "MISS"
    assert build.cached_alpha(first[1]) == b"ab"
    assert build.run() == ("HIT", first[1])
    assert build.builds() == 1


def test_failed_rebuild_does_not_cache_old_debs(build):
    assert build.run()[0] == "MISS"
    assert build.run()[0] == "HIT"
    (build.overlay / "src/alpha").write_bytes(b"new content")
    build.stage()
    builder = build.package / "build.py"
    builder.write_text("#!/bin/sh\nexit 0\n")
    result = build.run(check=False)
    assert result.returncode != 0
    assert "produced no vyos-1x_*_arm64.deb" in result.stderr
    assert build.builds() == 1


def test_symlinked_overlay_root_hashes_target_content(build):
    source_target = build.workspace / "source-overlay-target"
    staged_target = build.workspace / "staged-overlay-target"
    build.overlay.rename(source_target)
    build.overlay.symlink_to(source_target, target_is_directory=True)
    build.staged.rename(staged_target)
    build.staged.symlink_to(staged_target, target_is_directory=True)
    first = build.run()
    (source_target / "src/alpha").write_bytes(b"new content")
    (staged_target / "src/alpha").write_bytes(b"new content")
    second = build.run()
    assert first[0] == second[0] == "MISS"
    assert first[1] != second[1]
    assert build.cached_alpha(second[1]) == b"new content"


def test_empty_overlay_hits_cache(build):
    shutil.rmtree(build.overlay / "src")
    build.stage()
    first = build.run()
    assert first[0] == "MISS"
    assert build.run() == ("HIT", first[1])
    assert build.builds() == 1


def test_cache_key_is_independent_of_workspace_order_and_mtime(tmp_path):
    cache = tmp_path / "shared-cache"
    first = PackageBuild(tmp_path / "workspace one", cache)
    status, key = first.run()
    assert status == "MISS"
    second = PackageBuild(tmp_path / "workspace two", cache, reverse=True)
    for path in [second.overlay, *second.overlay.rglob("*")]:
        os.utime(path, (946684800, 946684800), follow_symlinks=False)
    second.stage()
    assert second.run() == ("HIT", key)
    assert second.builds() == 0


def test_environment_tar_options_cannot_hide_overlay_files(build, monkeypatch):
    monkeypatch.delenv("TAR_OPTIONS", raising=False)
    first = build.run()
    monkeypatch.setenv("TAR_OPTIONS", "--exclude=./src/alpha")
    assert build.run() == ("HIT", first[1])
    (build.overlay / "src/alpha").write_bytes(b"modified")
    build.stage()
    second = build.run()
    assert second[0] == "MISS"
    assert second[1] != first[1]


@pytest.mark.parametrize("missing", ["source", "staged"])
def test_missing_overlay_fails_closed(build, missing):
    assert build.run()[0] == "MISS"
    shutil.rmtree(build.overlay if missing == "source" else build.staged)
    result = build.run(check=False)
    assert result.returncode != 0
    assert "vyos-1x-files" in result.stderr
    assert build.builds() == 1


@pytest.mark.parametrize("relative", [
    "data/vyos-1x-001-test.patch", "bin/ci-setup-vyos1x.sh",
])
def test_existing_cache_inputs_still_invalidate(build, relative):
    first = build.run()
    path = build.workspace / relative
    path.write_bytes(path.read_bytes() + b"change\n")
    second = build.run()
    assert first[0] == second[0] == "MISS"
    assert first[1] != second[1]
    assert build.builds() == 2


def test_old_cache_entries_are_not_replayed(build):
    subprocess.run(
        ["./build.py"], cwd=build.package, env=build.env(),
        capture_output=True, check=True,
    )
    build.log.unlink()
    old_hash = hashlib.sha256(
        (build.workspace / "data/vyos-1x-001-test.patch").read_bytes()
        + (build.bin / "ci-setup-vyos1x.sh").read_bytes()
    ).hexdigest()[:16]
    old_key = f"vyos-1x_v2_{COMMIT[:12]}_{old_hash}"
    cache = build.cache / "vyos-1x-cache"
    cache.mkdir(parents=True)
    built = build.package / "vyos-1x_1_arm64.deb"
    shutil.copyfile(built, cache / f"{old_key}__{built.name}")
    built.unlink()
    status, key = build.run()
    assert status == "MISS"
    assert key != old_key
    assert build.builds() == 1
