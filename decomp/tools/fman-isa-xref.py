#!/usr/bin/env python3
"""Full field-level decode: parse bit-layout per instruction row, extract
actual operand values from each matched word (not just the mnemonic)."""
import re, struct, sys, html as htmlmod

HTML = "/mnt/builds/vyos-ls1046a-build/arch/fman-instruction-table.html"
BLOB = "/tmp/kilo/fman-ucode-mtd3.bin"

def load_words(path):
    raw = open(path, "rb").read()
    length = struct.unpack(">I", raw[0:4])[0]
    blob = raw[:length]
    code_off = 244
    words = []
    for i in range((len(blob) - 4 - code_off) // 4):
        words.append(struct.unpack(">I", blob[code_off + i*4:code_off + i*4 + 4])[0])
    return words

FIELD_RE = re.compile(r'<span class="bits">([^<]+)</span>\s*<span class="segment (\w+)">([^<]*)</span>')

def load_table(path):
    html = open(path, encoding="utf-8", errors="replace").read()
    rows = re.split(r'<tr[^>]*>', html)
    out = []
    for row in rows:
        m = re.search(r'"mnemonic"><code>([^<]+)</code>', row)
        v = re.search(r'value 0x([0-9a-fA-F]+)', row)
        mk = re.search(r'mask&nbsp; 0x([0-9a-fA-F]+)', row)
        d = re.search(r'class="description">([^<]*)', row)
        pc = re.search(r'class="pseudocode"><code>(.*?)</code></td>', row, re.S)
        fields = FIELD_RE.findall(row)
        if m and v and mk:
            out.append({
                "mn": m.group(1), "val": int(v.group(1), 16), "mask": int(mk.group(1), 16),
                "desc": htmlmod.unescape(d.group(1)) if d else "",
                "pseudo": htmlmod.unescape(re.sub('<[^<]+?>', '', pc.group(1))) if pc else "",
                "fields": fields,
            })
    return out

def popcount(x):
    return bin(x).count("1")

def bitrange(w, hi, lo):
    return (w >> lo) & ((1 << (hi - lo + 1)) - 1)

def extract_fields(w, fields):
    out = {}
    for bits, kind, label in fields:
        if kind == "fixed":
            continue
        name = label.split(":")[0].strip()
        if ".." in bits:
            hi, lo = [int(x) for x in bits.split("..")]
        else:
            hi = lo = int(bits)
        out[name] = bitrange(w, hi, lo)
    return out

def main():
    a, b = int(sys.argv[1]), int(sys.argv[2])
    words = load_words(BLOB)
    table = load_table(HTML)
    for i in range(a, min(b+1, len(words))):
        w = words[i]
        matches = [e for e in table if (w & e["mask"]) == e["val"]]
        matches.sort(key=lambda e: -popcount(e["mask"]))
        if not matches:
            print(f"w{i:<6d} {w:08x}  -- no match --")
            continue
        e = matches[0]
        fv = extract_fields(w, e["fields"])
        fvstr = " ".join(f"{k}=0x{v:x}" for k, v in fv.items())
        print(f"w{i:<6d} {w:08x}  {e['mn']:<16s} {fvstr}")
        print(f"          pseudo: {e['pseudo'].strip()[:140]}")

if __name__ == "__main__":
    main()
