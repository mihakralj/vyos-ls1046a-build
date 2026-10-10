"""Local rig runner; pytest owns selection, reporting and exit status."""

import argparse
import hashlib
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

from ask_orch.lab import Bench, SUITE_ROOT, source_archive


DEFAULT_RUNNER_ROOT = Path("/tmp/ask-tests-runner")


def trusted_host_keys(bench):
    output = []
    for role in ("dut", "lan", "wan"):
        host = bench.endpoint(role).host
        result = subprocess.run(["ssh-keygen", "-F", host], capture_output=True, text=True, timeout=10)
        if result.returncode:
            raise RuntimeError(f"no trusted SSH host key for {host}; verify it on the launcher first")
        output.append(result.stdout)
    return "\n".join(output)


def stage_runner(bench, root):
    if not root.is_absolute() or root == Path("/"):
        raise ValueError("runner root must be an absolute, dedicated directory")
    payload = source_archive(SUITE_ROOT)
    profile = {"duts": {bench.target: bench.dut}, "lan": bench.lan, "wan": bench.wan}
    script = '''
import io, json, pathlib, sys, tempfile, zipfile
root = pathlib.Path(sys.argv[1])
if root.is_symlink():
    raise ValueError("runner root must not be a symlink")
root.mkdir(parents=True, exist_ok=True)
suite = pathlib.Path(tempfile.mkdtemp(prefix="suite-", dir=root))
with zipfile.ZipFile(io.BytesIO(sys.stdin.buffer.read())) as archive:
    archive.extractall(suite)
(suite / "bench.json").write_text(sys.argv[2])
(suite / "known_hosts").write_text(sys.argv[3])
print(json.dumps({"suite": str(suite)}))
'''
    result = bench.endpoint("wan").execute(
        ["python3", "-c", script, str(root), json.dumps(profile), trusted_host_keys(bench)], data=payload,
        management=True, privileged=False, timeout=30)
    if result.rc:
        raise RuntimeError("dell2 staging failed: " + result.stdout)
    return Path(json.loads(result.stdout)["suite"]), hashlib.sha256(payload).hexdigest()


def prepare_runner(bench, root, suite):
    script = '''
import importlib.util, pathlib, subprocess, sys
if importlib.util.find_spec("ensurepip") is None:
    package = "python%d.%d-venv" % sys.version_info[:2]
    print("dell2 needs %s; the operator must install it with: sudo apt-get install %s" %
          (package, package), file=sys.stderr)
    sys.exit(2)
python = pathlib.Path(sys.argv[1]) / ".venv/bin/python"
subprocess.run(["python3", "-m", "venv", str(python.parent.parent)], check=True)
subprocess.run([str(python), "-m", "pip", "install", "-r", sys.argv[2]], check=True)
subprocess.run([str(python), "-m", "pip", "check"], check=True)
'''
    result = bench.endpoint("wan").execute(
        ["python3", "-c", script, str(root), str(suite / "requirements.txt")],
        management=True, privileged=False, timeout=600)
    print(result.stdout, end="")
    if result.rc:
        raise RuntimeError("dell2 runner preparation failed")


def remote_command(bench, root, suite, extra, digest=""):
    protected = {"ASK_TRANSPORT", "ASK_LAB_DUT", "ASK_DUT_KIND", "ASK_RUNNER_ROLE",
                 "ASK_RUNNER_NETNS", "ASK_SUITE_SHA256", "ASK_SSH_KNOWN_HOSTS"}
    settings = {name: value for name, value in os.environ.items()
                if name in {"K", "ARGS"} or (name.startswith("ASK_") and name not in protected
                    and "_SSH_" not in name and not name.endswith(("PASSWORD", "TOKEN", "SECRET", "PRIVATE_KEY")))}
    settings.update({"ASK_RUNNER_ROLE": "wan", "ASK_RUNNER_NETNS": bench.dut["namespace"],
                     "ASK_SUITE_SHA256": digest, "ASK_SSH_KNOWN_HOSTS": str(suite / "known_hosts")})
    argv = ["env", *(f"{key}={value}" for key, value in settings.items()),
            str(root / ".venv/bin/python"), str(suite / "run_test.py"),
            "--dut", bench.target, "--run", "--on-runner", *extra]
    return bench.endpoint("wan").command(argv, forward_agent=True)


def remove_stage(bench, suite):
    result = bench.endpoint("wan").execute(
        ["python3", "-c", "import shutil,sys; shutil.rmtree(sys.argv[1])", str(suite)],
        management=True, privileged=False)
    if result.rc:
        raise RuntimeError("dell2 staged-suite cleanup failed: " + result.stdout)


def command(suite, environ, executable=sys.executable):
    root = Path(__file__).resolve().parent
    env = dict(environ)
    for short, names in {
        "DUT_IP": ("ASK_TARGET_IP",),
        "WAN_IP": ("ASK_WAN_IP", "ASK_WAN_IPERF_IP"),
        "WAN_AGENT_IP": ("ASK_WAN_IP",),
    }.items():
        if env.get(short):
            env.update((name, env[short]) for name in names)
    if suite not in {"dut", "all"}:
        raise ValueError("this import contains the DUT suite, not upstream host/startup suites")
    env["PYTHONPATH"] = str(root)
    argv = [executable, "-m", "pytest", "-c", str(root / "pyproject.toml"), str(root)]
    if env.get("ASK_LAB_EXECUTE") != "1":
        argv.append("--collect-only")
    else:
        argv.append("--lab-run")
    if env.get("K"):
        argv.extend(["-k", env["K"]])
    argv += shlex.split(env.get("ASK_TEST_ARGS", "")) + shlex.split(env.get("ARGS", ""))
    return argv, env


def parse_arguments(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    suite = argv.pop(0) if argv[:1] and argv[0] in {"all", "dut"} else "dut"
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.set_defaults(suite=suite)
    parser.add_argument("--dut", choices=("dut1", "dut2", "185", "106", "both"), default="dut1")
    parser.add_argument("--bench", type=Path)
    parser.add_argument("--runner-root", type=Path, default=DEFAULT_RUNNER_ROOT)
    parser.add_argument("--on-runner", action="store_true", help=argparse.SUPPRESS)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--preflight", action="store_true")
    action.add_argument("--prepare-runner", action="store_true")
    action.add_argument("--collect-only", action="store_true")
    action.add_argument("--run", action="store_true")
    args, extra = parser.parse_known_args(argv)
    if extra[:1] == ["--"]:
        extra = extra[1:]
    if args.on_runner and os.environ.get("ASK_RUNNER_ROLE") != "wan":
        parser.error("--on-runner is reserved for the dell2 worker")
    return args, extra


def main():
    args, extra = parse_arguments()
    status = 0
    for target in (("dut1", "dut2") if args.dut == "both" else (args.dut,)):
        bench = Bench.load(target, args.bench)
        if args.preflight:
            report = bench.preflight()
            print(json.dumps(report, indent=2))
            status = max(status, int(not report["ok"]))
            continue
        if args.prepare_runner or (args.run and not args.on_runner):
            suite, digest = stage_runner(bench, args.runner_root)
            try:
                if args.prepare_runner:
                    prepare_runner(bench, args.runner_root, suite)
                    break
                print(f"Running {bench.target} on dell2 ({bench.dut['namespace'] or 'root namespace'})",
                      flush=True)
                result = subprocess.run(remote_command(bench, args.runner_root, suite, extra, digest))
                status = max(status, result.returncode)
            finally:
                remove_stage(bench, suite)
            continue
        env = bench.environment(os.environ)
        env["ASK_LAB_EXECUTE"] = "1" if args.run else "0"
        argv, env = command(args.suite, env)
        status = max(status, subprocess.run([*argv, *extra], env=env).returncode)
    return status


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, RuntimeError, TimeoutError) as error:
        print(f"Test runner: {error}", file=sys.stderr)
        sys.exit(2)
