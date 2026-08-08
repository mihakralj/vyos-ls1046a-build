#!/usr/bin/env python3
"""cfg-map.py — CFG skeleton v2 for FMan microcode blobs.

Branch models (decomp/maps/anchors.json C01-C06, confidence as noted):
  0xb7ffXXXX  absolute branch  -> target word = 48 + XXXX          [verified]
  0xb3ffXXXX  relative branch  -> target = PC + sext16(XXXX)       [high]
  0xb43fXXXX  relative branch  -> target = PC + sext16(XXXX)       [statistical]
  0xbc3fXXXX  relative branch  -> target = PC + sext16(XXXX)       [statistical]
  0xa3ffXXXX  long rel branch  -> target = PC + sext16(XXXX)       [statistical]
  0xb7dfXXXX  park/halt stub   -> terminator (self-loop)           [high]

Block starts = dispatch-slot targets + all branch targets + fall-through
after any branch/park + pad-run boundaries. Also detects:
  - secondary jump tables: runs of >=3 consecutive 0xb7ff words
  - raw offset tables: runs of >=4 words all in (0, code_words) — candidate
    indexed-dispatch data (the Q05 mechanism signature)
Output: JSON block map + validation stats. ±1 on relative targets
(PC vs PC+1 base) is unresolved; both are reported where they differ in kind.
"""
import argparse
import json
import struct
from collections import Counter
from pathlib import Path

ABS, REL, PARK = "abs", "rel", "park"
ABS_PREFIXES = {0xB7FF}
# Conditional/relative-branch family (2026-08-08: extended after a family scan
# found b03f/b83f/b41f/bc1f/b81f/b01f/b45f/b17f/a7ff were missed — the branch
# opcode's _f suffix byte encodes the condition). a3ff = unconditional rel.
REL_PREFIXES = {0xB3FF, 0xB43F, 0xBC3F, 0xA3FF,
                0xB03F, 0xB83F, 0xB41F, 0xBC1F, 0xB81F, 0xB01F,
                0xB45F, 0xB17F, 0xA7FF}
PARK_PREFIXES = {0xB7DF}


def load_words(path):
    raw = Path(path).read_bytes()
    length = struct.unpack(">I", raw[0:4])[0]
    blob = raw[:length]
    wcount, code_off = struct.unpack(">II", blob[124 + 104:124 + 112])
    return list(struct.unpack(f">{wcount}I", blob[code_off:code_off + wcount * 4]))


def sext16(v):
    return v - 0x10000 if v >= 0x8000 else v


def classify(words):
    """Per word: branch class + resolved target (or None)."""
    info = {}
    n = len(words)
    for i, w in enumerate(words):
        p = w >> 16
        if p in ABS_PREFIXES:
            t = 48 + (w & 0xFFFF)
            info[i] = (ABS, t if t < n else None)
        elif p in REL_PREFIXES:
            t = i + sext16(w & 0xFFFF)
            info[i] = (REL, t if 0 <= t < n else None)
        elif p in PARK_PREFIXES:
            info[i] = (PARK, None)
    return info


def pad_runs(words, min_len=4):
    runs, start = [], None
    for i, w in enumerate(words):
        if w == 0xFFFFFFFF:
            if start is None:
                start = i
        else:
            if start is not None and i - start >= min_len:
                runs.append((start, i - 1))
            start = None
    return runs


def jump_table_runs(words, min_len=3):
    """Runs of >=min_len consecutive absolute-branch (0xb7ff) words outside
    the main 24-slot table (words 0..47)."""
    runs, start = [], None
    for i, w in enumerate(words):
        if i < 48:
            continue
        if (w >> 16) == 0xB7FF:
            if start is None:
                start = i
        else:
            if start is not None and i - start >= min_len:
                runs.append({"start_word": start, "end_word": i - 1,
                             "entries": [48 + (x & 0xFFFF)
                                         for x in words[start:i]]})
            start = None
    if start is not None and len(words) - start >= min_len:
        runs.append({"start_word": start, "end_word": len(words) - 1,
                     "entries": [48 + (x & 0xFFFF) for x in words[start:]]})
    return runs


def offset_table_runs(words, branch_info, min_len=4):
    """Runs of >=min_len words all in (0, code_words) that are not themselves
    branch words — candidate indexed-dispatch offset tables."""
    n = len(words)
    runs, start = [], None
    for i, w in enumerate(words):
        ok = (i not in branch_info) and 0 < w < n
        if ok and start is None:
            start = i
        elif not ok and start is not None:
            if i - start >= min_len:
                runs.append({"start_word": start, "end_word": i - 1,
                             "entries": words[start:i]})
            start = None
    if start is not None and n - start >= min_len:
        runs.append({"start_word": start, "end_word": n - 1,
                     "entries": words[start:]})
    return runs


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("target")
    ap.add_argument("--tag", default=None)
    ap.add_argument("--outdir", default="decomp/maps")
    args = ap.parse_args()

    words = load_words(args.target)
    n = len(words)
    tag = args.tag or Path(args.target).stem

    bi = classify(words)
    dispatch_targets = [48 + (words[i * 2] & 0xFFFF)
                        for i in range(24)
                        if (words[i * 2] >> 16) == 0xB7FF
                        and 48 + (words[i * 2] & 0xFFFF) < n]
    starts = set(dispatch_targets)
    for i, (kind, t) in bi.items():
        if t is not None:
            starts.add(t)
        if i + 1 < n:
            starts.add(i + 1)          # fall-through after branch/park
    for s, e in pad_runs(words):
        starts.add(s)
        if e + 1 < n:
            starts.add(e + 1)
    starts = sorted(s for s in starts if 0 <= s < n)

    blocks = []
    for k, s in enumerate(starts):
        e = (starts[k + 1] - 1) if k + 1 < len(starts) else n - 1
        term = None
        for i in range(e, s - 1, -1):
            if i in bi:
                term = {"at": i, "kind": bi[i][0], "target": bi[i][1]}
                break
        blocks.append({"start": s, "end": e, "words": e - s + 1,
                       "terminator": term})

    # validation stats for the relative-branch model
    rel = [(i, t) for i, (k, t) in bi.items() if k == REL]
    rel_valid = sum(1 for _, t in rel if t is not None)
    conv = Counter(t for _, t in rel if t is not None)
    converged = sum(1 for t, c in conv.items() if c >= 2)
    backward = [(i, t) for i, t in rel if t is not None and t < i]
    loops = [(i, t) for i, t in backward if 2 <= i - t <= 4000]
    starts_set = set(starts)

    out = {
        "blob": str(args.target), "words": n,
        "models": {"abs": sorted(f"0x{p:04x}" for p in ABS_PREFIXES),
                   "rel": sorted(f"0x{p:04x}" for p in REL_PREFIXES),
                   "park": sorted(f"0x{p:04x}" for p in PARK_PREFIXES),
                   "rel_target": "PC + sext16(low16), +/-1 unresolved"},
        "counts": {"block_starts": len(starts),
                   "abs_branches": sum(1 for k, _ in bi.values() if k == ABS),
                   "rel_branches": len(rel),
                   "parks": sum(1 for k, _ in bi.values() if k == PARK),
                   "dispatch_targets": len(dispatch_targets)},
        "rel_model_stats": {
            "targets_in_range_pct": round(100 * rel_valid / max(1, len(rel)), 1),
            "convergent_targets(>=2 branches share)": converged,
            "backward_branches": len(backward),
            "loop_like(2..4000 back)": len(loops),
            "target_is_block_start_pct": 100.0,  # by construction
        },
        "jump_tables": jump_table_runs(words),
        "offset_tables": offset_table_runs(words, bi),
        "pad_runs": [{"start_word": s, "end_word": e} for s, e in pad_runs(words)],
        "largest_blocks": sorted(blocks, key=lambda b: -b["words"])[:25],
        "blocks": blocks,
    }

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    p = outdir / f"{tag}-blocks.json"
    p.write_text(json.dumps(out, indent=1))

    print(f"{tag}: {n} words, {len(starts)} block starts")
    print(f"  branches: abs={out['counts']['abs_branches']} "
          f"rel={len(rel)} park={out['counts']['parks']}")
    rs = out["rel_model_stats"]
    print(f"  rel model: in-range {rs['targets_in_range_pct']}%, "
          f"convergent targets {rs['convergent_targets(>=2 branches share)']}, "
          f"backward {rs['backward_branches']} (loop-like {rs['loop_like(2..4000 back)']})")
    print(f"  secondary jump tables: {len(out['jump_tables'])}, "
          f"raw offset tables: {len(out['offset_tables'])}")
    for jt in out["jump_tables"][:10]:
        print(f"    JT @w{jt['start_word']}..w{jt['end_word']} "
              f"({len(jt['entries'])} entries) -> {jt['entries'][:8]}")
    for ot in out["offset_tables"][:10]:
        print(f"    OT @w{ot['start_word']}..w{ot['end_word']} "
              f"({len(ot['entries'])} entries) -> {ot['entries'][:8]}")
    print(f"  largest blocks: " + ", ".join(
        f"w{b['start']}+{b['words']}" for b in out["largest_blocks"][:8]))
    print(f"wrote {p}")


if __name__ == "__main__":
    main()
