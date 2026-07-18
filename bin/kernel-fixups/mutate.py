#!/usr/bin/env python3
"""
mutate.py — count-gated text replacement for kernel post-patch fixups.

Replaces text in a file and hard-fails (exit 1) if the expected
match count does not match. Converts the silent-no-op `sed -i`
pattern into a loud failure.

Usage:
  python3 mutate.py <file> <old_text> <new_text> <expected_count> <label> [<once>]

  <file>             path to the source file to modify
  <old_text>         STRING to search for (literal, not regex)
  <new_text>         replacement STRING
  <expected_count>   expected occurrence count (usually 1)
  <label>            human-readable fixup label for log output
  <once>             optional: "once" = replace only first match (default: replace all)

Exit codes:
  0  — successful mutation
  1  — count mismatch (build should abort)
  2  — file not found
  3  — usage error

Example:
  python3 "${GITHUB_WORKSPACE}/bin/kernel-fixups/mutate.py" \
    drivers/net/ethernet/freescale/fman/fman_pcd.c \
    "err = fman_pcd_fe_enter_build(pcd, e->muram_off);" \
    "err = fman_pcd_fe_enter_build(pcd, pcd->fe_hash_off);" \
    1 "F-084: compose target EXT_HASH not ENQ"
"""
import pathlib, sys

def mutate(file_path, old_text, new_text, expected_count, label):
    p = pathlib.Path(file_path)
    if not p.exists():
        print(f"FATAL: {label}: file not found: {file_path}", file=sys.stderr)
        sys.exit(2)

    s = p.read_text()
    n = s.count(old_text)

    if n != expected_count:
        print(
            f"FATAL: {label}: expected {expected_count} match(es), found {n}. "
            f"Fixup anchor drifted — aborting build.",
            file=sys.stderr,
        )
        sys.exit(1)

    once = len(sys.argv) > 6 and sys.argv[6] == "once"
    if once and n > 1:
        # Replace only first occurrence
        idx = s.find(old_text)
        s2 = s[:idx] + new_text + s[idx + len(old_text):]
    else:
        s2 = s.replace(old_text, new_text)

    p.write_text(s2)
    print(f"### {label}: replaced {n} occurrence(s)")

if __name__ == "__main__":
    if len(sys.argv) < 6:
        print(f"Usage: mutate.py <file> <old> <new> <count> <label> [once]", file=sys.stderr)
        sys.exit(3)
    mutate(sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4]), sys.argv[5])
