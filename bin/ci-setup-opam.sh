#!/bin/bash
# ci-setup-opam.sh — Bootstrap OCaml/opam for libvyosconfig
#
# Called by: .github/workflows/auto-build.yml "Bootstrap OCaml/opam for
# libvyosconfig (cached on runner)" step. Also safe to run manually on a
# fresh self-hosted runner / LXC dev VM — every action is idempotent.
#
# vyos-1x's debian/rules calls `opam env --root=/opt/opam` to build
# libvyosconfig.  The vyos-builder Dockerfile (vyos-build/docker/
# Dockerfile lines 144-165) installs opam from upstream, runs
# `opam init --root=/opt/opam --comp=4.14.2`, then `opam install` 14
# OCaml packages (re, pcre2, num, ctypes, ctypes-foreign, ctypes-build,
# containers, fileutils, xml-light, mustache, yojson, fmt, logs). Total
# cold time: ~15 min.
#
# /opt/opam lives outside $GITHUB_WORKSPACE, so on the persistent
# self-hosted runner it survives between builds. Bootstrap once (slow),
# reuse forever (~3 s warm).
#
# Reads OCAML_VERSION from environment (set in auto-build.yml `env:` block).
# Defaults to 4.14.2 — must match vyos-1x's libvyosconfig/Makefile pin.
set -ex

OCAML_VERSION="${OCAML_VERSION:-4.14.2}"

# Install opam itself if missing (fast: Debian package).
if [ ! -x /usr/bin/opam ]; then
  apt-get install -y --no-install-recommends opam build-essential bubblewrap unzip
fi

# Init opam at the path vyos-1x's Makefile expects (slow first time only
# — survives between runner builds).
if [ ! -d /opt/opam ]; then
  opam init --root=/opt/opam --comp="$OCAML_VERSION" \
            --disable-sandboxing --no-setup --yes
fi
eval "$(opam env --root=/opt/opam --set-root)"

# `opam install -y` on already-installed packages is a fast no-op (~1 s
# for 13 packages), so unconditionally install — cheap and keeps the
# recipe declarative.
opam install -y \
  re pcre2 num ctypes ctypes-foreign ctypes-build containers \
  fileutils xml-light mustache yojson fmt logs

# Pin vyos1x-config and vyconf at the exact commits vyos-1x's
# libvyosconfig/Makefile depends on.  The Makefile's `depends` target
# tries to do these pins itself via `sudo sh -c`, but only when
# /usr/lib/libvyosconfig.so.0 isn't already present; vyos-1x's
# debian/rules then unconditionally copies $OPAM/share/vyconf/vyconf.proto
# and the vyconf{d,_cli,_cli_compat} binaries into the libvyosconfig
# .deb at install time ("cp /opt/opam/share/vyconf/vyconf.proto ..."),
# so vyconf MUST be opam-installed in /opt/opam regardless of whether
# libvyosconfig is rebuilt.
#
# Commit pins are taken verbatim from vyos-1x's libvyosconfig/Makefile
# lines 45-46.
opam pin add -y -n vyos1x-config \
  'https://github.com/vyos/vyos1x-config.git#52132ad2c0992bf6f17a06173384030d93a29053'
opam pin add -y -n vyconf \
  'https://github.com/vyos/vyconf.git#e25b13ae3040d02326f01bf9bedd097795fb3a62'
opam install -y vyos1x-config vyconf

# vyos-1x's top-level Makefile gates the libvyosconfig build behind
#   @if [ ! -f /usr/lib/libvyosconfig.so.0 ]; then make -C libvyosconfig all; ... fi
# but vyos-1x's debian/rules:159 unconditionally does
#   cp libvyosconfig/_build/libvyosconfig.so debian/tmp/libvyosconfig/usr/lib/libvyosconfig.so.0
# If a previous build's `sudo make install` left /usr/lib/libvyosconfig.so.0
# on the persistent self-hosted runner, the gate is true, the Makefile
# silently no-ops, _build/ is never created, and the cp fails with
#   cp: cannot stat 'libvyosconfig/_build/libvyosconfig.so'.
# On the GitHub-hosted ARM runner this never reproduces because the
# filesystem is fresh per-job. Force a clean state on every build.
rm -f /usr/lib/libvyosconfig.so.0 /usr/lib/libvyosconfig.so