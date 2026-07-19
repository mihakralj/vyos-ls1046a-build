#!/usr/bin/env python3
"""mutate.py — count-gated text replacement for kernel post-patch fixups.

Usage:
  python3 mutate.py [--check] <file> <old> <new> <count> <label> [once]

  --check    dry-run: validate only, report what would be done, exit 0
  <file>     source file to modify
  <old>      literal string to search for (not regex)
  <new>      replacement string
  <count>    expected occurrence count (usually 1)
  <label>    human-readable label for log output
  [once]     replace only the first match (default: replace all)

Exit codes: 0=ok, 1=count mismatch, 2=file not found
"""
import sys

def main():
    args = list(sys.argv[1:])
    dry_run = False
    if '--check' in args:
        dry_run = True
        args.remove('--check')

    if len(args) < 5:
        print("Usage: mutate.py [--check] <file> <old> <new> <count> <label> [once]", file=sys.stderr)
        sys.exit(3)

    file_path = args[0]
    old_text  = args[1]
    new_text  = args[2]
    expected  = int(args[3])
    label     = args[4]
    once      = len(args) > 5 and args[5] == "once"

    from pathlib import Path
    p = Path(file_path)
    if not p.exists():
        print(f"FATAL: {label}: file not found: {file_path}", file=sys.stderr)
        sys.exit(2)

    src = p.read_text()
    n   = src.count(old_text)

    if expected < 0:
        # expected=-1 means optional: apply if found, skip if not
        if n == 0:
            print(f"### {label}: optional — anchor not present, skipping")
            return
    elif n != expected:
        print(
            f"FATAL: {label}: expected {expected} match(es), found {n}. "
            f"Fixup anchor drifted — aborting build.",
            file=sys.stderr,
        )
        sys.exit(1)

    if dry_run:
        print(f"### {label}: DRY-RUN — would replace {n} occurrence(s)")
        return

    if once and n > 1:
        idx = src.find(old_text)
        src2 = src[:idx] + new_text + src[idx + len(old_text):]
    else:
        src2 = src.replace(old_text, new_text)

    p.write_text(src2)
    print(f"### {label}: replaced {n} occurrence(s)")

if __name__ == "__main__":
    main()
