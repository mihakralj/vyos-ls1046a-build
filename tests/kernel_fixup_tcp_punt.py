"""Host-only regression coverage for the F-266 control punt fixup."""

from pathlib import Path
import subprocess
import sys

import pytest

pytestmark = pytest.mark.host
FIXUP = Path(__file__).resolve().parents[1] / "bin/kernel-fixups/F_266.py"


def load_fixup():
    ns = {"__name__": "f266"}
    exec(compile(FIXUP.read_text(), str(FIXUP), "exec"), ns)
    return ns


F266 = load_fixup()


def anchors():
    sources = {}
    for path, old, _new, _want in F266["EDITS"]:
        sources.setdefault(path, "/* upstream */\n")
        sources[path] += old + "/* more upstream */\n"
    return sources


def write_tree(root, sources):
    for path, text in sources.items():
        dest = root / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text)
    return root


@pytest.fixture
def tree(tmp_path):
    return write_tree(tmp_path, anchors())


def apply(tree):
    return subprocess.run(
        [sys.executable, "-B", str(FIXUP)], cwd=tree,
        capture_output=True, text=True, check=False,
    )


def snapshot(tree):
    return {p.relative_to(tree): p.read_bytes() for p in tree.rglob("*") if p.is_file()}


def test_f266_fresh_and_idempotent(tree):
    result = apply(tree)
    assert result.returncode == 0, result.stdout + result.stderr
    for path in {e[0] for e in F266["EDITS"]}:
        assert F266["MARK"] in (tree / path).read_text(), path
    before = snapshot(tree)
    result = apply(tree)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "already applied" in result.stdout
    assert snapshot(tree) == before


def test_f266_exports_and_declarations(tree):
    assert apply(tree).returncode == 0
    port_c = (tree / F266["PORT_C"]).read_text()
    pcd_c = (tree / F266["PCD_C"]).read_text()
    assert "EXPORT_SYMBOL_GPL(fman_port_sp_attach);" in port_c
    assert "int fman_port_sp_attach(" in (tree / F266["PORT_H"]).read_text()
    pcd_h = (tree / F266["PCD_H"]).read_text()
    assert "EXPORT_SYMBOL_GPL(fman_pcd_ctl_punt_set);" in pcd_c
    assert "int fman_pcd_ctl_punt_set(struct fman *fm, u8 hw_port_id, u32 mask);" in pcd_h
    assert "#define FMAN_PCD_PUNT_TCP_CTL\t0x1" in pcd_h
    assert "#define FMAN_PCD_PUNT_TTL\t0x2" in pcd_h
    assert "#define FMAN_PCD_PUNT_ALL\t(FMAN_PCD_PUNT_TCP_CTL | FMAN_PCD_PUNT_TTL)" in pcd_h
    assert "tcp_ctl_punt_arm" not in pcd_c + pcd_h
    assert "IPv6 HXS soft-sequence disarmed" not in port_c
    assert "HXS slot %u soft-sequence disarmed" in port_c


def test_f266_attach_keeps_other_ssa_bits(tree):
    assert apply(tree).returncode == 0
    body = (tree / F266["PORT_C"]).read_text().split("int fman_port_sp_attach(", 1)[1]
    body = body.split("EXPORT_SYMBOL_GPL(fman_port_sp_attach);", 1)[0]
    # Read-modify-write inside the PCAC stop/start bracket, with readback.
    stop = body.index("stop_port_hwp(port);")
    rd = body.index("old = ioread32be(&regs->pmda[hxs_slot].ssa);")
    rmw = body.index("(old & ~(FMAN_PORT_SSA_SW_PRS_EN | FMAN_PORT_SSA_SW_LABEL))")
    wr = body.index("iowrite32be(val, &regs->pmda[hxs_slot].ssa);")
    check = body.index("if (ioread32be(&regs->pmda[hxs_slot].ssa) != val)")
    assert stop < rd < rmw < wr < check
    assert body.count("start_port_hwp(port);") == 2
    # Mirror the C arithmetic on init_hwp()'s TCP value: 0x80000000 -> 0x80000420.
    old, label = 0x80000000, 0x020
    assert (old & ~(0x400 | 0x3FF)) | 0x400 | label == 0x80000420


def test_f266_parser_load_follows_rm_pen_rule(tree):
    assert apply(tree).returncode == 0
    pcd_c = (tree / F266["PCD_C"]).read_text()
    body = pcd_c.split("static int fman_pcd_prs_write(", 1)[1].split("\n}\n", 1)[0]
    irq_off = body.index("local_irq_save(flags);")
    pen_off = body.index("iowrite32be(saved & ~FMAN_PCD_PRS_RPIMAC_PEN, rpimac);")
    idle = body.index("FMAN_PCD_PRS_RPIMAC_PSTAT))")
    write = body.index("iowrite32be(code[i], prs + off + i * 4);")
    readback = body.index("if (ioread32be(prs + off + i * 4) != code[i])")
    pen_restore = body.index("iowrite32be(saved, rpimac);")
    irq_on = body.index("local_irq_restore(flags);")
    assert irq_off < pen_off < idle < write < readback < pen_restore < irq_on
    # A parser that never goes idle skips the write but still gets PEN back.
    assert body.index("goto restore;") < body.index("restore:\n") < pen_restore
    assert "#define FMAN_PCD_PRS_RPIMAC\t\t0x844" in pcd_c
    assert "#define FMAN_PCD_PUNT_BASE_LABEL\t0x020" in pcd_c
    assert "(FMAN_PCD_PUNT_BASE_LABEL * 2)" in pcd_c
    code = pcd_c.split("fman_pcd_punt_code[] = {", 1)[1].split("};", 1)[0]
    words = [w.strip() for w in code.replace("\n", " ").split(",") if w.strip()]
    # fmc --sp_only -t 0x20 output of specs/reference/ask2-sp/ctl_punt.xml,
    # loaded live on .156 on 2026-10-20.
    assert words == [
        "0x0000b78a", "0x00da0007", "0x00000000", "0x00004031",
        "0x0000095f", "0x00020050", "0x094f0002", "0x00500001",
        "0x1bff0001", "0x1bfe0000", "0xa38e403c", "0x00010000",
        "0xa38e403c", "0x00001843", "0x095f0002", "0x0050094f",
        "0x00020050", "0x1bff0001", "0x1bfe0001", "0x1bfe0000",
        "0x9f8e4050", "0x00010000", "0x9f8e4050", "0x00001857",
        "0x095f0002", "0x0050094f", "0x00020050", "0x1bff0001",
        "0x1bfe0001", "0x1bfe0000",
    ]
    # 30 words from byte 0x40 end before the next fmc label space (0xB8).
    assert 0x40 + 4 * len(words) == 0xB8


def test_f266_hxs_table_matches_fmc_labels(tree):
    assert apply(tree).returncode == 0
    pcd_c = (tree / F266["PCD_C"]).read_text()
    table = pcd_c.split("fman_pcd_punt_hxs[FMAN_PCD_PUNT_NHXS] = {", 1)[1].split("};", 1)[0]
    assert "{ FMAN_PCD_PUNT_TCP_CTL, 10, 0x020 }" in table
    assert "{ FMAN_PCD_PUNT_TTL, 5, 0x033 }" in table
    assert "{ FMAN_PCD_PUNT_TTL, 6, 0x047 }" in table
    assert "#define FMAN_PCD_PUNT_NHXS\t\t3" in pcd_c
    # Live attach values seen on .156: TCP 0x80000000 -> 0x80000420,
    # IPv4 0 -> 0x433, IPv6 0 -> 0x447.
    for old, label, want in ((0x80000000, 0x020, 0x80000420),
                             (0, 0x033, 0x433), (0, 0x047, 0x447)):
        assert (old & ~(0x400 | 0x3FF)) | 0x400 | label == want


def test_f266_failed_code_restore_still_disarms_port(tree):
    assert apply(tree).returncode == 0
    pcd_c = (tree / F266["PCD_C"]).read_text()

    def body(name):
        return pcd_c.split(name, 1)[1].split("\n}\n", 1)[0]

    unload = body("static void fman_pcd_punt_unload(")
    # Ownership and the saved words are dropped only after a successful restore.
    assert unload.index("fman_pcd_punt_prev, NULL") < unload.index("return;")
    assert unload.index("return;") < unload.index("fman_pcd_punt_loaded = false;")
    assert unload.index("return;") < unload.index("fman_pcd_punt_fm = NULL;")

    st = body("int fman_pcd_ctl_punt_set(")
    # A load after a failed restore keeps the original words, and a pending
    # restore on one FMan blocks arming the others but not disarming.
    assert "fman_pcd_punt_loaded ? NULL :" in st
    assert "if (fman_pcd_punt_fm && fman_pcd_punt_fm != fm) {\n\t\tret = mask ? -EBUSY : 0;" in st
    # Disarming an unknown port is a no-op, not a lookup.
    assert st.index("if (!mask)\n\t\t\tgoto out;") < st.index("fman_port_lookup_rx(")
    # The resident check runs only when an HXS is being added.
    assert "} else if (add && !fman_pcd_punt_resident(fm)) {" in st
    # Per-HXS state follows each attach/detach result; errors do not stop
    # the loop, so the port converges on a later call.
    loop = st.split("for (i = 0; i < FMAN_PCD_PUNT_NHXS; i++) {\n\t\tbool want", 1)[1]
    err = loop.index("if (rc) {")
    assert err < loop.index("continue;", err) < loop.index("slot->attached ^= BIT(i);")
    # Users count ports with any HXS attached; the last one unloads, even
    # when a detach failed elsewhere or the code restore itself fails.
    assert "if (!was && slot->attached) {" in st
    assert "} else if (was && !slot->attached) {" in st
    assert st.index("fman_pcd_punt_users--;") < st.index(
        "if (!fman_pcd_punt_users)\n\t\tfman_pcd_punt_unload(fm);")
    assert "ret = fman_pcd_punt_unload" not in st


@pytest.mark.parametrize("edit", range(len(F266["EDITS"])))
def test_f266_missing_anchor_fails_without_writing(tmp_path, edit):
    sources = anchors()
    path, old, _new, _want = F266["EDITS"][edit]
    sources[path] = sources[path].replace(old, "/* drifted */\n", 1)
    tree = write_tree(tmp_path, sources)
    before = snapshot(tree)
    result = apply(tree)
    assert result.returncode != 0
    assert "FATAL" in result.stdout
    assert snapshot(tree) == before


def test_f266_duplicate_anchor_fails_without_writing(tmp_path):
    sources = anchors()
    path, old, _new, _want = F266["EDITS"][3]
    sources[path] += old
    tree = write_tree(tmp_path, sources)
    before = snapshot(tree)
    result = apply(tree)
    assert result.returncode != 0
    assert snapshot(tree) == before


def test_f266_partial_application_fails(tmp_path):
    sources = anchors()
    sources[F266["PCD_H"]] += "/* F-266 */\n"
    tree = write_tree(tmp_path, sources)
    before = snapshot(tree)
    result = apply(tree)
    assert result.returncode != 0
    assert "partially applied" in result.stdout
    assert snapshot(tree) == before
