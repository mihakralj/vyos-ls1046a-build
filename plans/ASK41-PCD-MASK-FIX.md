# ASK41 — Fix: SDK FMan rejects PCD program ioctl (`icIndxMask` last-nibble != 0)

## Symptom (ask40-r2 boot, build 25035168114)

```
[fm_cc.c:4074 IcHashIndexedCheckParams]: icIndxMask has to be with last nibble 0
[fm_cc.c:4577 MatchTableSet]: Invalid Value
[fm_cc.c:6879 FM_PCD_MatchTableSet]: Invalid Value
cdx_module_init::start_dpa_app failed rc 65280
```

`dpa_app` reaches the kernel cleanly (rebuild from r2 works); ioctl is rejected.

## Root cause — two incompatible mask conventions

### Upstream convention (NXP `nxp-qoriq/fmc@5b9f4b1` + SDK kernel `sdk_fman/Peripherals/FM/Pcd/fm_cc.c`)

- XML `<hashtable mask="..."/>` value is loaded as-is into `IcHashIndexedCheckParams.icIndxMask`.
- Kernel validator (`fm_cc.c:4061`):
  ```c
  if (glblMask & 0x000f)  // low nibble must be 0
      RETURN_ERROR(... "icIndxMask has to be with last nibble 0");
  countMask = glblMask >> 4;
  while (countMask) { countOnes++; countMask >>= 1; }
  if (numOfKeys != (1 << countOnes)) RETURN_ERROR(...);
  ```
- `FMCPCDReader.cpp:671` and `FMCTaskDef.cpp:138` agree: `numOfKeys = 1 << popcount(mask >> 4)`.
- **Hard cap**: `mask` is `uint16_t`, low nibble must be 0 → max legal mask `0xFFF0` → max `numOfKeys = 4096`.

### Local Mono convention (downstream divergence)

- `ask-ls1046a-6.6/dpa_app/files/etc/cdx_pcd.xml` uses `mask="0x7fff" | 0xff | 0xf` (low nibble all-1s; the "count − 1" form).
- `ask-ls1046a-6.6/dpa_app/dpa.c:574` reads it back as `num_sets = hashResMask + 1` (also count − 1 form).

These two pieces are internally consistent with each other but contradict the kernel's authoritative validator and NXP upstream fmc. The prebuilt vendor `dpa_app` SIGSEGVed before this code path was reached (ABI mismatch fixed by ask40), masking the bug.

## Fix strategy

### Two-part change to align Mono with NXP upstream

1. **`cdx_pcd.xml`**: convert every `mask=` to upstream form `mask = (count − 1) << 4` (i.e. left-shift by 4). Per-classification `max="512"` is the actual key budget; the mask only chooses the number of hash sets. All cases must satisfy `numOfKeys = 1 << popcount(mask >> 4) >= numOfSets` and `numOfKeys ≤ 4096`.
2. **`dpa_app/dpa.c:574`**: replace the `(hashResMask + 1)` formula with the upstream-correct `1 << popcount(hashResMask >> 4)` so the userspace bookkeeping matches the kernel's view.

### Mask conversion table (current → fixed)

| Current | Sets implied (current `mask+1`) | Fixed mask | Sets (upstream `1<<popcount(>>4)`) | OK |
|---|---|---|---|---|
| 0x7fff | 32768 | 0x7FF0 | 2048 | clamped to 2048 (>4096 is illegal) |
| 0xff   | 256   | 0xFF0  | 256  | identical |
| 0xf    | 16    | 0xF0   | 16   | identical |

Note on 0x7fff → 0x7FF0: the original "32768 sets" exceeds the FMan PCD hardware ceiling (4096). Closest legal mask is `0xFFF0` (4096 sets), but each classification only has `max="512"` keys, so any `numOfSets >= 512` works — `0x7FF0` (2048 sets, sparsely populated) is fine and preserves the qualitative intent of "many sets, few keys per set". This matches what fmc upstream implicitly enforces.

### Affected file (single source-of-truth)

- `ask-ls1046a-6.6/dpa_app/files/etc/cdx_pcd.xml` (16 hashtable entries)
- `ask-ls1046a-6.6/dpa_app/dpa.c:574` (1 line)

`cdx_cfg*.xml` and `cdx_sp.xml` contain no `<hashtable>` entries — verified.

## Stage-4 cmm autoconf bundle

Bundle into the same iteration: `bin/ci-build-ask-userspace.sh` to always pass `--build`/`--host` to cmm `configure` (avoids autoconf cross-compile detection failure on ubuntu-24.04-arm).

## Validation plan

1. Local: rerun fmc generator against fixed XML, verify `numOfKeys` reported in fmc model matches table.
2. Tag `kernel-6.6.135-ask41`, rebuild, deploy.
3. On Mono Gateway: `dmesg | grep -i pcd` should be clean; `systemctl status dpa_app` active; `ask-check` improves from 54/59 toward 59/59.