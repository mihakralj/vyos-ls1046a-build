"""A bounded DUT process whose cleanup survives a lost launch response."""

from ask_orch.commands import console_python


async def dut_process(stack, console, argv, *, base, env=None, lifetime=300):
    async def stop():
        await console_python(console, f'''
import json,os,pathlib,signal,time
path = pathlib.Path({base + '.owner'!r})
if path.exists():
    owner = json.loads(path.read_text())
    proc = pathlib.Path('/proc/%d/stat' % owner['pid'])
    for index in range(60):
        try:
            stat = proc.read_text().split()
        except FileNotFoundError:
            break
        if stat[21] != owner['start'] or stat[2] == 'Z':
            break
        if index in (0, 50):
            try:
                os.killpg(owner['pid'], signal.SIGTERM if index == 0 else signal.SIGKILL)
            except ProcessLookupError:
                break
        time.sleep(0.1)
    else:
        raise TimeoutError('owned DUT process did not stop')
print('stopped')
''', timeout=10)

    stack.push(stop)
    await console_python(console, f'''
import json,os,pathlib,subprocess
with open({base + '.log'!r}, 'w') as log:
    process = subprocess.Popen({['timeout', '-k', '2', str(lifetime), *argv]!r},
        stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True,
        env={{**os.environ, **{env or {}!r}}})
pathlib.Path({base + '.owner'!r}).write_text(json.dumps({{'pid':process.pid,
    'start':pathlib.Path('/proc/%d/stat' % process.pid).read_text().split()[21]}}))
print('launched')
''')
