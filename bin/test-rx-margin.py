#!/usr/bin/env python3
"""Host check for F-308: fman_port_set_rx_start_margin() in the generated fman_port.c.

Extracts the function and the BMI/SP #defines it uses, compiles it under
ASan/UBSan against a mock register file and checks: a valid change quiesces,
writes FMBM_REBM (end margin preserved), reads back and re-enables; an
unchanged margin touches nothing; bad, overlapping-IC or too-large margins
and non-RX ports are refused without a write; a drain timeout keeps the old
margin and re-enables; a disabled port is written and left disabled; a
readback mismatch returns -EIO with the port re-enabled.
Usage: bin/test-rx-margin.py <kernel-src>
"""

from pathlib import Path
import re
import subprocess
import sys
import tempfile

DEFINES = ["BMI_PORT_CFG_EN", "BMI_IC_TO_EXT_SHIFT", "BMI_IC_TO_EXT_MASK", "BMI_IC_SIZE_MASK",
           "BMI_EXT_BUF_MARG_START_SHIFT", "BMI_EXT_BUF_MARG_START_MASK", "BMI_EXT_BUF_MARG_END_MASK",
           "BMI_EXT_BUF_POOL_VALID", "MIN_EXT_BUF_SIZE", "FMAN_SP_IC_TO_EXT_SHIFT",
           "FMAN_SP_EXT_BUF_MARG_START_SHIFT", "FMAN_SP_EXT_BUF_POOL_VALID"]

HARNESS = r"""
#include <stdint.h>
#include <stdio.h>
#include <stdbool.h>
typedef uint8_t u8; typedef uint16_t u16; typedef uint32_t u32;
#define U32_MAX 0xffffffffu
#define EINVAL 22
#define ERANGE 34
#define EBUSY 16
#define EIO 5
#define min_t(t, a, b) ((t)(a) < (t)(b) ? (t)(a) : (t)(b))
#define FMAN_PORT_MAX_EXT_POOLS_NUM 8
#define EXPORT_SYMBOL_GPL(x)
enum fman_port_type { FMAN_PORT_TYPE_TX = 0, FMAN_PORT_TYPE_RX };
@DEFINES@
struct rx_regs { u32 fmbm_rcfg, fmbm_ricp, fmbm_rebm, fmbm_ebmpi[FMAN_PORT_MAX_EXT_POOLS_NUM]; };
union fman_port_bmi_regs { struct rx_regs rx; };
struct fman_port_cfg { int unused; };
struct fman_port { enum fman_port_type port_type; struct fman_port_cfg *cfg;
		   union fman_port_bmi_regs *bmi_regs; u8 ext_pools_num; };
static int writes, disables, enables, disable_err, corrupt_rebm;
static u32 ioread32be(const u32 *p) { return *p; }
static void iowrite32be(u32 v, u32 *p) { writes++; *p = corrupt_rebm ? v ^ 0x10000 : v; }
static bool is_init_done(struct fman_port_cfg *cfg) { return !cfg; }
static union fman_port_bmi_regs regs;
static int fman_port_disable(struct fman_port *port)
{ (void)port; disables++; regs.rx.fmbm_rcfg &= ~BMI_PORT_CFG_EN; return disable_err; }
static int fman_port_enable(struct fman_port *port)
{ (void)port; enables++; regs.rx.fmbm_rcfg |= BMI_PORT_CFG_EN; return 0; }
@FUNC@
static struct fman_port port;
static void reset(void)
{
	regs.rx.fmbm_rcfg = BMI_PORT_CFG_EN; regs.rx.fmbm_ricp = 0x000D0203;	/* IC at 208, 48 B */
	regs.rx.fmbm_rebm = 0x01000000; regs.rx.fmbm_ebmpi[0] = 0xC0030E00;	/* bpid 3, 3584 B */
	port.port_type = FMAN_PORT_TYPE_RX; port.cfg = NULL; port.bmi_regs = &regs; port.ext_pools_num = 1;
	writes = disables = enables = disable_err = corrupt_rebm = 0;
}
static int fails;
#define CHECK(c) do { if (!(c)) { printf("FAIL line %d: %s\n", __LINE__, #c); fails++; } } while (0)
int main(void)
{
	reset();	/* valid change on an enabled port */
	CHECK(fman_port_set_rx_start_margin(&port, 260) == 0);
	CHECK(regs.rx.fmbm_rebm == 0x01040000 && disables == 1 && enables == 1);
	CHECK(regs.rx.fmbm_rcfg & BMI_PORT_CFG_EN);
	writes = disables = enables = 0;	/* unchanged margin: no quiesce, no write */
	CHECK(fman_port_set_rx_start_margin(&port, 260) == 0 && !writes && !disables && !enables);
	CHECK(fman_port_set_rx_start_margin(&port, 256) == 0 && regs.rx.fmbm_rebm == 0x01000000);

	reset();	/* refusals touch nothing */
	CHECK(fman_port_set_rx_start_margin(&port, 258) == -EINVAL);
	CHECK(fman_port_set_rx_start_margin(&port, 600) == -EINVAL);
	CHECK(fman_port_set_rx_start_margin(&port, 252) == -ERANGE);	/* overlaps the IC */
	port.port_type = FMAN_PORT_TYPE_TX;
	CHECK(fman_port_set_rx_start_margin(&port, 260) == -EINVAL);
	port.port_type = FMAN_PORT_TYPE_RX;
	regs.rx.fmbm_ebmpi[0] = 0xC0030140;	/* 320 B pool: 260 + 64 > 320 */
	CHECK(fman_port_set_rx_start_margin(&port, 260) == -ERANGE);
	regs.rx.fmbm_ebmpi[0] = 0;	/* no valid pool */
	CHECK(fman_port_set_rx_start_margin(&port, 260) == -ERANGE);
	CHECK(!writes && !disables && !enables && regs.rx.fmbm_rebm == 0x01000000);

	reset();	/* drain timeout: old margin kept, port re-enabled */
	disable_err = -EBUSY;
	CHECK(fman_port_set_rx_start_margin(&port, 260) == -EBUSY);
	CHECK(regs.rx.fmbm_rebm == 0x01000000 && enables == 1 && (regs.rx.fmbm_rcfg & BMI_PORT_CFG_EN));

	reset();	/* disabled port: written, left disabled */
	regs.rx.fmbm_rcfg = 0;
	CHECK(fman_port_set_rx_start_margin(&port, 260) == 0);
	CHECK(regs.rx.fmbm_rebm == 0x01040000 && !disables && !enables && !regs.rx.fmbm_rcfg);

	reset();	/* end margin preserved */
	regs.rx.fmbm_rebm = 0x01000010;
	CHECK(fman_port_set_rx_start_margin(&port, 260) == 0 && regs.rx.fmbm_rebm == 0x01040010);

	reset();	/* readback mismatch */
	corrupt_rebm = 1;
	CHECK(fman_port_set_rx_start_margin(&port, 260) == -EIO && enables == 1);

	reset();	/* not initialised */
	port.cfg = (struct fman_port_cfg *)&port;
	CHECK(fman_port_set_rx_start_margin(&port, 260) == -EINVAL && !writes);

	printf(fails ? "F-308 rx margin: %d failure(s)\n" : "F-308 rx margin: all checks passed\n", fails);
	return fails != 0;
}
"""


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    fman = Path(sys.argv[1]) / "drivers/net/ethernet/freescale/fman"
    src = (fman / "fman_port.c").read_text() + (fman / "fman_sp.h").read_text()
    head = "int fman_port_set_rx_start_margin(struct fman_port *port, u16 margin)\n{\n"
    i = src.find(head)
    if i < 0:
        sys.exit("FAIL: F-308 fman_port_set_rx_start_margin() not found")
    func = src[i:src.index("\n}\n", i) + 3]
    defs = []
    for name in DEFINES:
        m = re.search(rf"^#define\s+{name}\s+(.+)$", src, re.M)
        if not m:
            sys.exit(f"FAIL: #define {name} not found")
        defs.append(f"#define {name} {m.group(1).strip()}")
    # FMAN_SP_* first so the BMI_* aliases resolve.
    defs.sort(key=lambda d: not d.startswith("#define FMAN_SP_"))
    with tempfile.TemporaryDirectory(prefix="f308-") as d:
        c = Path(d) / "margin.c"
        c.write_text(HARNESS.replace("@DEFINES@", "\n".join(defs)).replace("@FUNC@", func))
        exe = Path(d) / "margin"
        cc = ["gcc", "-std=gnu11", "-Wall", "-Wextra", "-Wno-unused-parameter",
              "-fsanitize=address,undefined", "-fno-sanitize-recover=all", str(c), "-o", str(exe)]
        if subprocess.run(cc).returncode:
            sys.exit("FAIL: F-308 harness does not compile against this source")
        if subprocess.run([str(exe)]).returncode:
            sys.exit("FAIL: F-308 rx margin check failed")


if __name__ == "__main__":
    main()
