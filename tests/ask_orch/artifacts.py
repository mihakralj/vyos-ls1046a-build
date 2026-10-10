"""Artifact paths shared by fixtures, transports, and pytest reporting."""

import hashlib
import json
import os
import re
import shutil
import time
from pathlib import Path

RUN_NAME = re.compile(r"^\d{8}-\d{6}-")


def prune_runs(root, keep_days, keep_runs, min_free):
    """Delete earlier run directories under `root`, oldest first.

    A run goes once it is older than `keep_days`, or while the filesystem has
    less than `min_free` bytes free; the newest `keep_runs` always stay. Only
    directories named as a run is (see pytest_configure) are touched, so
    anything else kept beside them survives. The default root is a tmpfs on
    the bench: a run that fills it fails every later write, the harness's
    own included, so the space has to be found before the run starts."""
    root = Path(root)
    runs = sorted((p for p in root.iterdir() if p.is_dir() and RUN_NAME.match(p.name)),
                  key=lambda p: p.name)
    cutoff = time.time() - keep_days * 86400
    for run in runs[:max(len(runs) - keep_runs, 0)]:
        if run.stat().st_mtime < cutoff or shutil.disk_usage(root).free < min_free:
            shutil.rmtree(run, ignore_errors=True)


def artifact_dir(nodeid=None):
    root = Path(os.environ.get("ASK_TEST_RUN_DIR", "/tmp/ask-tests/unmanaged"))
    if nodeid is None:
        current = os.environ.get("PYTEST_CURRENT_TEST", "session")
        nodeid = current.rsplit(" (", 1)[0]
    name = re.sub(r"[^a-zA-Z0-9_.-]+", "_", nodeid)[:140]
    digest = hashlib.sha256(nodeid.encode()).hexdigest()[:12]
    path = root / f"{name}-{digest}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def record(name, data, *, nodeid=None):
    path = artifact_dir(nodeid) / f"{name}.json"
    path.write_text(json.dumps(data, indent=2, default=str) + "\n")
    return path
