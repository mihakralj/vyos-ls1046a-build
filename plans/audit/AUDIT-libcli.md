# AUDIT-libcli

Defensive-coding & optimization audit of the libcli sources shipped at
`libcli/` (`libcli.c` 3,660 lines, `libcli.h` 842 lines, plus
`clitest.c` test harness — 4,502 LoC total). libcli is the open-source
single-connection-per-instance CLI library used by ASK CLI tooling
(NXP `dpa_app`, `cmm`, etc.). The library does **not** open a PTY: it
performs in-band TELNET option negotiation on a caller-supplied socket
file descriptor.

The audit was driven by full-text grep across `libcli.c`/`libcli.h`,
hand review of the loop driver (`cli_loop`, lines 1067–1408), the
print/format layer (`_print`, `cli_print`, `cli_error`, lines
1855–1935), allocator-and-format helpers (`vasprintf`/`asprintf` —
libcli.c:51–76), the command-tree walker
(`cli_int_locate_command` — 2872–2919) and TELNET handling
(libcli.c:1248–1264). Mappings to the brief's defect classes
(D1/D7/D8/D10/D14/D17) and optimization classes (O1/O7) are noted on
each finding.

No source files were modified.

## Findings summary

| Severity | Count |
|---|---|
| P0 | 4  |
| P1 | 6  |
| P2 | 4  |

Plus 4 optimization candidates.

## Module inventory

- LoC: 3,660 (`libcli.c`) + 842 (`libcli.h`) + test = 4,502.
- Public ABI declared in `libcli.h` (selection):
  - lifecycle: `cli_init`, `cli_done`, `cli_loop(struct cli_def*, int sockfd)`
  - command tree: `cli_register_command`, `cli_unregister_command`,
    `cli_register_filter`, `cli_register_optarg`
  - I/O: `cli_print`, `cli_bufprint`, `cli_error`, `cli_vabufprint`
  - auth: `cli_set_auth_callback`, `cli_set_enable_callback`,
    `cli_allow_user`, `cli_allow_enable`, `cli_deny_user`
  - knobs: `cli_telnet_protocol`, `cli_set_idle_timeout`,
    `cli_regular`, `cli_print_callback`
- External deps: POSIX (`<sys/socket.h>`, `<poll.h>`/`<sys/select.h>`,
  `<regex.h>`, `<crypt.h>`), libc (`vsnprintf`, `strcasecmp`,
  `realloc`).
- Single-connection model: `struct cli_def` is per-connection; no
  `static` mutable state in `libcli.c` apart from five constant
  delimiter strings (`DELIM_OPT_START`/`_END`/`DELIM_ARG_START`/`_END`/
  `DELIM_NONE` — libcli.c:182–186), which are read-only. Multi-instance
  use is therefore safe **provided** each thread owns its own
  `struct cli_def` and its own `sockfd`. See P2.3 for the one
  global-scope concern (`int read`/`int write` shadows in
  libcli.c:43–49).
- Build artefacts: hand-written Makefile, ships `libcli.so.1.10.8` and
  static `libcli.a`; ABI version 1.10.

## P0 findings (security / heap-corruption / DoS)

### P0.1 — Public variadic prototypes lack `format(printf,…)` attributes
- **Files:**
  - `libcli/libcli.c:1923-1935` (`cli_print`, `cli_error` definitions).
  - `libcli/libcli.c:1855-1909` (`_print`, calls `vasprintf`).
  - `libcli/libcli.h:711` (`cli_print`), `:716` (`cli_error`),
    plus `cli_bufprint`, `cli_vabufprint` declarations.
- **Defect class:** D8/D14 — non-literal format strings reach
  `vsnprintf` via `_print → vasprintf`. Without
  `__attribute__((format(printf, 2, 3)))` the compiler does not warn
  consumers that write `cli_print(cli, user_string)`.
- **Trigger:** A consumer that passes user-derived text as the format
  argument enables classic `%n`/`%s` format-string attacks (OOB read
  or, with `%n`, OOB write into `_print`'s rebuilt buffer).
- **Recommendation:** add `__attribute__((format(printf, 2, 3)))` to
  every variadic prototype in `libcli.h` and rebuild ASK consumers
  with `-Wformat=2 -Werror=format-security`.
- **Fix routing:** Defensive-fixes patch (header-only; preserves ABI).
  Worth upstreaming.

### P0.2 — `cli_loop` keeps `cli->client = fdopen(sockfd, "w+")` and never `fclose`s on exit
- **File:** `libcli/libcli.c:1095-1101` (the `fdopen`),
  libcli.c:1238-1242 (`if (n == 0) { l = -1; break; }`),
  exit path libcli.c:1404+ — the function returns without
  `fclose(cli->client)`.
- **Defect class:** D5 (resource leak) + D17 (error-path symmetry).
  `fdopen` ownership is ambiguous: `fclose` on the resulting `FILE*`
  *would* close `sockfd`, which the caller still owns, so the current
  code intentionally never `fclose`s — but that leaks the `FILE*`
  itself (the buffered stdio buffers and `_IO_FILE` struct).
- **Trigger:** Long-lived processes that call `cli_loop` per
  connection (the ASK `cmm` daemon pattern) leak one `FILE*` per
  disconnect.
- **Recommendation:** `dup(sockfd)` before `fdopen` so the duplicate
  fd can be `fclose`d safely without touching the caller's
  `sockfd`. Add `fclose(cli->client); cli->client = NULL;` to the
  exit path.
- **Fix routing:** Defensive-fixes patch. Behaviour change — confirm
  ASK consumers do not assume `cli->client` survives the call.

### P0.3 — TELNET sub-negotiation (IAC SB … IAC SE) is not parsed
- **File:** `libcli/libcli.c:1248-1264`
- **Snippet:**
  ```c
  if (c == 255 && !is_telnet_option) {       // IAC
      is_telnet_option++;
      continue;
  }
  if (is_telnet_option) {
      if (c >= 251 && c <= 254) {            // WILL/WONT/DO/DONT
          is_telnet_option = c;
          continue;                          // swallow next byte
      }
      if (c != 255) {                        // anything else
          is_telnet_option = 0;
          continue;
      }
      is_telnet_option = 0;                  // IAC IAC literal 0xFF
  }
  ```
  No state for `IAC SB <opt> … IAC SE` (250…240). RFC 854/855 require
  the receiver to discard sub-negotiation payload until `IAC SE`.
- **Defect class:** D7/D14 — TELNET protocol violation; arbitrary
  payload bytes get treated as command input.
- **Trigger:** Any standards-conforming telnet client that sends NAWS
  (`IAC SB NAWS w1 w2 h1 h2 IAC SE`, RFC 1073) or terminal-type
  negotiation. The 5-byte NAWS payload becomes injected keystrokes;
  if any byte is `0x08` (BS) it scrambles the command buffer; if it
  is `0x0d` (CR) it executes whatever is currently typed. Trivially
  reachable from PuTTY/Linux `telnet` defaults.
- **Recommendation:** small state machine: IDLE → IAC →
  CMD/OPT/SB; in SB consume bytes until `IAC SE`. ~20 LoC patch.
- **Fix routing:** Defensive-fixes patch. Worth upstreaming.

### P0.4 — Per-byte `read(sockfd, &c, 1)` with brittle errno handling
- **File:** `libcli/libcli.c:1228-1235`
- **Snippet:**
  ```c
  if ((n = read(sockfd, &c, 1)) < 0) {
      if (errno == EINTR) continue;
      perror("read");
      l = -1;
      break;
  }
  ```
  `read` is the libcli internal shim
  `int read(int, void*, unsigned int) { return recv(fd, buf, count, 0); }`
  (libcli.c:43-45). On `EAGAIN`/`EWOULDBLOCK` the loop exits — any
  non-blocking caller is DoS-disconnected on the first idle interval.
  No retry on `ECONNRESET`-style spurious errors either.
- **Defect class:** D17 + O3 (per-byte syscall).
- **Recommendation:** (a) treat `EAGAIN`/`EWOULDBLOCK` like `EINTR`;
  (b) read up to 64 B into a small ring and drain byte-by-byte.
  Cuts per-keystroke syscalls ~10×.
- **Fix routing:** Defensive-fixes patch (treat as P0 because the
  current code disconnects any non-blocking consumer).

## P1 findings

### P1.1 — `_write` retry loop never propagates `EPIPE`/`ECONNRESET`
- **File:** `libcli/libcli.c:188-202`
- **Snippet:**
  ```c
  static ssize_t _write(int fd, const void *buf, size_t count) {
    ...
    while (count != written) {
      thisTime = write(fd, (char*)buf + written, count - written);
      if (thisTime == -1) {
        if (errno == EINTR) continue;
        else return written ? written : thisTime;
      } else
        written += thisTime;
    }
    ...
  }
  ```
  Every caller (`cli_loop`, `show_prompt` libcli.c:1056-1066) ignores
  the return — `len += write(...)` style. After remote close the loop
  keeps writing to a dead socket until the next `read()` returns 0.
- **Defect class:** D17.
- **Recommendation:** propagate errors; if `_write` returns < 0 set
  `l = -1` and break.
- **Fix routing:** Defensive-fixes patch.

### P1.2 — `join_words` builds with chained `strcat` (O(n²))
- **File:** `libcli/libcli.c:855-877`
- **Snippet:**
  ```c
  p = malloc(len + 1);
  ...
  for (i = 0; i < argc; i++) {
    if (i) strcat(p, " ");
    strcat(p, argv[i]);
  }
  ```
  Each `strcat` rescans `p` from the start; cost is O(N²) for a
  command line of N total bytes. Also no overflow check on `len`
  accumulation (D7).
- **Defect class:** O7 + D7.
- **Recommendation:** outgoing-cursor `memcpy`; check
  `__builtin_add_overflow` on `len`.

### P1.3 — `realloc` failure leaks the original buffer
- **File:** `libcli/libcli.c:2647-2652`
- **Snippet:**
  ```c
  if ((tptr = (char *)realloc(cmdline, oldlen + 1 + 1 + wordlen + 1 + 1))) {
      strcat(tptr, " ");
      strcat(tptr, quoteChar);
      strcat(tptr, word);
      strcat(tptr, quoteChar);
  }
  return tptr;          // tptr == NULL on failure; cmdline leaked
  ```
- **Defect class:** D1 + D5 + O7 (chained `strcat` again).
- **Recommendation:** the standard idiom —
  `tmp = realloc(p, sz); if (!tmp) { free(p); return NULL; } p = tmp;`

### P1.4 — `_print` realloc growth is O(n²) and never shrinks
- **File:** `libcli/libcli.c:1862-1879`
- **Snippet:**
  ```c
  if (cli->buffer) {
      int len = strlen(cli->buffer);          // O(len) per print
      unsigned size = len + n + 1;
      if (size > cli->buf_size) {
          char *buf = realloc(cli->buffer, size);   // grows by n each time
          ...
      }
      memcpy(cli->buffer + len, p, n);
  }
  ```
  Each call walks the entire buffer with `strlen`, then `realloc`s
  by exactly the extra needed — quadratic memcpy across the lifetime
  of a long output (`show running-config` style).
- **Defect class:** O7.
- **Recommendation:** track `buf_used` next to `buf_size`; double on
  growth (`new = max(2*buf_size, size)`); avoid the per-call
  `strlen`.

### P1.5 — `cli_int_locate_command` linear scan + double `strncasecmp` per node
- **File:** `libcli/libcli.c:2872-2919`
- **Snippet:**
  ```c
  for (c = commands; c; c = c->next) {
      if (c->command_type != command_type) continue;
      if (cli->privilege < c->privilege) continue;
      if (strncasecmp(c->command, stage->words[start_word], c->unique_len)) continue;
      if (strncasecmp(c->command, stage->words[start_word], strlen(stage->words[start_word]))) continue;
      ...
      rc = cli_int_locate_command(cli, c->children, ...);
  }
  ```
  Linked-list walk for every word of every command on every
  keystroke (tab-completion calls the same locator). For ASK
  consumers that register hundreds of commands per mode this is the
  dominant per-keystroke cost.
- **Defect class:** O1 (linear cmd-tree search — the brief's named
  optimisation).
- **Recommendation:** sort `cli->commands` and `cmd->children` once
  at registration and switch to binary search bucketed by
  `command_type`/privilege, OR keep a per-mode hash keyed by the
  lowercase first byte (partial matches still need scan but bucket
  is ~20× smaller).
- **Fix routing:** Optimization patch.

### P1.6 — TELNET negotiation string is empty
- **File:** `libcli/libcli.c:1079-1084`
- **Snippet:**
  ```c
  static const char *negotiate =
      ""
      ""
      ""
      "";
  _write(sockfd, negotiate, strlen(negotiate));
  ```
  All four literals are empty — the negotiation `_write` sends 0
  bytes. Historically these contained the canonical IAC sequences
  `IAC WILL ECHO`, `IAC WILL SUPPRESS_GO_AHEAD`,
  `IAC DO SUPPRESS_GO_AHEAD`, `IAC DO ECHO`. With no `WILL ECHO`
  many telnet clients leave local echo on; users see doubled
  characters during password entry, and password bytes leak to the
  terminal scrollback.
- **Defect class:** D14 (silent regression of advertised
  behaviour) + D17.
- **Trigger:** any consumer relying on `cli_telnet_protocol(cli, 1)`
  to mute local echo during password prompts.
- **Recommendation:** restore the canonical four IAC sequences:
  `\xff\xfb\x03` (WILL SGA), `\xff\xfb\x01` (WILL ECHO),
  `\xff\xfd\x03` (DO SGA), `\xff\xfd\x01` (DO ECHO).
- **Fix routing:** Defensive-fixes patch. Looks like a copy-paste
  loss during a previous rebase — the four empty strings preserve
  the literal-concatenation shape of the original constant.

## P2 findings

### P2.1 — `setbuf(cli->client, NULL)` (libcli.c:1112) defeats stdio buffering
- Combined with P0.4, every `fprintf(cli->client, "%s\r\n", p)`
  becomes a syscall per token.
- **Recommendation:** keep `setbuf(NULL)` only across the password
  prompt; switch to `setvbuf(_IOLBF, 256)` afterwards.
- **Fix routing:** Optimization patch.

### P2.2 — `unsigned size = len + n + 1;` in `_print` silently narrows on 64-bit
- **File:** `libcli/libcli.c:1865`. `len` is `int`, `n` is `int`,
  result stored as `unsigned`. Practically unreachable (>4 GiB
  output) but a defect class D7.
- **Recommendation:** make `len`/`size` `size_t`; add overflow check.

### P2.3 — Unconditional `read`/`write` shims at libcli.c:42-50
- **File:** `libcli/libcli.c:42-50`
  ```c
  int read(int fd, void *buf, unsigned int count) {
      return recv(fd, buf, count, 0);
  }
  int write(int fd, const void *buf, unsigned int count) {
      return send(fd, buf, count, 0);
  }
  ```
  These were likely meant to be `#ifdef WIN32`-guarded (Winsock
  emulation). On Linux they shadow libc's `read`/`write` for any
  consumer that links libcli statically — `recv`/`send` fail with
  `ENOTSOCK` on a regular fd (e.g. `read(STDIN_FILENO, …)`).
- **Defect class:** D14/D17 — silent ABI deviation.
- **Recommendation:** wrap the block in `#ifdef WIN32 … #endif`.
- **Fix routing:** Defensive-fixes patch.

### P2.4 — History not cleared on `cli_loop` exit
- **File:** libcli.c:1077 calls `cli_free_history(cli)` at *start*,
  not at exit. Long-running daemons that recycle the same
  `struct cli_def` carry the previous user's command history into
  the next session — low-grade information disclosure.
- **Recommendation:** `cli_free_history(cli)` in the exit path too.

## Optimization candidates

| ID | Where | Class | Win |
|---|---|---|---|
| O1 | `cli_int_locate_command` libcli.c:2872 | O1 | per-keystroke command lookup. Sort children + bsearch, or first-byte bucket hash. |
| O7a | `_print` libcli.c:1862 | O7 | quadratic buffer growth — switch to doubling allocator + cache `buf_used`. |
| O7b | `join_words` libcli.c:855 / append-quoted-word libcli.c:2647 | O7 | replace `strcat` chain with single `memcpy` walker. |
| O3  | `read(sockfd, &c, 1)` libcli.c:1228 | O3 | per-byte syscall — buffer 64 B at a time. |

## Recommendations & fix routing

1. **`02-libcli-defensive-fixes.patch`** (single local patch):
   - P0.1: `format(printf,…)` attributes in `libcli.h`.
   - P0.2: `dup(sockfd)` before `fdopen` + `fclose` on exit (or
     document ownership transfer).
   - P0.3: SB/SE state machine in `cli_loop`.
   - P0.4: `EAGAIN` retry + small read buffer.
   - P1.1: propagate `_write` errors.
   - P1.3: realloc-failure leak in append-quoted-word.
   - P1.6: restore IAC negotiation literals.
   - P2.3: gate `read`/`write` shims with `#ifdef WIN32`.
   - P2.4: free history on exit.
2. **`03-libcli-perf.patch`** (optimization):
   - O1 (P1.5): bsearch-able sorted children, or first-byte bucket.
   - O7a (P1.4): doubling allocator for `cli->buffer`.
   - O7b (P1.2): `memcpy`-based `join_words` and append helper.
   - O3 (P0.4 secondary): read-buffer ring.
3. **Upstream candidates:** P0.1, P0.3, P1.6 and the format
   attributes are general-purpose fixes worth proposing to the
   upstream `dparrish/libcli` repository. P0.2's ownership change
   should not be upstreamed without an ABI-version bump.
4. **Audit follow-up:** sweep ASK consumers for `cli_print`/`cli_error`
   calls that pass user input as the format argument; once the
   format attribute lands a `-Wformat=2` build flags any survivors.
