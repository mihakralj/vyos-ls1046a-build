#!/usr/bin/env python3
"""
Test that ci-setup-kernel.sh REPLACEMENT block produces valid bash
after Python escape-sequence processing.
Run before pushing any new fixup.
"""
import re, codecs, subprocess, sys

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

with open('/tmp/_fixup_bash_check.sh', 'w') as f:
    f.write('#!/bin/bash\n' + processed)

r = subprocess.run(['bash', '-n', '/tmp/_fixup_bash_check.sh'], capture_output=True, text=True)
if r.returncode != 0:
    print(f"FAIL: {r.stderr}")
    sys.exit(1)

# Also validate all base64 blobs are syntactically valid Python
import base64
errors = []
for i, m2 in enumerate(re.finditer(r"echo '([A-Za-z0-9+/=\n]+)' \| base64 -d \| python3", content)):
    b64 = m2.group(1).replace('\n','')
    try:
        code = base64.b64decode(b64).decode()
        compile(code, f'<block{i}>', 'exec')
    except SyntaxError as e:
        errors.append(f"Block {i}: {e}")
if errors:
    print("FAIL: Base64 Python syntax errors:")
    for e in errors: print(f"  {e}")
    sys.exit(1)

print(f"OK: REPLACEMENT bash valid, {i+1} base64 Python blocks valid")
