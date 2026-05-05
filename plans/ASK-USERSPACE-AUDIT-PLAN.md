# ASK Userspace Audit Plan

**Goal:** systematic defensive-coding + optimization review of every userspace ASK module that ships in the VyOS LS1046A image.

**Scope (this repo only — `/root/vyos-ls1046a-build`):**

| # | Module | Tree | LoC | Prior audit |
|---|---|---:|---:|---|
| 1 | `fmc` (FMan Configuration tool) | `nxp-fmc/source/` | ~41,719 | none |
| 2 | `fmlib` (`libfm.a` userspace API) | `nxp-fmlib/src+include/` | ~18,192 | none |
| 3 | `cdx` (kernel OOT module) | `ASK/cdx/` | ~44,270 | `ASK-CODE-REVIEW.md §1` (5C 5H 4M, fixes still open) |
| 4 | `cmm` (userspace daemon) | `ASK/cmm/` | ~45,096 | `ASK-CODE-REVIEW.md §2` (2C 5H 7M, fixes still open) |
| 5 | `dpa_app` (PCD applier) | `ASK/dpa_app/` | ~1,147 | `ASK-CODE-REVIEW.md §3` (3H 3M) |
| 6 | `fci` (kernel OOT module) | `ASK/fci/` | ~1,560 | `ASK-CODE-REVIEW.md §4` + `ASK-CODE-QUALITY.md §1–2` (2 CRIT netlink, 2 HIGH) |
| 7 | `auto_bridge` (kernel OOT module) | `ASK/auto_bridge/` | ~1,960 | `ASK-CODE-REVIEW.md §5` + `ASK-CODE-QUALITY.md §3` (1 HIGH, 3 MED) |
| 8 | `libcli` (CLI parser) | `libcli/` | ~4,502 | none |
| 9 | `accel-ppp` (consumer-patched build) | `vyos-1x-*` patches | — | none |

Total addressable surface: ~158,500 LoC + accel-ppp patches.

## Audit method (applied uniformly to each module)

Each module gets one Markdown report `plans/audit/AUDIT-<module>.md` with the format below. The report is self-contained — anyone can pick up a finding and produce a fix patch from the file/line/severity/recommendation triple alone.

### 1. Inventory pass (5 min/module)
- File list, LoC per file, build target, public ABI surface (headers exported, ioctls, netlink families, char devs).
- Origin tag (Mindspeed / Freescale / NXP era from copyright) — informs which legacy patterns to expect.

### 2. Defensive-coding pass
For each TU, grep for and manually review the following classes of defect (the same checklist used in `ASK-CODE-REVIEW.md`):

| Class | What to grep | Why it matters |
|---|---|---|
| **D1. Unchecked allocation** | `\b(k|kz|v|kv)alloc\(`, `\bmalloc\(`, `\bcalloc\(`, `\bnew\b` (C++) | NULL deref on OOM. |
| **D2. Wrong GFP / missing flag** | `kzalloc.*,\s*0\)`, `kmalloc.*,\s*0\)`, `GFP_KERNEL` inside spinlock | sleeping in atomic; silent OOM. |
| **D3. Unchecked `copy_*_user` / `put_user`** | `copy_from_user`, `copy_to_user` — return value not mapped to `-EFAULT` | userspace boundary mishandling (A1 class). |
| **D4. User-supplied size/count not bounded** | `_user(... .* count\|len\|num)` near `kmalloc`/`kzalloc`/loops | OOB write / heap corruption (C1 class). |
| **D5. Refcount leaks** | `dev_get_by_name`, `xfrm_state_lookup`, `bman_pool_new`, `qman_create_*` without matching `_put`/`_destroy` on every path | kernel mem leak. |
| **D6. NULL deref of pointer chain** | `->.*->` after only outer NULL check | oops (C3 class). |
| **D7. Buffer overflow** | `sprintf(`, `strcpy(`, `strcat(`, fixed-size dest with variable-size src | stack/heap overflow (C7 class). |
| **D8. Format-string** | `printf.*%s.*\b(user|input|name)\b` where format is a runtime variable | infoleak / write-where. |
| **D9. Integer overflow on size mul** | `sizeof.*\*.*\bcount\b`, `sizeof.*\*.*->n\b` | wraps to small alloc + big copy. |
| **D10. Race on shared state** | global writes without lock; `volatile` misuse; cross-thread flags w/o atomic/barrier | data corruption / TOCTOU. |
| **D11. Spinlock semantics** | `spin_lock_irqsave` followed by `kmalloc(GFP_KERNEL)` / `mutex_lock` / `msleep` | atomic-context sleep panic (B7 class). |
| **D12. Capability check missing** | ioctl handler without `capable(CAP_NET_ADMIN)` / `CAP_SYS_ADMIN` | privilege bypass (C10 class). |
| **D13. Async-signal-unsafe in handler** | signal handler calling `printf`/`malloc`/non-AS-safe libc | undefined behavior (M9 class). |
| **D14. Format string with non-literal** | `printf(buf)` instead of `printf("%s", buf)` | %n / format injection. |
| **D15. Uninitialized stack data on partial init path** | local struct populated only in one branch then unconditionally read | infoleak / wrong behavior (B3 class). |
| **D16. Macro hygiene** | `#define X(arg)` where `(arg)` looks like a value (no space before paren) | function-like macro instead of value (C8 class). |
| **D17. Error-path resource symmetry** | every `goto err_*` releases everything acquired up to that point | leaks under load. |
| **D18. ABI/UAPI struct sizing** | every ioctl number depends on `sizeof(struct)`; struct edits change `_IOR/_IOW` numbers | silent ABI break (B5 class). |

Each hit is recorded as one finding with: file, line, snippet, severity (P0/P1/P2 same as `SDK-AUDIT-ask26.md`), recommendation, fix-routing (here / fmlib upstream PR / mono extension patch / kernel-side).

### 3. Optimization pass
For each module, evaluate the following nudges:

| Class | Look for | Win |
|---|---|---|
| **O1. Linear search dispatch** | switch/if-chain on enum | indexed table → branch-prediction + smaller icache. |
| **O2. Per-call `malloc`** | hot-path `malloc`/`kmalloc` in flow add/del | slab cache or per-CPU freelist. |
| **O3. Per-call lock+unlock** | hot-path mutex | RCU read or seqlock. |
| **O4. NAPI poll uses `dev_kfree_skb`** | should be `napi_consume_skb` | skb cache reuse (C1 class in SDK audit). |
| **O5. Missing `prefetch()`** | Rx/Tx batch loops | hide DRAM latency. |
| **O6. Hash collision walk unbounded** | linked-list bucket lookup | bound + skiplist (C9 class). |
| **O7. `__read_mostly` missing** | global state never written after init | cache-line dirtying under SMP (C3 in SDK audit). |
| **O8. Tree-vs-stream parser** | full-tree DOM XML parse | SAX walker for big input (`cdx_pcd.xml`). |
| **O9. RTNL buf pool lock** | mutex on SPSC pattern | lock-free ring. |
| **O10. `strlen` in loop** | `for (...; i < strlen(s); ...)` | hoist length. |

### 4. Build / static analysis pass
- `-Wall -Wextra -Wformat=2 -Wnull-dereference -Wstack-usage=4096 -Wshadow -Werror` build sanity (note: NXP code carries `-Wno-*` overrides; surface them explicitly in the report).
- `cppcheck --enable=all --inconclusive --quiet` for C/C++ modules.
- `clang-tidy` with `bugprone-*,performance-*,clang-analyzer-*` for fmc.
- Kernel-side: `make C=2 W=1` (sparse) on cdx/fci/auto_bridge after applying.

### 5. Output
Per module:
- `plans/audit/AUDIT-<module>.md` — the report (severity-sorted findings + optimization candidates).
- A `Findings:` summary block at top.

After all modules, a roll-up:
- `plans/audit/AUDIT-SUMMARY.md` — combined severity table + prioritized fix sequencing across all modules.

## Execution sequence

Ordered by largest blast-radius (modules whose ABI mismatch has already burned production) and by gap severity (zero coverage today).

| Step | Module | Why this position |
|---|---|---|
| 1 | `fmc` | Largest C++ corpus (~42k LoC). Zero coverage. Owns the XML parse path that produces every byte sent into kernel via `FM_PCD_IOC_*`. ABI surface that produced last quarter's `dpa_app` SIGSEGV. |
| 2 | `fmlib` | ~18k LoC. Zero coverage. Owns every userspace ioctl wrapper. Direct producer-side dependency (UAPI sizes match `fixes/112`). |
| 3 | `cdx` | ~44k LoC kernel OOT, audited but fixes open — the kernel-side panic surface (CAAM SEC, IPsec, ehash). |
| 4 | `cmm` | ~45k LoC userspace daemon, audited but fixes open. |
| 5 | `fci` | ~1.5k LoC, kernel OOT, two known CRITICAL netlink-validation defects. |
| 6 | `auto_bridge` | ~2k LoC, one HIGH NULL-deref, otherwise small. |
| 7 | `dpa_app` | ~1.1k LoC, audited at HIGH/MED. Quick. |
| 8 | `libcli` | ~4.5k LoC, zero coverage — ASK CLI subsystems use it. |
| 9 | `accel-ppp` patches | Patch set audit (vs. mainline accel-ppp). Smallest scope last. |

## Scope discipline (do not violate)

- Every commit lands **only** in `/root/vyos-ls1046a-build` (consumer repo). Per `.clinerules/05-workspace-layout.md`, this is Chain-2 territory.
- The producer repo `/root/lts_6.6_ls1046a` is read-only for this audit; only kernel-API-drift fixes that surface during `cdx`/`fci`/`auto_bridge` review may be hoisted there.
- All audit reports go under `plans/audit/` so they don't pollute the existing top-level `plans/` index.
- No code changes in this audit phase — we produce the reports and the prioritized backlog. Implementation is a follow-up sprint per the prioritization in `AUDIT-SUMMARY.md`.

## Deliverables

- [ ] `plans/audit/AUDIT-fmc.md`
- [ ] `plans/audit/AUDIT-fmlib.md`
- [ ] `plans/audit/AUDIT-cdx.md` (refresh of existing review with the D1..D18/O1..O10 lens)
- [ ] `plans/audit/AUDIT-cmm.md` (refresh)
- [ ] `plans/audit/AUDIT-fci.md` (refresh)
- [ ] `plans/audit/AUDIT-auto_bridge.md` (refresh)
- [ ] `plans/audit/AUDIT-dpa_app.md` (refresh)
- [ ] `plans/audit/AUDIT-libcli.md`
- [ ] `plans/audit/AUDIT-accel-ppp.md`
- [ ] `plans/audit/AUDIT-SUMMARY.md` (roll-up + sequencing)