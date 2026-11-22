"""Host tests for the kernel patch round-trip verification gate."""

import os
from pathlib import Path
import shutil
import subprocess

import pytest

pytestmark = pytest.mark.host
ROOT = Path(__file__).resolve().parents[1]


def patch(name, replacement="after"):
    return (
        f"Patch-Name: {name}\n\n"
        "diff --git a/file b/file\n"
        "--- a/file\n"
        "+++ b/file\n"
        "@@ -1 +1 @@\n"
        "-before\n"
        f"+{replacement}\n"
    )


@pytest.fixture
def roundtrip(tmp_path):
    repo = tmp_path / "repo"
    bindir = repo / "bin"
    patches = repo / "kernel/common/patches/board"
    exports = tmp_path / "exports"
    fakebin = tmp_path / "fakebin"
    source = tmp_path / "kernel"
    for path in (bindir, patches, exports, fakebin, source):
        path.mkdir(parents=True)
    shutil.copy(ROOT / "bin/kernel-roundtrip.sh", bindir)
    (patches / "series").write_text("a.patch\nb.patch\n")
    (patches / "a.patch").write_text(patch("a.patch"))
    (patches / "b.patch").write_text(patch("b.patch"))
    git = fakebin / "git"
    git.write_text("""#!/bin/bash
set -eu
printf '%s\\n' "$*" >> "$CALLS"
case "$1" in
  checkout) exit 0 ;;
  rev-parse)
    [ "${MISSING_REF:-0}" = 0 ] || exit 1
    case "$*" in *refs/tags/*) [ "${MISSING_TAG:-0}" = 0 ] || exit 1 ;; esac
    echo reference ;;
  merge-base) [ "${NOT_ANCESTOR:-0}" = 0 ] ;;
  log) echo base ;;
  format-patch)
    while [ "$#" -gt 0 ]; do
      if [ "$1" = --output-directory ]; then out=$2; shift 2; else shift; fi
    done
    for file in "$EXPORTS"/*.patch; do
      [ -f "$file" ] || continue
      cp "$file" "$out/$(basename "$file")"
    done ;;
  *) echo "unexpected git command: $*" >&2; exit 1 ;;
esac
""")
    git.chmod(0o755)
    calls = tmp_path / "git-calls"
    env = os.environ | {
        "PATH": f"{fakebin}:{os.environ['PATH']}",
        "KERNEL_VERSION": "6.18.64",
        "EXPORTS": str(exports),
        "CALLS": str(calls),
        "TMPDIR": str(tmp_path),
    }

    def run(**extra):
        return subprocess.run(
            ["bash", str(bindir / "kernel-roundtrip.sh"), "verify", str(source)],
            capture_output=True, text=True, env=env | extra, timeout=10,
        )

    return patches, exports, calls, run


def export(exports, name, replacement="after"):
    (exports / f"0001-{name}").write_text(patch(name, replacement))


def test_missing_export_fails_verification(roundtrip):
    _, exports, _, run = roundtrip
    export(exports, "a.patch")
    result = run()
    assert result.returncode != 0
    assert "MISSING-FROM-EXPORT: b.patch" in result.stdout
    assert "VERIFIED" not in result.stdout


def test_empty_export_fails_without_a_phantom_new_patch(roundtrip):
    _, _, calls, run = roundtrip
    result = run()
    assert result.returncode != 0
    assert "MISSING-FROM-EXPORT: a.patch" in result.stdout
    assert "NEW-IN-EXPORT: *.patch" not in result.stdout
    assert "VERIFIED" not in result.stdout
    assert not list(calls.parent.glob("tmp.*"))


def test_unexpected_export_fails_verification(roundtrip):
    _, exports, _, run = roundtrip
    for name in ("a.patch", "b.patch", "extra.patch"):
        export(exports, name)
    result = run()
    assert result.returncode != 0
    assert "NEW-IN-EXPORT: extra.patch" in result.stdout
    assert "VERIFIED" not in result.stdout


def test_modified_export_fails_verification(roundtrip):
    _, exports, _, run = roundtrip
    export(exports, "a.patch", "different")
    export(exports, "b.patch")
    result = run()
    assert result.returncode != 0
    assert "MISMATCH: a.patch" in result.stdout


def test_matching_export_does_not_checkout_kernel_tree(roundtrip):
    _, exports, calls, run = roundtrip
    export(exports, "a.patch")
    export(exports, "b.patch")
    result = run()
    assert result.returncode == 0, result.stderr
    assert "VERIFIED" in result.stdout
    commands = calls.read_text().splitlines()
    assert not any(line.startswith("checkout ") for line in commands)
    assert any("base..refs/heads/vyos-6.18.64-dpaa1" in line for line in commands)
    assert not list(calls.parent.glob("tmp.*"))


def test_missing_canonical_ref_fails_before_export(roundtrip):
    _, exports, _, run = roundtrip
    export(exports, "a.patch")
    export(exports, "b.patch")
    result = run(MISSING_REF="1")
    assert result.returncode != 0
    assert "VERIFIED" not in result.stdout


@pytest.mark.parametrize(("environment", "message"), [
    ({"MISSING_TAG": "1"}, "Kernel tag"),
    ({"NOT_ANCESTOR": "1"}, "not an ancestor"),
])
def test_invalid_base_fails_before_export(roundtrip, environment, message):
    _, exports, calls, run = roundtrip
    export(exports, "a.patch")
    export(exports, "b.patch")
    result = run(**environment)
    assert result.returncode != 0
    assert message in result.stderr
    assert not any(line.startswith("format-patch ") for line in calls.read_text().splitlines())


def test_empty_source_patch_directory_fails(roundtrip):
    patches, _, _, run = roundtrip
    (patches / "a.patch").unlink()
    (patches / "b.patch").unlink()
    result = run()
    assert result.returncode != 0
    assert "No board patches" in result.stderr
