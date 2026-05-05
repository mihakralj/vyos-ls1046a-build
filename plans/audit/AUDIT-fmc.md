# AUDIT-fmc

Defensive-coding & optimization audit of the NXP FMan Configuration tool
(`nxp-fmc/source/`, ~21.6k LoC C++/C plus generated lex/yacc ≈ 42k LoC
total) and the local mono ASK extension delta
(`ask-ls1046a-6.6/patches/fmc/01-mono-ask-extensions.patch`, 403 lines).

The audit was driven by a build at `-Wall -Wextra -Wformat=2 -Wcast-align
-Wsign-compare`, by full-text grep across the tree, and by line-level
review of every file the task brief flagged as high blast-radius
(`FMC.cpp`, `libfmc.cpp`, `FMCPCDReader.cpp`, `FMCPCDModel.cpp`,
`FMCCModelOutput.cpp`, `FMCSP.cpp`, `FMCSPCreateCode.cpp`,
`FMCUtils.cpp`, `FMCGenericError.cpp`, `FMCTaskDef.cpp`, `fmc_exec.c`,
`FMCDummyDriver.c`).

No source files were modified.

## Findings summary

| Severity | Count |
|---|---|
| P0 | 7  |
| P1 | 11 |
| P2 | 12 |

Plus 30 unique build-warning kinds and 6 optimization candidates.

## Module inventory

- LoC (hand-written C/C++ excluding `spa/` generated files): ≈ 17k
- LoC (incl. generated lex/yacc + `FMCSPExpr*.cpp`):
  21,569 `.cpp/.c` + 3,329 headers = **24,967** in `source/`,
  plus the `spa/` C runtime. Brief states ≈41,719 LoC family-wide; consistent.
- Public ABI exported by `libfmc.cpp` / declared in `fmc.h` (extern "C"):
  - `int  fmc_compile(fmc_model*, const char*, const char*, const char*, const char*, unsigned int, unsigned int, const char**)`
  - `int  fmc_execute(fmc_model*)`
  - `int  fmc_clean(fmc_model*)`
  - `bool fmc_load(fmc_model*)`
  - `bool fmc_save(fmc_model*)`
  - `void fmc_release(fmc_model*)`
  - `const char* fmc_get_error(void)`
  - `t_Handle fmc_get_handle(fmc_model*, const char*)`
  - `int32_t fmc_log(int32_t)`
  - `void    fmc_log_write(int32_t, const char*, ...)`
  Internal symbol leaked to ABI surface (no `static`):
  `int createDevices(fmc_model*)` (libfmc.cpp:222) — see P1.9.
- External deps: `libxml2` (DOM-only — no SAX/streaming),
  `libtclap` (header-only, FMC.cpp), `libfm` / `fmlib` (kernel UAPI
  wrapper via `fmc.h`'s `<Peripherals/fm_pcd_ext.h>` etc.).
- Mono ASK extensions delta
  (`ask-ls1046a-6.6/patches/fmc/01-mono-ask-extensions.patch`, 403 lines):
  - Adds `portid` to `fmc_port_t` (fmc.h) and emits it
    (FMCCModelOutput.cpp:728).
  - Adds `shared` scheme replication via two new helpers
    `CFMCModel::replicateCCNodes()` / `replicateHtNodes()`
    (FMCPCDModel.cpp). These contain unguarded `push_back`s and never
    copy `ref.keys[ii].data` — see P1.4.
  - Adds `external`/`aging`/`data_mem_id`/`data_liodn_offs` attributes
    on `<hashtable>` (FMCPCDReader.cpp parsing + FMCTaskDef.h members +
    FMCPCDModel.cpp/.h propagation + FMCCModelOutput.cpp emit).
  - Updates `errorFuncHandler` signature (drops `const`) — required for
    libxml2 ≥ 2.12 structured-error callback signature; portability fix.
  - Fixes `pppoe.nextp` → `NET_HEADER_FIELD_PPPoE_PID` (was
    `NET_HEADER_FIELD_PPP_PID`).
- Origin era: NXP / Freescale 2009–2012 (most files), with a 2021 NXP
  refresh of `FMCDummyDriver.c`. Author headers: Serge Lamikhov-Center,
  Levy Roe, Hendricks Vince. C-with-classes, predates RAII; manual
  `xmlFreeDoc()` cleanup with throws-through-DOM is systemic.

## P0 findings (security / heap-corruption / ABI break)

### P0.1 — `vsprintf` into 256-byte stack buffer with caller-supplied format
- **File:** `libfmc.cpp:206-220` (`fmc_log_write`)
- **Snippet:**
  ```cpp
  void fmc_log_write(int32_t level, const char* format, ...) {
      ...
      char buffer[256];
      vsprintf(buffer, format, args);
      LOG((log_level_t)level) << buffer << std::endl;
  }
  ```
- **Defect class:** D7 (fixed-dest buffer overflow), D14 (no
  `__attribute__((format(printf,2,3)))` so the compiler cannot validate
  callers).
- **Trigger:** Several callers pass XML-derived strings (port names,
  `pmodel->fman[index].name`, `pmodel->port[index].name` —
  libfmc.cpp:245,256,285+). XML attributes are bounded only by libxml2's
  parser, not by 256 bytes. A long `<port name="…">` overflows the
  stack buffer; with `-fstack-protector` it aborts; without it, classic
  stack smash.
- **Recommendation:** Switch to `vsnprintf(buffer, sizeof(buffer), …)`
  and add the `format(printf, 2, 3)` GCC attribute on the
  `fmc_log_write` prototype in `fmc.h`.
- **Fix routing:** `02-defensive-fixes.patch` (local). Trivial. Worth
  upstreaming.

### P0.2 — `strcpy` into 100-byte global `parseString` from lexer text
- **File:** `FMCSPExprLexer.cpp:1126,1131,1136,1141,1147,1153,1160,1166,1172,1179,1185`
  and `FMCSPExpr.cpp:87` (`extern char parseString[100]`,
  `FMCSPExprLexer.cpp:709 char parseString[100];`).
- **Snippet:** `{ strcpy(parseString, FMCSPExprtext); return DEC; }`
- **Defect class:** D7 (fixed-size buffer + variable XML payload), D10
  (the buffer is a global; lexer is invoked during XML softparser
  processing).
- **Trigger:** `parseString` is 100 bytes. flex's `yytext` is unbounded;
  `<assign>`/`<if>` expressions inside `<custom-protocol>` XML can
  produce tokens longer than 100 bytes. All eleven rules `strcpy`
  without a length check.
- **Recommendation:** Replace with
  `snprintf(parseString, sizeof(parseString), "%s", FMCSPExprtext)`
  and emit a CGenericError if `yyleng >= sizeof(parseString)`. Better:
  promote `parseString` to `std::string`.
- **Fix routing:** Defensive-fixes patch (or regenerate flex). Worth
  upstream.

### P0.3 — DOM tree leaked on every throw out of a child parser
- **File:** `FMCPCDReader.cpp:118-214` (`parseNetPCD`); identical
  pattern in `FMCPDLReader.cpp:127-189` and `FMCCFGReader.cpp:98-…`.
- **Snippet:**
  ```cpp
  xmlDocPtr doc = xmlParseFile(filename.c_str());
  ...
  cur = cur->xmlChildrenNode;
  while (cur) {
      if (...) parseDistribution(&distribution, cur);   // throws
      else if (...) parseClassification(...);           // throws
      ...
  }
  xmlFreeDoc(doc);   // <-- only reached on the happy path
  ```
- **Defect class:** D5 (libxml2 DOM-tree leak) + D17 (error-path
  resource symmetry).
- **Trigger:** Any of dozens of `throw CGenericError(...)` inside
  `parseDistribution`, `parseClassification`, `parseClassificationKey`,
  `parsePolicer`, `parseHashTable`, `parseReplicator`,
  `parseFragmentation`, `parseHeaderManip`, `parseManipulations`, etc.
  The throw unwinds past `xmlFreeDoc(doc)`; for a realistic Mono PCD
  the DOM is hundreds of KiB to MiB, leaked. Also
  `xmlCleanupParser()` is skipped, leaving libxml2 thread-local state
  dirty for the next call.
- **Recommendation:** Wrap body in try/catch that calls
  `xmlFreeDoc(doc)` + `xmlCleanupParser()` then rethrows; or use a
  RAII guard:
  ```cpp
  struct XmlDocGuard { xmlDocPtr p; ~XmlDocGuard(){ if(p) xmlFreeDoc(p);} };
  ```
- **Fix routing:** Defensive-fixes patch. Pure addition; preserves ABI;
  fixes a long-running-server leak (`fmc -a` is one-shot but
  `libfmc.a` links into long-lived `dpa_app`/`cmm` daemons).

### P0.4 — `fmc_load()` blindly memcpy-deserializes the whole `fmc_model`
- **File:** `libfmc.cpp:430-456` (`fmc_load`)
- **Snippet:**
  ```cpp
  std::ifstream ifs(TMPFILENAME, std::ios::in | std::ios::binary);
  ...
  ifs.read((char*)pmodel, sizeof(*pmodel));
  ifs.close();
  res = createDevices(pmodel);   // dereferences pointers from disk
  ```
- **Defect class:** D6 (NULL/wild pointer deref via attacker-controlled
  contents), D18 (no version/magic check), D15 (uninitialized fields if
  truncated read silently succeeds). `fmc.h:46` defines
  `FMC_OUTPUT_FORMAT_VER 0x106` and `fmc_model_t` has a leading
  `format_version` member, but `fmc_load` never validates it.
- **Trigger:** `/tmp/fmc.bin` is world-writable predictable path
  (libfmc.cpp:49 `const char* TMPFILENAME = "/tmp/fmc.bin"`). Any user
  can write a crafted file; subsequent `fmc -a` (run as root) reads it
  back and `createDevices()` walks chains of pointers/handles found in
  the buffer. Also no check for `gcount() == sizeof(*pmodel)`; short
  read leaves later fields with stack garbage.
- **Recommendation:**
  1. Check `pmodel->format_version == FMC_OUTPUT_FORMAT_VER` and refuse
     otherwise.
  2. Move the state file to `/var/lib/fmc/state.bin` (root-only mode
     0600) or use `mkstemp`-derived path, not `/tmp`.
  3. Verify `ifs.gcount() == sizeof(*pmodel)`.
- **Fix routing:** Defensive-fixes patch (path move may need a
  config-time toggle to avoid breaking on-target deployments).

### P0.5 — `/tmp/fmc.bin` symlink/race vulnerability
- **File:** `libfmc.cpp:49,442,464,472`
- **Defect class:** D7/security (predictable temp path; `std::ofstream`
  open does not use `O_NOFOLLOW | O_EXCL`).
- **Trigger:** Local unprivileged user does
  `ln -s /etc/shadow /tmp/fmc.bin` before root invokes
  `fmc -a -p ... -c ...`. The subsequent `ofstream` write (or
  `std::remove(TMPFILENAME)` in `fmc_save(NULL)`) follows the symlink
  and overwrites/deletes the real file.
- **Recommendation:** Same fix as P0.4 — root-only state dir, open
  with `O_WRONLY|O_CREAT|O_TRUNC|O_NOFOLLOW`, mode 0600. Use POSIX
  `write()` directly (or `__gnu_cxx::stdio_filebuf`).
- **Fix routing:** Defensive-fixes patch.

### P0.6 — `Opcode op;` may be used uninitialized (case fall-through)
- **File:** `FMCSPCreateCode.cpp:281-315`
- **Snippet:**
  ```cpp
  Opcode op;
  switch (valuesSize) {
  case 1: ... return;
  case 2: ... break;
  case 3: ... break;
  case 4: ... break;
  }                       // no default clause
  addInstr(createCaseInstr(op, 0, switchTable->labels), code);
  ```
- **Defect class:** D15 (uninitialized stack data fed to instruction
  emitter). Confirmed by GCC `-Wmaybe-uninitialized` at line 314 (this
  is the lone `-Wmaybe-uninitialized` finding from the build).
- **Trigger:** A `<switch>` element in custom-protocol XML with
  `valuesSize == 0` (empty switch) or `> 4`. The opcode written into
  the SP binary is then a random integer; the assembler may produce
  binary that the FMan Soft Parser microengine executes as garbage.
- **Recommendation:** Initialize `Opcode op = (Opcode)0;` and add a
  `default: throw CGenericError(ERR_INTERNAL_SP_ERROR, "unsupported case-count");`.
- **Fix routing:** Defensive-fixes patch.

### P0.7 — Silent truncation in classification entry data/mask loops
- **File:** `FMCPCDReader.cpp:717-755` (parsing `<entry><data>` /
  `<mask>`).
- **Snippet:**
  ```cpp
  int index = sizeof(ce.data);
  while (data.length() > 0 && index >= 1) {
      ...
      ce.data[--index] = (char)std::strtol(tmp.c_str(), 0, 0);
  }
  ```
- **Defect class:** D4 (user-supplied size not bounded → silent
  truncation of security-relevant data). Near-miss D7: the
  decrementing `--index` would underflow if `ce.data` were ever
  changed to a heap pointer with a smaller actual size.
- **Trigger:** XML supplies more hex bytes than `sizeof(ce.data)`. The
  excess silently truncates with no warning, so the on-the-wire match
  key differs from the operator's intent — a silent ACL bypass.
- **Recommendation:** Validate `data.length() <= 2 * sizeof(ce.data)`
  before the loop; throw `ERR_INVALID_ENTRY_DATA` on overrun.
- **Fix routing:** Defensive-fixes patch.

## P1 findings (leaks, NULL deref, build break)

### P1.1 — NULL deref of `pr->children->content` for empty XML element
- **File:** `FMCPCDReader.cpp:710,734` and ~12 similar sites.
- **Snippet:** `std::string data = stripBlanks((const char*)pr->children->content);`
- **Defect:** D6. If XML contains `<data></data>` or
  `<data><!--c--></data>`, `pr->children` may be NULL or
  `pr->children->content` NULL → `stripBlanks(NULL)` segfaults
  (`FMCUtils.cpp:34` indexes `str[i]`).
- **Recommendation:** Add
  `if (!pr->children || !pr->children->content) throw CGenericError(ERR_INVALID_ENTRY_DATA, ...);`
  Apply identical guard at every direct `pr->children->content` site.
  `getXMLElement(FMCPCDReader.cpp:71)` already does this — just be
  consistent.

### P1.2 — `errorFuncHandler` writes through `error->message` discarding `const`
- **File:** `FMCGenericError.cpp:36-50` (post-patch signature is
  `xmlError * error`, not `const xmlError *`).
- **Snippet:**
  ```cpp
  char *msgstr = (char*)"";
  if (error->message != 0) msgstr = error->message;   // assigns const char*
  ...
  ((CGenericError*)ctx)->createMessage(ERR_XML_PARSE_ERROR1, -1, msgstr);
  ```
- **Defect:** D14 (signature widened by ASK patch — required for libxml2
  ≥ 2.12 — but the body still treats the string as if writable). The
  current call chain happens to copy via `CErrorElem`, so works today,
  but the cast is fragile UB-bait: any future caller that mutates
  `msgstr` corrupts libxml2 internal storage.
- **Recommendation:** Use `const char* msgstr` and adjust call sites.

### P1.3 — `int main(...)` cleanup-path silently returns 0 on `fmc_clean` errors
- **File:** `FMC.cpp:230` (`return ret;` inside the try-block) and
  `FMC.cpp:262` (terminal `return 0;`).
- **Defect:** D17 minor — `fmc_load`'s `bool` return is checked, but
  `fmc_clean`'s int return is not assigned to `ret`. Cleanup-only
  invocation always reports success.
- **Recommendation:** Capture and propagate `fmc_clean()`'s return.

### P1.4 — `replicateHtNodes` / `replicateCCNodes` (ASK extension) drop per-key data
- **File:** `FMCPCDModel.cpp` (ASK patch insertion lines ~133-220 in
  `01-mono-ask-extensions.patch`).
- **Snippet:**
  ```cpp
  for (ii = 0; ii < ref.keys.size(); ii++) {
      htNode.keys.push_back(HTNode::CCData());   // default-constructed!
  }
  ```
- **Defect:** D6/D17. Replicated node receives default-constructed
  `CCData` entries whose `data`/`mask` arrays are uninitialized; the
  reference node's actual key payload is never copied. When fed to
  `FM_PCD_HashTableSet` / `MatchTableSet`, the kernel reads stack
  garbage for key bytes. Symptom: PCD apply rc=65280 with no kernel
  diagnostic — the exact failure class the patch documents itself as
  fixing.
- **Recommendation:** Replace the empty-default loops with full copy:
  ```cpp
  htNode.keys = ref.keys;
  htNode.masks = ref.masks;
  htNode.nextEngines = ref.nextEngines;
  ```
  Or assert `ref.keys.empty()` at entry and document that shared
  schemes only support empty seed tables.
- **Fix routing:** Defensive-fixes patch (in fact, this is a
  must-have: the existing patch's stated problem is exactly what this
  bug produces).

### P1.5 — `iv_word_size` set but never used in SP assembler
- **File:** `fm_sp_assembler.y:2632` (compiled output:
  `spa/fm_sp_assembler.tab.c`).
- **Defect:** D17 (dead state variable; suggests forgotten validation).
  In an assembler, "word_size" tracking that is computed but not
  checked is exactly the shape of a missing instruction-width cap.
- **Recommendation:** Investigate whether `iv_word_size` was meant to
  feed an "instruction word too wide" diagnostic. If yes, restore the
  check; if no, delete to satisfy `-Wunused-but-set-variable`.

### P1.6 — `fmc_save(NULL)` race with `std::remove`
- **File:** `libfmc.cpp:463-470`
- **Snippet:**
  ```cpp
  if (pmodel == NULL) {
      int err = std::remove(TMPFILENAME);
      ...
  }
  ```
- **Defect:** D7 (race; `std::remove` follows symlinks; combined with
  P0.5 this is unprivileged-controlled file deletion as root).
- **Recommendation:** Open with `O_NOFOLLOW`, then `unlinkat`. Treat
  ENOENT as success silently.

### P1.7 — `softparser()` `int m = filePath.find(".xml")` silent on missing ext
- **File:** `FMCSP.cpp:53-55`
- **Snippet:**
  ```cpp
  int m = filePath.find(".xml");
  fileNoExt = filePath.substr(0, m);
  ```
- **Defect:** D9. `find` returns `npos` (= -1 cast to int);
  `substr(0, -1)` becomes `substr(0, npos)` → entire string copied.
  Result: `fileNoExt = "foo.bar"`, `dumpPath = "foo.bar.dump"` — a
  surprise rather than a crash on glibc, but UB-prone on any libstdc++
  where `npos != (size_t)-1`.
- **Recommendation:** Validate `filePath` ends in `.xml`; throw
  `ERR_SP_REQUIRED` otherwise.

### P1.8 — `int len = str.length()` widening loss in FMCUtils
- **File:** `FMCUtils.cpp:34,47,63` (`stripBlanks`, `innerBlanks`,
  `stripDollar`).
- **Defect:** D9 — `int len = str.length()` truncates for >2 GiB
  strings (not realistic) and, more importantly, the `int` loop
  counters drive the `-Wsign-compare` warnings observed in
  `FMCGenericError.cpp:98,102` and similar.
- **Recommendation:** Use `size_t` / `std::string::size_type`.

### P1.9 — Missing `static` on `int createDevices(fmc_model*)`
- **File:** `libfmc.cpp:222`
- **Defect:** D17 — accidental ABI export; only called from
  `fmc_load`. Symbol is in `libfmc.a` and could collide with a
  `dpa_app` implementation.
- **Recommendation:** Mark `static`.

### P1.10 — `fmc_log_write` in `FMCDummyDriver.c` uses `%d` for `t_Handle`
- **File:** `FMCDummyDriver.c:61,67,…` (~12 call-sites).
- **Snippet:** `fmc_log_write(LOG_DBG2, "Calling IOCTL::ReleaseDevice %d", h_Dev);`
  where `h_Dev` is `void*`.
- **Defect:** D14. On 64-bit builds the upper 32 bits of the pointer
  are silently truncated and varargs alignment may mis-walk subsequent
  arguments — UB. Currently invisible because `fmc_log_write` lacks
  the `format(printf,…)` attribute (P0.1); fixing P0.1 will unmask
  this automatically.
- **Recommendation:** Use `%p` and `(void*)h_Dev`.

### P1.11 — `t_FmPcdHashTableParams` unnamed inner struct never initialized
- **File:** `FMCCModelOutput.cpp:1620-1640` vs.
  `ask-userspace/fmlib/include/fmd/Peripherals/fm_pcd_ext.h:2025-2033`
- **Defect:** D15 (uninitialized data shipped to kernel via ioctl),
  D18 (ABI/UAPI sizing). The `t_FmPcdHashTableParams` definition has
  an **unnamed** inner struct
  `{ uint32_t table_type; uint32_t timeout_val; uint32_t timeout_fqid;
     uint32_t max_frags; uint32_t min_frag_size; uint32_t max_sessions; }`
  for ip-reassembly. The fmc emit step never writes any of these
  members; the global model is `memset(0)`'d once at start so the
  in-process call works, but `fmc_load()` (P0.4) re-reads them from
  disk *before* `createDevices` and the values then come from the
  attacker-supplied file. Combined with P0.4 this is exploitable.
- **Recommendation:** Explicitly emit zero-initialization of the
  reassembly block in `output_htnode`:
  ```cpp
  EMIT4(htnode[, index, ].table_type =, 0);
  EMIT4(htnode[, index, ].timeout_val =, 0);
  // ... etc
  ```
  And/or `memset(&cmodel->htnode[index], 0, sizeof(...))` before
  populating fields. Verified: ASK patch's `#if (DPAA_VERSION >= 11)`
  gate around `externalHash`/`externalHashParams` correctly matches
  the fmlib header gate.

## P2 findings (style, micro-perf, hardening)

- **P2.1** `FMCTaskDef.h:188,250` — unused `custom_confirms` parameter
  in two ctors (-Wunused-parameter). Dead API surface.
- **P2.2** `FMCTaskDef.h:226-277, logger.hpp:140-141` — 14 instances of
  `-Wreorder` for member-initializer order mismatching declaration
  order. UB if any reordered member depends on another. D15 latent.
- **P2.3** `libfmc.cpp:230,231` — `t_FmPcdParams fmPcdParams = {0};`
  triggers 19 `-Wmissing-field-initializers` per call-site. C++ rule
  for `{0}` is to value-initialize remaining members so behaviorally
  fine; cosmetically use `{}` or explicit `memset`.
- **P2.4** `libfmc.cpp:229,347` — `current_port` set but never read
  (-Wunused-but-set-variable). The `fmc_exec.c` cousin actually reads
  it; the libfmc copy was forked and lost the read.
- **P2.5** `fmc_exec.c:520,1271,1294,…` — 9 -Wunused-parameter on
  static functions; either `(void)engine;` or remove.
- **P2.6** `FMCSPIR.cpp:854` — -Wparentheses "suggest parentheses
  around `&&` within `||`". Operator-precedence bug-magnet.
- **P2.7** `FMCSPIR.cpp:149` — `firstBefore` set but unused; dead
  state-machine bookkeeping.
- **P2.8** `FMCPCDReader.cpp:50-66` — `getAttr` builds the result by
  per-character `insert(end, *p)` instead of `retStr.assign(pAttr)`.
  Quadratic in attribute length; XML attributes can be megabytes (PCD
  configs have long key blobs). O10/O3 candidate.
- **P2.9** `FMCSP.cpp:80-99` — Manual freelist walk freeing
  `labels->name` then `labels`; if `fmsp_assemble` returns a
  partially-built list with internal cycle, infinite loop / double
  free. Add a sanity bound on iterations.
- **P2.10** `fmc_exec.c:57-62` — `fmc_heap_check` does
  `malloc/memset/free` unconditionally, called twice per HT/CC node.
  Gate behind `g_fmc_trace` like `FMC_TRACE`; otherwise measurable
  overhead on configs with hundreds of nodes.
- **P2.11** `FMCPCDReader.cpp:113` — `xmlInitParser()` /
  `xmlCleanupParser()` invoked per `parseNetPCD` call. libxml2 docs
  recommend once per process; `xmlCleanupParser` deletes thread-local
  memo state shared with simultaneously-parsing PDL/CFG readers —
  race risk if fmc were ever made multi-threaded (D10 latent).
- **P2.12** `FMCCModelOutput.cpp:217` — `memcpy(cmodel->spCode,
  model.swPrs.p_Code, cmodel->sp.size)` with no bound check that
  `cmodel->sp.size <= MAX_SP_CODE_SIZE` (=0x7C0, fmc.h:55). If the SP
  assembler emits more than 0x7C0 bytes, destination is overrun. Add
  guard: `if (cmodel->sp.size > MAX_SP_CODE_SIZE) throw …`. D7/D4.

## Optimization candidates (O1..O10)

- **O1 (linear search dispatch in PCD model lookup)** —
  `FMCPCDModel.cpp` contains 29 `std::map<string,X>::find()` calls in
  the model builder; O(log N), acceptable. **However** the new ASK
  helpers `replicateCCNodes`/`replicateHtNodes` (FMCPCDModel.cpp, ASK
  patch ~lines 133-220) do a **linear scan** of
  `port.htnodes`/`port.ccnodes` via the index vector then a string
  compare each — O(N·M). For a 2-port × 200-HT-node Mono config that's
  80 000 strcmp per scheme replication. Add a per-port
  `unordered_map<string, unsigned int>` cache.
- **O3 (vector reserve)** — 97 `push_back` calls in `FMCPCDModel.cpp`
  versus only 7 `reserve()` calls. Hot loops over `xmlChildrenNode`
  know the upper bound at parse time; `reserve(expected)` on `keys`,
  `masks`, `nextEngines`, `frag`, `header`, `indices` would avoid
  log-base-2 reallocations.
- **O8 (DOM vs SAX XML parse)** — `FMCPCDReader.cpp:118` uses
  `xmlParseFile` (full DOM into memory). For a 5 MiB Mono PCD the DOM
  is ≈25 MiB resident. SAX (`xmlSAXUserParseFile`) would cut working
  set to near-zero. Probably not worth the refactor.
- **O10 (strlen in loops)** — `FMCUtils.cpp` recomputes `str.length()`
  in loop conditions; mostly hoisted by -O2. Larger win is the
  per-character build in `FMCPCDReader::getAttr` (P2.8).
- **O-extra-1 (`std::map` vs `std::unordered_map`)** — `CTaskDef`
  stores `distributions`, `classifications`, `policers`, `policies`,
  `replicators`, `vsps`, `headermanips` as `std::map<string,…>`. Keys
  are unique XML names. With Mono configs ~150 distributions, ~100
  classifications, `unordered_map` drops log-N to amortized O(1).
  Tradeoff: slight memory bump and loss of ordered iteration; verify
  that emitted-C dump determinism does not depend on insertion order
  before flipping.
- **O-extra-2 (`fmc_heap_check`)** — see P2.10. Cheap to gate behind
  `g_fmc_trace`.

## Build warnings observed

Build invocation:
```
make FMD_USPACE_HEADER_PATH=/root/vyos-ls1046a-build/ask-userspace/fmlib/include/fmd \
     CC="cc -Wall -Wextra -Wformat=2 -Wcast-align -Wsign-compare" \
     CXX="g++ -Wall -Wextra -Wformat=2 -Wcast-align -Wsign-compare"
```

496 warning lines, deduplicating to **30 unique warning kinds** across
roughly 270 unique source locations. Counts (after `sort -u`):

| Count | Category |
|---|---|
| 138 | -Wmissing-field-initializers (libfmc.cpp `t_FmPcd*Params = {0}`, FMCCModelOutput) |
|  58 | -Wswitch (incomplete switches over `e_FmPcdEngine`, `e_FmPortType`) |
|  21 | -Wsign-compare (libfmc.cpp:233/259/265/349/360/366; fmc_exec.c:585-637; FMCPCDModel.cpp:2433/2486/2536; FMCTaskDef.cpp:43; FMCSP.cpp:218; FMCSPCreateCode.cpp:1168; FMCSPIR.cpp:750; FMCGenericError.cpp:98/102; FMCSPExprLexer.cpp:782; lex._fmsp_assembler_yy.c:791) |
|  20 | -Wunused-parameter (FMCTaskDef.h, fmc_exec.c) |
|  14 | -Wreorder (FMCTaskDef.h CExecuteIf/CExecuteCase/CExecuteSwitch ctors; logger.hpp `logger_`) |
|   7 | -Wunused-variable |
|   6 | -Wunused-function |
|   4 | -Wunused-but-set-variable (libfmc.cpp current_port ×2; FMCSPIR.cpp:149 firstBefore; fm_sp_assembler.y:2632 iv_word_size) |
|   1 | -Wparentheses (FMCSPIR.cpp:854 `&&` within `||`) |
|   1 | -Wmaybe-uninitialized (**FMCSPCreateCode.cpp:314 `op`** — see P0.6) |

No -Wcast-align, -Wformat-truncation, or -Wstringop-* warnings fired.
-Wformat=2 produced no hits, but only because `fmc_log_write` lacks
the `format(printf,…)` attribute (P0.1). Fixing P0.1 will likely add
new -Wformat warnings that are worth fixing (notably P1.10).

## Recommendations / next steps

### Should land in `ask-ls1046a-6.6/patches/fmc/02-defensive-fixes.patch`
(local-only, narrow blast radius, no upstream coordination needed):

1. **P0.1** `vsprintf → vsnprintf` + `format(printf,2,3)` on
   `fmc_log_write` (`fmc.h` + `libfmc.cpp`). Will surface several
   latent format issues including **P1.10**.
2. **P0.4 + P0.5 + P1.6** Move `TMPFILENAME` to `/var/lib/fmc/state.bin`,
   open with `O_NOFOLLOW`/`O_EXCL`, validate `format_version` and
   `gcount()` on read.
3. **P0.6** Default-init `Opcode op = (Opcode)0;` and add
   `default: throw …` in `FMCSPCreateCode.cpp:282`.
4. **P0.7** Bound-check `data.length() <= 2*sizeof(ce.data)` in the
   `<entry>` data/mask loops.
5. **P1.1** NULL guard around every direct `pr->children->content`
   dereference in `FMCPCDReader.cpp` (~12 sites; one-liner each).
6. **P1.4 (mono ASK extension)** Replace the empty-CCData
   default-construct loops in `replicateCCNodes`/`replicateHtNodes`
   with full vector-copy. **High value** — the patch's stated goal
   (PCD apply rc=65280) is precisely what uninitialized key data
   would trigger.
7. **P1.9** `static` on `createDevices`.
8. **P1.11** Explicit zero-init of the unnamed
   `{table_type, timeout_val, …}` block in `t_FmPcdHashTableParams`
   when emitting `htnode[index]` (FMCCModelOutput.cpp).
9. **P2.10** Gate `fmc_heap_check` behind `g_fmc_trace`.
10. **P2.12** Bound-check `cmodel->sp.size <= MAX_SP_CODE_SIZE` before
    `memcpy` in `FMCCModelOutput.cpp:217`.

These are localized, self-contained, and preserve ABI/UAPI. They fit
naturally in the existing mono-extensions patch series.

### Should be filed upstream against `nxp-qoriq/fmc`
(systemic refactors / breaking style changes / generated-code regen):

1. **P0.2** `parseString[100] strcpy` — needs lex regen with bigger
   buffer / `snprintf`. Touches generated files.
2. **P0.3** RAII `xmlFreeDoc` guard in `parseNetPCD` /
   `parseNetPDL` / `parseCfgData`. Pattern change across three readers.
3. **P1.2** `errorFuncHandler` const-correctness.
4. **P1.5** `fm_sp_assembler.y:2632 iv_word_size` set-but-unused —
   needs maintainer to confirm whether a forgotten width check.
5. **P1.8** `int → size_t` index types in `FMCUtils.cpp` and the
   knock-on -Wsign-compare elimination across the tree.
6. **P2.1, P2.2, P2.5, P2.6, P2.7, P2.8** — janitorial cleanup batch.
7. **O1 / O-extra-1** — perf rework (`port_signature` cache;
   `unordered_map` migration).

### Not worth fixing now

- O8 (DOM→SAX) — cost/benefit poor for a one-shot tool.
- O10 — already optimized away by the compiler.
- The 138 -Wmissing-field-initializers warnings — silenced by
  switching `{0}` to `{}` aggregate init; cosmetic.
