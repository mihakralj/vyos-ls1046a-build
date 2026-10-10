"""The running service keeps its control state outside unprivileged reach."""

from _flowtable_connections import (peer)
from _flowtable_rig import (console_python)
from _flowtable_selective_neighbour import (hardware, warm)
from _flowtable_service import (FLOWS)


async def test_protection(service):
    r = service
    flows = [{**f, "lan": r.lan_ip} for f in FLOWS[:2]]
    async with peer(r, flows) as p:
        await warm(r, p, [0, 1], "runtime-before", flows)
        await console_python(r.service_console, '''
import os, subprocess
from pathlib import Path
root = Path('/run/ask-flowtable')
assert root.stat().st_uid == 0 and root.stat().st_mode & 0o777 == 0o700
assert (root / 'service.sock').is_socket()
assert (root / 'worker.pid').is_file()
assert (root / 'supervisor.pid').is_file()
pid = os.fork()
if pid == 0:
    os.setgroups([])
    os.setgid(65534)
    os.setuid(65534)
    for name in ('policy.lock', 'paused', 'daemon.lock', 'control.lock',
                 'service.lock', 'worker.pid', 'supervisor.pid'):
        try:
            os.open(root / name, os.O_CREAT | os.O_RDWR, 0o600)
        except PermissionError:
            continue
        os._exit(1)
    os._exit(0)
assert os.waitpid(pid, 0)[1] == 0, 'unprivileged runtime write succeeded'

# Existing files must also be checked, even if a privileged installer left
# an unsafe object behind. Restore each one before the service continues.
for name, verbs in [('policy.lock', ('status',)),
                    ('paused', ('status', 'stop', 'resume'))]:
    path = root / name
    existed = path.exists()
    if not existed:
        path.touch(mode=0o600)
    os.chown(path, 65534, 65534)
    try:
        for verb in verbs:
            result = subprocess.run(['/usr/sbin/ask-flowtable', verb],
                                    capture_output=True, text=True, timeout=3)
            assert result.returncode != 0, (name, verb, result)
            assert 'Permission denied' in result.stderr or 'Operation not permitted' in result.stderr, result
    finally:
        os.chown(path, 0, 0)
        if not existed:
            path.unlink()
print('private runtime directory and file-owner checks passed')
''')
        await hardware(r, p, "runtime-after", flows)
