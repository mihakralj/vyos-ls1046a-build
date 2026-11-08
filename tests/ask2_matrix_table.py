"""bin/ask2-matrix-table.py: cell mapping, fastest rep, "-" for unmeasured slots, exact two-row header."""
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "amt", Path(__file__).resolve().parents[1] / "bin/ask2-matrix-table.py")
amt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(amt)


def test_table(tmp_path):
    q = ["ts,cell,gbps,dut_busy,dut_softirq,gen_cpu,retrans,hw_pkts,data_pkts_est,hw_ratio,verdict"]
    for cell, g in [("unicast-v4", 9.40), ("unicast-v4", 9.42), ("unicast-v4", 9.30),
                    ("unicast-v6-rev", 9.36), ("unicast-v4-bidir", 16.93),
                    ("pppoe-up-v4", 9.35), ("pppoe-down-v4", 9.33), ("nat-v4", 0.0)]:
        q.append(f"t,{cell},{g},0,0,0,0,0,0,1.0,HW")
    (tmp_path / "quick-uni-r1.csv").write_text("\n".join(q) + "\n")
    (tmp_path / "wg-185-eth4.csv").write_text(
        "ts,cell,dir,rep,gbps\nt,wg-v4,up,1,2.45\nt,wg-v6,bidir,1,4.7\n")
    lines = amt.render(amt.collect(tmp_path)).splitlines()
    assert lines[0] == "Flow type       Upload          Download          Bidir"
    assert lines[1] == "                v4      v6      v4      v6      v4      v6"
    assert lines[2] == "unicast       9.42       -       -    9.36    16.9       -"
    assert lines[3] == "nat              -       -       -       -       -       -"
    assert lines[6] == "pppoe         9.35       -    9.33       -       -       -"
    assert lines[7] == "wireguard     2.45       -       -       -       -    4.70"
    assert len(lines) == 14
