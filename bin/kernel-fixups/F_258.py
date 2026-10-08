"""F-258 (T-M6-SP4 parser probe, 2026-10-08): let the F-239 probe2 capture
also fire on PPPoE session frames, on any port.

The PPPoE offload plan (plans/ASK2-PPPOE-OFFLOAD-PLAN.md section 3) is gated
on one silicon question: does the hard parser continue past PPPoE+PPP into
the inner IPv4/IPv6 header (valid L3R/L4R and ip_off pointing at the inner
header), or does it stop at PPP? probe2 already copies the parse result plus
the start of the frame, but only for eth1, and the PPPoE test session runs on
eth3. This widens the predicate: eth1 as before, OR a contiguous frame whose
EtherType is 0x8864 (PPPoE session), on any port.

Safety is unchanged from F-239: the copy is synchronous, bounded (176 bytes),
inside rx_default_dqrr() right after the mainline RX-hash read has just
dereferenced vaddr+hash_offset, and after F-216's zero-FD guard. The extra
EtherType read is at vaddr + fd offset + 12 of a contig FD of at least
ETH_HLEN bytes, which is inside the frame the driver is about to hand to the
stack. No register, MURAM, scheme or datapath write. PPPoE frames always MISS
today (step 0 guard), so they reach this callback.

Diagnostic only: retire together with F-239 once the probe is answered.
Must run after F-239 (anchors on its predicate). Idempotent; exits non-zero
unless the anchor matches exactly once.
"""

import sys

SRC = "drivers/net/ethernet/freescale/dpaa/dpaa_eth.c"
MARK = "F-258"

OLD = """		if (vaddr && !strcmp(net_dev->name, "eth1") &&
		    hash_offset >= 0x28) {
"""
NEW = """		/* F-258: also any port's contiguous PPPoE session frame
		 * (T-M6-SP4 hard-parser probe). */
		if (vaddr && hash_offset >= 0x28 &&
		    (!strcmp(net_dev->name, "eth1") ||
		     (fd_format == qm_fd_contig &&
		      qm_fd_get_length(fd) >= ETH_HLEN &&
		      *(__be16 *)(vaddr + qm_fd_get_offset(fd) + 12) ==
		      htons(ETH_P_PPP_SES)))) {
"""

with open(SRC) as f:
    src = f.read()

if MARK in src:
    print("### F-258: already applied")
    sys.exit(0)

n = src.count(OLD)
if n != 1:
    print(f"### F-258: FATAL: F-239 probe2 predicate found {n} times "
          "(expected 1); F-239 must have run first")
    sys.exit(1)

with open(SRC, "w") as f:
    f.write(src.replace(OLD, NEW))
print("### F-258: probe2 also captures PPPoE session frames on any port")
