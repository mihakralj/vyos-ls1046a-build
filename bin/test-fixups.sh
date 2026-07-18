#!/usr/bin/env python3
"""
Pre-push validation for ci-setup-kernel.sh fixups.
Run before every CI dispatch: python3 bin/test-fixups.sh

Checks:
1. REPLACEMENT bash is valid after Python escape processing
2. All bin/kernel-fixups/*.py are valid Python (py_compile)
3. All fixup files referenced in ci-setup-kernel.sh exist on disk
"""
import re, codecs, subprocess, sys, pathlib

ok = True

# ── 1. REPLACEMENT bash syntax ─────────────────────────────────────────────
with open('bin/ci-setup-kernel.sh') as f:
    content = f.read()

m = re.search(r'REPLACEMENT = SENTINEL \+ """(.*?)^"""', content, re.DOTALL|re.MULTILINE)
if not m:
    print("ERROR: REPLACEMENT block not found"); sys.exit(1)

raw = m.group(1)
try:
    processed = codecs.decode(raw.encode(), 'unicode_escape').encode('latin-1').decode('utf-8')
except Exception as e:
    print(f"ERROR: Python escape processing failed: {e}"); sys.exit(1)

with open('_fixup_bash_check.sh', 'w') as f:
    f.write('#!/bin/bash\n' + processed)
r = subprocess.run(['bash', '-n', '/tmp/_fixup_bash_check.sh' if __import__('os').access('/tmp', __import__('os').W_OK) else '_fixup_bash_check.sh'], capture_output=True, text=True)
if r.returncode != 0:
    print(f"FAIL [1]: REPLACEMENT bash syntax:\n{r.stderr}")
    ok = False
else:
    print(f"OK [1]: REPLACEMENT bash valid ({len(raw.splitlines())} lines)")

# ── 2. py_compile all fixup files ──────────────────────────────────────────
fixup_dir = pathlib.Path('bin/kernel-fixups')
fixup_files = sorted(fixup_dir.glob('*.py'))
errors = []
for fpath in fixup_files:
    r2 = subprocess.run(['python3', '-m', 'py_compile', str(fpath)], capture_output=True, text=True)
    if r2.returncode != 0:
        errors.append(f"  {fpath.name}: {r2.stderr.strip()}")
if errors:
    print(f"FAIL [2]: {len(errors)} fixup files have syntax errors:")
    for e in errors: print(e)
    ok = False
else:
    print(f"OK [2]: {len(fixup_files)} fixup files pass py_compile")

# ── 3. Referenced files exist ──────────────────────────────────────────────
refs = re.findall(r'bin/kernel-fixups/(\S+\.py)', content)
missing = []
for ref in set(refs):
    if not (pathlib.Path('bin/kernel-fixups') / ref).exists():
        missing.append(ref)
if missing:
    print(f"FAIL [3]: {len(missing)} referenced fixup files missing: {missing}")
    ok = False
else:
    print(f"OK [3]: all {len(set(refs))} referenced fixup files exist on disk")
# ── 4. No-zombie gate: all fixup files match manifest ──────────────────────
import json
with open('bin/kernel-fixups/manifest.json') as f:
    manifest = json.load(f)
on_disk = set(f.name for f in pathlib.Path('bin/kernel-fixups').glob('*.py'))
manifested = set(manifest['active'].keys())
referenced = set(m.group(1) for m in re.finditer(
    r'bin/kernel-fixups/(\S+\.py)', content))

zombie_disk = on_disk - manifested
zombie_manifest = manifested - on_disk
zombie_refs = referenced - manifested

all_ok = True
if zombie_disk:
    print(f"FAIL [4]: {len(zombie_disk)} unmanifested fixup files on disk: {zombie_disk}")
    all_ok = False
elif zombie_manifest:
    print(f"FAIL [4]: {len(zombie_manifest)} manifest entries missing from disk: {zombie_manifest}")
    all_ok = False
elif zombie_refs:
    print(f"FAIL [4]: {len(zombie_refs)} fixups referenced but not manifest: {zombie_refs}")
    all_ok = False
else:
    print(f"OK [4]: all {len(manifested)} fixup files match manifest ({len(referenced)} referenced)")
ok = ok and all_ok

sys.exit(0 if ok else 1)

