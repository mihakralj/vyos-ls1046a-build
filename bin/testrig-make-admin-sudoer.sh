#!/bin/bash
# Grants passwordless sudo to the 'admin' account on the testrig servers.
# Requires working root SSH credentials (password auth) — run interactively
# so the root password is never stored or passed on the command line.
#
# Usage: bin/testrig-make-admin-sudoer.sh 192.168.1.112 192.168.1.113

set -euo pipefail

if [[ $# -eq 0 ]]; then
    echo "Usage: $0 <host> [host...]" >&2
    exit 1
fi

read -r -s -p "Root password for target host(s): " ROOT_PASS
echo

for host in "$@"; do
    echo "=== $host ==="
    sshpass -p "$ROOT_PASS" ssh -o StrictHostKeyChecking=no "root@$host" '
        set -e
        echo "admin ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/90-admin-nopasswd
        chmod 0440 /etc/sudoers.d/90-admin-nopasswd
        visudo -c -f /etc/sudoers.d/90-admin-nopasswd
        usermod -aG sudo admin 2>/dev/null || usermod -aG wheel admin 2>/dev/null || true
        echo "OK: admin is now a passwordless sudoer on $(hostname)"
    '
done
