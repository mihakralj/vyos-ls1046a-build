/* fmtrace.c -- kprobe-based execution tracer for the vendor FMan PCD
 * built-in kernel functions on .116 (real NXP OpenWrt/Mono gateway-dk
 * board). Read-only observation only: kprobes/kretprobes + bounded
 * copy_from_kernel_nofault() reads, never writes anything, never
 * modifies control flow (all handlers return 0 / do nothing to regs).
 *
 * WHY: this project's own soft-parser investigation (.185, mainline
 * driver) has exhausted every register/bytecode-level comparison
 * against the real vendor board (.116) without finding a functional
 * difference -- see specs/ask2-soft-parser-lcv-scheme-select.md Sec6p.
 * The one thing never observed is the REAL vendor boot-time call
 * sequence itself: FM_PCD_Disable() -> fmc_execute() (internally calls
 * FM_PCD_PrsLoadSw() etc) -> FM_PCD_Enable(), traced in
 * /mnt/builds/ASK/dpa_app/dpa.c:258-265,826 but never actually
 * instrumented at the kernel-function level. This module traces that
 * sequence directly, once, at boot (dpa_app's PCD setup is boot-only --
 * confirmed via dpa_app source, no runtime re-trigger found).
 *
 * REUSABLE: this is a generic kprobe-tracer pattern (register a probe
 * by kernel symbol name, log timestamped args/retval to a ring buffer,
 * expose via /proc), not soft-parser-specific. Point PROBE_TABLE at
 * different symbols (e.g. cdx_dpa_ipsec.c functions, all present and
 * fully symbolized in /proc/kallsyms on .116 -- confirmed 2026-09-04)
 * for future ASK2 investigations (IPsec, etc) without redesigning the
 * mechanism.
 *
 * Target symbols (all confirmed present & real, non-stripped, in
 * .116's /proc/kallsyms 2026-09-04 -- fman flib is built into the
 * kernel here, not a separate module):
 *   fman_prs_enable(struct fman_prs_regs *)
 *   fman_prs_disable(struct fman_prs_regs *)
 *   fman_prs_is_enabled(struct fman_prs_regs *) -> bool
 *   FM_PCD_PrsLoadSw(t_Handle h_FmPcd, t_FmPcdPrsSwParams *p_SwPrs) -> t_Error
 *   FmPcdGetSwPrsOffset(t_Handle h_FmPcd, e_NetHeaderType hdr, u8 indexPerHdr) -> u32
 *
 * t_FmPcdPrsSwParams / t_FmPcdPrsLabelParams mirrored below field-for-
 * field from fm_pcd_ext.h:435-466 (no #pragma pack in that header --
 * confirmed 2026-09-04 -- so natural/default struct alignment applies,
 * and since this module is compiled by the identical toolchain/target
 * as the running kernel, the mirrored struct's compiler-computed
 * layout will match the real one exactly, without manual offset math).
 * `bool` assumed uint8_t and `e_NetHeaderType` assumed a plain C enum
 * (4-byte int) per standard NCSW/NXP SDK convention -- if wrong, the
 * worst case is a garbled label table log, not a crash (all reads are
 * bounded copy_from_kernel_nofault, never a raw dereference).
 */

#include <linux/module.h>
#include <linux/kprobes.h>
#include <linux/proc_fs.h>
#include <linux/seq_file.h>
#include <linux/spinlock.h>
#include <linux/ktime.h>
#include <linux/uaccess.h>
#include <linux/ptrace.h>
#include <linux/slab.h>
#include <linux/atomic.h>

#define LOG_MAX		96
#define LOG_LINE	320

static char log_buf[LOG_MAX][LOG_LINE];
static unsigned int log_head;
static unsigned int log_count;
static DEFINE_SPINLOCK(log_lock);
static ktime_t start_time;

static __printf(1, 2) void log_add(const char *fmt, ...)
{
	va_list args;
	unsigned long flags;
	char tmp[LOG_LINE - 24];
	s64 ms;

	va_start(args, fmt);
	vsnprintf(tmp, sizeof(tmp), fmt, args);
	va_end(args);

	ms = ktime_ms_delta(ktime_get(), start_time);

	spin_lock_irqsave(&log_lock, flags);
	snprintf(log_buf[log_head], LOG_LINE, "[+%8lldms] %s", ms, tmp);
	log_head = (log_head + 1) % LOG_MAX;
	if (log_count < LOG_MAX)
		log_count++;
	spin_unlock_irqrestore(&log_lock, flags);
}

/* --- mirrored vendor structs (fm_pcd_ext.h:435-466) --- */
struct fmsp_prs_label {
	u32 instructionOffset;
	u32 hdr;
	u8  indexPerHdr;
};

#define FMSP_NUM_OF_HDRS	16
#define FMSP_NUM_OF_LABELS	32

struct fmsp_sw_params {
	u8  override;
	u32 size;
	u16 base;
	u8  *p_Code;
	u32 swPrsDataParams[FMSP_NUM_OF_HDRS];
	u8  numOfLabels;
	struct fmsp_prs_label labelsTable[FMSP_NUM_OF_LABELS];
};

/* --- FM_PCD_PrsLoadSw(h_FmPcd, p_SwPrs) --- */
static int h_prsloadsw_entry(struct kretprobe_instance *ri, struct pt_regs *regs)
{
	struct fmsp_sw_params sp;
	u8 code[128];
	void *p_SwPrs = (void *)regs_get_kernel_argument(regs, 1);
	int i, n;

	if (copy_from_kernel_nofault(&sp, p_SwPrs, sizeof(sp))) {
		log_add("FM_PCD_PrsLoadSw(p_SwPrs=%px): struct read FAILED", p_SwPrs);
		return 0;
	}
	log_add("FM_PCD_PrsLoadSw: override=%u size=%u base=0x%x(instr) p_Code=%px numOfLabels=%u",
		sp.override, sp.size, sp.base, sp.p_Code, sp.numOfLabels);

	n = min_t(u32, sp.numOfLabels, FMSP_NUM_OF_LABELS);
	for (i = 0; i < n; i++)
		log_add("  label[%d]: instructionOffset=0x%x(word) hdr=%u indexPerHdr=%u",
			i, sp.labelsTable[i].instructionOffset,
			sp.labelsTable[i].hdr, sp.labelsTable[i].indexPerHdr);

	if (sp.p_Code && !copy_from_kernel_nofault(code, sp.p_Code, sizeof(code))) {
		log_add("  code[0..31]: %*phN", 32, code);
		log_add("  code[32..63]: %*phN", 32, code + 32);
	}
	return 0;
}

static int h_prsloadsw_ret(struct kretprobe_instance *ri, struct pt_regs *regs)
{
	log_add("FM_PCD_PrsLoadSw returned %ld", regs_return_value(regs));
	return 0;
}

static struct kretprobe krp_prsloadsw = {
	.kp.symbol_name = "FM_PCD_PrsLoadSw",
	.entry_handler = h_prsloadsw_entry,
	.handler = h_prsloadsw_ret,
	.maxactive = 4,
};

/* --- FmPcdGetSwPrsOffset(h_FmPcd, hdr, indexPerHdr) --- */
/* Found live 2026-09-05: this is called every ~20ms in steady state (a
 * periodic PCD/soft-parser table poll, not boot-only), which would flood
 * and wrap the small ring buffer in ~200ms and evict a one-shot event
 * (e.g. FM_PCD_PrsLoadSw) logged earlier. Cap logging so a handful of
 * cycles are visible (enough to prove the periodic pattern) without
 * drowning out rarer events. */
static atomic_t getoffset_calls = ATOMIC_INIT(0);
#define GETOFFSET_LOG_CAP 40

static int h_getoffset_entry(struct kretprobe_instance *ri, struct pt_regs *regs)
{
	u32 hdr = (u32)regs_get_kernel_argument(regs, 1);
	u8 idx = (u8)regs_get_kernel_argument(regs, 2);

	if (atomic_inc_return(&getoffset_calls) > GETOFFSET_LOG_CAP)
		return 0;
	log_add("FmPcdGetSwPrsOffset(hdr=%u, indexPerHdr=%u) called", hdr, idx);
	return 0;
}

static int h_getoffset_ret(struct kretprobe_instance *ri, struct pt_regs *regs)
{
	if (atomic_read(&getoffset_calls) > GETOFFSET_LOG_CAP)
		return 0;
	log_add("FmPcdGetSwPrsOffset returned 0x%lx", regs_return_value(regs));
	return 0;
}

static struct kretprobe krp_getoffset = {
	.kp.symbol_name = "FmPcdGetSwPrsOffset",
	.entry_handler = h_getoffset_entry,
	.handler = h_getoffset_ret,
	.maxactive = 8,
};

/* --- fman_prs_enable / fman_prs_disable / fman_prs_is_enabled --- */
static int h_prs_enable(struct kprobe *p, struct pt_regs *regs)
{
	log_add("fman_prs_enable(regs=%px) called",
		(void *)regs_get_kernel_argument(regs, 0));
	return 0;
}
static struct kprobe kp_prs_enable = {
	.symbol_name = "fman_prs_enable",
	.pre_handler = h_prs_enable,
};

static int h_prs_disable(struct kprobe *p, struct pt_regs *regs)
{
	log_add("fman_prs_disable(regs=%px) called",
		(void *)regs_get_kernel_argument(regs, 0));
	return 0;
}
static struct kprobe kp_prs_disable = {
	.symbol_name = "fman_prs_disable",
	.pre_handler = h_prs_disable,
};

static int h_isenabled_ret(struct kretprobe_instance *ri, struct pt_regs *regs)
{
	log_add("fman_prs_is_enabled returned %ld", regs_return_value(regs));
	return 0;
}
static struct kretprobe krp_isenabled = {
	.kp.symbol_name = "fman_prs_is_enabled",
	.handler = h_isenabled_ret,
	.maxactive = 4,
};

/* --- /proc/fmtrace --- */
static int fmtrace_show(struct seq_file *m, void *v)
{
	unsigned long flags;
	unsigned int i, start, n;
	char (*local)[LOG_LINE];

	local = kmalloc_array(LOG_MAX, LOG_LINE, GFP_KERNEL);
	if (!local)
		return -ENOMEM;

	spin_lock_irqsave(&log_lock, flags);
	n = log_count;
	start = (log_head + LOG_MAX - n) % LOG_MAX;
	for (i = 0; i < n; i++)
		memcpy(local[i], log_buf[(start + i) % LOG_MAX], LOG_LINE);
	spin_unlock_irqrestore(&log_lock, flags);

	if (n == 0)
		seq_printf(m, "(no events captured yet -- probes armed, waiting)\n");
	for (i = 0; i < n; i++)
		seq_printf(m, "%s\n", local[i]);
	kfree(local);
	return 0;
}

static int fmtrace_open(struct inode *inode, struct file *file)
{
	return single_open(file, fmtrace_show, NULL);
}

static const struct proc_ops fmtrace_fops = {
	.proc_open	= fmtrace_open,
	.proc_read	= seq_read,
	.proc_lseek	= seq_lseek,
	.proc_release	= single_release,
};

static struct proc_dir_entry *fmtrace_entry;

static int __init fmtrace_init(void)
{
	int ret;

	start_time = ktime_get();

	ret = register_kretprobe(&krp_prsloadsw);
	if (ret)
		pr_warn("fmtrace: FM_PCD_PrsLoadSw probe failed: %d\n", ret);

	ret = register_kretprobe(&krp_getoffset);
	if (ret)
		pr_warn("fmtrace: FmPcdGetSwPrsOffset probe failed: %d\n", ret);

	ret = register_kprobe(&kp_prs_enable);
	if (ret)
		pr_warn("fmtrace: fman_prs_enable probe failed: %d\n", ret);

	ret = register_kprobe(&kp_prs_disable);
	if (ret)
		pr_warn("fmtrace: fman_prs_disable probe failed: %d\n", ret);

	ret = register_kretprobe(&krp_isenabled);
	if (ret)
		pr_warn("fmtrace: fman_prs_is_enabled probe failed: %d\n", ret);

	fmtrace_entry = proc_create("fmtrace", 0444, NULL, &fmtrace_fops);
	if (!fmtrace_entry)
		return -ENOMEM;

	pr_info("fmtrace: loaded, probes armed, cat /proc/fmtrace to sample\n");
	return 0;
}

static void __exit fmtrace_exit(void)
{
	unregister_kretprobe(&krp_prsloadsw);
	unregister_kretprobe(&krp_getoffset);
	unregister_kprobe(&kp_prs_enable);
	unregister_kprobe(&kp_prs_disable);
	unregister_kretprobe(&krp_isenabled);
	proc_remove(fmtrace_entry);
	pr_info("fmtrace: unloaded\n");
}

module_init(fmtrace_init);
module_exit(fmtrace_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Read-only kprobe tracer for vendor FMan PCD soft-parser boot sequence");
