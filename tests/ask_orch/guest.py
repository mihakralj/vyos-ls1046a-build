"""Bounded LAN-side commands on dell1 over its management SSH connection."""

from .lab import Endpoint


class Guest:
    def __init__(self, domain="dell1"):
        self.domain = domain
        self.endpoint = Endpoint.from_env("lan")

    def check(self):
        result = self.run("command -v timeout && python3 --version")
        if result.rc:
            raise RuntimeError("dell1 needs Python 3 and coreutils timeout: " + result.stdout)
        return result.stdout.strip()

    def execute(self, argv, *, data=b"", timeout=30):
        return self.endpoint.execute(argv, data=data, timeout=timeout)

    def run(self, command, timeout=30):
        return self.execute(["/bin/sh", "-c", command], timeout=timeout)

    def python(self, source, timeout=30):
        return self.execute(["python3", "-"], data=source.encode(), timeout=timeout)
