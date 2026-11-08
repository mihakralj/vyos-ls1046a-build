"""bin/wg-line.py: six medians in up/down/bidir x v4/v6 order, failures excluded, missing cells marked."""
import csv
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "wg_line", Path(__file__).resolve().parents[1] / "bin/wg-line.py")
wg_line = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wg_line)


def test_six_medians_in_order(tmp_path):
    p = tmp_path / "r.csv"
    rows = [("wg-v4", "up", g) for g in (2.6, 2.7, 2.8)] + [
        ("wg-v6-eth3", "up", 2.0), ("wg-v4", "down", 1.6), ("wg-v4", "down", 0.0),
        ("wg-v6", "down", 1.5), ("wg-v4", "bidir", 2.2), ("plain-v4", "up", 9.4)]
    with open(p, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ts", "cell", "dir", "rep", "gbps"])
        for cell, d, g in rows:
            w.writerow(["t", cell, d, 1, g])
    out = wg_line.line(wg_line.medians([p]), "img sw eth4")
    assert out == "img sw eth4   2.70  2.00  1.60  1.50  2.20     -"
