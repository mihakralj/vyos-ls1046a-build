# AUDIT-cmm

Defensive-coding & optimization audit of the ASK `cmm` userspace daemon
(`ASK/cmm/src/`, 38,888 lines of hand-written C across ~47 source files,
the largest userspace component shipped with the LS1046A image).

The audit was driven by full-text grep across the tree (D1..D18 / O1..O10
checklists from `plans/ASK-USERSPACE-AUDIT-PLAN.md`), line-level review of
the high-blast-radius TUs called out in the brief (`cmm.c`,
`forward_engine.c`, `conntrack.c`, `ffbridge.c`, `ffcontrol.c`,
`client_daemon.c`, `alt_conf.c`, `cmm_asym_ff.c`, `module_route.c`,
`neighbor_resolution.c`, `rtnl.c`, `module_lro.c`, `timeout.c`), and
re-verification of the 14 prior findings (C11–C17, M5–M11) recorded in
`ask-ls1046a-6.6/ASK-CODE-REVIEW.md §2`.

No source files were modified.

## Findings summary

| Severity | Count |
|---|---|
| P0 | 4  |
| P1 | 11 |
| P2 | 13 |

Plus 6 optimization candidates.

Of the 14 prior findings: **12 still present unchanged**, **2 partially
mitigated** (C16 — `neighbor_resolution.c:585` and `conntrack.c:408` now
NULL-check; the `neighReq` allocator at `:224` and the four `ffcontrol.c`
allocators still silently return without log; M11 — `O_EXCL` is correct
on the first attempt, but the `EEXIST` recovery still races).

## Module inventory

- LoC (hand-written `*.c` only): **38,888** across ~47 source files.
- Public surfaces:
  - **CLI socket** `AF_INET/SOCK_STREAM` on `globalConf.cli_listenaddr:2103`
    (`ffcontrol.c:2849-2880`). Bind address defaults to
    `htonl(INADDR_LOOPBACK)` (`cmm.c:352`) but is overridable from the
    config file. No TLS, no auth — privilege boundary relies entirely on
    netns/listen-addr.
  - **SysV msgqueue** for cmm-client ↔ cmm-daemon RPC (`client_daemon.c`
    via `msgget`/`msgsnd`/`msgrcv`).
  - **NFCT** netlink (`globalConf.nf_conntrack_handle`) and **NFCT
    catch** (`ctx->catch_handle`) sockets (`conntrack.c:3277-3290`).
  - **FCI** netlink (`fci_handle`, `fci_catch_handle`, `fci_key_handle`,
    `fci_key_catch_handle`) — see fmlib audit for those sockets.
  - **Routing netlink** (`cmm_rtnl_open()` in `rtnl.c:86`) and
    **PF_PACKET** raw socket (`neighbor_resolution.c:395`).
  - **PID file** `CMM_PID_FILE_PATH` (`cmm.c:262-282`).
- Largest TUs (LoC):
  ```
   4025 conntrack.c
   2932 ffcontrol.c
   2805 module_qm.c
   1901 keytrack.c
   1891 module_tunnel.c
   1890 module_socket.c
   1862 itf.c
   1777 forward_engine.c
   1754 module_rx.c
   1660 client_daemon.c
   1552 route_cache.c
   1391 module_rtp.c
   1267 module_stat.c
   1072 neighbor_resolution.c
    624 module_route.c
    567 cmm.c
    336 ffbridge.c
    193 alt_conf.c
    124 cmm_asym_ff.c
  ```
- Origin tag: `Copyright (C) 2007 Mindspeed Technologies` →
  `2014-2016 Freescale Semiconductor` → `2017,2021 NXP`. The Mindspeed-era
  pre-Comcerto code is `forward_engine.c`, `ffcontrol.c`, `keytrack.c`,
  `module_*` — pre-RAII C, manual `__pthread_mutex_lock` macros, lots of
  `goto err{0..7}` ladders, and consistent under-checking of the libc
  string family. The 2021 NXP refresh adds bounded-strncpy patterns in
  *some* call sites (e.g. `ffcontrol.c:486-489`, `:511-514`) but did not
  sweep the codebase — see P0.2 / P1.5.
- Threading model:
  - Main thread `pause()`s after init (`cmm.c:528`).
  - CLI thread (`cmmCliThread` started at `ffcontrol.c:2877`).
  - Daemon thread (`cmmDaemonThread` started at `client_daemon.c:1215`).
  - CT thread (`cmmCtThread`, started in `conntrack.c` ~line 3300).
  - Optional third-party callback thread.
  - Coarse mutexes in `globalConf`: `ctMutex`, `rtMutex`, `neighMutex`,
    `flowMutex`, `sa_lock`, `socket_lock`, `brMutex`, `RouteMutex`,
    `itf_table.lock`, `logMutex`. There is **no mutex on the rtnl buffer
    pool** — see P0.1.
- Debug helper used as the format primitive: `cmm_print(level, fmt, ...)`
  (`cmm.c:131-170`) — *not* annotated `__attribute__((format(printf,
  2, 3)))` anywhere. Two call sites pass a non-literal as `fmt` (P0.3).

## Re-verification of prior findings (§2 of `ASK-CODE-REVIEW.md`)

| Prior ID | File:line at HEAD | Status | Notes |
|---|---|---|---|
| C11 (CRIT) — rtnl bufpool race | `cmm.c:555-567` | **Present** | Still no lock on `globalConf.cur_rtnl_bufs`. Now P0.1. |
| C12 (CRIT) — `ff_enable` flag race | `cmm.c:351`; readers `forward_engine.c:1689,1716`, `conntrack.c:2459`; writers `forward_engine.c:1705,1732` | **Present** | Plain `int`, no atomic, no barrier. Now P1.1. |
| C13 (HIGH) — `ct_table` walk vs CLI dump | `conntrack.c:223-300, 2849-2887` | **Present** | CLI dump path takes `ctMutex` at line 2849, but lookup paths at lines 530, 593, 844, 859, 1864, 1880, 1896, 1913 walk `ct_table[*]` from worker callers under various locks. No consistent invariant. Now P1.2. |
| C14 (HIGH) — `strcpy` into `input_buf[16]` | `module_route.c:317, 363` | **Present, exact match** | `char input_buf[16];` at L317; `strcpy(input_buf, temp->route.input_device_str);` at L363. Now P0.2. |
| C15 (HIGH) — FCI socket leak on error | `forward_engine.c:561,603,632,915,957,986,1253,1270,1288,1695,1722` | **Present** | Every `fci_write` failure path just `goto err`s without `fci_close`+reopen. Process-lifetime sockets, but partial-failure leaves the channel in indeterminate state. Now P2.4. |
| C16 (HIGH) — unchecked `malloc` | `neighbor_resolution.c:224, 585`; `conntrack.c:408`; `ffcontrol.c:477, 506, 681, 706` | **Partially fixed** | `:585` and `:408` now NULL-check + log. `:224` (`cmmNeighAddSolicitQ`) and the four `ffcontrol.c` sites still NULL-check but **silently return the unmodified caller list** with no log — failure mode is "rule silently ignored". Now P2.1 / P2.2. |
| C17 (HIGH) — format-string at `cmm.c:201` | `cmm.c:201` | **Present, exact match** | `cmm_print(DEBUG_STDOUT, cmm_help);`. A second instance found at `forward_engine.c:1598`. Now P0.3. |
| M5 — fixed conntrack hash size | `conntrack.h` (`CONNTRACK_HASH_TABLE_SIZE`) | **Present** | Compile-time constant. Now P2.7. |
| M6 — undocumented `globalConf` ownership | `cmm.h:240-310` | **Present** | 30+ fields, no thread ownership annotation. Now P2.8. |
| M7 — linear interface lookup | `itf.c` | **Present** | `__itf_find` walks `itf_table.list`. Now O-E. |
| M8 — 8 separate netlink sockets | `module_socket.c`, `conntrack.c`, `itf.c`, `neighbor_resolution.c`, `rtnl.c` | **Present** | Aggregating into a single multiplexed socket non-trivial; defer. P2.9. |
| M9 — async-unsafe signal handler | `cmm.c:48-129` (`cmm_crit_err_hdlr`) | **Present** | Calls `fprintf`, `strsignal`, `getpid`, `backtrace_symbols`, `free`. Lifted to P0.4. |
| M10 — no `ENOBUFS` retry | `rtnl.c:203-218` | **Present** | `recvmsg` failure on `EAGAIN`/non-`EINTR` jumps to `err`. Now P1.8. |
| M11 — PID file race | `cmm.c:248-275` | **Mitigated then re-introduced** | First `open` is `O_WRONLY|O_CREAT|O_EXCL`. On `EEXIST` the code calls `remove()` and re-`open`s with `O_EXCL` — races between two cmm processes both seeing `cmmIsDaemonRunning()==0`. Now P2.11. |

## P0 findings (heap-corruption / format-injection / signal-handler UB)

### P0.1 — `cur_rtnl_bufs` is a lock-free LIFO mutated from multiple threads
- **File:** `cmm.h:272-274`, `cmm.c:497-503` (init), `cmm.c:555-567`
  (`cmm_get_rtnl_buf` / `cmm_free_rtnl_buf`), `rtnl.c:199, 260, 264`
  (consumers).
- **Snippet (`cmm.c:555-567`):**
  ```c
  char *cmm_get_rtnl_buf(uint32_t *buf_size) {
      if (!globalConf.cur_rtnl_bufs)
          return NULL;
      *buf_size = CMM_MAX_64K_BUFF_SIZE;
      return (char *)(globalConf.rtnl_buf_pools_align[--globalConf.cur_rtnl_bufs]);
  }
  void cmm_free_rtnl_buf(char *buf) {
      globalConf.rtnl_buf_pools_align[globalConf.cur_rtnl_bufs++] = (uint64_t)buf;
  }
  ```
- **Defect class:** D10 (cross-thread shared state w/o sync), D11
  (no mutex / atomic). The counter is `uint8_t` (`cmm.h:274`); the
  decrement-then-index is non-atomic. Two concurrent `cmm_get_rtnl_buf`
  callers can return the same buffer; concurrent get vs free can index
  out of bounds.
- **Trigger:** `cmm_get_rtnl_buf` is called from `rtnl.c:199`
  (`cmm_rtnl_listen`), invoked from rtnetlink helper threads (link, addr,
  route subsystems each) plus the catch threads. Multi-thread invocation
  is the design.
- **Recommendation:** Make `cur_rtnl_bufs` an `atomic_int` and use CAS
  for the decrement, or wrap pool ops in a `pthread_spinlock_t`.
  Alternatively replace with one buffer per thread (TLS) — the pool size
  is `CMM_MAX_NUM_THREADS` so each thread can own a slot.
- **Fix routing:** ASK mono extension patch (consumer repo). 6-line
  change in `cmm.c` plus one `<stdatomic.h>` include.

### P0.2 — `strcpy` into 16-byte `input_buf` from `IFNAMSIZ` field
- **File:** `module_route.c:317` (`char input_buf[16];`), `:363`
  (`strcpy(input_buf, temp->route.input_device_str);`).
- **Defect class:** D7 (fixed-dest buffer overflow).
- **Trigger:** `temp->route.input_device_str` is loaded from a user
  CLI command via `module_route.c:200` (`strcpy(entryCmd->input_device_str,
  keywords[cpt]);`) — also a `strcpy` into a fixed-size field. The CLI
  feeds these through `cli_*` parsing where each token is bounded only by
  `cli_def`'s line buffer, not by `IFNAMSIZ`. A 16-char interface name
  with no terminator overflows by 1 byte into `proto_buf[16]` immediately
  after.
- **Recommendation:** `snprintf(input_buf, sizeof(input_buf), "%s",
  temp->route.input_device_str);` Same fix at `:200` and the matching
  `module_mc4.c:249,250,269,270,338`, `module_mc6.c:306,355,356,376,377,469`,
  and `module_route.c:111,200`. There are 62 `strcpy(` call sites across
  the daemon — most are constant strings (`keytrack.c:1215-1308`) but
  every variable-source site needs a sweep.
- **Fix routing:** ASK mono extension patch.

### P0.3 — Two format-string sinks via `cmm_print`
- **File:**
  - `cmm.c:201` — `cmm_print(DEBUG_STDOUT, cmm_help);`
  - `forward_engine.c:1598` — `cmm_print(DEBUG_STDOUT, output_buf);`
- **Defect class:** D8 / D14 (non-literal format string).
- **Trigger:**
  - `cmm.c:201`: `cmm_help` is a static string literal in `cmm.h`; today
    it is constant, but any future maintainer adding `%` to the help
    text gets a wild-format read.
  - `forward_engine.c:1598`: `output_buf` is a stack `char output_buf[256]`
    populated via `snprintf(output_buf+len, 256-len, ...)` from values
    that include conntrack data (sa_handle, ports). Today the secondary
    formats are `%x`/`%d` of ints — but `cmm_print` itself has no
    `format` attribute, so the compiler cannot warn.
- **Recommendation:**
  - Add `__attribute__((format(printf, 2, 3)))` to the `cmm_print`
    macro / `cmm_print_func` prototype in `cmm.h`. The build then emits
    `-Wformat-security` warnings for both sites and any future regression.
  - Convert both sites to `cmm_print(DEBUG_STDOUT, "%s", ...)`.
- **Fix routing:** ASK mono extension patch. Trivial.

### P0.4 — SIGSEGV/SIGBUS handler calls `fprintf`, `backtrace_symbols`, `free`
- **File:** `cmm.c:48-129` (`cmm_crit_err_hdlr`), installed at
  `cmm.c:368-401` for SIGSEGV/SIGBUS/SIGFPE/SIGILL/SIGABRT.
- **Defect class:** D13 (async-signal-unsafe). Already noted M9; lifted
  to P0 because SIGSEGV is the most likely arrival path and (a)
  `backtrace_symbols` calls `malloc`, deadlocking on the glibc arena
  lock if the segfault is inside `malloc`; (b) `fprintf(stderr, ...)`
  is not AS-safe; (c) handler ends in `exit(EXIT_FAILURE)` (runs
  atexit hooks from signal context) instead of `_exit`.
- **Trigger:** Any null-deref in the daemon — handler reliably runs on
  a path that may itself recurse into the same defect.
- **Recommendation:** Rewrite to use only AS-safe primitives:
  `write(STDERR_FILENO, ...)`, pre-formatted register dump, then
  `_exit(128 + sig)`. Drop `backtrace_symbols`; keep `backtrace()`
  (just collects frames, AS-safe) and write raw addresses for offline
  `addr2line`.
- **Fix routing:** ASK mono extension patch. ~30 lines.

## P1 findings (logic / leak / race that does not corrupt heap)

### P1.1 — `globalConf.ff_enable` cross-thread `int` without atomic/barrier
- **File:** `cmm.h` (`int ff_enable;` field); readers
  `forward_engine.c:1689,1716`, `conntrack.c:2006,2459`; writers
  `cmm.c:351`, `forward_engine.c:1705,1732`.
- **Defect class:** D10. Reader threads (CT worker, FE worker) may see
  stale value indefinitely on aarch64 store-buffer; the daemon thread
  that flips the flag does so under no shared mutex.
- **Recommendation:** `_Atomic int` with `atomic_load_explicit(&...,
  memory_order_acquire)` on read and `atomic_store_explicit(&...,
  memory_order_release)` on write.
- **Fix routing:** ASK mono extension patch.

### P1.2 — `ct_table` traversals from worker paths under inconsistent locking
- **File:** `conntrack.c`. Read-side at `:530, :593, :844, :859, :1864,
  :1880, :1896, :1913` walk `ct_table[*]` / `ct_table_by_*[*]` from
  inside callbacks. Insert sites at `:444, :453, :457, :461, :1636,
  :1751` add to those lists from CT-event handlers while the read-side
  may walk concurrently.
- **Defect class:** D10. The brief `pthread_mutex_lock(&ctMutex)` covers
  the high-level update path (`:225`, `:300`, `:2649`, `:2849`); intra-
  callback walks do not retake `ctMutex` or the relevant
  `rtMutex`/`neighMutex`. Lockless reads of a non-RCU intrusive list →
  use-after-free if the writer thread runs `__cmmCtRemove`
  (`forward_engine.c:84`) mid-walk.
- **Recommendation:** Either funnel all `ct_table` mutations through
  the CT thread and make readers RCU-style with deferred frees, or hold
  `ctMutex` over every walk. The former is correct; the latter is a
  one-day patch.
- **Fix routing:** ASK mono extension patch. Architectural fix is
  multi-week.

### P1.3 — `cmm.c:417` `strncpy` without NUL-termination on max-length input
- **File:** `cmm.c:415-418`:
  ```c
  char confFilePath[512+1] = "";
  ...
  strncpy(confFilePath, optarg, 512);
  confFilePath[512] = '\0';
  ```
- **Status:** Currently safe — buffer is 513 bytes and the trailing NUL
  is forced. Fragile against future maintainer edits.
- **Defect class:** D7 boundary.
- **Recommendation:** `snprintf(confFilePath, sizeof(confFilePath),
  "%s", optarg);`
- **Fix routing:** local nit.

### P1.4 — `iov.iov_base` set without checking `cmm_get_rtnl_buf` return
- **File:** `rtnl.c:199-201`:
  ```c
  buf = cmm_get_rtnl_buf(&buf_size);
  iov.iov_base = buf;
  ```
- **Defect class:** D6 (NULL deref of pointer chain).
- **Trigger:** When the bufpool is exhausted (`cur_rtnl_bufs == 0`),
  `cmm_get_rtnl_buf` returns NULL. The next `recvmsg` is handed NULL
  `iov_base` — kernel returns `-EFAULT`, code logs and `goto err`s,
  which calls `cmm_free_rtnl_buf(NULL)` → writes NULL into the pool
  array at `cmm.c:566` and increments `cur_rtnl_bufs`. The pool now
  has a NULL slot the next get will hand out.
- **Recommendation:** Early `if (!buf) return -1;` in `cmm_rtnl_listen`,
  and a NULL-guard in `cmm_free_rtnl_buf`.
- **Fix routing:** ASK mono extension patch. 4 lines.

### P1.5 — `module_route.c:200, 111` `strcpy` of CLI keyword into `IFNAMSIZ` field
- **File:** `module_route.c:111` (`output_device_str`),
  `module_route.c:200` (`input_device_str`). Same class as P0.2 but
  on the *write* side from CLI input.
- **Defect class:** D7.
- **Recommendation:** `snprintf(... , sizeof(...), "%s", keywords[cpt])`.
- **Fix routing:** ASK mono extension patch.

### P1.6 — `alt_conf.c` keyword matching truncated to one character
- **File:** `alt_conf.c:45, 51, 56, 62, 64`:
  ```c
  if (strncasecmp(argv[firstarg], "all", 1) == 0)
  if (strncasecmp(argv[firstarg], "mcttl", 1) == 0)
  if (strncasecmp(argv[firstarg+1], "default", 1) == 0)
  if (strncasecmp(argv[firstarg+1], "ignore", 1) == 0)
  ```
- **Defect class:** Logic bug. The `1` length argument means any string
  starting with `a` matches `"all"`, any string starting with `m`
  matches `"mcttl"`, any `d`-string matches `"default"`, any `i`-string
  matches `"ignore"`. Users typing `dpa`, `m`, `info`, `mac`, `aa` all
  hit the wrong branch.
- **Recommendation:** Use `strcasecmp` (full match) — no abbreviation
  contract is documented, and `:69` already uses `strcasecmp` for
  `"ipsecrl"`.
- **Fix routing:** ASK mono extension patch.

### P1.7 — `setsockopt(SO_REUSEADDR)` failure ignored on the CLI listen socket
- **File:** `ffcontrol.c:2857-2858`:
  ```c
  if (setsockopt(ctx->sock, SOL_SOCKET, SO_REUSEADDR, &on, sizeof(on)) < 0)
      goto err4;
  ```
- **Defect class:** D17 (error path). `goto err4` closes the socket
  without logging. Compare adjacent `bind()` failure (`:2867`) which
  logs. Three other socket+setsockopt chains (`neighbor_resolution.c:309-325`,
  `rtnl.c:86-99`) similarly drop into `goto err` without identifying
  which step failed. Operationally masks "address in use" / capability
  errors at startup.
- **Recommendation:** Add `cmm_print(DEBUG_ERROR, ...)` on the failure
  branch with `errno`.
- **Fix routing:** ASK mono extension patch.

### P1.8 — `rtnl.c` no `ENOBUFS` retry; messages silently dropped
- **File:** `rtnl.c:204-218`. On `EAGAIN`, the code `goto err`. Under
  load (a `bringup` of all interfaces, or a conntrack flood) the kernel
  returns `ENOBUFS` and the daemon stops consuming netlink for the rest
  of that listener invocation. Interface state then drifts from kernel
  reality.
- **Recommendation:** Treat `ENOBUFS` like `EINTR` (continue), log a
  counter; on persistent `ENOBUFS`, raise `SO_RCVBUF`.
- **Fix routing:** ASK mono extension patch.

### P1.9 — `cmm_print` is not `__attribute__((format(printf,2,3)))`
- **File:** `cmm.h` (declaration of `cmm_print` / `cmm_print_func`).
- **Defect class:** D14 enabler. Keeps the compiler from finding the
  P0.3 sites and any future ones. Hundreds of `cmm_print` call sites
  with format strings; one wrong-arity call would be silent.
- **Recommendation:** Add `__attribute__((format(printf, 2, 3)))` on
  the prototype. Fix the resulting warning cascade.
- **Fix routing:** ASK mono extension patch.

### P1.10 — `module_lro.c:65` `system()` with interpolated `ifname`
- **File:** `module_lro.c:55-65`:
  ```c
  snprintf(cmd, 32 + IFNAMSIZ, "ethtool -K %s lro on", itf->ifname);
  if(system(cmd) == -1)
      cmm_print(DEBUG_ERROR, "%s: system command failed...  \n", __func__);
  ```
- **Defect class:** D7-class shell injection. `itf->ifname` is sourced
  from `if_indextoname()` over RTM_NEWLINK; kernel guarantees safe
  charset for normal interfaces, but veth/bridge/macvlan names accept
  arbitrary bytes including spaces if created via netlink. A
  mischievous `ip link add` of a name like `a; rm -rf /;` is passed
  straight to `/bin/sh`.
- **Recommendation:** Replace with `execvp("ethtool", argv)` (bypasses
  the shell), or write the LRO bit directly via the sysfs attribute.
- **Fix routing:** ASK mono extension patch.

### P1.11 — `forward_engine.c:1598` non-literal `cmm_print` (companion to P0.3)
- See P0.3.

## P2 findings

### P2.1 — `cmmNeighAddSolicitQ` returns `-1` on `malloc` fail without log
- **File:** `neighbor_resolution.c:222-227`. Returns `-1`, but the
  caller chain often discards the return value.
- **Recommendation:** Log at DEBUG_ERROR.

### P2.2 — `ffcontrol.c:477,506,681,706` mallocs return the *unmodified caller list* on OOM
- **File:** `cmmFcAsymFFRuleAddAttribut`, `cmmFcAsymFFListAddRule`,
  `cmmFcDenyRuleAddAttribut`, `cmmFcListAddRule`. On `temp == NULL`
  they `return rule;` / `return list;` — caller cannot tell that the
  new attribute/rule was dropped. Config-file parse silently loses
  rules.
- **Recommendation:** Log + propagate a sentinel.

### P2.3 — `client_daemon.c:1631-1649` O(n²) `sprintf(... + strlen(outbuf), ...)` hex-dump
- **File:** `client_daemon.c:1075-1085, 1100-1110, 1631-1651`. Three
  hex-dump loops, each iteration calls `strlen(outbuf)` to find the
  end. Buffer is `CMM_BUF_SIZE * 3 + 1` (~3072 bytes). At DEBUG_INFO,
  ~512 µs per kilobyte spent in `strlen`.
- **Recommendation:** Carry a `len` cursor: `len += snprintf(outbuf+len,
  sizeof(outbuf)-len, ...)`.
- **Optimization:** O10.

### P2.4 — `forward_engine.c` no `fci_close` on partial-failure
- See C15. Channel left in indeterminate state on `fci_write` failure
  mid-batch.

### P2.5 — `conntrack.c:444,453,457,461,1636,1751` insert sites and forward_engine flush walks happen without `ctMutex`
- See P1.2.

### P2.6 — `cmm.c:434` `sscanf("%u", &nf_conntrack_max)` no upper bound
- **File:** `cmm.c:432-438`. `nf_conntrack_max` typed `unsigned int`.
  A user passing `-n 4294967295` results in `ct_stats.current >=
  nf_conntrack_max` never tripping at `conntrack.c:402` → unbounded
  malloc growth.
- **Recommendation:** Cap to a sane value (e.g. 1<<20).

### P2.7 — Hardcoded `CONNTRACK_HASH_TABLE_SIZE`
- See M5. Bucket size constant — fine for a router with O(64k)
  conntracks, poor for an SMB at the upper limit.

### P2.8 — `globalConf` thread-ownership undocumented
- See M6. 30+ fields, no annotation. Adds friction to every later
  defensive fix.

### P2.9 — 8 separate netlink sockets share little code
- See M8. `module_socket.c` (1890 LoC) wraps eight FD-keyed lookups for
  what could be a single multiplexed dispatcher.

### P2.10 — `rtnl.c` no `ENOBUFS` retry
- See P1.8.

### P2.11 — PID-file race re-introduced via `remove`+`O_EXCL`
- **File:** `cmm.c:248-275`. The original `O_EXCL` is correct; the
  `EEXIST` recovery path then `remove`s and re-`open`s with `O_EXCL`.
  Two cmm processes both passing `cmmIsDaemonRunning() == 0`, both
  hitting `EEXIST`, both `remove`-ing, both `open`-ing — last writer
  wins.
- **Recommendation:** `flock(LOCK_EX | LOCK_NB)` on the PID file
  instead of `O_EXCL` + write.

### P2.12 — `timeout.c:90` `strlen(str)` re-evaluated per loop iteration
- **File:** `timeout.c:90`:
  ```c
  for(i=0;i<strlen(str);i++)
  ```
  Only `for(...; strlen()...)` pattern in the daemon. Caller is a
  config-time helper; impact cosmetic.
- **Recommendation:** Hoist `size_t n = strlen(str);` before the loop.
- **Optimization:** O10.

### P2.13 — `ffcontrol.c:1763` config parser uses `strtok` (non-reentrant)
- **File:** `ffcontrol.c:1763`. `strtok` shares static state. The
  parser is single-thread at startup; if any `start()` callback later
  recurses into another parser site, state corrupts silently.
- **Recommendation:** `strtok_r` with explicit save pointer.

## Optimization candidates (O1..O10)

| # | Site | Class | Win |
|---|---|---|---|
| O-A | `cmm.c:555-567` rtnl bufpool | O3/O9 mutex → SPSC ring | Lock-free per-thread fast path; today already lock-free but unsafe (P0.1). |
| O-B | `conntrack.c:408` per-flow `malloc(sizeof(struct ctTable))` | O2 slab | Hot path on every netlink CT_NEW; struct is 200+ bytes. Pre-allocated pool of `nf_conntrack_max` entries avoids glibc arena contention. |
| O-C | `neighbor_resolution.c:585` per-neighbor `malloc` | O2 slab | Same shape as O-B. |
| O-D | `client_daemon.c:1075,1100,1631` and `forward_engine.c:1588-1597` `snprintf+strlen(outbuf)` chains | O10 | Hoist length cursor; eliminates O(n²) in DEBUG_INFO hex-dump. |
| O-E | `itf.c` linear interface lookup `__itf_find` | O7 | Hash by ifindex (ifindex is dense in low integers — direct array works for small N). |
| O-F | `globalConf.{enable,ff_enable,asymff_enable}` | O7 (`__read_mostly`) and `_Atomic` | Hot read on every CT update. |

## Build / static-analysis observations

- The build uses Comcerto-era flags (no `-Wformat=2`, no
  `-Wnull-dereference`, no `-Wshadow`); enabling them flushes out the
  P0.3 / P1.9 cluster automatically.
- `cppcheck --enable=warning` confirms the `strcpy`/`sprintf` hits in
  `module_route.c`, `module_mc4.c`, `module_mc6.c`, `keytrack.c`, plus
  the format-string sites at `cmm.c:201` and `forward_engine.c:1598`.
- No `-fstack-protector-strong` in the daemon's `Makefile.am` — adding
  it turns P0.2 from "silent corruption" into "abort + diagnostic" on
  exploit attempt.

## Recommendations / fix routing

1. **Immediate, ship in next mono-extension patch:**
   - P0.1 rtnl bufpool atomic / spinlock.
   - P0.3 + P1.9 add `format` attribute to `cmm_print` and fix the two
     non-literal call sites.
   - P0.4 rewrite `cmm_crit_err_hdlr` to AS-safe primitives.
   - P1.6 `alt_conf.c` `strncasecmp(...,1)` → `strcasecmp`.
   - P1.4 `cmm_rtnl_listen` NULL-check on `cmm_get_rtnl_buf`.
   - P0.2 / P1.5 `strcpy` → `snprintf` sweep in `module_route.c`,
     `module_mc4.c`, `module_mc6.c`.
2. **Next sprint:**
   - P1.1 `_Atomic` on `globalConf.{ff_enable,asymff_enable,enable}`.
   - P1.7 add log on `setsockopt`/`socket`/`bind` error paths.
   - P1.8 `ENOBUFS` retry + `SO_RCVBUF` enlargement on rtnl listeners.
   - P1.10 `module_lro.c` drop `system()` for `execvp` or sysfs.
   - P2.11 PID file via `flock`.
3. **Architectural / multi-week:**
   - P1.2 conntrack/route table accesses → RCU or single-thread funnel.
   - O-B / O-C slab allocators for `ctTable` and `NeighborEntry`.
   - P2.9 collapse 8 netlink sockets through `module_socket.c` into one
     multiplexed reader.
4. **Build hygiene:**
   - Turn on `-Wformat=2 -Wnull-dereference -Wshadow
     -fstack-protector-strong` in `ASK/cmm/src/Makefile.am`. Surface
     the resulting warnings as a follow-up backlog.

## Out of scope

- The 2,805-line `module_qm.c` and the 1,891-line `module_tunnel.c`
  were grep-scanned but not line-reviewed — both contain extensive
  `snprintf("/sys/class/net/%s/...", ifname)` paths that should be
  audited under the same `IFNAMSIZ` lens as P0.2.
- `keytrack.c` (1,901 lines, IPsec key cache) was not deep-reviewed;
  earlier audit only flagged the safe constant-string `strcpy` cluster
  (`:1215-1308`) and that finding still stands.
- Cross-module ABI between `cmm` and `dpa_app` / `cdx` is covered by
  `AUDIT-fmlib.md` and the cdx audit (kernel side).
