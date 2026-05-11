#!/bin/bash
# Local VyOS ISO build orchestrator — runs each ci-*.sh step in sequence,
# mimicking the env that .github/workflows/auto-build.yml provides.
set -e

# Resolve this repo's root from the script's location, so the script
# works whether invoked from /workspace (legacy docker bind), /home/vyos/...
# (native on the runner VM), or anywhere else.
WORKSPACE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$WORKSPACE"

# Top-level workflow env vars (auto-build.yml `env:` block)
export DEBIAN_MIRROR="http://deb.debian.org/debian/"
export DEBIAN_SECURITY_MIRROR="http://deb.debian.org/debian-security"
export VYOS_MIRROR="https://packages.vyos.net/repositories/current/"
export OCAML_VERSION="4.14.2"

# Fake GitHub Actions env so ci-set-version.sh and friends work
export GITHUB_WORKSPACE="$WORKSPACE"
export GITHUB_REPOSITORY=local/vyos-ls1046a-build
export GITHUB_REPOSITORY_OWNER=local
export GITHUB_OUTPUT=/tmp/gh_output
export GITHUB_ENV=/tmp/gh_env
export GITHUB_STEP_SUMMARY=/tmp/gh_step_summary
export REPO_OWNER_ID=0
export REPO_OWNER=local
: > "$GITHUB_OUTPUT"
: > "$GITHUB_ENV"
: > "$GITHUB_STEP_SUMMARY"

# Helper: source $GITHUB_ENV after each step so subsequent ones see exported vars
sync_env() { while IFS='=' read -r k v; do [ -n "$k" ] && export "$k=$v"; done < "$GITHUB_ENV"; }

step() {
  local name="$1"; shift
  echo
  echo "============================================================"
  echo "==> $name"
  echo "============================================================"
  "$@"
  sync_env
}

# Step 1: host base deps (already mostly in vyos-builder image, but re-run is cheap)
step "Install host base deps" bash -c '
apt-get update -qq
apt-get install -y --no-install-recommends \
  libsystemd-dev libglib2.0-dev libip4tc-dev libipset-dev libnfnetlink-dev \
  libnftnl-dev libnl-nf-3-dev libpopt-dev libpcap-dev libbpf-dev \
  bubblewrap git-lfs kpartx clang llvm cmake \
  protobuf-compiler python3-cracklib python3-protobuf \
  libreadline-dev liblua5.3-dev byacc flex \
  dosfstools mtools zstd u-boot-tools \
  libpcre2-dev xorriso \
  libxml2-dev libtclap-dev \
  python3-tomli python3-jinja2 python3-yaml python3-toml python3-git \
  python3-pystache pbuilder \
  python3-setuptools python3-pip python3-build python3-wheel \
  python3-stdeb dh-python debhelper devscripts equivs quilt \
  fakeroot rsync curl ca-certificates jq
'

step "Set version" env INPUT_BUILD_BY="" INPUT_BUILD_VERSION="" bash bin/ci-set-version.sh

step "Clone vyos-build" bash -c '
if [ ! -d vyos-build/.git ]; then
  rm -rf vyos-build
  git clone --depth=1 https://github.com/vyos/vyos-build.git vyos-build
fi
'

step "Install vyos-1x build deps (VyOS apt repo)" bash -c '
install -m 0644 vyos-build/docker/vyos-dev.key /usr/share/keyrings/vyos-dev-archive-keyring.asc
echo "deb [trusted=yes signed-by=/usr/share/keyrings/vyos-dev-archive-keyring.asc] https://packages.vyos.net/repositories/current current main" \
  > /etc/apt/sources.list.d/vyos-dev.list
apt-get update -qq
apt-get install -y --no-install-recommends \
  libzmq3-dev pylint whois \
  python3-pyudev python3-systemd python3-pam python3-pyroute2 \
  python3-voluptuous python3-lxml python3-xmltodict python3-coverage \
  python3-netaddr python3-netifaces python3-paramiko python3-passlib \
  python3-psutil python3-tabulate python3-zmq python3-fastapi \
  python3-vici python3-certbot-nginx python3-hurry.filesize \
  python3-nose2 python3-jmespath python3-pyhumps
'

step "Install j2lint" bash -c '
if ! command -v j2lint >/dev/null 2>&1; then
  pip install --break-system-packages "git+https://github.com/aristanetworks/j2lint.git@341b5d5db86"
fi
j2lint --version
'

step "Bootstrap OCaml/opam" bash -c '
if [ ! -x /usr/bin/opam ]; then
  apt-get install -y --no-install-recommends opam build-essential bubblewrap unzip
fi
if [ ! -d /opt/opam ]; then
  opam init --root=/opt/opam --comp=4.14.2 --disable-sandboxing --no-setup --yes
fi
eval "$(opam env --root=/opt/opam --set-root)"
opam install -y re pcre2 num ctypes ctypes-foreign ctypes-build containers \
  fileutils xml-light mustache yojson fmt logs
opam pin add -y -n vyos1x-config "https://github.com/vyos/vyos1x-config.git#52132ad2c0992bf6f17a06173384030d93a29053"
opam pin add -y -n vyconf "https://github.com/vyos/vyconf.git#e25b13ae3040d02326f01bf9bedd097795fb3a62"
opam install -y vyos1x-config vyconf
rm -f /usr/lib/libvyosconfig.so.0 /usr/lib/libvyosconfig.so
'

export OCAML_VERSION=4.14.2

step "Setup vyos-1x patches" bash bin/ci-setup-vyos1x.sh

# Stage ASK kernel: prefer a LOCAL prebuilt .deb dir if LOCAL_KERNEL_DEB_DIR
# is set; else download the pinned tag from the archived kernel-build repo's
# GitHub release.
if [ -n "${LOCAL_KERNEL_DEB_DIR:-}" ] && [ -d "$LOCAL_KERNEL_DEB_DIR" ]; then
    : "${KVER:?set KVER alongside LOCAL_KERNEL_DEB_DIR (e.g. KVER=6.18.28)}"
    export LOCAL_KERNEL_DEB_DIR KVER
    step "Stage prebuilt ASK kernel (local)" bash bin/local-stage-ask-kernel.sh
else
    ASK_KERNEL_TAG="$(tr -d '[:space:]' < kernel/flavors/ask/kernel.pin)"
    export ASK_KERNEL_TAG
    echo "ASK_KERNEL_TAG=$ASK_KERNEL_TAG"
    step "Consume prebuilt ASK kernel" bash bin/ci-consume-ask-kernel.sh
fi

step "Setup kernel config and patches" bash bin/ci-setup-kernel.sh
step "Setup ASK fast-path kernel" bash bin/ci-setup-kernel-ask.sh
step "Compile Mono DTB" bash bin/ci-compile-mono-dtb.sh
step "Setup vyos-build" bash bin/ci-setup-vyos-build.sh
step "Build image packages" bash bin/ci-build-packages.sh
step "Pick packages" bash bin/ci-pick-packages.sh
step "Install extra packages" bash bin/ci-install-extra-packages.sh

export BUILD_VERSION="$(grep '^build_version=' "$GITHUB_OUTPUT" | tail -1 | cut -d= -f2)"
echo "BUILD_VERSION=$BUILD_VERSION"
step "Build VyOS ISO" bash bin/ci-build-iso.sh

echo
echo "============================================================"
echo "BUILD COMPLETE"
echo "============================================================"
ls -la "$WORKSPACE"/*.iso 2>/dev/null || true
