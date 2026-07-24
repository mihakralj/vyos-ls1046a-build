# Vendored pyynl (YNL Python client)

Source: Linux kernel `tools/net/ynl/pyynl/` from `linux-6.18.34`.
Vendored for ASK2 T-M7-5: op-mode `show interfaces ethernet ethN offload
ask flows` wraps `ynl --family ask --dump dump-flows` per
`kernel/flavors/ask/uapi/ask.yaml` (§3.5 "Operator UX"). ynl is not in the
VyOS apt archive, so the pure-Python client is shipped in the image.

Install layout (bin/ci-setup-vyos-build.sh):
  board/ynl/cli.py       -> /usr/share/ynl/pyynl/cli.py
  board/ynl/lib/*.py     -> /usr/share/ynl/pyynl/lib/
  kernel/.../ask.yaml    -> /usr/share/ynl/specs/ask.yaml
  wrapper                -> /usr/local/bin/ynl   (python3 .../cli.py "$@")

Runtime deps: python3-yaml (present). jsonschema is optional and only used
for --schema validation; op-mode calls ynl with --no-schema so it is not
required. `ynl --family ask` resolves the spec from /usr/share/ynl/specs/.

Refresh when the kernel ynl lib changes: re-copy from the matching
tools/net/ynl/pyynl/ of the built kernel version.
