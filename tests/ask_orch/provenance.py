"""Identify the checkout and installed components behind a test report."""

import hashlib
import os
from pathlib import Path
import subprocess


def checkout(root):
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=root, timeout=10)

    names = sorted(set(git("ls-files", "-z", "--cached", "--others", "--exclude-standard").split(b"\0")) - {b""})
    digest = hashlib.sha256()
    for name in names:
        path = Path(root) / os.fsdecode(name)
        if path.is_symlink():
            kind, content = b"link", hashlib.sha256(os.fsencode(os.readlink(path))).digest()
        elif path.is_file():
            kind = b"executable" if path.stat().st_mode & 0o111 else b"file"
            with path.open("rb") as stream:
                content = hashlib.file_digest(stream, "sha256").digest()
        else:
            kind, content = b"missing", b""
        digest.update(name + b"\0" + kind + b"\0" + content)
    return {"revision": git("rev-parse", "HEAD").decode().strip(),
            "sha256": digest.hexdigest(), "files": len(names)}


def agent_sources(root):
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(Path(root).rglob("*.py"))}


def firmware_script(required):
    # Run once on the DUT; only hashes and component identities cross UART.
    return f'''
import hashlib, json, pathlib, platform, shutil
import askd_agent
def digest(path):
    path = pathlib.Path(path)
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
root = pathlib.Path(askd_agent.__file__).parent
modules = {{}}
for path in sorted(pathlib.Path('/sys/module').iterdir()):
    note = path / 'notes/.note.gnu.build-id'
    version = path / 'srcversion'
    if note.exists() or version.exists():
        modules[path.name] = {{'build_id_note_sha256': digest(note),
                              'srcversion': version.read_text().strip() if version.exists() else None}}
tools = {{name: shutil.which(name) for name in {sorted(required)!r}}}
print(json.dumps({{'kernel': platform.release(), 'kernel_notes_sha256': digest('/sys/kernel/notes'),
                  'agent_sources': {{str(path.relative_to(root)): digest(path) for path in sorted(root.rglob('*.py'))}},
                  'modules': modules, 'binaries': {{name: {{'path': path, 'sha256': digest(path)}}
                                                for name, path in tools.items() if path}},
                  'missing_binaries': [name for name, path in tools.items() if not path]}}))
'''
