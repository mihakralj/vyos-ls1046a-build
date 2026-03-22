#!/bin/bash
# setup-heidi.sh — Provision vyos-builder LXC on Proxmox (heidi)
#
# Run on heidi (Proxmox host) as root:
#   bash setup-heidi.sh
#
# Creates LXC 200 (vyos-builder) with:
#   - ARM64 cross-compile toolchain (gcc-aarch64-linux-gnu)
#   - TFTP server serving /srv/tftp (kernel, DTB, initrd for Mono Gateway)
#   - Docker + ARM64 binfmt (for vyos-1x package builds and full ISO)
#   - Git + build dependencies
#
# The LXC gets a DHCP address on vmbr0 (same LAN as Mono Gateway).
# After setup, use build-local.sh inside the LXC for fast dev iteration.

set -euo pipefail

CTID=200
CT_NAME="vyos-builder"
CT_TEMPLATE="local:vztmpl/debian-12-standard_12.7-1_amd64.tar.zst"
CT_STORAGE="local-lvm"
CT_DISK="80"     # GB — enough for kernel source + vyos-build + ISO
CT_RAM="16384"   # MB — 16 GB for kernel compile
CT_SWAP="4096"   # MB
CT_CORES="12"    # Leave 4 cores for other LXCs
BRIDGE="vmbr0"

echo "=== VyOS Builder LXC Setup ==="
echo "CTID:     $CTID"
echo "Name:     $CT_NAME"
echo "Storage:  $CT_STORAGE (${CT_DISK}GB)"
echo "RAM:      ${CT_RAM}MB, Cores: $CT_CORES"
echo ""

#--- Step 1: Create LXC container ---
if pct status "$CTID" &>/dev/null; then
    echo "Container $CTID already exists. Checking status..."
    STATUS=$(pct status "$CTID" | awk '{print $2}')
    if [ "$STATUS" = "running" ]; then
        echo "Container is running. Skipping creation."
    else
        echo "Container exists but stopped. Starting..."
        pct start "$CTID"
    fi
else
    echo "Creating LXC $CTID ($CT_NAME)..."

    # Verify template exists
    if [ ! -f "/var/lib/vz/template/cache/debian-12-standard_12.7-1_amd64.tar.zst" ]; then
        echo "ERROR: Template not found. Download with:"
        echo "  pveam download local debian-12-standard_12.7-1_amd64.tar.zst"
        exit 1
    fi

    pct create "$CTID" "$CT_TEMPLATE" \
        --hostname "$CT_NAME" \
        --memory "$CT_RAM" \
        --swap "$CT_SWAP" \
        --cores "$CT_CORES" \
        --rootfs "${CT_STORAGE}:${CT_DISK}" \
        --net0 "name=eth0,bridge=${BRIDGE},ip=dhcp" \
        --unprivileged 0 \
        --features "nesting=1,keyctl=1" \
        --onboot 1 \
        --startup "order=5"

    echo "Starting LXC $CTID..."
    pct start "$CTID"
    sleep 5
fi

# Wait for networking
echo "Waiting for network..."
for i in $(seq 1 30); do
    if pct exec "$CTID" -- ping -c1 -W1 deb.debian.org &>/dev/null; then
        break
    fi
    sleep 2
done

# Show assigned IP
CT_IP=$(pct exec "$CTID" -- hostname -I 2>/dev/null | awk '{print $1}')
echo "LXC $CTID IP: $CT_IP"

#--- Step 2: Install packages inside LXC ---
echo ""
echo "=== Installing packages in LXC $CTID ==="

pct exec "$CTID" -- bash -c '
set -ex

# Update package lists
apt-get update -qq

#--- ARM64 cross-compile toolchain ---
apt-get install -y \
    gcc-aarch64-linux-gnu \
    binutils-aarch64-linux-gnu \
    make \
    bc \
    bison \
    flex \
    libssl-dev \
    libelf-dev \
    device-tree-compiler \
    cpio \
    kmod

#--- TFTP server ---
apt-get install -y tftpd-hpa
mkdir -p /srv/tftp
cat > /etc/default/tftpd-hpa << "TFTPCFG"
TFTP_USERNAME="tftp"
TFTP_DIRECTORY="/srv/tftp"
TFTP_ADDRESS="0.0.0.0:69"
TFTP_OPTIONS="--secure --create"
TFTPCFG
systemctl enable tftpd-hpa
systemctl restart tftpd-hpa

#--- Git + utilities ---
apt-get install -y \
    git \
    git-lfs \
    curl \
    wget \
    rsync \
    jq \
    xz-utils \
    squashfs-tools \
    p7zip-full \
    unzip \
    sudo

#--- Docker (for vyos-1x builds and full ISO) ---
apt-get install -y ca-certificates gnupg
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/debian/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
chmod a+r /etc/apt/keyrings/docker.gpg
echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian bookworm stable" \
    > /etc/apt/sources.list.d/docker.list
apt-get update -qq
apt-get install -y docker-ce docker-ce-cli containerd.io

# Enable ARM64 binary execution via binfmt/QEMU
apt-get install -y qemu-user-static binfmt-support
systemctl enable binfmt-support
systemctl restart binfmt-support
# Register ARM64 format
update-binfmts --enable qemu-aarch64 2>/dev/null || true

#--- Create workspace ---
mkdir -p /opt/vyos-dev
mkdir -p /srv/tftp

echo ""
echo "=== Package installation complete ==="
'

#--- Step 3: Pull vyos-builder Docker image (ARM64) ---
echo ""
echo "=== Pulling vyos-builder Docker image (ARM64 via QEMU) ==="
pct exec "$CTID" -- bash -c '
docker pull --platform linux/arm64 ghcr.io/huihuimoe/vyos-arm64-build/vyos-builder:current-arm64 || \
    echo "WARNING: Docker pull failed. Will retry later. Cross-compile still works."
'

#--- Step 4: Clone kernel source ---
echo ""
echo "=== Cloning Linux kernel source ==="
pct exec "$CTID" -- bash -c '
cd /opt/vyos-dev
if [ ! -d linux ]; then
    # VyOS current uses kernel 6.6.x (LTS)
    git clone --depth 1 --branch linux-6.6.y \
        https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git linux
    echo "Kernel source cloned: $(du -sh linux | cut -f1)"
else
    echo "Kernel source already exists"
fi
'

#--- Step 5: Clone build-scripts repo ---
echo ""
echo "=== Cloning build-scripts repo ==="
pct exec "$CTID" -- bash -c '
cd /opt/vyos-dev
if [ ! -d build-scripts ]; then
    git clone https://github.com/mihakralj/vyos-ls1046a-build.git build-scripts
    echo "build-scripts cloned"
else
    cd build-scripts && git pull
    echo "build-scripts updated"
fi
'

#--- Summary ---
echo ""
echo "============================================"
echo "=== VyOS Builder LXC $CTID Setup Complete ==="
echo "============================================"
echo ""
echo "LXC IP:          $CT_IP"
echo "SSH:             ssh root@$CT_IP"
echo "  or from helga: ssh -J admin@heidi root@$CT_IP"
echo ""
echo "Workspace:       /opt/vyos-dev/"
echo "  linux/         Kernel 6.6.x source tree"
echo "  build-scripts/ This repo (patches, configs, DTS)"
echo ""
echo "TFTP dir:        /srv/tftp/"
echo "  (Mono Gateway U-Boot loads from here)"
echo ""
echo "Next steps:"
echo "  1. Copy initrd.img from last good ISO to /srv/tftp/"
echo "  2. Run: build-local.sh kernel   (cross-compile → TFTP)"
echo "  3. From helga serial: run dev_boot"
echo ""
echo "U-Boot setup (one-time, from helga serial console):"
echo "  setenv heidi_ip $CT_IP"
echo "  setenv dev_boot 'dhcp; tftp \${kernel_addr_r} \${heidi_ip}:vmlinuz; tftp \${fdt_addr_r} \${heidi_ip}:mono-gw.dtb; tftp \${ramdisk_addr_r} \${heidi_ip}:initrd.img; setenv bootargs \"console=ttyS0,115200 earlycon=uart8250,mmio,0x21c0500 net.ifnames=0 boot=live rootdelay=5 noautologin vyos-union=/boot/2026.03.21-2144-rolling\"; booti \${kernel_addr_r} \${ramdisk_addr_r}:\${filesize} \${fdt_addr_r}'"
echo "  saveenv"
