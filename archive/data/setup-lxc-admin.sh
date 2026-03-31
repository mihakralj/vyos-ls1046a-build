#!/bin/bash
# Setup admin user on LXC 200
set -e

# Create admin user with home dir and bash shell
useradd -m -s /bin/bash admin 2>/dev/null || echo "User admin already exists"

# Set password
echo "admin:auckland" | chpasswd

# Add to sudo group
usermod -aG sudo admin 2>/dev/null || true

# Setup SSH authorized_keys
mkdir -p /home/admin/.ssh
echo "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAICalFzBKS9oY/SDLz6kJDA9A1ktsrWQOA7FckaZMtkwr ed25519-key-20210110" > /home/admin/.ssh/authorized_keys
chmod 700 /home/admin/.ssh
chmod 600 /home/admin/.ssh/authorized_keys
chown -R admin:admin /home/admin/.ssh

# Ensure sudo without password for admin
echo "admin ALL=(ALL) NOPASSWD: ALL" > /etc/sudoers.d/admin
chmod 440 /etc/sudoers.d/admin

# Ensure SSH is installed and running
if which sshd >/dev/null 2>&1; then
    systemctl enable ssh 2>/dev/null || true
    systemctl start ssh 2>/dev/null || true
    echo "SSH server: running"
else
    apt-get update -qq && apt-get install -y -qq openssh-server
    systemctl enable ssh
    systemctl start ssh
    echo "SSH server: installed and started"
fi

echo "=== DONE ==="
echo "User: admin"
echo "Password: set"
echo "SSH key: installed"
id admin
