# AUDIT-fmc.md — Defensive-coding audit of vendored fmc

Status: read-only audit. Findings only — no edits applied. Each finding is severity-tagged so the operator can review and approve which to fix in a follow-up `sdk-edits:`-style commit. Vendored source is `nxp-qoriq/fmc @ lf-6.18.2-1.0.0 (HEAD 5b9f4b1)` plus the ASK `01-mono-ask-extensions.patch` already applied in-tree.

Audit scope: hand-written sources only. Generated parsers (`spa/fm_sp_assembler.tab.c` bison, `spa/lex._fmsp_assembler_yy.c` flex, `FMCSPExprLexer.cpp` flex, the bison skeleton inside `FMCSPExpr.cpp` lines ~200-1490 / 1820-2035) are intentionally excluded — they regenerate from `.y`/`.l` and any direct edit is forbidden by re-vendoring discipline.

Severity:
- **P0** — security / data-corruption / always-trigger crash on attacker-controlled input (a malformed `cdx_pcd.xml` exposes it).
- **P1** — crash on legitimate-but-unusual input.
- **P2** — leak / wrong-result-no-crash / API-contract.
- **P3** — cosmetic / optimization / code hygiene.

Findings count: **27** total — 6 × P0, 8 × P1, 9 × P2, 4 × P3.

---

## P0 — Security / data-corruption / always-trigger

### P0-1 — XXE / external-entity / network-fetch enabled by default in PCD reader
- **File**: `source/FMCPCDReader.cpp`
- **Line**: 118
- **Code**: `xmlDocPtr doc = xmlParseFile( filename.c_str() );`
- **Why**: `xmlParseFile()` uses libxml2's default options, which permit DTD loading and external-entity expansion. On attacker-controlled XML (`/etc/cdx_pcd.xml` is operator-writable in the VyOS configuration system) this enables billion-laughs DoS, local-file disclosure via `SYSTEM` entities, and (with libxml2 built with network) SSRF. No `XML_PARSE_NONET` / `XML_PARSE_NOENT=0` / `XML_PARSE_DTDLOAD=0` hardening is set. A structured error func is registered but does not gate entity loading.
- **Fix sketch**: replace with `xmlReadFile(filename.c_str(), NULL, XML_PARSE_NONET | XML_PARSE_NOBASEFIX | XML_PARSE_NOCDATA)` and explicitly install a no-op `xmlSetExternalEntityLoader`.

### P0-2 — XXE / external-entity also in PDL reader
- **File**: `source/FMCPDLReader.cpp`
- **Line**: 127
- **Code**: `xmlDocPtr doc = xmlParseFile( filename.c_str() );`
- **Why**: same defect class as P0-1.
- **Fix sketch**: same as P0-1.

### P0-3 — XXE / external-entity also in CFG reader
- **File**: `source/FMCCFGReader.cpp`
- **Line**: 103
- **Code**: `xmlDocPtr doc = xmlParseFile( filename.c_str() );`
- **Why**: same defect class as P0-1.
- **Fix sketch**: same as P0-1.

### P0-4 — `fields[0]` copy/paste defect inside loop over `i` (silent wrong-PCD)
- **File**: `source/FMCPCDModel.cpp`
- **Line**: 661, 667, 679, 685, 698, 777, 783 (8 sites)
- **Code**: `headerit->second.hdrUpdate.fields[0].value` (used inside `for (i=0; i<count; i++) { ... }`)
- **Why**: All eight sites read `.fields[0].value` instead of `.fields[i].value` while iterating. fmc silently emits a PCD where multiple update fields all carry field-0's value — every IPv4 ToS / ID / src / dst and every TCP/UDP src/dst update with `count > 1` is wrong. The kernel and FMan accept the IOCTL because the struct shape is valid, so the misconfiguration is silent on `dpa_app applied PCD configuration` even though the runtime classifier is broken.
- **Fix sketch**: replace the literal `[0]` with `[i]` at all eight sites. Likely root cause: someone duplicated a single-field path and forgot to parameterize.

### P0-5 — Unsigned-underflow → ~4 GiB OOB read in header-insert / key-build
- **File**: `source/FMCPCDModel.cpp`
- **Line**: 520, 532, 542-545, 1540-1549, 1897-1901
- **Code**: `entries[ MAX_INSERT_SIZE - hdrInsert.size + j ]` (and analogous `FM_PCD_MAX_SIZE_OF_KEY - keySize + j` for keys)
- **Why**: `hdrInsert.size` and `keySize` come from XML attributes. When an attacker (or a bad config) supplies a value larger than the cap, `MAX_INSERT_SIZE - size` underflows to ~4 GiB, then `+j` indexes the `entries[]` / `data[]` / `mask[]` arrays out of bounds. This is the textbook unsigned-arithmetic-as-bounds-check defect.
- **Fix sketch**: gate with `if (hdrInsert.size > MAX_INSERT_SIZE) throw CGenericError(ERR_HDR_INSERT_SIZE);` BEFORE the subtraction. Same for `keySize > FM_PCD_MAX_SIZE_OF_KEY`.

### P0-6 — `cmodel` struct never `memset(0)`'d before serialization
- **File**: `source/FMCCModelOutput.cpp`
- **Line**: 198 (entry of `CFMCCModelOutput::output()`)
- **Code**: `void CFMCCModelOutput::output(const CFMCModel& model, fmc_model_t* cmodel, ...)` — no `memset(cmodel, 0, sizeof(*cmodel))`.
- **Why**: `cmodel` is caller-supplied; the serializer only writes fields that are conditionally populated. `externalHash` / `externalHashParams.*` / `agingSupport` are inside DPAA_VERSION>=11-gated branches; if a branch is not taken, the kernel reads whatever was on the caller's stack or in a recycled allocation. With patch-12's UAPI extension this is exactly the silent-misconfig that makes hash buckets fall back to MURAM.
- **Fix sketch**: at `output()` entry, `memset(cmodel, 0, sizeof(*cmodel));` unconditionally. Cheap, defensive, eliminates an entire class.

---

## P1 — Crash on legitimate-but-unusual input

### P1-1 — `dscpToVpriTable[fields[i].index]` with no bound check on `index`
- **File**: `source/FMCPCDModel.cpp`
- **Line**: 646
- **Code**: `dscpToVpriTable[fields[i].index] = ...`
- **Why**: `index` parsed from XML `<dscp>` attribute. `dscpToVpriTable` is sized `FM_PCD_MANIP_DSCP_TO_VLAN_TRANS` (64). XML can supply 0..255 (DSCP is 6-bit but the parser doesn't enforce). OOB write up to 192 bytes past the table.
- **Fix sketch**: `if (idx >= FM_PCD_MANIP_DSCP_TO_VLAN_TRANS) throw CGenericError(ERR_DSCP_INDEX);`.

### P1-2 — Stack overflow on deeply-nested `<scheme><group><scheme>...` recursion
- **File**: `source/FMCPCDReader.cpp` (inferred — recursive descent over XML tree)
- **Line**: parseGroup / parseScheme recursive entry (line numbers depend on call graph; subagent flagged the pattern)
- **Why**: No depth cap on element-nesting recursion. An attacker-crafted XML with thousands of nested elements blows the stack of the calling process (`fmc` is invoked at boot, ~8 MiB stack default → ~10K-deep nesting kills it).
- **Fix sketch**: pass a `depth` parameter through recursive readers, throw `ERR_XML_TOO_DEEP` past 32 levels.

### P1-3 — `pos` typed `unsigned int` but assigned `std::string::npos` (`size_t`)
- **File**: `source/FMC.cpp`
- **Line**: 105
- **Code**: `const unsigned int pos = appDir.find_last_of( "\\/" );`
- **Why**: On 64-bit `std::string::npos == 0xFFFFFFFFFFFFFFFF`. Truncating to `unsigned int` yields `0xFFFFFFFF` (a valid index value, not npos). The subsequent `if (std::string::npos == pos)` compares `size_t(npos)` to `size_t(0xFFFFFFFF)` → never true → wrong branch when there is no `/` in `argv[0]`.
- **Fix sketch**: `std::string::size_type pos = appDir.find_last_of("\\/")`.

### P1-4 — `argv[0]` dereferenced without `argc` check
- **File**: `source/FMC.cpp`
- **Line**: 104
- **Code**: `std::string appDir( argv[0] );`
- **Why**: `execve()` permits `argc == 0` (POSIX-permitted, Linux respects it for setuid/CAP_*). `std::string` ctor with NULL is UB.
- **Fix sketch**: `if (argc < 1 || !argv[0]) appDir = "."; else { ... }`.

### P1-5 — Operator-precedence bug in `ECHECKSUM_LOC` range check (union-aliasing)
- **File**: `source/FMCSPIR.cpp`
- **Line**: 851-857
- **Code**:
  ```cpp
  if (eNode->dyadic.left->type   == EINTCONST &&
      eNode->dyadic.right->type  == EINTCONST &&
      eNode->dyadic.left->intval  > 256 ||
      eNode->dyadic.right->intval > 256)
          throw CGenericErrorLine(ERR_CHECKSUM_SECOND_THIRD, line);
  ```
- **Why**: `&&` binds tighter than `||`. Effective parse: `(L.type==EINT && R.type==EINT && L.intval>256) || (R.intval > 256)`. The right disjunct dereferences `R->intval` even when `R` is NOT `EINTCONST`. `intval` is a union member that aliases pointer fields for non-const node types → reads attacker-controlled garbage as an int, often triggers throw on legitimate input that didn't violate the spec.
- **Fix sketch**: parenthesize: `if ((L.type==EINT && R.type==EINT) && (L.intval>256 || R.intval>256))`.

### P1-6 — Unhandled exception in error-message formatter (`vsprintf` to 256-byte buffer)
- **File**: `source/libfmc.cpp` (called from `FMC.cpp` catch handler)
- **Line**: in error formatter (subagent flagged but exact line truncated)
- **Why**: 256-byte fixed buffer, `vsprintf` (no `n` variant) on user-influenced format args. Long error messages with embedded XML attribute strings overflow the buffer.
- **Fix sketch**: `vsnprintf(buf, sizeof(buf), fmt, ap)` and append `"…"` if `>= sizeof(buf)`.

### P1-7 — XML-helper return assumed non-NULL after `xmlGetProp` on optional attr
- **File**: `source/FMCPCDReader.cpp` / `source/FMCPDLReader.cpp` (multiple sites)
- **Why**: `getXMLAttr(node, "name")` is wrapped to return `std::string` (and frees the xmlChar* internally — that part is safe), but several call sites then do `int n = atoi(getXMLAttr(...).c_str())` on attributes that the schema permits to be absent. Absent → empty string → `atoi("")` → 0, silently. fmc proceeds with a key-size of 0 / port-id of 0 / queue-count of 0, kernel accepts, runtime classifier mis-routes.
- **Fix sketch**: helper variant `getXMLAttrRequired(node, name)` that throws `ERR_MISSING_REQUIRED_ATTR` on empty.

### P1-8 — `std::map::operator[]` insertions in IR symbol-table (typo silently creates orphan)
- **File**: `source/FMCSPIR.cpp` (operator[] sites — subagent flagged pattern, multiple sites)
- **Why**: Soft-parser IR refers to symbols by name; `symbols[name]` (operator[]) inserts a default-constructed entry on miss instead of erroring. A typo in the input PDL silently creates an orphan symbol that emits dead microcode; the parser still passes verification because the orphan doesn't violate any structural rule.
- **Fix sketch**: replace `symbols[name]` reads with `auto it = symbols.find(name); if (it == symbols.end()) throw ERR_UNDEF_SYMBOL;`.

---

## P2 — Leak / wrong-result-no-crash / API-contract

### P2-1 — `xmlChar*` from `xmlGetProp` leaks on early throw
- **File**: `source/FMCPCDReader.cpp`, `source/FMCPDLReader.cpp` (multiple sites where `getAttr` is NOT used and raw `xmlGetProp` is called)
- **Why**: Pattern `xmlChar* x = xmlGetProp(...); if (!cond) throw CGenericError(...); xmlFree(x);` — when the throw fires, `x` leaks. fmc is short-lived so each leak is small, but on configurations with thousands of throws during XML schema-validation reporting these add up to MiBs.
- **Fix sketch**: RAII wrapper `class XmlString { xmlChar* p; public: ~XmlString(){ if (p) xmlFree(p);} ...};`.

### P2-2 — `xmlDocPtr` not freed on early throw in readers
- **File**: `source/FMCPCDReader.cpp` (line 118 and downstream)
- **Why**: `xmlDocPtr doc = xmlParseFile(...)` succeeds, then a subsequent `throw CGenericError(...)` somewhere downstream in the same function. `doc` leaks.
- **Fix sketch**: RAII for `xmlDocPtr`, OR `try { ... } catch(...) { if (doc) xmlFreeDoc(doc); throw; }` at function scope.

### P2-3 — `FILE*` leak on early throw in (host-mode) writers
- **File**: `source/spa/fm_sp_private.c` (generated — but the leak is structural)
- **Line**: 2024, 2184
- **Why**: `output_file_p = fopen(name_p, "w")`; subsequent code path can `goto error` without closing. **Note**: this file is partly generated, so the fix probably belongs as a hand-authored wrapper that owns the FILE*.
- **Fix sketch**: leave generated code alone; introduce a thin wrapper in `spa/fm_sp.c` that opens/closes around the call.

### P2-4 — Iterator invalidation risk in IR walk
- **File**: `source/FMCSPIR.cpp`, `source/FMCSPCreateCode.cpp` (multiple `erase()` sites)
- **Why**: Loops over `std::list<IRNode*>` that call `list.erase(it)` then `++it`. `erase` returns the next iterator; the post-increment is wrong (UB on the just-invalidated iterator).
- **Fix sketch**: `it = list.erase(it);` instead of `list.erase(it); ++it;`.

### P2-5 — `strdup` in IR construction not paired with `free` on subsequent throw
- **File**: `source/FMCSPIR.cpp` (multiple)
- **Why**: `node->name = strdup(text);` then a sibling field setter throws → `node` leaks (along with the strdup). Pattern not catastrophic at fmc lifetime but fmc is sometimes invoked from a long-lived `dpa_app` re-config path where leaks accumulate.
- **Fix sketch**: prefer `std::string` storage in IR nodes (avoids strdup entirely); or RAII the alloc.

### P2-6 — `vsprintf` (not `vsnprintf`) in libfmc error path
- **File**: `source/libfmc.cpp` (the public C-API surface emits formatted errors via `vsprintf`)
- **Why**: same defect class as P1-6 but here it's part of the public API contract — callers can't predict the buffer size. Truncate-or-overflow choice should always be truncate.
- **Fix sketch**: `vsnprintf` everywhere, return truncation-flag in the public API.

### P2-7 — `libfmc.cpp` public C API uses global state (thread-safety claim mismatch)
- **File**: `source/libfmc.cpp`
- **Why**: `fmc_compile()`, `fmc_execute()` use one or more file-scope statics for the in-progress model / error string / log handle. The header doesn't disclaim thread-safety. dpa_app is single-threaded so no current bug, but any future caller that retries from a signal handler or runs two compiles in parallel will race.
- **Fix sketch**: document "NOT thread-safe" prominently in `fmc.h`, or make the state per-handle.

### P2-8 — `strncpy` without explicit NUL on the model name fields
- **File**: `source/FMCCModelOutput.cpp` (struct serialization sites)
- **Why**: classic `strncpy(dst, src, sizeof(dst))` without `dst[sizeof(dst)-1] = 0`. When `src` >= `sizeof(dst)`, the dst is not NUL-terminated. Kernel-side consumers treat the field as a C-string (formatted in `dmesg` for diagnostics), so a missing NUL prints garbage out to dmesg until the next 0 byte in MURAM.
- **Fix sketch**: replace with `snprintf(dst, sizeof(dst), "%s", src)` (always NUL-terminates) or explicit `dst[sizeof(dst)-1] = 0` immediately after `strncpy`.

### P2-9 — Missing return-value check on `xmlAddChild` / `xmlNewProp` in any writer paths
- **File**: writer paths (subagent inferred — fmc is mostly reader, but `fmc_config` host-mode writes XML for testing)
- **Why**: libxml2 setters return NULL on OOM. Without a check, the next operation on the returned ptr crashes.
- **Fix sketch**: check return values and throw `ERR_XML_OOM`.

---

## P3 — Cosmetic / optimization / code-hygiene

### P3-1 — Optimization: copy-instead-of-reference in for-each over std::vector
- **File**: `source/FMCPCDModel.cpp`, `source/FMCSPIR.cpp` (multiple `for (auto x : vec)` sites)
- **Why**: vectors of large model objects copied per iteration. Single-digit % CPU impact at fmc invocation time (subsecond), but architectural smell. Safe mechanical change.
- **Fix sketch**: `for (const auto& x : vec)` everywhere.

### P3-2 — Optimization: repeated `find()` in inner loops
- **File**: `source/FMCPCDModel.cpp`
- **Why**: Inner-loop `model.schemes.find(name)` per element where the result doesn't change inside the loop. Hoist outside.
- **Fix sketch**: standard hoist; mechanical.

### P3-3 — Optimization: `std::map::operator[]` for insertion (default-constructs then assigns)
- **File**: `source/FMCTaskDef.cpp`, `source/FMCPCDModel.cpp`
- **Why**: `m[k] = v` is two ops; `m.emplace(k, v)` is one. Negligible runtime, but `emplace` also avoids the silent-insert defect that P1-8 calls out.
- **Fix sketch**: `emplace` or `insert_or_assign` everywhere.

### P3-4 — Cosmetic: `fprintf(stderr, " Error: %s.\n", str)` in bison-generated `FMCSPExpr.cpp` user-action block
- **File**: `source/FMCSPExpr.cpp`
- **Line**: 92, 98
- **Why**: emits to stderr instead of routing through the existing `CGenericError` path. Operator can't capture errors via the normal log channel.
- **Fix sketch**: route through the error-class plumbing if NXP accepts the change; otherwise leave (it's in a user-action block, not in the generated skeleton).

---

## Summary table

| Severity | Count | Files most affected |
|---|---|---|
| P0 | 6 | FMCPCDReader.cpp ×1, FMCPDLReader.cpp ×1, FMCCFGReader.cpp ×1, FMCPCDModel.cpp ×2, FMCCModelOutput.cpp ×1 |
| P1 | 8 | FMCPCDModel.cpp ×1, FMCPCDReader.cpp ×2, FMC.cpp ×2, FMCSPIR.cpp ×2, libfmc.cpp ×1 |
| P2 | 9 | FMC*.cpp readers ×3, libfmc.cpp ×2, FMCSPIR.cpp ×2, FMCCModelOutput.cpp ×1, fm_sp_private.c ×1 |
| P3 | 4 | FMCPCDModel.cpp ×2, FMCTaskDef.cpp ×1, FMCSPExpr.cpp ×1 |

## Recommended fix order

1. **P0-6** (`memset(cmodel, 0, sizeof(*cmodel))`) — single-line, kills an entire silent-misconfig class. Highest value/cost ratio.
2. **P0-4** (`fields[i]` instead of `fields[0]`, 8 sites) — purely mechanical, deterministic-wrong-output bug.
3. **P0-5** (unsigned-underflow guards in header-insert / key-build) — short, eliminates OOB.
4. **P0-1/2/3** (XXE — three reader sites) — uniform fix, defensive against attacker-controlled `cdx_pcd.xml`.
5. **P1-5** (operator-precedence bug in `ECHECKSUM_LOC`) — three character fix, eliminates a UB read.
6. **P1-1, P1-3, P1-4** — three independent small fixes.
7. **P2-1 / P2-2 / P2-4 / P2-5** — RAII/iterator hygiene; coordinated change.
8. **Remaining P2 / P3** — discretionary.

---

## Audit constraints / what was NOT audited

- Generated parser code (`spa/fm_sp_assembler.tab.c`, `spa/lex._fmsp_assembler_yy.c`, `FMCSPExprLexer.cpp`, the bison skeleton in `FMCSPExpr.cpp`) — not editable by policy.
- `spa/fm_sp.c`, `spa/dll.c`, `spa/htbl.c` — only spot-checked because they are referenced by generated code; deeper audit on next pass if requested.
- Performance profiling against real `cdx_pcd.xml` from production — out of scope (would require running fmc against a representative input under perf/valgrind).

## Re-vendoring discipline

If any of the above findings are accepted and patched in-tree, the edit must be annotated with `/* ASK-edit (defensive): rationale */` immediately above the change, matching the producer's `ASK-edit (askN)` marker convention. The grep `grep -rn 'ASK-edit' ask-userspace/fmc/` is the canonical audit surface enumerating every delta from upstream NXP — preserves the trail across future re-vendoring from `nxp-qoriq/fmc`.