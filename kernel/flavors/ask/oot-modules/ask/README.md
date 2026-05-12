# ASK 2.0 — out-of-tree kernel module (PR1 / M0.1 skeleton)

Reference: `specs/ask-2.0-rewrite-spec.md` (v0.7), `plans/ASK-2.0-IMPLEMENTATION.md`.

## What this is

A skeleton `ask.ko` that:

1. Registers a generic-netlink family named **`ask`** (UAPI version 1).
2. Answers exactly one command — `ASK_CMD_GET_INFO` — with a fully-formed
   nested `ASK_ATTR_INFO` reply (driver version string, genl version, all
   ucode/capabilities/numeric fields stubbed to 0).
3. Has init/exit hooks for every future subsystem (`flow`, `flow_offload`,
   `xfrm`, `caam`, `bridge`, `neigh`, `op`, `hostcmd`, `stats`, `debugfs`)
   wired through a strict reverse-order unwind chain in `ask_main.c`.
4. Exports zero offload functionality. No flows accelerated. No CAAM
   descriptors built. No bridge / xfrm / nf_flow_table integration.
   Each subsystem's `_init()` returns 0 and `_exit()` is a no-op.

PR2 onwards fills in real behavior subsystem by subsystem.

## Files

| File                | Role                                                                 |
|---------------------|----------------------------------------------------------------------|
| `Kbuild`            | builds `ask.o` from all subsystem `.o`'s; adds `-I$(src)/include` |
| `Kconfig`           | `config NET_ASK` tristate (skeleton deps: `NET`, `ARM64`)         |
| `Makefile`          | OOT wrapper: `make` / `make sign` / `make clean`                  |
| `ask_main.c`        | `module_init`/`exit` + reverse-order unwind chain                 |
| `ask_genl.c`        | `genl_family` registration + `ASK_CMD_GET_INFO` doit              |
| `ask_genl_attr.c`   | 7 nla_policy tables (top + 6 nested categories)                   |
| `ask_<subsys>.c`    | 10 lifecycle stubs (each is just `_init()` / `_exit()`)            |
| `ask_trace.h`       | empty placeholder for later `TRACE_EVENT()` definitions           |
| `include/ask_internal.h` | module-private API (function decls + extern policy decls)    |
| `include/uapi/linux/ask/ask.h` | UAPI header — userspace + module both compile against |

## Build

```bash
make KDIR=/path/to/linux-6.18-src \
     ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu-
```

Produces `ask.ko` (~800 KB unstripped; ~70 KB stripped).

## Sign

`MODULE_SIG_FORCE=y` in production VyOS kernels — every `.ko` MUST be
signed by a key in the kernel's `.builtin_trusted_keys` ring or it gets
`Key was rejected by service` on `insmod`.

```bash
make KDIR=... ARCH=arm64 CROSS_COMPILE=... sign
```

This invokes `$KDIR/scripts/sign-file sha512 …/signing_key.{pem,x509}
ask.ko` and appends the `~Module signature appended~` trailer.

The signing key MUST be the same one whose public certificate is baked
into the running target kernel. CI builds use the VyOS Networks Secure
Boot Signer key (private key in `${{ secrets.MOK_KEY }}`); local builds
use the per-tree auto-generated `certs/signing_key.pem`.

## PR1 verification status

Build verified end-to-end on the LXC dev host (5/12/2026):

```
$ make clean && make KDIR=/home/vyos/kernel-ls1046a-build/work/linux-6.18.26 \
       ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- modules
…
  LD [M]  ask.ko
$ /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/scripts/sign-file sha512 \
      …/signing_key.pem …/signing_key.x509 ask.ko
$ tail -c 28 ask.ko | od -c
0000000   ~   M   o   d   u   l   e       s   i   g   n   a   t   u   r
0000020   e       a   p   p   e   n   d   e   d   ~  \n
```

On-device runtime validation deferred to PR1-followup: `ask.ko` must be
built and signed inside the same CI run that builds the target kernel
(so the trust chain matches). The device-side check is then:

```
sudo insmod /tmp/ask.ko
sudo dmesg | grep ask:                          # expect "ready (genl …)"
genl ctrl-list | grep -i ask                    # expect "ask" family
genl ctrl-resolve name ask                      # numeric family ID
sudo rmmod ask
sudo dmesg | grep ask:                          # expect "unloaded"
```

(See `plans/ASK-2.0-IMPLEMENTATION.md` PR1 acceptance criteria.)

## Future PRs

Each later PR (2 through 21) lands one subsystem at a time, replacing
its `_init()` stub with real work. The `genl_small_ops[]` table in
`ask_genl.c` will grow accordingly. Userspace UAPI in `ask.h` must NOT
be edited after PR1 except by appending — every existing enum value is
ABI-locked.