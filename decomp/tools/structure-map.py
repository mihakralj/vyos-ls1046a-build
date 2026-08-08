#!/usr/bin/env python3
"""structure-map.py — zero-ISA structural analysis of FMan microcode blobs.

Phase 2 of the decomp program (see decomp/02-static-structure.md). No opcode
semantics are assumed; everything here is derived from container structure,
branch-target statistics, data-run detection, and cross-blob alignment.

Outputs decomp/maps/<tag>-structure.json per analyzed blob.

  python3 structure-map.py TARGET.bin [--vs OTHER.bin ...] [--tag NAME]
      [--outdir DIR] [--top N]
"""
import argparse
import json
import struct
from collections import Counter
from pathlib import Path

DISPATCH_SLOTS = 24          # 24 slots x 2 words at bytes 0x00..0xBF
DISPATCH_WORDS = 48          # table length in words (0xC0 bytes); targets are
                             # word offsets counted from end of table (word 48)
PAD_WORD = 0xFFFFFFFF
MIN_DATA_RUN = 4             # >=4 consecutive pad words = data/pad region
MATCH_MIN_WORDS = 4          # alignment: >=16 B identical = meaningful match


def load_words(path):
    """Minimal QEF parse: return (words, meta) for the first code section."""
    raw = Path(path).read_bytes()
    length = struct.unpack(">I", raw[0:4])[0]
    assert raw[4:7] == b"QEF" and raw[7] == 1, f"{path}: not QEF v1"
    blob = raw[:length]
    d = blob[124:244]
    wcount, code_off = struct.unpack(">II", d[104:112])
    words = list(struct.unpack(f">{wcount}I", blob[code_off:code_off + wcount * 4]))
    meta = {"path": str(path), "id": blob[8:70].split(b"\0")[0].decode(),
            "wcount": wcount, "version": list(d[112:115])}
    return words, meta


def dispatch_table(words):
    slots = []
    for i in range(DISPATCH_SLOTS):
        br, pad = words[i * 2], words[i * 2 + 1]
        populated = not (br == PAD_WORD and pad == PAD_WORD)
        slots.append({"slot": i, "branch": f"0x{br:08x}", "populated": populated,
                      "target_word": (DISPATCH_WORDS + (br & 0xFFFF))
                      if populated and (br >> 16) == 0xB7FF else None})
    return slots


def branch_class_stats(words, top_n):
    """Per top-16 prefix: count + fraction whose low16 is an in-range target.

    A branch encoding should mostly carry in-range word targets; an immediate
    or ALU class should look uniformly distributed. Purely statistical, but a
    strong opcode-family signal."""
    n = len(words)
    c = Counter(w >> 16 for w in words)
    stats = []
    for prefix, count in c.most_common(top_n):
        in_range = sum(1 for w in words
                       if (w >> 16) == prefix
                       and DISPATCH_WORDS + (w & 0xFFFF) < n)
        stats.append({"prefix": f"0x{prefix:04x}", "count": count,
                      "pct_of_code": round(100 * count / n, 2),
                      "in_range_targets": in_range,
                      "in_range_pct": round(100 * in_range / count, 1)})
    return stats


def harvest_entries(words):
    """Candidate function entry points: dispatch targets + in-range targets
    of the b7XX branch family (0xb7ff confirmed by the dispatch table;
    other b7XX treated as the same family pending Phase 4)."""
    n = len(words)
    entries = {}
    for slot in dispatch_table(words):
        if slot["populated"] and slot["target_word"] is not None:
            entries.setdefault(slot["target_word"], set()).add(
                f"dispatch slot {slot['slot']}")
    for i, w in enumerate(words):
        if (w >> 16) & 0xFF00 == 0xB700:
            t = DISPATCH_WORDS + (w & 0xFFFF)
            if t < n:
                entries.setdefault(t, set()).add(f"0x{(w >> 16):04x} @ w{i}")
    return [{"word": w, "byte": w * 4, "refs": sorted(r)}
            for w, r in sorted(entries.items())]


def data_runs(words):
    runs, start = [], None
    for i, w in enumerate(words):
        if w == PAD_WORD:
            if start is None:
                start = i
        else:
            if start is not None and i - start >= MIN_DATA_RUN:
                runs.append({"start_word": start, "end_word": i - 1,
                             "words": i - start})
            start = None
    if start is not None and len(words) - start >= MIN_DATA_RUN:
        runs.append({"start_word": start, "end_word": len(words) - 1,
                     "words": len(words) - start})
    return runs


def align(target_words, other_words):
    """Per target position, length of the longest word-sequence match found
    anywhere in other (exact, relocation-tolerant). Returns match array."""
    a = struct.pack(f">{len(target_words)}I", *target_words)
    b = struct.pack(f">{len(other_words)}I", *other_words)
    mlen = [0] * len(target_words)
    for i in range(len(target_words)):
        needle = a[i * 4:i * 4 + MATCH_MIN_WORDS * 4]
        if len(needle) < MATCH_MIN_WORDS * 4:
            break
        pos = b.find(needle)
        if pos < 0:
            continue
        # extend the 4-word seed as far as it goes
        l = MATCH_MIN_WORDS
        while (i + l < len(target_words)
               and pos + l * 4 + 4 <= len(b)
               and a[(i + l) * 4:(i + l) * 4 + 4] == b[pos + l * 4:pos + l * 4 + 4]):
            l += 1
        mlen[i] = l
    return mlen


def covered_runs(mlen, min_len=MATCH_MIN_WORDS):
    """Maximal runs where mlen >= min_len (shared) and where mlen < min_len
    (unique). Returns (shared_runs, unique_runs) as word ranges."""
    shared, unique, start, state = [], [], 0, None
    for i, l in enumerate(mlen + [0]):
        s = l >= min_len
        if state is None:
            state, start = s, i
        elif s != state:
            (shared if state else unique).append(
                {"start_word": start, "end_word": i - 1, "words": i - start})
            state, start = s, i
    return shared, unique


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("target")
    ap.add_argument("--vs", nargs="*", default=[])
    ap.add_argument("--tag", default=None)
    ap.add_argument("--outdir", default="decomp/maps")
    ap.add_argument("--top", type=int, default=40)
    args = ap.parse_args()

    words, meta = load_words(args.target)
    tag = args.tag or Path(args.target).stem.replace("fsl_fman_ucode_", "")
    out = {"blob": meta, "dispatch": dispatch_table(words),
           "branch_class_stats": branch_class_stats(words, args.top),
           "entries": harvest_entries(words),
           "data_runs": data_runs(words), "alignment": {}}

    for other_path in args.vs:
        owords, ometa = load_words(other_path)
        ml = align(words, owords)
        shared, unique = covered_runs(ml)
        cov = sum(r["words"] for r in shared)
        otag = Path(other_path).stem.replace("fsl_fman_ucode_", "")
        out["alignment"][otag] = {
            "other_id": ometa["id"],
            "covered_words": cov,
            "covered_pct": round(100 * cov / len(words), 1),
            "shared_runs": shared,
            "unique_runs_top": sorted(unique, key=lambda r: -r["words"])[:25],
        }
        print(f"{tag} vs {otag}: covered {cov}/{len(words)} words "
              f"({out['alignment'][otag]['covered_pct']}%), "
              f"{len(shared)} shared runs, {len(unique)} unique runs")

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    p = outdir / f"{tag}-structure.json"
    p.write_text(json.dumps(out, indent=1))
    print(f"wrote {p} "
          f"({len(out['entries'])} candidate entries, "
          f"{len(out['data_runs'])} data runs)")


if __name__ == "__main__":
    main()
