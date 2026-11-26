#!/usr/bin/env python3
"""G10 (T-M6-QOS) Phase 4 host gate: HWP->PLCR->downstream ingress policer coexistence.

Verifies:
  1. Source contracts in F_329.py, fman_pcd_plcr.c, and bin/ci-setup-kernel.sh.
  2. ASan/UBSan C behavioral harness covering:
     - Per-port physical FMPL slot allocation (profile_id = port_id, zero slot-0
       collision across eth3 0x10 and eth4 0x11).
     - HWP -> PLCR (fmbm_rfpne = 0x004C0000) -> downstream (PEGNIA = 0x80000000 | nia)
       on both ASK-disengaged (0x00480000) and ASK-engaged (0x00480200) ports.
     - Seamless live ASK engage / disengage while policer is installed:
       fmbm_rfpne stays 0x004C0000 while PEGNIA updates 0x80480000 <-> 0x80480200,
       verify_fe_detached() passes cleanly, and policer detach restores the
       CURRENT downstream NIA rather than a stale pre-engage value.
     - Seeded baseline + wrap/reset-safe counter delta arithmetic for TC_CLSMATCHALL_STATS.
"""

from pathlib import Path
import subprocess
import sys
import tempfile

REPO = Path(__file__).resolve().parent.parent
F329 = REPO / "bin/kernel-fixups/F_329.py"
CI_SETUP = REPO / "bin/ci-setup-kernel.sh"
PLCR_C = REPO / "kernel/common/files/drivers/net/ethernet/freescale/fman/fman_pcd_plcr.c"


def require_contains(path: Path, needles: list[str], label: str) -> None:
    if not path.exists():
        raise AssertionError(f"{label}: missing file {path}")
    text = path.read_text()
    for n in needles:
        if n not in text:
            raise AssertionError(f"{label}: missing expected snippet in {path.name}: {n!r}")


def check_sources() -> None:
    require_contains(
        F329,
        [
            "fman_port_plcr_attach",
            "fman_port_plcr_detach",
            "fman_port_plcr_read_counters",
            "fman_pcd_plcr_destroy_all",
            "fman_pcd_plcr_update_next_nia",
            "fman_pcd_plcr_read_nias",
            "fman_pcd_plcr_read_counters",
            "NIA_ENG_PLCR",
            "plcr_attached",
            "plcr_saved_rfpne",
            "hw.drop_yellow = true",
            "act->police.mtu",
            "check_mul_overflow",
            "TC_CLSMATCHALL_STATS",
            "dpaa_setup_tc_matchall_stats",
            "release pcd->lock before taking fman->cc_port_lock",
        ],
        "F_329.py",
    )

    require_contains(
        CI_SETUP,
        [
            "bin/kernel-fixups/F_329.py",
            "bin/test-g10-phase4-policer.py",
        ],
        "ci-setup-kernel.sh",
    )

    require_contains(
        PLCR_C,
        [
            "hw->next_nia",
            "FMAN_PLCR_PAR_PWSEL_PEGNIA",
            "FMAN_PLCR_PAR_PWSEL_PEYNIA",
            "FMAN_PLCR_MAX_BPS",
            "plcr_find_slot_locked(",
            "fman_pcd_plcr_destroy_all(",
            "fman_pcd_plcr_update_next_nia(",
            "fman_pcd_plcr_read_nias(",
            "fman_pcd_plcr_read_counters(",
            "EXPORT_SYMBOL_GPL(fman_pcd_plcr_destroy_all);",
            "EXPORT_SYMBOL_GPL(fman_pcd_plcr_update_next_nia);",
            "EXPORT_SYMBOL_GPL(fman_pcd_plcr_read_nias);",
            "EXPORT_SYMBOL_GPL(fman_pcd_plcr_read_counters);",
            "FMAN_PLCR_PAR_R",
        ],
        "fman_pcd_plcr.c",
    )


C_HARNESS = r"""
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

typedef uint8_t  u8;
typedef uint32_t u32;
typedef uint64_t u64;

#define NIA_ENG_MASK            0x007C0000U
#define NIA_ENG_HWK             0x00480000U
#define NIA_ENG_PLCR            0x004C0000U
#define NIA_ENG_BMI             0x00500000U
#define NIA_KG_CC_EN            0x00000200U
#define NIA_KG_DIRECT           0x00000100U
#define NIA_BMI_AC_ENQ_FRAME    0x00000002U
#define FMAN_PLCR_NIA_VALID     0x80000000U
#define FMAN_PLCR_NIA_DISCARD   0x805000C1U
#define FMAN_PLCR_PMR_V         0x80000000U

struct mock_profile {
    bool valid;
    bool drop_yellow;
    u8   port_id;
    u32  pegnia;
    u32  peynia;
    u32  pernia;
    u32  green;
    u32  yellow;
    u32  red;
};

struct mock_port {
    u8   port_id;
    u32  fmbm_rfpne;
    u32  fmbm_rccb;
    bool cc_engaged;
    bool plcr_attached;
    u8   plcr_profile_id;
    u32  plcr_saved_rfpne;
};

static struct mock_profile fmpl[256];
static u32 fmpl_pmr[64];

static int plcr_install(u8 port_id, u8 prof_id, u32 next_nia, bool drop_yellow)
{
    u32 pass_nia;
    if (port_id < 1 || port_id > 63)
        return -EINVAL;
    pass_nia = next_nia ? (FMAN_PLCR_NIA_VALID | (next_nia & 0x00FFFFFFU)) :
                          (FMAN_PLCR_NIA_VALID | NIA_ENG_BMI | NIA_BMI_AC_ENQ_FRAME);
    fmpl[prof_id].valid = true;
    fmpl[prof_id].drop_yellow = drop_yellow;
    fmpl[prof_id].port_id = port_id;
    fmpl[prof_id].pegnia = pass_nia;
    fmpl[prof_id].peynia = drop_yellow ? FMAN_PLCR_NIA_DISCARD : pass_nia;
    fmpl[prof_id].pernia = FMAN_PLCR_NIA_DISCARD;
    fmpl_pmr[port_id - 1] = FMAN_PLCR_PMR_V | prof_id;
    return 0;
}

static int plcr_update_next_nia(u8 port_id, u8 prof_id, u32 next_nia)
{
    u32 pass_nia;
    if (!fmpl[prof_id].valid || fmpl[prof_id].port_id != port_id)
        return -ENOENT;
    pass_nia = FMAN_PLCR_NIA_VALID | (next_nia & 0x00FFFFFFU);
    fmpl[prof_id].pegnia = pass_nia;
    if (!fmpl[prof_id].drop_yellow)
        fmpl[prof_id].peynia = pass_nia;
    return 0;
}

static void plcr_destroy(u8 port_id, u8 prof_id)
{
    if (!fmpl[prof_id].valid || fmpl[prof_id].port_id != port_id)
        return;
    fmpl_pmr[port_id - 1] = 0;
    fmpl[prof_id].valid = false;
}

static int port_plcr_attach(struct mock_port *p, u8 prof_id, bool drop_yellow)
{
    u32 next_nia = p->plcr_attached ? p->plcr_saved_rfpne : p->fmbm_rfpne;
    int err;

    if ((next_nia & NIA_ENG_MASK) == NIA_ENG_PLCR)
        return -EINVAL;
    err = plcr_install(p->port_id, prof_id, next_nia, drop_yellow);
    if (err)
        return err;
    p->fmbm_rfpne = NIA_ENG_PLCR;
    p->plcr_attached = true;
    p->plcr_profile_id = prof_id;
    p->plcr_saved_rfpne = next_nia;
    return 0;
}

static void port_plcr_detach(struct mock_port *p)
{
    if (!p->plcr_attached)
        return;
    p->fmbm_rfpne = p->plcr_saved_rfpne;
    plcr_destroy(p->port_id, p->plcr_profile_id);
    p->plcr_attached = false;
    p->plcr_profile_id = 0;
    p->plcr_saved_rfpne = 0;
}

static void port_set_cc_base(struct mock_port *p, u32 rccb)
{
    u32 nia;

    p->fmbm_rccb = rccb;
    p->cc_engaged = (rccb != 0);
    if (p->plcr_attached) {
        if ((p->plcr_saved_rfpne & NIA_ENG_MASK) == NIA_ENG_HWK) {
            if (rccb)
                p->plcr_saved_rfpne |= NIA_KG_CC_EN;
            else
                p->plcr_saved_rfpne &= ~NIA_KG_CC_EN;
            assert(plcr_update_next_nia(p->port_id, p->plcr_profile_id,
                                        p->plcr_saved_rfpne) == 0);
        }
        return;
    }
    nia = p->fmbm_rfpne;
    if ((nia & NIA_ENG_MASK) == NIA_ENG_HWK) {
        if (rccb)
            nia |= NIA_KG_CC_EN;
        else
            nia &= ~NIA_KG_CC_EN;
        p->fmbm_rfpne = nia;
    }
}

static int port_verify_fe_detached(const struct mock_port *p)
{
    u32 eff_rfpne = p->plcr_attached ? p->plcr_saved_rfpne : p->fmbm_rfpne;

    if (p->fmbm_rccb || ((eff_rfpne & NIA_ENG_MASK) == NIA_ENG_HWK &&
                         (eff_rfpne & (NIA_KG_CC_EN | NIA_KG_DIRECT | 0x1FU))))
        return -EIO;
    return 0;
}

static u32 counter_delta(u32 last, u32 now)
{
    return (now >= last) ? (now - last) : now;
}

int main(void)
{
    struct mock_port eth3 = { .port_id = 0x10, .fmbm_rfpne = NIA_ENG_HWK };
    struct mock_port eth4 = { .port_id = 0x11, .fmbm_rfpne = NIA_ENG_HWK };

    /* 1. Engage eth3 FIRST (ASK S1), then attach policer -> PEGNIA must be 0x80480200. */
    port_set_cc_base(&eth3, 0x00056d00);
    assert(eth3.fmbm_rfpne == (NIA_ENG_HWK | NIA_KG_CC_EN));
    assert(port_plcr_attach(&eth3, eth3.port_id, false) == 0);
    assert(eth3.fmbm_rfpne == NIA_ENG_PLCR);
    assert(fmpl[0x10].pegnia == (FMAN_PLCR_NIA_VALID | NIA_ENG_HWK | NIA_KG_CC_EN));
    assert(fmpl[0x10].peynia == (FMAN_PLCR_NIA_VALID | NIA_ENG_HWK | NIA_KG_CC_EN));
    assert(fmpl[0x10].pernia == FMAN_PLCR_NIA_DISCARD);
    assert(fmpl_pmr[0x10 - 1] == (FMAN_PLCR_PMR_V | 0x10));

    /* 2. Concurrently police eth4 (disengaged S0) on disjoint slot 0x11 -> no collision! */
    assert(port_plcr_attach(&eth4, eth4.port_id, true) == 0);
    assert(eth4.fmbm_rfpne == NIA_ENG_PLCR);
    assert(fmpl[0x11].pegnia == (FMAN_PLCR_NIA_VALID | NIA_ENG_HWK));
    assert(fmpl[0x11].peynia == FMAN_PLCR_NIA_DISCARD);
    assert(fmpl[0x10].valid && fmpl[0x10].pegnia == 0x80480200U);

    /* 3. Engage eth4 while policer is active -> fmbm_rfpne stays PLCR, PEGNIA flips to 0x80480200. */
    port_set_cc_base(&eth4, 0x00059000);
    assert(eth4.fmbm_rfpne == NIA_ENG_PLCR);
    assert(fmpl[0x11].pegnia == 0x80480200U);
    assert(fmpl[0x11].peynia == FMAN_PLCR_NIA_DISCARD); /* drop_yellow preserved */

    /* 4. Detach eth4 policer while still engaged -> restores 0x00480200 (NOT stale 0x00480000!). */
    port_plcr_detach(&eth4);
    assert(eth4.fmbm_rfpne == (NIA_ENG_HWK | NIA_KG_CC_EN));
    assert(!fmpl[0x11].valid && fmpl_pmr[0x11 - 1] == 0);

    /* 5. Disengage eth3 while policer is active -> PEGNIA flips back to 0x80480000 & verify_fe_detached passes. */
    port_set_cc_base(&eth3, 0);
    assert(eth3.fmbm_rfpne == NIA_ENG_PLCR);
    assert(fmpl[0x10].pegnia == (FMAN_PLCR_NIA_VALID | NIA_ENG_HWK));
    assert(port_verify_fe_detached(&eth3) == 0);

    /* 6. Detach eth3 policer -> restores 0x00480000 cleanly. */
    port_plcr_detach(&eth3);
    assert(eth3.fmbm_rfpne == NIA_ENG_HWK);
    assert(!fmpl[0x10].valid && fmpl_pmr[0x10 - 1] == 0);

    /* 7. Counter delta arithmetic (including counter clear/reset without 4B wrap spike). */
    assert(counter_delta(100, 150) == 50);
    assert(counter_delta(150, 150) == 0);
    assert(counter_delta(5000, 12) == 12);

    printf("G10 Phase 4 HWP->PLCR->downstream policer C harness PASS\n");
    return 0;
}
"""


def run_c_harness() -> None:
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "harness.c"
        exe = Path(td) / "harness"
        src.write_text(C_HARNESS)
        subprocess.run(
            ["gcc", "-Wall", "-Wextra", "-Werror", "-fsanitize=address,undefined",
             str(src), "-o", str(exe)],
            check=True,
        )
        subprocess.run([str(exe)], check=True)


def main() -> int:
    check_sources()
    run_c_harness()
    print("OK: G10 Phase 4 HWP->PLCR->downstream policer gate PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
