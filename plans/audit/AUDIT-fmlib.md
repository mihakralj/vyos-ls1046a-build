# AUDIT-fmlib.md — Userspace `nxp-fmlib` defensive-coding & optimization audit

Scope: `nxp-fmlib/src/fm_lib.c` (post-ASK-patch working tree, 2705 LoC) plus
the producer-side UAPI in
`/root/lts_6.6_ls1046a/release/patches/kernel/sdk-sources/include/uapi/linux/fmd/Peripherals/`.
Patch reviewed: `ask-ls1046a-6.6/patches/fmlib/01-mono-ask-extensions.patch`.

The fmlib does **not** ship its own `fm_*ioctls.h` — the Makefile
(`nxp-fmlib/Makefile:64-68`) directs the compiler at the kernel's UAPI
tree, so every ioctl number is sourced from
`include/uapi/linux/fmd/Peripherals/fm_pcd_ioctls.h` etc. There is
therefore *no* duplicate ioctl table to drift; the entire ABI risk is
in the **`memcpy` shim layer** that bridges `t_Fm*` (caller / fmd ABI)
to `ioc_fm_*_t` (kernel ABI). That is where this audit concentrates.

---

## 1. Findings summary

| # | Sev | Cat | Location | One-line |
|---|-----|-----|----------|----------|
| F01 | **P0** | D18 / ABI | `fm_lib.c:678-715` + `fm_pcd_ext.h:1717-1720` | `bool shared` added to `t_FmPcdKgSchemeParams` but NOT to kernel `ioc_fm_pcd_kg_scheme_params_t`; `memcpy(&params, p_Scheme, sizeof(t_FmPcdKgSchemeParams))` overruns `params` and shifts every following field |
| F02 | **P0** | D18 / ABI | `fm_lib.c:1245-1295` + `fm_pcd_ext.h:1999-2023` | `t_FmPcdHashTableParams`/`ioc_fm_pcd_hash_table_params_t` are fragile siblings; no `ASSERT_COND(sizeof(...))` and only `params.id = NULL;` is initialised — `agingSupport`, `externalHash`, `externalHashParams.miss_monitor_addr` rely on caller having zeroed their copy |
| F03 | **P0** | D7 / overflow | `fm_lib.c:165, 384, 1689-1706` | `sprintf` into fixed-size `devName[20]` (FM_Open/FM_PCD_Open) and `devName[30]` (FM_PORT_Open). For port-name with `FM_MAX_NUM_OF_1G_RX_PORTS+portId` the formatted result can reach 36 chars — silent stack overflow if ids ever exceed 99 |
| F04 | **P0** | D3 / silent failure | `fm_lib.c:182, 304-318` | `FM_GetApiVersion` invoked from `FM_Open` for the version-mismatch warning, return value discarded — if the very first ioctl fails, `ver` is uninitialised stack and the printed numbers are garbage |
| F05 | **P0** | D5 / FD-leak | `fm_lib.c:404-423` | `FM_PCD_Close` performs `close(p_Dev->fd)` *before* checking `p_Dev->owners`; if owners > 0 the function `return`s **without `free(p_Dev)`** → leaks `t_Device` and uses an already-closed fd |
| F06 | **P1** | D17 / cleanup-asym | `fm_lib.c:609-624, 717-733, 825-839, 925-938, 1282-1295, 1483-1497, 1548-1563, 2199-2212, 2484-2497` | After successful `*_SET` ioctl wrapper does `malloc(t_Device)`; if it returns NULL the wrapper returns NULL **without deleting the kernel-side object** — kernel allocation leaks across every PCD set/build/profile call. ~9 sites |
| F07 | **P1** | D6 / NULL-deref | `fm_lib.c:1038-1045, 1084-1088, 1113-1117, 1146-1156, 1217-1228, 2177-2189, 2256-2268` | `t_Device *p_NextDev = (t_Device*) p_FmPcdCcNextEngineParams->params.ccParams.h_CcNode;` then `p_NextDev->id` — no NULL guard; caller passing engine=CC with NULL handle segfaults inside fmlib |
| F08 | **P1** | D4 / unchecked size | `fm_lib.c:867-919, 1921-1928, 2110-2111, 2133-2134, 2175-2191` | Loop bounds copied from caller (`num_of_keys`, `num_of_schemes`, `numOfEntries`) drive `for` loops over fixed-size arrays in the stack copy `params`; no upper-bound check before iterating — out-of-range value walks past the array end and corrupts the wrapper's stack |
| F09 | **P1** | D3 / errno mishandling | `fm_lib.c:224-225, 241-242, 260-261, 433-434, 449-450, 466-467, 502-503, 522-523, 541-542, 711-714, 1276-1280, 1543-1546, 1955-1956` (and ~95 more) | `if (ioctl(...)) RETURN_ERROR(MINOR, E_INVALID_OPERATION, NO_MSG)` discards both the ioctl return and `errno` — every kernel error (EBUSY, EFAULT, EINVAL, ENOMEM …) is collapsed into one `E_INVALID_OPERATION` |
| F10 | **P1** | D5 / FD-leak | `fm_lib.c:1738-1752` | `FM_PORT_Close` does `close(p_Dev->fd)` then `free(p_Dev->h_UserPriv)` then `free(p_Dev)`; cleanup ordering symmetric with F05 — fragile if any future field requires teardown after the close |
| F11 | **P1** | D17 / partial-delete | `fm_lib.c:2279-2304` | `FM_PCD_FrmReplicRemoveMember` calls `p_PcdDev->owners--; free(p_Dev);` after a successful member-remove ioctl. `p_Dev` is the *replic-group* handle, not a per-member handle — every successful member removal frees the group device, leaving a dangling pointer in the caller. Copy-paste defect from `FrmReplicDeleteGroup` (line 2215) |
| F12 | **P1** | D17 / owners underflow | `fm_lib.c:643, 859, 958, 1314, 1517, 1608, 2231, 2298, 2537` | Every Delete-style wrapper does `p_PcdDev->owners--;` *unconditionally*. Only `FM_PCD_KgSchemeDelete` (line 753) guards `if (owners > 0)`. Unbalanced delete underflows `uint32_t owners` to 0xffffffff — `FM_PCD_Close` then takes the "still has modules bound" branch forever, leaking the parent fd |
| F13 | **P2** | D6 / NULL-handle | `fm_lib.c:362-402, 1653-1736` | `FM_PCD_Open` checks `p_FmPcdParams->h_Fm` but **not `p_FmPcdParams` itself**; `FM_PORT_Open` likewise — caller passing NULL params traps in `((t_Device*)…->h_Fm)->id` |
| F14 | **P2** | Debug-spam | `fm_lib.c:597-601, 619, 1889-1908` | Six `fprintf(stderr, "DBG ...")` calls left in production code paths (`FM_PCD_NetEnvCharacteristicsSet`, `FM_PORT_SetPCD`). Dump fd numbers and kernel addresses to stderr on every PCD load — noise and information disclosure |
| F15 | **P2** | D5 / version-check | `fm_lib.c:152, 178-195` | `static bool called = FALSE;` makes the version-mismatch banner per-process, not per-fd. Closing fm0 and reopening fm1 silently skips the version check on the second device |
| F16 | **P2** | D9 / int-overflow | `fm_lib.c:1387-1403` | `FM_PCD_MatchTableModifyKey` does `memcpy(key, p_Key, keySize)` into stack `key[IOC_FM_PCD_MAX_SIZE_OF_KEY]`. Today safe (MAX=56, uint8 max=255), but no bounds check; if MAX is ever raised above 255 silent stack overflow |
| F17 | **P2** | D17 / no audit | `fm_lib.c:404-422` | `FM_PCD_Close` only `XX_Print`s on owner imbalance, no abort/log-once. Caller lifecycle bugs silently leak descriptors |
| F18 | **P2** | D18 / missing UAPI | `fm_lib.c:329, 348` | `FM_ReadTimeStamp` / `FM_GetTimeStampIncrementPerUsec` issue ioctls (`FM_IOC_READ_TIMESTAMP`, `FM_IOC_GET_TIMESTAMP_INCREMENT`) **not present** in lf-6.6.y UAPI. Patch description states they should be stubbed; working tree still calls the kernel — runtime ENOTTY at every invocation |
| F19 | **P2** | O1 / linear scan | `fm_lib.c:1686-1713` | `FM_PORT_Open` 7-case `switch` over `e_FmPortType`; could be a static `[type] = {prefix, offset_extra}` table, eliminating the long branch on hot init |
| F20 | **P2** | O3 / open-per-call | `fm_lib.c:147-198, 362-402, 1653-1736` | Every `*_Open` does a fresh `open("/dev/fmN…", O_RDWR)`. fmc + dpa_app open ~18 fds per boot; a basename-keyed cache would refcount them and remove ~14 syscalls |

---

## 2. Module inventory

Single-TU library. All public ABI is delivered through 18 headers under
`include/fmd/`; the implementation lives entirely in `src/fm_lib.c`
(2705 LoC, 1 file). No internal helpers split out.

| File | LoC | Public API count | Producer (kernel UAPI) cross-check |
|------|-----|------------------|-------------------------------------|
| `src/fm_lib.c` | 2705 | ~105 functions exported (`FM_*`, `FM_PCD_*`, `FM_PORT_*`, `FM_MAC_*`, `FM_VSP_*`, `FM_CtrlMon*`) | uses `fm_ioctls.h`, `fm_pcd_ioctls.h`, `fm_port_ioctls.h`, `fm_vsp_ioctls.h` from kernel UAPI tree |
| `include/fmd/Peripherals/fm_pcd_ext.h` | 4016 | `t_FmPcd*` struct ABI | **Mismatch** in `t_FmPcdKgSchemeParams` (extra `bool shared`) and `t_FmPcdHashTableParams` (extra `agingSupport`, `externalHash`, `externalHashParams`) — see F01/F02 |
| `include/fmd/Peripherals/fm_port_ext.h` | 2608 | `t_FmPort*` struct ABI | matches kernel `ioc_*` 1:1 |
| `include/fmd/Peripherals/fm_mac_ext.h` | 843 | MAC API | n/a — fmlib has no FM_MAC_* ioctls of its own; routed through fm_port fd |
| `include/fmd/Peripherals/fm_vsp_ext.h` | 405 | VSP API | matches |
| `include/fmd/Peripherals/fm_ext.h` | 626 | core FM API | matches |
| 12 other headers | ~3600 total | enums / types only | n/a |

### 2.1 Critical ioctl ABI cross-check (D18)

`FM_IOC_TYPE_BASE` = 0xe1 (= `NCSW_IOC_TYPE_BASE` 0xe0 + 1, see
`integration_ioctls.h:43` and `ioctls.h:52`).
`FM_PCD_IOC_NUM(n) = n + 20` (`fm_ioctls.h:63`).

| ioctl name | dir | nr | size (bytes, kernel) | expected `_IOC` | "ask24" baseline | match |
|------------|-----|----|----------------------|------------------|------------------|------|
| `FM_PCD_IOC_HASH_TABLE_SET` | `_IOWR` | 37+20 = 57 = 0x39 | sizeof(ioc_fm_pcd_hash_table_params_t) = 0x078 (120 B) | 0xc078e139 | 0xc078e139 | ✓ |
| `FM_PCD_IOC_KG_SCHEME_SET` | `_IOWR` | 24+20 = 44 = 0x2C | sizeof(ioc_fm_pcd_kg_scheme_params_t) | 0x????e12c | (see F01) | mostly ✓ but **userspace `t_FmPcdKgSchemeParams` is +1 byte** ⇒ payload mismatch |
| `FM_PCD_IOC_NET_ENV_CHARACTERISTICS_SET` | `_IOWR` | 20+20 = 40 = 0x28 | sizeof(ioc_fm_pcd_net_env_params_t) | match | match | ✓ |
| `FM_PCD_IOC_MATCH_TABLE_SET` | `_IOWR` | 28+20 = 48 = 0x30 | `sizeof(void*)` (kernel uses `void *` workaround at `fm_pcd_ioctls.h:2650`) | match | match | ✓ |
| `FM_IOC_READ_TIMESTAMP` | — | not defined in lf-6.6.y UAPI | — | — | — | **F18** |
| `FM_IOC_GET_TIMESTAMP_INCREMENT` | — | same | — | — | — | F18 |

The `_IOC` cookie size is taken from `sizeof(struct)` at the kernel
side; if userspace's same-named struct is larger or smaller, the
kernel's `copy_from_user(…, _IOC_SIZE(cmd))` reads only the kernel
size — fields beyond it are silently dropped, while the *userspace*
`memcpy` overruns the destination. F01 is the worst case.

---

## 3. P0 findings (fix before next boot)

### F01 — `bool shared` in `t_FmPcdKgSchemeParams` desyncs from kernel

`fm_pcd_ext.h:1714-1761` (post-patch) defines:

```
typedef struct t_FmPcdKgSchemeParams {
    bool                                modify;
    union { ... } id;
    bool                                shared;     /* <-- ADDED by ASK patch */
    bool                                alwaysDirect;
    struct { ... } netEnvParams;
    ...
} t_FmPcdKgSchemeParams;
```

Kernel UAPI `fm_pcd_ioctls.h:1233-1285` does **not** have a corresponding
`shared` field. With C99 `bool` plus alignment, fmlib's struct is up
to 4 bytes larger than the kernel's, and *every field after `id`* sits
at a different offset.

`fm_lib.c:688`:

```
memcpy(&params, p_Scheme, sizeof(t_FmPcdKgSchemeParams));
```

Two distinct bugs at once:

1. **Stack overflow write**: `params` is `ioc_fm_pcd_kg_scheme_params_t`,
   smaller than `t_FmPcdKgSchemeParams` — the `memcpy` writes past
   `params`'s end, scribbling whatever lives next on the stack.
2. **Field shift**: even if (1) didn't matter, kernel reads
   `always_direct` from the offset that fmlib wrote `shared` into, etc.
   `next_engine` (an enum used to dispatch) is read from a wrong slot —
   undefined behaviour, in practice the kernel routes to the wrong PCD
   engine.

**Fix**: either (a) drop `shared` from `t_FmPcdKgSchemeParams` and pass
"shared" out-of-band, or (b) extend the kernel ioc_t the same way and
bump `FM_PCD_IOC_KG_SCHEME_SET` size, or (c) do a field-by-field
translation in `FM_PCD_KgSchemeSet` instead of `memcpy`.

The fmc mono delta uses `shared` to drive shared-scheme replication;
cross-reference `ask-ls1046a-6.6/patches/fmc/01-mono-ask-extensions.patch`
to determine whether the bit is ever actually set on the wire.

### F02 — `t_FmPcdHashTableParams` lacks ASSERT_COND tripwire

`fm_pcd_ext.h:1999-2023` (patched) has the layout:

```
ccNextEngineParamsForMiss
agingSupport          /* added by ASK */
#if DPAA_VERSION>=11
externalHash          /* added by ASK */
#endif
table_type
struct { timeout_val, ... }   /* anonymous */
#if DPAA_VERSION>=11
externalHashParams { dataMemId, dataLiodnOffs, missMonitorAddr } /* added */
#endif
```

Kernel `fm_pcd_ioctls.h:1506-1554` matches with `DPAA_VERSION=11`
(`dpaa_integration_LS1043.h:40` & `dpaa_integration_FMAN_V3L.h:45`).

But `fm_lib.c:1255` does:

```
memcpy(&params, p_Param, sizeof(t_FmPcdHashTableParams));
```

with no `ASSERT_COND(sizeof(t_FmPcdHashTableParams) ==
sizeof(ioc_fm_pcd_hash_table_params_t))` (compare ASSERT_COND uses at
lines 219, 236, 461, 974, 1000, 1027, 1075, 1104, 1135, 1206, 1420,
1622, 1806, 1881, 2047, 2104). Without the tripwire, any future
asymmetric edit of either struct silently corrupts the wire.

Additionally `params.id = NULL;` (line 1256) is the *only* explicit
init — `agingSupport`, `externalHash`, the entire reassembly anon-struct
and `externalHashParams` rely on the caller having zeroed its source.
Add `memset(&params, 0, sizeof(params))` *before* the `memcpy`.

### F03 — `sprintf` into fixed-size `devName` (D7)

```
char devName[20];                              // fm_lib.c:151
sprintf(devName, "%s%s%d", "/dev/", DEV_FM_NAME, id);   // line 165
```

`DEV_FM_NAME` is `"fm"`, so the literal prefix is `/dev/fm` (7 chars) +
at most 3 chars for `id` + NUL = 11 — buffer is fine for `FM_Open`.
**But** `FM_PORT_Open` at line 1685 uses `devName[30]` and formats
`"%s%s%u-port-rx%d"` — `/dev/fm` + 10 (uint32 worst case) + `-port-rx`
+ 10 (int worst case) + NUL = 36 bytes, exceeds 30 by 6.  Today the
relevant ids are < 16 so it never trips, but if
`FM_MAX_NUM_OF_1G_RX_PORTS+portId` (line 1697-1698) ever exceeds 99
the formatted result is 31+ chars and overflows. `snprintf(devName,
sizeof devName, ...)` plus a return-on-truncation check is the fix.

### F04 — `FM_GetApiVersion` return discarded (D3)

```
FM_GetApiVersion((t_Handle)p_Dev, &ver);    // fm_lib.c:182
if (FMD_API_VERSION_MAJOR != ver.version.major || ...)
    printf("Warning:...");
```

If the GET_API_VERSION ioctl fails (line 312 returns
`E_INVALID_OPERATION` silently because the call site doesn't capture
the return), `ver` is uninitialised and the printed comparison is
meaningless. Likely benign — but on a kernel without the version
ioctl this path leaks 4-12 bytes of stack into stderr.

### F05 — `FM_PCD_Close` early-return leaks `t_Device`

```
void FM_PCD_Close(t_Handle h_FmPcd) {                       // fm_lib.c:404
    ...
    close(p_Dev->fd);                                       // line 412
    if (p_Dev->owners) {
        XX_Print("Trying to delete a pcd handler ...");
        return;                                             // line 417: leaks p_Dev
    }
    free(p_Dev);                                            // line 420
}
```

The `close()` happens *before* the owner check, so the owner-leaked
path also has an already-closed fd embedded in `p_Dev->fd`.
Two fixes: move `close()` after the owner check (or refuse to close
when owners != 0) **and** add `free(p_Dev)` to the early-return branch.

---

## 4. P1 findings

### F06 — Set-side malloc failure leaks kernel-side object

Pattern (e.g. `fm_lib.c:1276-1287`):

```
if (ioctl(p_PcdDev->fd, FM_PCD_IOC_HASH_TABLE_SET, &params)) { ... return NULL; }
p_Dev = malloc(sizeof(t_Device));
if (!p_Dev) {
    REPORT_ERROR(MAJOR, E_NO_MEMORY, ...);
    return NULL;          /* kernel hash table never deleted */
}
```

If `malloc` fails, the kernel-side hash table (or scheme, profile,
manip-node, cc-tree, cc-node, replic-group, vsp, …) lives forever.
Apply: `if (!p_Dev) { ioctl(fd, FM_PCD_IOC_HASH_TABLE_DELETE, &params.id); return NULL; }`
to all 9 sites listed in F06.

### F07 — Unconditional next-engine pointer deref

`fm_lib.c:1038-1041`:

```
if (p_FmPcdCcNextEngineParams->nextEngine == e_FM_PCD_CC) {
    t_Device *p_NextDev = (t_Device*) p_FmPcdCcNextEngineParams->params.ccParams.h_CcNode;
    params.cc_next_engine_params.params.cc_params.cc_node_id = UINT_TO_PTR(p_NextDev->id);
}
```

No `if (p_NextDev)` guard. Same in lines 1043-1044, 1085, 1114, 1147,
1218, 1340, 2178, 2257, 2261. A caller that builds a tree from
config and forgets to bind a key's next-engine handle gets SIGSEGV
inside fmlib instead of `E_INVALID_HANDLE`.

### F08 — Caller-provided `num_of_keys` / `num_of_schemes` walks stack

`fm_lib.c:882`:

```
for (i = 0; i < params.keys_params.num_of_keys; i++) {  /* no upper bound */
    ... params.keys_params.key_params[i] ...
}
```

`key_params` is fixed-size `IOC_FM_PCD_MAX_NUM_OF_KEYS`. If caller's
`num_of_keys` exceeds the array bound the loop walks past the end of
`params` (stack copy) and DEV_TO_ID-rewrites unrelated stack memory.
Kernel will reject the ioctl on the size mismatch, but only *after*
the wrapper has already trampled its own stack. Add:

```
if (params.keys_params.num_of_keys > IOC_FM_PCD_MAX_NUM_OF_KEYS)
    RETURN_ERROR(MINOR, E_INVALID_VALUE,
                 ("num_of_keys=%u > MAX", params.keys_params.num_of_keys));
```

at every loop site (lines 882, 1921, 2110, 2133, 2175).

### F09 — Every ioctl error collapses to `E_INVALID_OPERATION`

`fm_lib.c` has ~110 occurrences of:

```
if (ioctl(...)) RETURN_ERROR(MINOR, E_INVALID_OPERATION, NO_MSG);
```

This loses both the ioctl return value and `errno`. fmc cannot tell
whether a scheme-set failed because the kernel ran out of MURAM vs.
because the caller passed a stale handle. Either replace the macro
expansion or provide a new `RETURN_ERROR_ERRNO` that interpolates
`strerror(errno)`.

### F10 — `FM_PORT_Close` cleanup ordering

Same close-before-check pattern as F05. `h_UserPriv` is freed
unconditionally — fine in current code, but fragile if any future
field requires teardown after the close.

### F11 — `FrmReplicRemoveMember` frees the group device

`fm_lib.c:2298-2299`:

```
if (ioctl(p_PcdDev->fd, FM_PCD_IOC_FRM_REPLIC_MEMBER_REMOVE, &param)) ...
p_PcdDev->owners--;
free(p_Dev);                /* p_Dev is the GROUP, not a member */
```

`RemoveMember` should leave the group alive. The body was clearly
copied from `DeleteGroup` (line 2231-2232) without removing the
destruction step. Fix: delete the `p_PcdDev->owners--` and
`free(p_Dev)` lines entirely; members have no fmlib-side handle to
free.

### F12 — Unbalanced `owners--` underflow

Most Delete wrappers do `p_PcdDev->owners--` unconditionally
(`fm_lib.c:643, 859, 958, 1314, 1517, 1608, 2231, 2298, 2537`). Only
`FM_PCD_KgSchemeDelete` (line 753) guards `if (owners > 0)`. A
double-delete (or a delete on a handle that was never tracked, e.g.
via `FrmReplicRemoveMember`) underflows `uint32_t owners` to
`UINT32_MAX` and traps `FM_PCD_Close` in the owner-leak branch (F05)
forever.

---

## 5. P2 findings

### F13 — NULL params not checked

`FM_PCD_Open:362` checks `p_FmPcdParams->h_Fm` but not
`p_FmPcdParams`; `FM_PORT_Open:1653` likewise.
`FM_VSP_Config:2462` does `SANITY_CHECK_RETURN_VALUE((void*)p_FmVspParams, …)`
which works, but the immediate next line dereferences before the
macro's check has unwound.

### F14 — Production stderr debug spam

`fm_lib.c:597-601, 619, 1889-1908` emit `fprintf(stderr, "DBG ...")`
on every PCD load — adds noise to journald and discloses kernel ioctl
numbers and pointer values. Wrap in `#ifdef FM_LIB_DBG` like the
`_fml_dbg` helper at line 68-74.

### F15 — `FM_Open` version-check `static called` is process-global

A daemon that opens fm0 and fm1 in sequence checks the version on fm0
only. Tie `called` to `p_Dev->id` (or check on every open).

### F16 — Stack `key[IOC_FM_PCD_MAX_SIZE_OF_KEY]` vs `keySize` (uint8_t)

`fm_lib.c:1387-1403`. Today safe (MAX is 56, uint8 max 255), but no
comment guards against MAX being raised. Add
`if (keySize > IOC_FM_PCD_MAX_SIZE_OF_KEY) RETURN_ERROR(...)`.

### F17 — `FM_PCD_Close` no audit trail for unbalanced lifecycle

Caller bug detection is by `XX_Print` only; no abort/log-once.

### F18 — `FM_ReadTimeStamp` / `FM_GetTimeStampIncrementPerUsec`

The patch description states these should be stubbed to return 0 on
lf-6.6.y; however the working-tree `fm_lib.c:329, 348` still does
`ioctl(...)` which returns `ENOTTY`. Verify whether the patch's stub
hunk landed; if not, apply it manually (replace bodies with
`(void)h_Fm; return 0;`).

---

## 6. Optimization candidates

| # | Where | Cat | Note |
|---|-------|-----|------|
| O01 | `fm_lib.c:1686-1713` | O1 | port-type → device-name dispatch is a 7-case `switch`. A static `[type] = { fmt, offset_extra }` table would shrink the function from 28 to 8 lines |
| O02 | `fm_lib.c:147, 362, 1653` | O3 | every `*_Open` does a fresh `open("/dev/fmN…", O_RDWR)`. fmc opens 1 base FM, 1 PCD, 16 ports per boot = ~18 syscalls. Cache by basename in a static `t_Device *` table — drops ~14 of those 18 to a refcount bump |
| O03 | F09 (RETURN_ERROR) | O7 | `RETURN_ERROR` on the hot path emits to stderr + builds a printf format. Add a `_FAST` variant for the high-traffic per-key add/remove ioctls |
| O04 | `fm_lib.c:1255, 1448, 1536, 2109, 2474` | O5 | `memcpy(&params, p_Foo, sizeof(t_Foo))` followed by ~5 DEV_TO_ID rewrites. Without LTO this is two passes over the struct |
| O05 | `fm_lib.c:1399-1403` | O8 | unconditional `memcpy(mask, p_Mask, keySize)` with `keySize ≤ 56` — small enough to use `__builtin_memcpy` inline |
| O06 | `fm_lib.c:1644-1647 (t_FmPort)` | O9 | `t_FmPort` is a 2-byte payload (`portType` + `portId`) wasting ~14 bytes of malloc overhead per port. Pack into `t_Device->h_UserPriv` as a `uintptr_t` |

---

## 7. Recommendations (ranked)

1. **Resolve F01/F02 ABI delta before the next boot.**  Either revert
   the `bool shared` extension to `t_FmPcdKgSchemeParams` (and pass
   shared-ness via a separate ioctl such as a new
   `FM_PCD_IOC_KG_SCHEME_SET_SHARED`) or extend the kernel UAPI struct
   so that fmlib and kernel agree on the size embedded in the
   `_IOWR(...)` cookie. Today's working tree silently corrupts the
   wire-format every time fmc creates a scheme.

2. **Add `ASSERT_COND(sizeof(t_FmPcdKgSchemeParams) == sizeof(ioc_fm_pcd_kg_scheme_params_t))`**
   at the top of every `*_Set` wrapper that uses `memcpy` translation.
   Existing patterns at lines 219, 236, 461, 974, 1000, 1027, 1075,
   1104, 1135, 1206, 1420, 1622, 1806, 1881, 2047, 2104 cover ~16 of
   the ~25 candidate structs — add the missing 9, **especially**
   `t_FmPcdKgSchemeParams` (line 688) and `t_FmPcdHashTableParams`
   (line 1255) where ASSERT_COND is conspicuously absent.

3. **Replace every `memcpy(&params, caller, sizeof(t_*))` with explicit
   field translation** for the structs whose `t_*` and `ioc_*` are not
   `static_assert`-equal. Only ~4 structs need this surgery
   (`t_FmPcdKgSchemeParams`, `t_FmPcdHashTableParams`,
   `t_FmPortPcdParams`, `t_FmPcdCcTreeParams`).

4. **Plug F06 leak** by reverting the kernel-side allocation when the
   userspace `malloc` fails.  Boilerplate; ~9 sites.

5. **Fix F11** (`FrmReplicRemoveMember` corrupting the group handle) —
   one-line bug, urgent.

6. **Fix F05** (`FM_PCD_Close` close-before-check + early-return leak)
   — three lines.

7. **Plug F12** owners-underflow by replacing every
   `p_PcdDev->owners--` with `if (p_PcdDev->owners) p_PcdDev->owners--`.

8. **Strip F14 stderr `DBG` spam** before next vendor release; gate
   under `FM_LIB_DBG` like the existing `_fml_dbg` helper.

9. **Replace `RETURN_ERROR(MINOR, E_INVALID_OPERATION, NO_MSG)`** at
   ioctl sites with a variant that includes `errno` /
   `strerror(errno)` so callers can route on EBUSY vs EINVAL.

10. **Convert `sprintf`→`snprintf`** in F03 and check truncation;
    cheap defensive hardening.

11. **(Opt)** Implement O02 fd-cache for `/dev/fmN…` — a one-screen
    change that removes ~70 `open()`s per boot in fmc + dpa_app.

12. **Re-validate the patch hunks:** the audit found two timestamp
    helpers (`fm_lib.c:320-356`) that the patch description claims
    are stubbed but the working tree still issues the ioctl. Re-apply
    or re-confirm.

---

## Appendix A — ABI drift table (for future regressions)

| struct | userspace LoC | kernel UAPI LoC | size delta | layout-shifted fields |
|--------|---------------|-----------------|------------|------------------------|
| `t_FmPcdKgSchemeParams` ↔ `ioc_fm_pcd_kg_scheme_params_t` | `fm_pcd_ext.h:1714-1761` (+1 field) | `fm_pcd_ioctls.h:1233-1285` | **+1 byte (+ alignment) USERSPACE** | every field after `id` |
| `t_FmPcdHashTableParams` ↔ `ioc_fm_pcd_hash_table_params_t` | `fm_pcd_ext.h:1999-2023` | `fm_pcd_ioctls.h:1506-1554` | nominally equal, but no ASSERT_COND | none today, but no tripwire |
| All other Set-side structs | — | — | equal | covered by ASSERT_COND |

End of audit.
