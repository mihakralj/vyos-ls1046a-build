# AUDIT-dpa_app

**Module:** `ASK/dpa_app/` — DPAA offload userspace PCD applier (one-shot, launched by `cdx.ko` via `call_usermodehelper`)
**Origin:** Freescale (2011, 2014), NXP (2017–2018, 2021)
**License:** GPL-2.0+
**Audit lens:** D1..D18 / O1..O10
**Date:** refresh of `ASK-CODE-REVIEW.md §3`

---

## Findings summary (P0/P1/P2 table)

| ID | Severity | Class | File:Line | Title |
|---|---|---|---|---|
| DA-01 | **P0** | D7 | `ASK/dpa_app/dpa.c:535–537, 540–543` | `sscanf("…/ccnode/%s", …, &info->name[0])` — unbounded `%s` into fixed `info->name[64]` (TABLE_NAME_SIZE), driven by table names from `cdx_pcd.xml`. **Stack overflow** if PCD authoring produces a name > 63 chars. |
| DA-02 | **P0** | D7 | `ASK/dpa_app/dpa.c:328, 353, 365–366, 370–371, 375–376, 424` | Multiple `sprintf(port_info->name, "dpa-fman%d-oh@%d", …)` and `sprintf(name, "fm%d", finfo->index)` into fixed-size buffers — `port_info->name` is `CDX_CTRL_PORT_NAME_LEN` (32 bytes per prior audit), `name` is `char[256]` local. The `%d` substitutions for `fm_index`/`port_id` are in principle bounded by hardware (LS1046A has ≤ 2 FMans, ≤ 16 ports), but no compile-time enforcement and the source style is unsafe-by-default. |
| DA-03 | **P1** | D1 / D17 | `ASK/dpa_app/dpa.c:761–766, 873–895` | `dpa_init()` cleanup path frees `params.fman_info`/`ipr_info`/`tbl_info`/`portinfo` only if `retval` was set — but on the **success path** (`retval = 0` after ioctl) the function falls through `err_ret:` and `if (retval)` is false, so `cdx_dev_handle` is **kept open** (intentional? no comment) and the buffers **are freed unconditionally** — including those that were passed by pointer to the kernel via `CDX_CTRL_DPA_SET_PARAMS`. If the kernel retains pointers (it does — see `params.fman_info->portinfo`, `tbl_info`), it now references freed userspace memory. Use-after-free across the kernel/user boundary. |
| DA-04 | **P1** | D1 | `ASK/dpa_app/dpa.c:768–774` | `fmc_compile()` failure returns -1 directly without the `goto err_ret` cleanup, so when the cdx device was already opened (line 761) but `fmc_compile` fails, `cdx_dev_handle` leaks (process is short-lived so leak is small, but the kernel side `open` count is incremented). |
| DA-05 | **P1** | D6 | `ASK/dpa_app/dpa.c:283–289` | `get_fm_pcd_handle()` casts `finfo->fm_handle` to `struct t_Device *` and dereferences `dev->fd` with no NULL check on `dev`. `fm_handle` is set by `set_fm_adv_options()` from `FM_Open()` return; `set_fm_adv_options` does check NULL (line 255), but the call sequence in `dpa_init()` (lines 815–820) does **not** verify that `set_fm_adv_options` succeeded for *every* fman before calling `get_fm_pcd_handle` later (line 854). If one fman's `FM_Open` failed but the loop did `goto err_ret`, the cleanup path at line 877 walks `fman_info[]` and frees `tbl_info`/`portinfo` for fmans that may not have been initialized — see DA-06. |
| DA-06 | **P1** | D17 | `ASK/dpa_app/dpa.c:875–890` | Cleanup loop iterates `cmodel.fman_count` fmans regardless of how far init progressed. `fman_info[ii].tbl_info` / `portinfo` are checked for NULL before free, which mitigates this — but `fman_info` is `calloc`'d so the NULL check works. **Verify**: this is currently safe but fragile to refactor. P1 per audit conservative bound. |
| DA-07 | **P2** | D7 / D14 | `ASK/dpa_app/testapp.c:167, 170, 210, 213` | `sprintf(dst_ptr, "%04x:%02x ", ii, *src_ptr)` and `sprintf(dst_ptr, "%02x ", *src_ptr)` write into `print_data[128]` advancing `dst_ptr += sprintf(...)` — every 16 bytes the buffer is flushed and reset; max growth per loop iteration is 4 chars (`"%02x "`) and printf-format-width is bounded so this is safe in practice, but no `snprintf` boundary check. |
| DA-08 | **P2** | D7 | `ASK/dpa_app/dpa.c:309, 417` | `char name[256]` local + `sprintf(name, "fm%d", finfo->index)` — `finfo->index` is a `uint32_t` so `%d` could in theory be ~11 chars; total ≤ 14, well within 256. Bounded but unsafe idiom. |
| DA-09 | **P2** | D14 | `ASK/dpa_app/main.c:18, 27` | `//#define ENABLE_TESTAPP1` (commented out, with broken whitespace `1` glued to `ENABLE_TESTAPP`) vs `#ifdef ENABLE_TESTAPP` (line 27). The intended macro is `ENABLE_TESTAPP`; the macro-with-1 in line 18 would not enable the test path even if uncommented because `ENABLE_TESTAPP1` ≠ `ENABLE_TESTAPP`. **Test code is unreachable.** Confirms prior M12. |
| DA-10 | **P2** | D2 | `ASK/dpa_app/dpa.c:343, 503, 783, 793` | `calloc(1, size)` is used correctly (zeroed), but `size` for `dpa.c:343` (`sizeof(struct cdx_port_info)` × `ports` + `sizeof(struct cdx_dist_info)` × Σschemes) is computed by accumulating into a local `size` variable without overflow check — `cmodel.port_count` is uint32 and `schemes_count` is uint32; if PCD XML is hostile, multiplication can wrap. Mitigation: `cdx_pcd.xml` is shipped read-only by the build; trust boundary is the build pipeline. P2. |
| DA-11 | **P2** | D14 | `ASK/dpa_app/testapp.c:140` | `#ifdef DPAA_DEBUG_ENABLE` block executes ioctl + sprintf path; macro is set in `Makefile` CFLAGS unconditionally — debug code in production. Confirms prior M14. |
| DA-12 | **P2** | D8 | `ASK/dpa_app/testapp.c:50–54` | Hardcoded test MAC addresses + IP addresses (also `IFACE_1`/`IFACE_2` = `eth2`/`eth3`). Confirms prior M13. |

---

## Module inventory

| File | LoC | Role |
|---|---:|---|
| `ASK/dpa_app/dpa.c` | 896 | core PCD applier: opens cdx chardev, calls `fmc_compile`/`fmc_execute`, fills fman/port/table/dist info, ioctls `CDX_CTRL_DPA_SET_PARAMS` |
| `ASK/dpa_app/main.c` | 32 | `main()` thin wrapper: calls `dpa_init()` then optional `test_app_init()` |
| `ASK/dpa_app/testapp.c` | 219 | optional test connection inserter + muram dump CLI helper (gated by `ENABLE_TESTAPP` and `DPAA_DEBUG_ENABLE`) |

**Userspace ABI surface (consumer of):**
- `/dev/cdx_ctrl` (chardev opened at `dpa.c:761`).
- ioctls: `CDX_CTRL_DPA_SET_PARAMS` (line 871), `CDX_CTRL_DPA_CONNADD` (testapp:120), `CDX_CTRL_DPA_GET_MURAM_DATA` (testapp:153, 196).
- Config files: `/etc/cdx_cfg.xml`, `/etc/cdx_pcd.xml`, `/etc/cdx_sp.xml`, `/etc/fmc/config/hxs_pdl_v3.xml` (paths hardcoded at `dpa.c:36–39`).
- Library deps: `fmc.h` (libfmc.a), `cdx_ioctl.h` (kernel UAPI from `ASK/cdx/`), `libcli.h` (testapp only).

**Process model:** one-shot CLI tool launched via `call_usermodehelper` from `cdx.ko` during PCD init. No long-running state. No signal handlers. No threads.

---

## Re-verification of prior findings

### Prior HIGH C18 — `sscanf` buffer overflow in `dpa.c:535-543`

**Status:** **STILL OPEN** — re-cited as **DA-01**. Current line numbers are `dpa.c:535–537` (`sscanf(tblname, "fm%d/port/%dG/%d/ccnode/%s", &fm_idx, &speed, &port_id, &info->name[0])`) and the offline-port retry at `dpa.c:540–543`. `info->name` is declared `char[64]` (TABLE_NAME_SIZE) per `cdx_ioctl.h` (consumed via `info->name`). `%s` has no width specifier — overflow on >63 char node names.

**Fix:** `%63s` in both `sscanf` format strings.

### Prior HIGH C19 — `sprintf` overflow in `dpa.c:365-376`

**Status:** **STILL OPEN** — re-cited as **DA-02**. Lines `dpa.c:365–366, 370–371, 375–376` `sprintf(port_info->name, "dpa-fman%d-oh@%d", …)`, `sprintf(port_info->name, "dpa-fm%d-1G-eth%d", …)`, `sprintf(port_info->name, "dpa-fm%d-10G-eth%d", …)`. `port_info->name` is `CDX_CTRL_PORT_NAME_LEN`-byte (32 per prior audit). Worst-case rendering: `"dpa-fman4294967295-oh@4294967296"` = 32 chars + NUL = 33 — overflow by 1 in the pathological case.

In practice `fm_index ≤ 1` and `port id ≤ 15` so safe at runtime. Defense-in-depth still needs `snprintf(buf, sizeof(buf), …)`.

### Prior HIGH C20 — "No error recovery — `dpa.c` exits on any failure"

**Status:** **STILL OPEN** — pattern present (every `goto err_ret` returns -1 from `dpa_init`, and `main.c:25` `return -1;`). Architectural — out of pure defensive-coding lens. **Reframed**: the kernel `cdx.ko` cannot retry; userspace failure leaves the kernel module in `partially-initialized` state. Resolution requires kernel-side support (graceful degradation), not userspace.

### Prior MED M12 — `ENABLE_TESTAPP1` vs `ENABLE_TESTAPP` mismatch

**Status:** **STILL OPEN** — re-cited as **DA-09**. `main.c:18` `//#define ENABLE_TESTAPP1` vs `main.c:27` `#ifdef ENABLE_TESTAPP`. Test code is unreachable.

### Prior MED M13 — Hardcoded IPs/MACs

**Status:** **STILL OPEN** — `testapp.c:50–54, 25–35`. **DA-12**. Test-only file but not parameterized.

### Prior MED M14 — `DPAA_DEBUG_ENABLE` always defined

**Status:** **STILL OPEN** — `testapp.c:140` (`show_muram` body gated by it). **DA-11**.

---

## P0 findings

### DA-01 — `sscanf` `%s` unbounded into 64-byte struct field

**File:** `ASK/dpa_app/dpa.c:535–537, 540–543`.

```c
535: if (sscanf(tblname, "fm%d/port/%dG/%d/ccnode/%s",
536:           &fm_idx, &speed, &port_id,
537:           &info->name[0]) != 4) {
...
540: if (sscanf(tblname,
541:           "fm%d/port/OFFLINE/%d/ccnode/%s",
542:           &fm_idx, &port_id,
543:           &info->name[0]) != 3) {
```

`info->name[0]` is the first byte of `info->name[TABLE_NAME_SIZE]` (= 64 bytes per `cdx_ioctl.h`). `%s` with no width consumes until whitespace; `tblname` comes from `cmodel.ccnode_name[ii]` / `cmodel.htnode_name[ii]` which are populated by `fmc_compile()` from `/etc/cdx_pcd.xml`. A name longer than 63 chars overflows the destination on the heap (`info` is `calloc`'d at `dpa.c:503`).

**Fix:** `%63s` in both formats:

```c
sscanf(tblname, "fm%d/port/%dG/%d/ccnode/%63s", ...)
sscanf(tblname, "fm%d/port/OFFLINE/%d/ccnode/%63s", ...)
```

### DA-02 — `sprintf` into bounded port name buffer

**File:** `ASK/dpa_app/dpa.c:365–366, 370–371, 375–376`.

```c
365:    sprintf(port_info->name, "dpa-fman%d-oh@%d",
366:        port_info->fm_index, (port_info->index + 1));
...
370:    sprintf(port_info->name, "dpa-fm%d-1G-eth%d",
371:        port_info->fm_index, port_info->index);
...
375:    sprintf(port_info->name, "dpa-fm%d-10G-eth%d",
376:        port_info->fm_index, port_info->index);
```

`port_info->name` is fixed-size (`CDX_CTRL_PORT_NAME_LEN` per `cdx_ioctl.h`). `%d` on `uint32_t` can render 10 digits + sign. Worst case: `"dpa-fm4294967295-10G-eth4294967295"` = 35 chars — overflow.

In practice values are small (fm 0..1, port 0..15). Safety relies on hardware bounds, not on the code.

**Fix:** replace every `sprintf(buf, ...)` with `snprintf(buf, sizeof(buf), ...)`. Also `dpa.c:328, 353, 424` for the `name[256]` local (currently safe but unsafe-idiom).

---

## P1 findings

### DA-03 — Cleanup path in `dpa_init` runs on success too, freeing kernel-referenced buffers

**File:** `ASK/dpa_app/dpa.c:870–895`.

```c
870:    /* set defaults loop */
871:    retval = ioctl(cdx_dev_handle, CDX_CTRL_DPA_SET_PARAMS, &params);
872: if (retval)
873:         printf("%s:set params ioctl failed\n", __FUNCTION__);
874: err_ret:
875: //release resources allocated
876: fman_info = params.fman_info;
877: if (fman_info) {
878:     for (ii = 0; ii < cmodel.fman_count; ii++) {
879:         if (fman_info->tbl_info)
880:             free(fman_info->tbl_info);
881:         if (fman_info->portinfo)
882:             free(fman_info->portinfo);
883:         fman_info++;
884:     }
885:     free(params.fman_info);
886: }
887: if (params.ipr_info)
888:     free(params.ipr_info);
889: ...
891: if (retval) {
892:     printf("%s::retval %d\n", __FUNCTION__, retval);
893:     close(cdx_dev_handle);
894: }
895: return retval;
```

After `ioctl(CDX_CTRL_DPA_SET_PARAMS)` succeeds, control falls through to `err_ret:` and **frees** `tbl_info`, `portinfo`, `fman_info`, `ipr_info`. The kernel side of the ioctl (`cdx.ko`) **copies** the struct fields it cares about into kernel storage — this is the contract — but **only** if the ioctl handler does a deep copy of `tbl_info[]` array, `portinfo` array, and `dist_info[]` sub-array. If any of those are stored as pointers in kernel state, the kernel now has dangling userspace pointers.

This pattern is correct **only** if the kernel ioctl handler performs a deep copy of every nested array. **Verification target**: `ASK/cdx/cdx_ctrl.c` `CDX_CTRL_DPA_SET_PARAMS` handler. (Outside this audit; flagged for cdx audit.)

If the kernel keeps pointers, this is **use-after-free across the ABI boundary** — silent corruption. Even if the kernel deep-copies, the userspace process exits immediately after `dpa_init` returns (`main.c:26` returns), so the buffers are reclaimed by the OS regardless. **Net effect today**: if kernel deep-copies, no bug; if kernel doesn't, severe corruption. P1 contingent on cdx audit.

**Fix recommendation:** add comment documenting the kernel contract and consider `mlock` on the buffers until the userhelper is reaped — or migrate to a copy-based ioctl.

### DA-04 — `cdx_dev_handle` leaked on early `fmc_compile` failure

**File:** `ASK/dpa_app/dpa.c:761–774`.

```c
761:    cdx_dev_handle = open(devname, O_RDWR);
762:    if (cdx_dev_handle < 0) { ... return -1; }
...
768:    retval = fmc_compile(&cmodel, cfg_file, pcd_file, pdl_file, sp_file, SP_OFFSET, 0, NULL);
769:    if (retval) {
770:        printf("%s::unable to compile fmc input files, err %d\n", __FUNCTION__, retval);
771:        return -1;        /* <-- cdx_dev_handle never closed */
772:    }
```

On `fmc_compile` failure, `return -1` skips `close(cdx_dev_handle)`. Process is short-lived so OS reclaims the fd, but the kernel `open()` refcount is incremented (`cdx_ctrl_open` may have side-effects) — confirms the prior-audit concern that "any failure leaves cdx in a partially initialized state".

**Fix:** `goto err_ret` (or explicit `close` then `return -1`).

### DA-05 — `get_fm_pcd_handle` deref of `fm_handle` without NULL check

**File:** `ASK/dpa_app/dpa.c:281–289`.

```c
281: static int get_fm_pcd_handle(struct cdx_fman_info *finfo)
282: {
283:    struct t_Device *dev;
284:
285:    //exract and pass device handles to kernel
286:    dev = (struct t_Device *)finfo->fm_handle;
287:    finfo->pcd_handle = (void *)((uint64_t)dev->fd);
288:    return 0;
289: }
```

If `set_fm_adv_options()` was skipped or failed silently for any fman (e.g. partial loop in `dpa.c:809–821`), `finfo->fm_handle` is NULL and `dev->fd` is a NULL deref. The current call chain does `goto err_ret` on `set_fm_adv_options` failure (line 818), but a defensive `if (!dev) return -1;` is warranted.

### DA-06 — Cleanup loop walks all fmans regardless of init progress

**File:** `ASK/dpa_app/dpa.c:875–886`. `fman_info` is `calloc`'d so per-fman zero-initialized; `tbl_info`/`portinfo` NULL checks before free make this safe today. Flagged P1 because refactoring (e.g. moving `calloc` to per-fman) silently breaks the assumption.

---

## P2 findings

### DA-07 — `sprintf` chain in `show_muram_temp` / `show_muram`

**File:** `ASK/dpa_app/testapp.c:167, 170, 210, 213`. Bounded by buffer flush every 16 iterations and short format width — safe in practice but lacks `snprintf` defense-in-depth.

### DA-08 — `sprintf(name, "fm%d", ...)` into 256-byte local

**File:** `ASK/dpa_app/dpa.c:309, 328, 353, 417, 424`. Bounded but unsafe-idiom; convert to `snprintf`.

### DA-09 — `ENABLE_TESTAPP1` typo (test code unreachable)

**File:** `ASK/dpa_app/main.c:18`. `//#define ENABLE_TESTAPP1` vs `#ifdef ENABLE_TESTAPP` at `main.c:27`. Even if the comment were removed the macro name doesn't match, so test path is dead. Reframed prior M12.

### DA-10 — Allocation size accumulation without overflow check

**File:** `ASK/dpa_app/dpa.c:320–337`. `size` accumulates `sizeof(struct cdx_port_info)` × ports + `sizeof(struct cdx_dist_info)` × Σschemes. If PCD XML is hostile, `cmodel.port[ii].schemes_count` could overflow on 32-bit `size_t`. Trust boundary is the build pipeline. Replace with `array_size()`-equivalent or `__builtin_mul_overflow`.

### DA-11 — `DPAA_DEBUG_ENABLE` always defined

**File:** `ASK/dpa_app/Makefile` (CFLAGS). Confirms prior M14. Production binary contains debug paths.

### DA-12 — Hardcoded test MACs / IPs / interface names

**File:** `ASK/dpa_app/testapp.c:50–54, 25–36`. Confirms prior M13. Test-only code — accept as-is for unit-test fixture.

---

## Optimization candidates

| # | Class | File:Line | Notes |
|---|---|---|---|
| O-da-1 | O10 | `dpa.c:294, 444, 614–680` | `for (ii = 0; ii < MAX_TABLE_PARAMS; ii++) { strstr(name, dist_name[ii].name) }` — multiple linear scans against fixed table. Replace with `bsearch` over sorted table or hash lookup. ~20 entries × N htnodes — small but trivial win. |
| O-da-2 | O1 | `dpa.c:614–680` | `set_reassembly_params()` is a chain of `if (strstr(...)) ... break;` — replace with table-driven dispatch using `dist_name[]`/`table_params[]` already defined nearby. |
| O-da-3 | O8 | top of file | XML config (`cdx_pcd.xml`, `hxs_pdl_v3.xml`) is parsed via `fmc_compile` (libfmc); SAX vs DOM choice belongs in `fmlib`/`fmc` audit. |
| O-da-4 | O2 | `dpa.c:343, 503, 783, 793` | `calloc` once per fman in init — fine; not a hot path. |

---

## Recommendations / fix routing

| Finding | Routing | Patch target |
|---|---|---|
| DA-01 | **Userspace** (`ASK/dpa_app/dpa.c`) | Add `%63s` to both `sscanf` formats. |
| DA-02 | Userspace | Replace all `sprintf(buf, ...)` with `snprintf(buf, sizeof(buf), ...)` mechanically. |
| DA-03 | **cross-cutting** (cdx audit) | Verify `CDX_CTRL_DPA_SET_PARAMS` deep-copies; add comment in `dpa.c` documenting contract. |
| DA-04 | Userspace | `goto err_ret` on `fmc_compile` failure. |
| DA-05 | Userspace | NULL check in `get_fm_pcd_handle`. |
| DA-06 | Userspace | Document calloc invariant; add per-fman init flag. |
| DA-07, DA-08 | Userspace janitorial | Mechanical `snprintf` migration. |
| DA-09 | Userspace | Fix typo `ENABLE_TESTAPP1` → `ENABLE_TESTAPP` or remove. |
| DA-10 | Userspace | Add overflow check (`__builtin_mul_overflow`). |
| DA-11 | Build | Remove `-DDPAA_DEBUG_ENABLE` from production CFLAGS in `Makefile`. |
| DA-12 | Userspace test-only | Accept or parameterize via env vars. |

**Suggested merge order:**
1. DA-01 (definite stack overflow on hostile PCD XML).
2. DA-02 (defensive `snprintf` migration).
3. DA-04 + DA-05 (init-path NULL/leak fixes).
4. DA-09 (typo — minor but correctness).
5. DA-03 cross-checked against cdx audit.
6. Janitorial (DA-07, DA-08, DA-10, DA-11).
