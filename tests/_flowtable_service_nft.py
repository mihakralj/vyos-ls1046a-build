#!/usr/bin/python3
"""DUT-only nft fault wrapper, staged with executable/root placeholders replaced."""
import json
import os
from pathlib import Path
import signal
import sys
import time

root = Path(__FAULT_ROOT__)
real = __REAL_NFT__
args = sys.argv[1:]


def run_real():
    child = os.fork()
    if child == 0:
        os.execv(real, [real, *args])
    _, status = os.waitpid(child, 0)
    assert status == 0, status
    return child


def crash(point):
    armed = root / "crash"
    if not armed.exists() or armed.read_text().strip() != point:
        return
    armed.rename(root / "crash-consumed")
    child = run_real() if point in ("drain", "commit") else 0
    controller = int(Path("/run/ask-flowtable/worker.pid").read_text())
    guardian = os.getppid()
    # Prove the chosen fault targets this wrapper's actual controller.
    parent = int(Path(f"/proc/{guardian}/stat").read_text().rsplit(") ", 1)[1].split()[1])
    assert controller == parent, (controller, parent)
    hit = dict(point=point, controller=controller, guardian=guardian,
               wrapper=os.getpid(), real_nft=child, time=time.monotonic(),
               backend=Path("/proc/cdx_flowtable").read_text().split("\nflow ", 1)[0])
    (root / "crash-hit.tmp").write_text(json.dumps(hit))
    (root / "crash-hit.tmp").replace(root / "crash-hit")
    os.kill(controller, signal.SIGKILL)
    time.sleep(30)
    # An orphan left alive would commit/replay the obsolete transaction.
    os.execv(real, [real, *args])


if args == ["delete", "table", "inet", "ask_flowtable"]:
    crash("drain")
if args == ["-f", "-"]:
    with (root / "attempts").open("a") as log:
        print(time.monotonic(), file=log)
    crash("install")
    crash("commit")
    try:
        (root / "armed").rename(root / "consumed")
    except FileNotFoundError:
        pass
    else:
        fault = (root / "consumed").read_text().strip()
        if fault == "once":
            sys.exit("injected one-shot nft transaction failure")
        child = os.fork()
        if child == 0:
            if fault == "hung-apply":
                os.setsid()
                signal.signal(signal.SIGTERM, signal.SIG_IGN)
                time.sleep(30)
            os.execv(real, [real, *args])
        (root / "pids.tmp").write_text(json.dumps(dict(worker=os.getpid(), guardian=os.getppid(), child=child)))
        (root / "pids.tmp").replace(root / "pids")
        if fault == "lost-reply":
            _, status = os.waitpid(child, 0)
            assert status == 0, status
            (root / "committed").touch()
        time.sleep(60)
        sys.exit("test timeout did not terminate nft")
os.execv(real, [real, *args])
