savedcmd_tests/ask_test_genl.o := aarch64-linux-gnu-gcc -Wp,-MMD,tests/.ask_test_genl.o.d -nostdinc -I/home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include -I/home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/generated -I/home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include -I/home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include -I/home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/uapi -I/home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/generated/uapi -I/home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/uapi -I/home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/generated/uapi -include /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/compiler-version.h -include /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/kconfig.h -include /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/compiler_types.h -D__KERNEL__ -mlittle-endian -DCC_USING_PATCHABLE_FUNCTION_ENTRY -DKASAN_SHADOW_SCALE_SHIFT= -std=gnu11 -fshort-wchar -funsigned-char -fno-common -fno-PIE -fno-strict-aliasing -mgeneral-regs-only -DCONFIG_CC_HAS_K_CONSTRAINT=1 -Wno-psabi -mabi=lp64 -fno-asynchronous-unwind-tables -fno-unwind-tables -mbranch-protection=pac-ret -Wa,-march=armv8.5-a -DARM64_ASM_ARCH='"armv8.5-a"' -DKASAN_SHADOW_SCALE_SHIFT= -fno-delete-null-pointer-checks -O2 -fno-allow-store-data-races -fstack-protector-strong -fno-omit-frame-pointer -fno-optimize-sibling-calls -fno-stack-clash-protection -fpatchable-function-entry=4,2 -falign-functions=8 -fno-strict-overflow -fno-stack-check -fconserve-stack -fno-builtin-wcslen -Wall -Wextra -Wundef -Werror=implicit-function-declaration -Werror=implicit-int -Werror=return-type -Werror=strict-prototypes -Wno-format-security -Wno-trigraphs -Wno-frame-address -Wno-address-of-packed-member -Wmissing-declarations -Wmissing-prototypes -Wframe-larger-than=2048 -Wno-main -Wno-dangling-pointer -Wvla-larger-than=1 -Wno-pointer-sign -Wcast-function-type -Wno-array-bounds -Wno-stringop-overflow -Wno-alloc-size-larger-than -Wimplicit-fallthrough=5 -Werror=date-time -Werror=incompatible-pointer-types -Werror=designated-init -Wenum-conversion -Wunused -Wno-unused-but-set-variable -Wno-unused-const-variable -Wno-packed-not-aligned -Wno-format-overflow -Wno-format-truncation -Wno-stringop-truncation -Wno-override-init -Wno-missing-field-initializers -Wno-type-limits -Wno-shift-negative-value -Wno-maybe-uninitialized -Wno-sign-compare -Wno-unused-parameter -g -mstack-protector-guard=sysreg -mstack-protector-guard-reg=sp_el0 -mstack-protector-guard-offset=1520 -I./tests/../include -I./tests/../include/uapi  -DMODULE  -DKBUILD_BASENAME='"ask_test_genl"' -DKBUILD_MODNAME='"ask_kunit"' -D__KBUILD_MODNAME=kmod_ask_kunit -c -o tests/ask_test_genl.o tests/ask_test_genl.c  

source_tests/ask_test_genl.o := tests/ask_test_genl.c

deps_tests/ask_test_genl.o := \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/compiler-version.h \
    $(wildcard include/config/CC_VERSION_TEXT) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/kconfig.h \
    $(wildcard include/config/CPU_BIG_ENDIAN) \
    $(wildcard include/config/BOOGER) \
    $(wildcard include/config/FOO) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/compiler_types.h \
    $(wildcard include/config/DEBUG_INFO_BTF) \
    $(wildcard include/config/PAHOLE_HAS_BTF_TAG) \
    $(wildcard include/config/FUNCTION_ALIGNMENT) \
    $(wildcard include/config/CC_HAS_SANE_FUNCTION_ALIGNMENT) \
    $(wildcard include/config/X86_64) \
    $(wildcard include/config/ARM64) \
    $(wildcard include/config/LD_DEAD_CODE_DATA_ELIMINATION) \
    $(wildcard include/config/LTO_CLANG) \
    $(wildcard include/config/HAVE_ARCH_COMPILER_H) \
    $(wildcard include/config/KCSAN) \
    $(wildcard include/config/CC_HAS_ASSUME) \
    $(wildcard include/config/CC_HAS_COUNTED_BY) \
    $(wildcard include/config/CC_HAS_MULTIDIMENSIONAL_NONSTRING) \
    $(wildcard include/config/UBSAN_INTEGER_WRAP) \
    $(wildcard include/config/CFI) \
    $(wildcard include/config/ARCH_USES_CFI_GENERIC_LLVM_PASS) \
    $(wildcard include/config/CC_HAS_ASM_INLINE) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/compiler_attributes.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/compiler-gcc.h \
    $(wildcard include/config/ARCH_USE_BUILTIN_BSWAP) \
    $(wildcard include/config/SHADOW_CALL_STACK) \
    $(wildcard include/config/KCOV) \
    $(wildcard include/config/CC_HAS_TYPEOF_UNQUAL) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/asm/compiler.h \
    $(wildcard include/config/ARM64_PTR_AUTH_KERNEL) \
    $(wildcard include/config/ARM64_PTR_AUTH) \
    $(wildcard include/config/BUILTIN_RETURN_ADDRESS_STRIPS_PAC) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/kunit/test.h \
    $(wildcard include/config/KUNIT) \
    $(wildcard include/config/KUNIT_DEBUGFS) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/kunit/assert.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/err.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/compiler.h \
    $(wildcard include/config/TRACE_BRANCH_PROFILING) \
    $(wildcard include/config/PROFILE_ALL_BRANCHES) \
    $(wildcard include/config/OBJTOOL) \
    $(wildcard include/config/64BIT) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/asm/rwonce.h \
    $(wildcard include/config/LTO) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/asm-generic/rwonce.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/kasan-checks.h \
    $(wildcard include/config/KASAN_GENERIC) \
    $(wildcard include/config/KASAN_SW_TAGS) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/types.h \
    $(wildcard include/config/HAVE_UID16) \
    $(wildcard include/config/UID16) \
    $(wildcard include/config/ARCH_DMA_ADDR_T_64BIT) \
    $(wildcard include/config/PHYS_ADDR_T_64BIT) \
    $(wildcard include/config/ARCH_32BIT_USTAT_F_TINODE) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/uapi/linux/types.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/generated/uapi/asm/types.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/uapi/asm-generic/types.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/asm-generic/int-ll64.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/uapi/asm-generic/int-ll64.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/uapi/asm/bitsperlong.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/asm-generic/bitsperlong.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/uapi/asm-generic/bitsperlong.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/uapi/linux/posix_types.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/stddef.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/uapi/linux/stddef.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/uapi/asm/posix_types.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/uapi/asm-generic/posix_types.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/kcsan-checks.h \
    $(wildcard include/config/KCSAN_WEAK_MEMORY) \
    $(wildcard include/config/KCSAN_IGNORE_ATOMICS) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/generated/uapi/asm/errno.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/uapi/asm-generic/errno.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/uapi/asm-generic/errno-base.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/printk.h \
    $(wildcard include/config/MESSAGE_LOGLEVEL_DEFAULT) \
    $(wildcard include/config/CONSOLE_LOGLEVEL_DEFAULT) \
    $(wildcard include/config/CONSOLE_LOGLEVEL_QUIET) \
    $(wildcard include/config/EARLY_PRINTK) \
    $(wildcard include/config/PRINTK) \
    $(wildcard include/config/SMP) \
    $(wildcard include/config/PRINTK_INDEX) \
    $(wildcard include/config/DYNAMIC_DEBUG) \
    $(wildcard include/config/DYNAMIC_DEBUG_CORE) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/stdarg.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/init.h \
    $(wildcard include/config/MEMORY_HOTPLUG) \
    $(wildcard include/config/HAVE_ARCH_PREL32_RELOCATIONS) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/build_bug.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/stringify.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/kern_levels.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/linkage.h \
    $(wildcard include/config/ARCH_USE_SYM_ANNOTATIONS) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/export.h \
    $(wildcard include/config/MODVERSIONS) \
    $(wildcard include/config/GENDWARFKSYMS) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/asm/linkage.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/ratelimit_types.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/bits.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/vdso/bits.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/vdso/const.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/uapi/linux/const.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/uapi/linux/bits.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/overflow.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/limits.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/uapi/linux/limits.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/vdso/limits.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/const.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/uapi/linux/param.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/uapi/asm/param.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/asm-generic/param.h \
    $(wildcard include/config/HZ) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/uapi/asm-generic/param.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/spinlock_types_raw.h \
    $(wildcard include/config/DEBUG_SPINLOCK) \
    $(wildcard include/config/DEBUG_LOCK_ALLOC) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/asm/spinlock_types.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/asm-generic/qspinlock_types.h \
    $(wildcard include/config/NR_CPUS) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/asm-generic/qrwlock_types.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/uapi/asm/byteorder.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/byteorder/little_endian.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/uapi/linux/byteorder/little_endian.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/swab.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/uapi/linux/swab.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/generated/uapi/asm/swab.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/uapi/asm-generic/swab.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/byteorder/generic.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/lockdep_types.h \
    $(wildcard include/config/PROVE_RAW_LOCK_NESTING) \
    $(wildcard include/config/LOCKDEP) \
    $(wildcard include/config/LOCK_STAT) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/once_lite.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/dynamic_debug.h \
    $(wildcard include/config/JUMP_LABEL) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/jump_label.h \
    $(wildcard include/config/HAVE_ARCH_JUMP_LABEL_RELATIVE) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/cleanup.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/args.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/asm/jump_label.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/asm/insn.h \
    $(wildcard include/config/ARM64_LSE_ATOMICS) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/asm/insn-def.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/asm/brk-imm.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/kunit/try-catch.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/container_of.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/kref.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/spinlock.h \
    $(wildcard include/config/PREEMPTION) \
    $(wildcard include/config/PREEMPT_RT) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/typecheck.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/preempt.h \
    $(wildcard include/config/PREEMPT_COUNT) \
    $(wildcard include/config/DEBUG_PREEMPT) \
    $(wildcard include/config/TRACE_PREEMPT_TOGGLE) \
    $(wildcard include/config/PREEMPT_NOTIFIERS) \
    $(wildcard include/config/PREEMPT_DYNAMIC) \
    $(wildcard include/config/PREEMPT_NONE) \
    $(wildcard include/config/PREEMPT_VOLUNTARY) \
    $(wildcard include/config/PREEMPT) \
    $(wildcard include/config/PREEMPT_LAZY) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/asm/preempt.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/thread_info.h \
    $(wildcard include/config/THREAD_INFO_IN_TASK) \
    $(wildcard include/config/GENERIC_ENTRY) \
    $(wildcard include/config/ARCH_HAS_PREEMPT_LAZY) \
    $(wildcard include/config/HAVE_ARCH_WITHIN_STACK_FRAMES) \
    $(wildcard include/config/SH) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/bug.h \
    $(wildcard include/config/GENERIC_BUG) \
    $(wildcard include/config/BUG_ON_DATA_CORRUPTION) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/asm/bug.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/asm/asm-bug.h \
    $(wildcard include/config/DEBUG_BUGVERBOSE) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/asm-generic/bug.h \
    $(wildcard include/config/BUG) \
    $(wildcard include/config/GENERIC_BUG_RELATIVE_POINTERS) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/instrumentation.h \
    $(wildcard include/config/NOINSTR_VALIDATION) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/panic.h \
    $(wildcard include/config/PANIC_TIMEOUT) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/restart_block.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/errno.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/uapi/linux/errno.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/asm/current.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/bitops.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/uapi/linux/kernel.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/uapi/linux/sysinfo.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/asm-generic/bitops/generic-non-atomic.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/asm/barrier.h \
    $(wildcard include/config/ARM64_PSEUDO_NMI) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/asm/alternative-macros.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/asm/cpucaps.h \
    $(wildcard include/config/ARM64_PAN) \
    $(wildcard include/config/ARM64_EPAN) \
    $(wildcard include/config/ARM64_SVE) \
    $(wildcard include/config/ARM64_SME) \
    $(wildcard include/config/ARM64_CNP) \
    $(wildcard include/config/ARM64_MTE) \
    $(wildcard include/config/ARM64_BTI) \
    $(wildcard include/config/ARM64_TLB_RANGE) \
    $(wildcard include/config/ARM64_POE) \
    $(wildcard include/config/ARM64_GCS) \
    $(wildcard include/config/ARM64_HAFT) \
    $(wildcard include/config/UNMAP_KERNEL_AT_EL0) \
    $(wildcard include/config/ARM64_ERRATUM_843419) \
    $(wildcard include/config/ARM64_ERRATUM_1742098) \
    $(wildcard include/config/ARM64_ERRATUM_2645198) \
    $(wildcard include/config/ARM64_ERRATUM_2658417) \
    $(wildcard include/config/CAVIUM_ERRATUM_23154) \
    $(wildcard include/config/NVIDIA_CARMEL_CNP_ERRATUM) \
    $(wildcard include/config/ARM64_WORKAROUND_REPEAT_TLBI) \
    $(wildcard include/config/ARM64_ERRATUM_3194386) \
    $(wildcard include/config/ARM64_ERRATUM_4193714) \
    $(wildcard include/config/HW_PERF_EVENTS) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/generated/asm/cpucap-defs.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/asm-generic/barrier.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/asm/bitops.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/asm-generic/bitops/builtin-__ffs.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/asm-generic/bitops/builtin-ffs.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/asm-generic/bitops/builtin-__fls.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/asm-generic/bitops/builtin-fls.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/asm-generic/bitops/ffz.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/asm-generic/bitops/fls64.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/asm-generic/bitops/sched.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/asm-generic/bitops/hweight.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/asm-generic/bitops/arch_hweight.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/asm-generic/bitops/const_hweight.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/asm-generic/bitops/atomic.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/atomic.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/asm/atomic.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/asm/cmpxchg.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/asm/lse.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/asm/atomic_ll_sc.h \
    $(wildcard include/config/CC_HAS_K_CONSTRAINT) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/asm/alternative.h \
    $(wildcard include/config/MODULES) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/asm/atomic_lse.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/atomic/atomic-arch-fallback.h \
    $(wildcard include/config/GENERIC_ATOMIC64) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/atomic/atomic-long.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/atomic/atomic-instrumented.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/instrumented.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/kmsan-checks.h \
    $(wildcard include/config/KMSAN) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/asm-generic/bitops/instrumented-atomic.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/asm-generic/bitops/lock.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/asm-generic/bitops/instrumented-lock.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/asm-generic/bitops/non-atomic.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/asm-generic/bitops/non-instrumented-non-atomic.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/asm-generic/bitops/le.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/asm-generic/bitops/ext2-atomic-setbit.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/asm/thread_info.h \
    $(wildcard include/config/ARM64_SW_TTBR0_PAN) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/asm/memory.h \
    $(wildcard include/config/ARM64_VA_BITS) \
    $(wildcard include/config/ARM64_16K_PAGES) \
    $(wildcard include/config/KASAN_SHADOW_OFFSET) \
    $(wildcard include/config/KASAN) \
    $(wildcard include/config/ARM64_4K_PAGES) \
    $(wildcard include/config/RANDOMIZE_BASE) \
    $(wildcard include/config/KASAN_HW_TAGS) \
    $(wildcard include/config/DEBUG_VIRTUAL) \
    $(wildcard include/config/EFI) \
    $(wildcard include/config/ARM_GIC_V3_ITS) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/sizes.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/asm/page-def.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/vdso/page.h \
    $(wildcard include/config/PAGE_SHIFT) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/mmdebug.h \
    $(wildcard include/config/DEBUG_VM) \
    $(wildcard include/config/DEBUG_VM_IRQSOFF) \
    $(wildcard include/config/DEBUG_VM_PGFLAGS) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/asm/boot.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/asm/sections.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/asm-generic/sections.h \
    $(wildcard include/config/HAVE_FUNCTION_DESCRIPTORS) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/asm/sysreg.h \
    $(wildcard include/config/BROKEN_GAS_INST) \
    $(wildcard include/config/ARM64_PA_BITS_52) \
    $(wildcard include/config/ARM64_64K_PAGES) \
    $(wildcard include/config/AMPERE_ERRATUM_AC04_CPU_23) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/kasan-tags.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/asm/gpr-num.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/generated/asm/sysreg-defs.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/bitfield.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/asm-generic/memory_model.h \
    $(wildcard include/config/FLATMEM) \
    $(wildcard include/config/SPARSEMEM_VMEMMAP) \
    $(wildcard include/config/SPARSEMEM) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/pfn.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/asm/stack_pointer.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/irqflags.h \
    $(wildcard include/config/PROVE_LOCKING) \
    $(wildcard include/config/TRACE_IRQFLAGS) \
    $(wildcard include/config/IRQSOFF_TRACER) \
    $(wildcard include/config/PREEMPT_TRACER) \
    $(wildcard include/config/DEBUG_IRQFLAGS) \
    $(wildcard include/config/TRACE_IRQFLAGS_SUPPORT) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/irqflags_types.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/asm/irqflags.h \
    $(wildcard include/config/ARM64_DEBUG_PRIORITY_MASKING) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/asm/ptrace.h \
    $(wildcard include/config/COMPAT) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/asm/cpufeature.h \
    $(wildcard include/config/ARM64_BTI_KERNEL) \
    $(wildcard include/config/ARM64_PA_BITS) \
    $(wildcard include/config/ARM64_HW_AFDBM) \
    $(wildcard include/config/ARM64_AMU_EXTN) \
    $(wildcard include/config/ARM64_LPA2) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/asm/cputype.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/asm/hwcap.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/uapi/asm/hwcap.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/log2.h \
    $(wildcard include/config/ARCH_HAS_ILOG2_U32) \
    $(wildcard include/config/ARCH_HAS_ILOG2_U64) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/kernel.h \
    $(wildcard include/config/PREEMPT_VOLUNTARY_BUILD) \
    $(wildcard include/config/HAVE_PREEMPT_DYNAMIC_CALL) \
    $(wildcard include/config/HAVE_PREEMPT_DYNAMIC_KEY) \
    $(wildcard include/config/PREEMPT_) \
    $(wildcard include/config/DEBUG_ATOMIC_SLEEP) \
    $(wildcard include/config/MMU) \
    $(wildcard include/config/TRACING) \
    $(wildcard include/config/DYNAMIC_FTRACE) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/align.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/vdso/align.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/array_size.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/hex.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/kstrtox.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/math.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/generated/asm/div64.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/asm-generic/div64.h \
    $(wildcard include/config/CC_OPTIMIZE_FOR_PERFORMANCE) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/minmax.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/sprintf.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/static_call_types.h \
    $(wildcard include/config/HAVE_STATIC_CALL) \
    $(wildcard include/config/HAVE_STATIC_CALL_INLINE) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/instruction_pointer.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/util_macros.h \
    $(wildcard include/config/FOO_SUSPEND) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/wordpart.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/cpumask.h \
    $(wildcard include/config/FORCE_NR_CPUS) \
    $(wildcard include/config/HOTPLUG_CPU) \
    $(wildcard include/config/DEBUG_PER_CPU_MAPS) \
    $(wildcard include/config/CPUMASK_OFFSTACK) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/bitmap.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/find.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/string.h \
    $(wildcard include/config/BINARY_PRINTF) \
    $(wildcard include/config/FORTIFY_SOURCE) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/uapi/linux/string.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/asm/string.h \
    $(wildcard include/config/ARCH_HAS_UACCESS_FLUSHCACHE) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/fortify-string.h \
    $(wildcard include/config/CC_HAS_KASAN_MEMINTRINSIC_PREFIX) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/bitmap-str.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/cpumask_types.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/threads.h \
    $(wildcard include/config/BASE_SMALL) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/gfp_types.h \
    $(wildcard include/config/SLAB_OBJ_EXT) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/numa.h \
    $(wildcard include/config/NUMA_KEEP_MEMINFO) \
    $(wildcard include/config/NUMA) \
    $(wildcard include/config/HAVE_ARCH_NODE_DEV_GROUP) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/nodemask.h \
    $(wildcard include/config/HIGHMEM) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/nodemask_types.h \
    $(wildcard include/config/NODES_SHIFT) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/random.h \
    $(wildcard include/config/VMGENID) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/list.h \
    $(wildcard include/config/LIST_HARDENED) \
    $(wildcard include/config/DEBUG_LIST) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/poison.h \
    $(wildcard include/config/ILLEGAL_POINTER_VALUE) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/uapi/linux/random.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/uapi/linux/ioctl.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/generated/uapi/asm/ioctl.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/asm-generic/ioctl.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/uapi/asm-generic/ioctl.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/irqnr.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/uapi/linux/irqnr.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/asm/sparsemem.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/asm/pgtable-prot.h \
    $(wildcard include/config/HAVE_ARCH_USERFAULTFD_WP) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/asm/pgtable-hwdef.h \
    $(wildcard include/config/PGTABLE_LEVELS) \
    $(wildcard include/config/ARM64_CONT_PTE_SHIFT) \
    $(wildcard include/config/ARM64_CONT_PMD_SHIFT) \
    $(wildcard include/config/ARM64_VA_BITS_52) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/asm/pgtable-types.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/asm-generic/pgtable-nop4d.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/asm/rsi.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/asm/rsi_cmds.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/arm-smccc.h \
    $(wildcard include/config/HAVE_ARM_SMCCC) \
    $(wildcard include/config/ARM) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/uuid.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/asm/rsi_smc.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/uapi/asm/ptrace.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/uapi/asm/sve_context.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/irqchip/arm-gic-v3-prio.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/asm/stacktrace/frame.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/asm/percpu.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/asm-generic/percpu.h \
    $(wildcard include/config/HAVE_SETUP_PER_CPU_AREA) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/percpu-defs.h \
    $(wildcard include/config/ARCH_MODULE_NEEDS_WEAK_PER_CPU) \
    $(wildcard include/config/DEBUG_FORCE_WEAK_PER_CPU) \
    $(wildcard include/config/AMD_MEM_ENCRYPT) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/bottom_half.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/lockdep.h \
    $(wildcard include/config/DEBUG_LOCKING_API_SELFTESTS) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/smp.h \
    $(wildcard include/config/UP_LATE_INIT) \
    $(wildcard include/config/CSD_LOCK_WAIT_DEBUG) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/smp_types.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/llist.h \
    $(wildcard include/config/ARCH_HAVE_NMI_SAFE_CMPXCHG) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/asm/smp.h \
    $(wildcard include/config/ARM64_ACPI_PARKING_PROTOCOL) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/generated/asm/mmiowb.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/asm-generic/mmiowb.h \
    $(wildcard include/config/MMIOWB) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/spinlock_types.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/rwlock_types.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/asm/spinlock.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/generated/asm/qspinlock.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/asm-generic/qspinlock.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/generated/asm/qrwlock.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/asm-generic/qrwlock.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/asm/processor.h \
    $(wildcard include/config/KUSER_HELPERS) \
    $(wildcard include/config/ARM64_FORCE_52BIT) \
    $(wildcard include/config/HAVE_HW_BREAKPOINT) \
    $(wildcard include/config/ARM64_TAGGED_ADDR_ABI) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/cache.h \
    $(wildcard include/config/ARCH_HAS_CACHE_LINE_SIZE) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/vdso/cache.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/asm/cache.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/kasan-enabled.h \
    $(wildcard include/config/ARCH_DEFER_KASAN) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/static_key.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/asm/mte-def.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/vdso/processor.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/asm/vdso/processor.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/asm/hw_breakpoint.h \
    $(wildcard include/config/CPU_PM) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/asm/virt.h \
    $(wildcard include/config/KVM) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/asm/kasan.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/asm/mte-kasan.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/asm/pointer_auth.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/uapi/linux/prctl.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/asm/spectre.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/asm/fpsimd.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/uapi/asm/sigcontext.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/rwlock.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/spinlock_api_smp.h \
    $(wildcard include/config/INLINE_SPIN_LOCK) \
    $(wildcard include/config/INLINE_SPIN_LOCK_BH) \
    $(wildcard include/config/INLINE_SPIN_LOCK_IRQ) \
    $(wildcard include/config/INLINE_SPIN_LOCK_IRQSAVE) \
    $(wildcard include/config/INLINE_SPIN_TRYLOCK) \
    $(wildcard include/config/INLINE_SPIN_TRYLOCK_BH) \
    $(wildcard include/config/UNINLINE_SPIN_UNLOCK) \
    $(wildcard include/config/INLINE_SPIN_UNLOCK_BH) \
    $(wildcard include/config/INLINE_SPIN_UNLOCK_IRQ) \
    $(wildcard include/config/INLINE_SPIN_UNLOCK_IRQRESTORE) \
    $(wildcard include/config/GENERIC_LOCKBREAK) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/rwlock_api_smp.h \
    $(wildcard include/config/INLINE_READ_LOCK) \
    $(wildcard include/config/INLINE_WRITE_LOCK) \
    $(wildcard include/config/INLINE_READ_LOCK_BH) \
    $(wildcard include/config/INLINE_WRITE_LOCK_BH) \
    $(wildcard include/config/INLINE_READ_LOCK_IRQ) \
    $(wildcard include/config/INLINE_WRITE_LOCK_IRQ) \
    $(wildcard include/config/INLINE_READ_LOCK_IRQSAVE) \
    $(wildcard include/config/INLINE_WRITE_LOCK_IRQSAVE) \
    $(wildcard include/config/INLINE_READ_TRYLOCK) \
    $(wildcard include/config/INLINE_WRITE_TRYLOCK) \
    $(wildcard include/config/INLINE_READ_UNLOCK) \
    $(wildcard include/config/INLINE_WRITE_UNLOCK) \
    $(wildcard include/config/INLINE_READ_UNLOCK_BH) \
    $(wildcard include/config/INLINE_WRITE_UNLOCK_BH) \
    $(wildcard include/config/INLINE_READ_UNLOCK_IRQ) \
    $(wildcard include/config/INLINE_WRITE_UNLOCK_IRQ) \
    $(wildcard include/config/INLINE_READ_UNLOCK_IRQRESTORE) \
    $(wildcard include/config/INLINE_WRITE_UNLOCK_IRQRESTORE) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/refcount.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/refcount_types.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/module.h \
    $(wildcard include/config/SYSFS) \
    $(wildcard include/config/MODULES_TREE_LOOKUP) \
    $(wildcard include/config/LIVEPATCH) \
    $(wildcard include/config/STACKTRACE_BUILD_ID) \
    $(wildcard include/config/ARCH_USES_CFI_TRAPS) \
    $(wildcard include/config/MODULE_SIG) \
    $(wildcard include/config/KALLSYMS) \
    $(wildcard include/config/TRACEPOINTS) \
    $(wildcard include/config/TREE_SRCU) \
    $(wildcard include/config/BPF_EVENTS) \
    $(wildcard include/config/DEBUG_INFO_BTF_MODULES) \
    $(wildcard include/config/EVENT_TRACING) \
    $(wildcard include/config/KPROBES) \
    $(wildcard include/config/MODULE_UNLOAD) \
    $(wildcard include/config/CONSTRUCTORS) \
    $(wildcard include/config/FUNCTION_ERROR_INJECTION) \
    $(wildcard include/config/MITIGATION_RETPOLINE) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/stat.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/asm/stat.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/generated/uapi/asm/stat.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/uapi/asm-generic/stat.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/time.h \
    $(wildcard include/config/POSIX_TIMERS) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/math64.h \
    $(wildcard include/config/ARCH_SUPPORTS_INT128) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/vdso/math64.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/time64.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/vdso/time64.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/uapi/linux/time.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/uapi/linux/time_types.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/time32.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/timex.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/uapi/linux/timex.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/asm/timex.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/asm/arch_timer.h \
    $(wildcard include/config/ARM_ARCH_TIMER_OOL_WORKAROUND) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/percpu.h \
    $(wildcard include/config/RANDOM_KMALLOC_CACHES) \
    $(wildcard include/config/PAGE_SIZE_4KB) \
    $(wildcard include/config/NEED_PER_CPU_PAGE_FIRST_CHUNK) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/alloc_tag.h \
    $(wildcard include/config/MEM_ALLOC_PROFILING_DEBUG) \
    $(wildcard include/config/MEM_ALLOC_PROFILING) \
    $(wildcard include/config/MEM_ALLOC_PROFILING_ENABLED_BY_DEFAULT) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/codetag.h \
    $(wildcard include/config/CODE_TAGGING) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/sched.h \
    $(wildcard include/config/VIRT_CPU_ACCOUNTING_NATIVE) \
    $(wildcard include/config/SCHED_INFO) \
    $(wildcard include/config/SCHEDSTATS) \
    $(wildcard include/config/SCHED_CORE) \
    $(wildcard include/config/FAIR_GROUP_SCHED) \
    $(wildcard include/config/RT_GROUP_SCHED) \
    $(wildcard include/config/RT_MUTEXES) \
    $(wildcard include/config/UCLAMP_TASK) \
    $(wildcard include/config/UCLAMP_BUCKETS_COUNT) \
    $(wildcard include/config/KMAP_LOCAL) \
    $(wildcard include/config/SCHED_CLASS_EXT) \
    $(wildcard include/config/CGROUP_SCHED) \
    $(wildcard include/config/CFS_BANDWIDTH) \
    $(wildcard include/config/BLK_DEV_IO_TRACE) \
    $(wildcard include/config/PREEMPT_RCU) \
    $(wildcard include/config/TASKS_RCU) \
    $(wildcard include/config/TASKS_TRACE_RCU) \
    $(wildcard include/config/MEMCG_V1) \
    $(wildcard include/config/LRU_GEN) \
    $(wildcard include/config/COMPAT_BRK) \
    $(wildcard include/config/CGROUPS) \
    $(wildcard include/config/BLK_CGROUP) \
    $(wildcard include/config/PSI) \
    $(wildcard include/config/PAGE_OWNER) \
    $(wildcard include/config/EVENTFD) \
    $(wildcard include/config/ARCH_HAS_CPU_PASID) \
    $(wildcard include/config/X86_BUS_LOCK_DETECT) \
    $(wildcard include/config/TASK_DELAY_ACCT) \
    $(wildcard include/config/STACKPROTECTOR) \
    $(wildcard include/config/ARCH_HAS_SCALED_CPUTIME) \
    $(wildcard include/config/VIRT_CPU_ACCOUNTING_GEN) \
    $(wildcard include/config/NO_HZ_FULL) \
    $(wildcard include/config/POSIX_CPUTIMERS) \
    $(wildcard include/config/POSIX_CPU_TIMERS_TASK_WORK) \
    $(wildcard include/config/KEYS) \
    $(wildcard include/config/SYSVIPC) \
    $(wildcard include/config/DETECT_HUNG_TASK) \
    $(wildcard include/config/IO_URING) \
    $(wildcard include/config/AUDIT) \
    $(wildcard include/config/AUDITSYSCALL) \
    $(wildcard include/config/DETECT_HUNG_TASK_BLOCKER) \
    $(wildcard include/config/UBSAN) \
    $(wildcard include/config/UBSAN_TRAP) \
    $(wildcard include/config/COMPACTION) \
    $(wildcard include/config/TASK_XACCT) \
    $(wildcard include/config/CPUSETS) \
    $(wildcard include/config/X86_CPU_RESCTRL) \
    $(wildcard include/config/FUTEX) \
    $(wildcard include/config/PERF_EVENTS) \
    $(wildcard include/config/NUMA_BALANCING) \
    $(wildcard include/config/RSEQ) \
    $(wildcard include/config/DEBUG_RSEQ) \
    $(wildcard include/config/SCHED_MM_CID) \
    $(wildcard include/config/FAULT_INJECTION) \
    $(wildcard include/config/LATENCYTOP) \
    $(wildcard include/config/FUNCTION_GRAPH_TRACER) \
    $(wildcard include/config/MEMCG) \
    $(wildcard include/config/UPROBES) \
    $(wildcard include/config/BCACHE) \
    $(wildcard include/config/VMAP_STACK) \
    $(wildcard include/config/SECURITY) \
    $(wildcard include/config/BPF_SYSCALL) \
    $(wildcard include/config/KSTACK_ERASE) \
    $(wildcard include/config/KSTACK_ERASE_METRICS) \
    $(wildcard include/config/X86_MCE) \
    $(wildcard include/config/KRETPROBES) \
    $(wildcard include/config/RETHOOK) \
    $(wildcard include/config/ARCH_HAS_PARANOID_L1D_FLUSH) \
    $(wildcard include/config/RV) \
    $(wildcard include/config/RV_PER_TASK_MONITORS) \
    $(wildcard include/config/USER_EVENTS) \
    $(wildcard include/config/UNWIND_USER) \
    $(wildcard include/config/SCHED_PROXY_EXEC) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/uapi/linux/sched.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/pid_types.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/sem_types.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/shm.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/asm/page.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/personality.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/uapi/linux/personality.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/asm-generic/getorder.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/asm/shmparam.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/asm-generic/shmparam.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/kmsan_types.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/mutex_types.h \
    $(wildcard include/config/MUTEX_SPIN_ON_OWNER) \
    $(wildcard include/config/DEBUG_MUTEXES) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/osq_lock.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/plist_types.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/hrtimer_types.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/timerqueue_types.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/rbtree_types.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/timer_types.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/seccomp_types.h \
    $(wildcard include/config/SECCOMP) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/resource.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/uapi/linux/resource.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/generated/uapi/asm/resource.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/asm-generic/resource.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/uapi/asm-generic/resource.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/latencytop.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/sched/prio.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/sched/types.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/signal_types.h \
    $(wildcard include/config/OLD_SIGACTION) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/uapi/linux/signal.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/asm/signal.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/uapi/asm/signal.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/asm-generic/signal.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/uapi/asm-generic/signal.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/uapi/asm-generic/signal-defs.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/generated/uapi/asm/siginfo.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/uapi/asm-generic/siginfo.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/syscall_user_dispatch_types.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/mm_types_task.h \
    $(wildcard include/config/ARCH_WANT_BATCHED_UNMAP_TLB_FLUSH) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/asm/tlbbatch.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/netdevice_xmit.h \
    $(wildcard include/config/NET_EGRESS) \
    $(wildcard include/config/NET_ACT_MIRRED) \
    $(wildcard include/config/NF_DUP_NETDEV) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/task_io_accounting.h \
    $(wildcard include/config/TASK_IO_ACCOUNTING) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/posix-timers_types.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/uapi/linux/rseq.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/seqlock_types.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/kcsan.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/rv.h \
    $(wildcard include/config/RV_LTL_MONITOR) \
    $(wildcard include/config/RV_REACTORS) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/uidgid_types.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/tracepoint-defs.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/unwind_deferred_types.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/generated/asm/kmap_size.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/asm-generic/kmap_size.h \
    $(wildcard include/config/DEBUG_KMAP_LOCAL) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/generated/rq-offsets.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/sched/ext.h \
    $(wildcard include/config/EXT_GROUP_SCHED) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/clocksource/arm_arch_timer.h \
    $(wildcard include/config/ARM_ARCH_TIMER) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/timecounter.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/asm-generic/timex.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/vdso/time32.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/vdso/time.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/asm/compat.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/asm-generic/compat.h \
    $(wildcard include/config/COMPAT_FOR_U64_ALIGNMENT) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/sched/task_stack.h \
    $(wildcard include/config/STACK_GROWSUP) \
    $(wildcard include/config/DEBUG_STACK_USAGE) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/uapi/linux/magic.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/kasan.h \
    $(wildcard include/config/KASAN_STACK) \
    $(wildcard include/config/KASAN_VMALLOC) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/uapi/linux/stat.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/uidgid.h \
    $(wildcard include/config/MULTIUSER) \
    $(wildcard include/config/USER_NS) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/highuid.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/buildid.h \
    $(wildcard include/config/VMCORE_INFO) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/kmod.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/umh.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/gfp.h \
    $(wildcard include/config/ZONE_DMA) \
    $(wildcard include/config/ZONE_DMA32) \
    $(wildcard include/config/ZONE_DEVICE) \
    $(wildcard include/config/CONTIG_ALLOC) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/mmzone.h \
    $(wildcard include/config/ARCH_FORCE_MAX_ORDER) \
    $(wildcard include/config/PAGE_BLOCK_MAX_ORDER) \
    $(wildcard include/config/CMA) \
    $(wildcard include/config/MEMORY_ISOLATION) \
    $(wildcard include/config/ZSMALLOC) \
    $(wildcard include/config/UNACCEPTED_MEMORY) \
    $(wildcard include/config/IOMMU_SUPPORT) \
    $(wildcard include/config/SWAP) \
    $(wildcard include/config/HUGETLB_PAGE) \
    $(wildcard include/config/TRANSPARENT_HUGEPAGE) \
    $(wildcard include/config/LRU_GEN_STATS) \
    $(wildcard include/config/LRU_GEN_WALKS_MMU) \
    $(wildcard include/config/MEMORY_FAILURE) \
    $(wildcard include/config/PAGE_EXTENSION) \
    $(wildcard include/config/DEFERRED_STRUCT_PAGE_INIT) \
    $(wildcard include/config/HAVE_MEMORYLESS_NODES) \
    $(wildcard include/config/SPARSEMEM_EXTREME) \
    $(wildcard include/config/SPARSEMEM_VMEMMAP_PREINIT) \
    $(wildcard include/config/HAVE_ARCH_PFN_VALID) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/list_nulls.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/wait.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/seqlock.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/mutex.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/debug_locks.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/pageblock-flags.h \
    $(wildcard include/config/HUGETLB_PAGE_SIZE_VARIABLE) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/page-flags-layout.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/generated/bounds.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/mm_types.h \
    $(wildcard include/config/HAVE_ALIGNED_STRUCT_PAGE) \
    $(wildcard include/config/HUGETLB_PMD_PAGE_TABLE_SHARING) \
    $(wildcard include/config/SLAB_FREELIST_HARDENED) \
    $(wildcard include/config/USERFAULTFD) \
    $(wildcard include/config/ANON_VMA_NAME) \
    $(wildcard include/config/PER_VMA_LOCK) \
    $(wildcard include/config/HAVE_ARCH_COMPAT_MMAP_BASES) \
    $(wildcard include/config/MEMBARRIER) \
    $(wildcard include/config/FUTEX_PRIVATE_HASH) \
    $(wildcard include/config/ARCH_HAS_ELF_CORE_EFLAGS) \
    $(wildcard include/config/AIO) \
    $(wildcard include/config/MMU_NOTIFIER) \
    $(wildcard include/config/SPLIT_PMD_PTLOCKS) \
    $(wildcard include/config/IOMMU_MM_DATA) \
    $(wildcard include/config/KSM) \
    $(wildcard include/config/MM_ID) \
    $(wildcard include/config/CORE_DUMP_DEFAULT_ELF_HEADERS) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/auxvec.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/uapi/linux/auxvec.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/uapi/asm/auxvec.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/rbtree.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/rcupdate.h \
    $(wildcard include/config/TINY_RCU) \
    $(wildcard include/config/RCU_STRICT_GRACE_PERIOD) \
    $(wildcard include/config/RCU_LAZY) \
    $(wildcard include/config/RCU_STALL_COMMON) \
    $(wildcard include/config/VIRT_XFER_TO_GUEST_WORK) \
    $(wildcard include/config/RCU_NOCB_CPU) \
    $(wildcard include/config/TASKS_RCU_GENERIC) \
    $(wildcard include/config/TASKS_RUDE_RCU) \
    $(wildcard include/config/TREE_RCU) \
    $(wildcard include/config/DEBUG_OBJECTS_RCU_HEAD) \
    $(wildcard include/config/PROVE_RCU) \
    $(wildcard include/config/ARCH_WEAK_RELEASE_ACQUIRE) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/context_tracking_irq.h \
    $(wildcard include/config/CONTEXT_TRACKING_IDLE) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/rcutree.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/maple_tree.h \
    $(wildcard include/config/MAPLE_RCU_DISABLED) \
    $(wildcard include/config/DEBUG_MAPLE_TREE) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/rwsem.h \
    $(wildcard include/config/RWSEM_SPIN_ON_OWNER) \
    $(wildcard include/config/DEBUG_RWSEMS) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/completion.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/swait.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/uprobes.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/timer.h \
    $(wildcard include/config/DEBUG_OBJECTS_TIMERS) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/ktime.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/jiffies.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/vdso/jiffies.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/generated/timeconst.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/vdso/ktime.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/timekeeping.h \
    $(wildcard include/config/POSIX_AUX_CLOCKS) \
    $(wildcard include/config/GENERIC_CMOS_UPDATE) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/clocksource_ids.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/debugobjects.h \
    $(wildcard include/config/DEBUG_OBJECTS) \
    $(wildcard include/config/DEBUG_OBJECTS_FREE) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/asm/uprobes.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/asm/debug-monitors.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/asm/esr.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/asm/probes.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/workqueue.h \
    $(wildcard include/config/DEBUG_OBJECTS_WORK) \
    $(wildcard include/config/FREEZER) \
    $(wildcard include/config/WQ_WATCHDOG) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/workqueue_types.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/percpu_counter.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/asm/mmu.h \
    $(wildcard include/config/ARM64_E0PD) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/page-flags.h \
    $(wildcard include/config/PAGE_IDLE_FLAG) \
    $(wildcard include/config/ARCH_USES_PG_ARCH_2) \
    $(wildcard include/config/ARCH_USES_PG_ARCH_3) \
    $(wildcard include/config/MIGRATION) \
    $(wildcard include/config/HUGETLB_PAGE_OPTIMIZE_VMEMMAP) \
    $(wildcard include/config/DEBUG_KMAP_LOCAL_FORCE_MAP) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/local_lock.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/local_lock_internal.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/zswap.h \
    $(wildcard include/config/ZSWAP) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/memory_hotplug.h \
    $(wildcard include/config/ARCH_HAS_ADD_PAGES) \
    $(wildcard include/config/MEMORY_HOTREMOVE) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/notifier.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/srcu.h \
    $(wildcard include/config/TINY_SRCU) \
    $(wildcard include/config/NEED_SRCU_NMI_SAFE) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/rcu_segcblist.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/srcutree.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/rcu_node_tree.h \
    $(wildcard include/config/RCU_FANOUT) \
    $(wildcard include/config/RCU_FANOUT_LEAF) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/generated/asm/mmzone.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/asm-generic/mmzone.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/topology.h \
    $(wildcard include/config/USE_PERCPU_NUMA_NODE_ID) \
    $(wildcard include/config/SCHED_SMT) \
    $(wildcard include/config/GENERIC_ARCH_TOPOLOGY) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/arch_topology.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/asm/topology.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/asm/numa.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/asm-generic/numa.h \
    $(wildcard include/config/NUMA_EMU) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/asm-generic/topology.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/sysctl.h \
    $(wildcard include/config/SYSCTL) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/uapi/linux/sysctl.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/elf.h \
    $(wildcard include/config/ARCH_HAVE_EXTRA_ELF_NOTES) \
    $(wildcard include/config/ARCH_USE_GNU_PROPERTY) \
    $(wildcard include/config/ARCH_HAVE_ELF_PROT) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/asm/elf.h \
    $(wildcard include/config/COMPAT_VDSO) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/generated/asm/user.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/asm-generic/user.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/uapi/linux/elf.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/uapi/linux/elf-em.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/fs.h \
    $(wildcard include/config/FANOTIFY_ACCESS_PERMISSIONS) \
    $(wildcard include/config/READ_ONLY_THP_FOR_FS) \
    $(wildcard include/config/FS_POSIX_ACL) \
    $(wildcard include/config/CGROUP_WRITEBACK) \
    $(wildcard include/config/IMA) \
    $(wildcard include/config/FILE_LOCKING) \
    $(wildcard include/config/FSNOTIFY) \
    $(wildcard include/config/EPOLL) \
    $(wildcard include/config/UNICODE) \
    $(wildcard include/config/FS_ENCRYPTION) \
    $(wildcard include/config/FS_VERITY) \
    $(wildcard include/config/QUOTA) \
    $(wildcard include/config/FS_DAX) \
    $(wildcard include/config/BLOCK) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/vfsdebug.h \
    $(wildcard include/config/DEBUG_VFS) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/wait_bit.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/kdev_t.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/uapi/linux/kdev_t.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/dcache.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/rculist.h \
    $(wildcard include/config/PROVE_RCU_LIST) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/rculist_bl.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/list_bl.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/bit_spinlock.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/lockref.h \
    $(wildcard include/config/ARCH_USE_CMPXCHG_LOCKREF) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/stringhash.h \
    $(wildcard include/config/DCACHE_WORD_ACCESS) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/hash.h \
    $(wildcard include/config/HAVE_ARCH_HASH) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/path.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/list_lru.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/shrinker.h \
    $(wildcard include/config/SHRINKER_DEBUG) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/xarray.h \
    $(wildcard include/config/XARRAY_MULTI) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/sched/mm.h \
    $(wildcard include/config/MMU_LAZY_TLB_REFCOUNT) \
    $(wildcard include/config/ARCH_HAS_MEMBARRIER_CALLBACKS) \
    $(wildcard include/config/ARCH_HAS_SYNC_CORE_BEFORE_USERMODE) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/sync_core.h \
    $(wildcard include/config/ARCH_HAS_PREPARE_SYNC_CORE_CMD) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/sched/coredump.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/radix-tree.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/pid.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/capability.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/uapi/linux/capability.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/semaphore.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/fcntl.h \
    $(wildcard include/config/ARCH_32BIT_OFF_T) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/uapi/linux/fcntl.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/uapi/asm/fcntl.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/uapi/asm-generic/fcntl.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/uapi/linux/openat2.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/migrate_mode.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/percpu-rwsem.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/rcuwait.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/sched/signal.h \
    $(wildcard include/config/SCHED_AUTOGROUP) \
    $(wildcard include/config/BSD_PROCESS_ACCT) \
    $(wildcard include/config/TASKSTATS) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/signal.h \
    $(wildcard include/config/DYNAMIC_SIGFRAME) \
    $(wildcard include/config/PROC_FS) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/sched/jobctl.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/sched/task.h \
    $(wildcard include/config/HAVE_EXIT_THREAD) \
    $(wildcard include/config/ARCH_WANTS_DYNAMIC_TASK_STRUCT) \
    $(wildcard include/config/HAVE_ARCH_THREAD_STRUCT_WHITELIST) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/uaccess.h \
    $(wildcard include/config/ARCH_HAS_SUBPAGE_FAULTS) \
    $(wildcard include/config/HARDENED_USERCOPY) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/fault-inject-usercopy.h \
    $(wildcard include/config/FAULT_INJECTION_USERCOPY) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/nospec.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/ucopysize.h \
    $(wildcard include/config/HARDENED_USERCOPY_DEFAULT_ON) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/asm/uaccess.h \
    $(wildcard include/config/CC_HAS_ASM_GOTO_OUTPUT) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/asm/kernel-pgtable.h \
    $(wildcard include/config/RELOCATABLE) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/asm/asm-extable.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/asm/mte.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/asm/extable.h \
    $(wildcard include/config/BPF_JIT) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/asm-generic/access_ok.h \
    $(wildcard include/config/ALTERNATE_USER_ADDRESS_SPACE) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/cred.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/key.h \
    $(wildcard include/config/KEY_NOTIFICATIONS) \
    $(wildcard include/config/NET) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/assoc_array.h \
    $(wildcard include/config/ASSOCIATIVE_ARRAY) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/sched/user.h \
    $(wildcard include/config/VFIO_PCI_ZDEV_KVM) \
    $(wildcard include/config/IOMMUFD) \
    $(wildcard include/config/WATCH_QUEUE) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/ratelimit.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/posix-timers.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/alarmtimer.h \
    $(wildcard include/config/RTC_CLASS) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/hrtimer.h \
    $(wildcard include/config/HIGH_RES_TIMERS) \
    $(wildcard include/config/TIME_LOW_RES) \
    $(wildcard include/config/TIMERFD) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/hrtimer_defs.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/timerqueue.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/rcuref.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/rcu_sync.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/delayed_call.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/errseq.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/ioprio.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/sched/rt.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/iocontext.h \
    $(wildcard include/config/BLK_ICQ) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/uapi/linux/ioprio.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/fs_types.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/mount.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/mnt_idmapping.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/slab.h \
    $(wildcard include/config/FAILSLAB) \
    $(wildcard include/config/KFENCE) \
    $(wildcard include/config/SLUB_TINY) \
    $(wildcard include/config/SLUB_DEBUG) \
    $(wildcard include/config/SLAB_BUCKETS) \
    $(wildcard include/config/KVFREE_RCU_BATCHED) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/percpu-refcount.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/rw_hint.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/file_ref.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/unicode.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/uapi/linux/fs.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/quota.h \
    $(wildcard include/config/QUOTA_NETLINK_INTERFACE) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/uapi/linux/dqblk_xfs.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/dqblk_v1.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/dqblk_v2.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/dqblk_qtree.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/projid.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/uapi/linux/quota.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/kobject.h \
    $(wildcard include/config/UEVENT_HELPER) \
    $(wildcard include/config/DEBUG_KOBJECT_RELEASE) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/sysfs.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/kernfs.h \
    $(wildcard include/config/KERNFS) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/idr.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/kobject_ns.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/moduleparam.h \
    $(wildcard include/config/ALPHA) \
    $(wildcard include/config/PPC64) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/rbtree_latch.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/error-injection.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/asm-generic/error-injection.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/asm/module.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/asm-generic/module.h \
    $(wildcard include/config/HAVE_MOD_ARCH_SPECIFIC) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/kunit/resource.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/skbuff.h \
    $(wildcard include/config/NF_CONNTRACK) \
    $(wildcard include/config/BRIDGE_NETFILTER) \
    $(wildcard include/config/NET_TC_SKB_EXT) \
    $(wildcard include/config/MAX_SKB_FRAGS) \
    $(wildcard include/config/NET_SOCK_MSG) \
    $(wildcard include/config/SKB_EXTENSIONS) \
    $(wildcard include/config/NET_XGRESS) \
    $(wildcard include/config/WIRELESS) \
    $(wildcard include/config/IPV6_NDISC_NODETYPE) \
    $(wildcard include/config/IP_VS) \
    $(wildcard include/config/NETFILTER_XT_TARGET_TRACE) \
    $(wildcard include/config/NF_TABLES) \
    $(wildcard include/config/NET_SWITCHDEV) \
    $(wildcard include/config/NET_REDIRECT) \
    $(wildcard include/config/NETFILTER_SKIP_EGRESS) \
    $(wildcard include/config/SKB_DECRYPTED) \
    $(wildcard include/config/IP_SCTP) \
    $(wildcard include/config/NET_SCHED) \
    $(wildcard include/config/CPE_FAST_PATH) \
    $(wildcard include/config/NET_RX_BUSY_POLL) \
    $(wildcard include/config/XPS) \
    $(wildcard include/config/NETWORK_SECMARK) \
    $(wildcard include/config/INET_IPSEC_OFFLOAD) \
    $(wildcard include/config/INET6_IPSEC_OFFLOAD) \
    $(wildcard include/config/DEBUG_NET) \
    $(wildcard include/config/FAIL_SKB_REALLOC) \
    $(wildcard include/config/HAVE_EFFICIENT_UNALIGNED_ACCESS) \
    $(wildcard include/config/NETWORK_PHY_TIMESTAMPING) \
    $(wildcard include/config/XFRM) \
    $(wildcard include/config/MPTCP) \
    $(wildcard include/config/MCTP_FLOWS) \
    $(wildcard include/config/INET_PSP) \
    $(wildcard include/config/PAGE_POOL) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/bvec.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/highmem.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/cacheflush.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/asm/cacheflush.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/kgdb.h \
    $(wildcard include/config/HAVE_ARCH_KGDB) \
    $(wildcard include/config/KGDB) \
    $(wildcard include/config/KGDB_HONOUR_BLOCKLIST) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/kprobes.h \
    $(wildcard include/config/KRETPROBE_ON_RETHOOK) \
    $(wildcard include/config/OPTPROBES) \
    $(wildcard include/config/KPROBES_ON_FTRACE) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/ftrace.h \
    $(wildcard include/config/HAVE_FUNCTION_GRAPH_FREGS) \
    $(wildcard include/config/FUNCTION_TRACER) \
    $(wildcard include/config/HAVE_DYNAMIC_FTRACE_WITH_ARGS) \
    $(wildcard include/config/HAVE_FTRACE_REGS_HAVING_PT_REGS) \
    $(wildcard include/config/HAVE_REGS_AND_STACK_ACCESS_API) \
    $(wildcard include/config/DYNAMIC_FTRACE_WITH_REGS) \
    $(wildcard include/config/DYNAMIC_FTRACE_WITH_ARGS) \
    $(wildcard include/config/DYNAMIC_FTRACE_WITH_DIRECT_CALLS) \
    $(wildcard include/config/STACK_TRACER) \
    $(wildcard include/config/DYNAMIC_FTRACE_WITH_CALL_OPS) \
    $(wildcard include/config/FRAME_POINTER) \
    $(wildcard include/config/FUNCTION_GRAPH_RETVAL) \
    $(wildcard include/config/FTRACE_SYSCALLS) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/trace_recursion.h \
    $(wildcard include/config/FTRACE_RECORD_RECURSION) \
    $(wildcard include/config/FTRACE_VALIDATE_RCU_IS_WATCHING) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/interrupt.h \
    $(wildcard include/config/IRQ_FORCED_THREADING) \
    $(wildcard include/config/GENERIC_IRQ_PROBE) \
    $(wildcard include/config/IRQ_TIMINGS) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/irqreturn.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/hardirq.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/context_tracking_state.h \
    $(wildcard include/config/CONTEXT_TRACKING_USER) \
    $(wildcard include/config/CONTEXT_TRACKING) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/ftrace_irq.h \
    $(wildcard include/config/HWLAT_TRACER) \
    $(wildcard include/config/OSNOISE_TRACER) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/vtime.h \
    $(wildcard include/config/VIRT_CPU_ACCOUNTING) \
    $(wildcard include/config/IRQ_TIME_ACCOUNTING) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/asm/hardirq.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/asm/irq.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/asm-generic/irq.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/asm/kvm_arm.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/asm-generic/hardirq.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/irq.h \
    $(wildcard include/config/GENERIC_IRQ_EFFECTIVE_AFF_MASK) \
    $(wildcard include/config/GENERIC_IRQ_IPI) \
    $(wildcard include/config/IRQ_DOMAIN_HIERARCHY) \
    $(wildcard include/config/DEPRECATED_IRQ_CPU_ONOFFLINE) \
    $(wildcard include/config/GENERIC_IRQ_MIGRATION) \
    $(wildcard include/config/GENERIC_PENDING_IRQ) \
    $(wildcard include/config/HARDIRQS_SW_RESEND) \
    $(wildcard include/config/GENERIC_IRQ_CHIP) \
    $(wildcard include/config/GENERIC_IRQ_MULTI_HANDLER) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/irqhandler.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/io.h \
    $(wildcard include/config/HAS_IOPORT_MAP) \
    $(wildcard include/config/PCI) \
    $(wildcard include/config/STRICT_DEVMEM) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/asm/io.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/pgtable.h \
    $(wildcard include/config/HIGHPTE) \
    $(wildcard include/config/ARCH_HAS_NONLEAF_PMD_YOUNG) \
    $(wildcard include/config/ARCH_HAS_HW_PTE_YOUNG) \
    $(wildcard include/config/GUP_GET_PXX_LOW_HIGH) \
    $(wildcard include/config/ARCH_WANT_PMD_MKWRITE) \
    $(wildcard include/config/HAVE_ARCH_TRANSPARENT_HUGEPAGE_PUD) \
    $(wildcard include/config/HAVE_ARCH_SOFT_DIRTY) \
    $(wildcard include/config/ARCH_ENABLE_THP_MIGRATION) \
    $(wildcard include/config/HAVE_ARCH_HUGE_VMAP) \
    $(wildcard include/config/X86_ESPFIX64) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/asm/pgtable.h \
    $(wildcard include/config/DEBUG_PAGEALLOC) \
    $(wildcard include/config/ARCH_SUPPORTS_PMD_PFNMAP) \
    $(wildcard include/config/PAGE_TABLE_CHECK) \
    $(wildcard include/config/ARM64_CONTPTE) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/asm/proc-fns.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/asm/tlbflush.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/mmu_notifier.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/mmap_lock.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/interval_tree.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/asm/fixmap.h \
    $(wildcard include/config/ACPI_APEI_GHES) \
    $(wildcard include/config/ARM_SDE_INTERFACE) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/asm-generic/fixmap.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/asm/por.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/page_table_check.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/asm-generic/pgtable_uffd.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/generated/asm/early_ioremap.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/asm-generic/early_ioremap.h \
    $(wildcard include/config/GENERIC_EARLY_IOREMAP) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/asm-generic/io.h \
    $(wildcard include/config/GENERIC_IOMAP) \
    $(wildcard include/config/TRACE_MMIO_ACCESS) \
    $(wildcard include/config/HAS_IOPORT) \
    $(wildcard include/config/GENERIC_IOREMAP) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/asm-generic/pci_iomap.h \
    $(wildcard include/config/NO_GENERIC_PCI_IOPORT_MAP) \
    $(wildcard include/config/GENERIC_PCI_IOMAP) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/logic_pio.h \
    $(wildcard include/config/INDIRECT_PIO) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/fwnode.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/generated/asm/irq_regs.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/asm-generic/irq_regs.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/irqdesc.h \
    $(wildcard include/config/GENERIC_IRQ_STAT_SNAPSHOT) \
    $(wildcard include/config/PM_SLEEP) \
    $(wildcard include/config/GENERIC_IRQ_DEBUGFS) \
    $(wildcard include/config/SPARSE_IRQ) \
    $(wildcard include/config/IRQ_DOMAIN) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/generated/asm/hw_irq.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/asm-generic/hw_irq.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/trace_clock.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/generated/asm/trace_clock.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/asm-generic/trace_clock.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/kallsyms.h \
    $(wildcard include/config/KALLSYMS_ALL) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/mm.h \
    $(wildcard include/config/HAVE_ARCH_MMAP_RND_BITS) \
    $(wildcard include/config/HAVE_ARCH_MMAP_RND_COMPAT_BITS) \
    $(wildcard include/config/MEM_SOFT_DIRTY) \
    $(wildcard include/config/ARCH_USES_HIGH_VMA_FLAGS) \
    $(wildcard include/config/ARCH_HAS_PKEYS) \
    $(wildcard include/config/ARCH_PKEY_BITS) \
    $(wildcard include/config/X86_USER_SHADOW_STACK) \
    $(wildcard include/config/PARISC) \
    $(wildcard include/config/SPARC64) \
    $(wildcard include/config/HAVE_ARCH_USERFAULTFD_MINOR) \
    $(wildcard include/config/PPC32) \
    $(wildcard include/config/FIND_NORMAL_PAGE) \
    $(wildcard include/config/SHMEM) \
    $(wildcard include/config/HAVE_GIGANTIC_FOLIOS) \
    $(wildcard include/config/ARCH_HAS_PTE_SPECIAL) \
    $(wildcard include/config/ARCH_SUPPORTS_PUD_PFNMAP) \
    $(wildcard include/config/ASYNC_KERNEL_PGTABLE_FREE) \
    $(wildcard include/config/SPLIT_PTE_PTLOCKS) \
    $(wildcard include/config/DEBUG_VM_RB) \
    $(wildcard include/config/PAGE_POISONING) \
    $(wildcard include/config/INIT_ON_ALLOC_DEFAULT_ON) \
    $(wildcard include/config/INIT_ON_FREE_DEFAULT_ON) \
    $(wildcard include/config/ARCH_WANT_OPTIMIZE_DAX_VMEMMAP) \
    $(wildcard include/config/HUGETLBFS) \
    $(wildcard include/config/MAPPING_DIRTY_HELPERS) \
    $(wildcard include/config/MSEAL_SYSTEM_MAPPINGS) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/pgalloc_tag.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/range.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/page_ext.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/stacktrace.h \
    $(wildcard include/config/ARCH_STACKWALK) \
    $(wildcard include/config/STACKTRACE) \
    $(wildcard include/config/HAVE_RELIABLE_STACKTRACE) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/page_ref.h \
    $(wildcard include/config/DEBUG_PAGE_REF) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/memremap.h \
    $(wildcard include/config/DEVICE_PRIVATE) \
    $(wildcard include/config/PCI_P2PDMA) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/ioport.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/cacheinfo.h \
    $(wildcard include/config/ACPI_PPTT) \
    $(wildcard include/config/ARCH_HAS_CPU_CACHE_ALIASING) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/cpuhplock.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/huge_mm.h \
    $(wildcard include/config/PGTABLE_HAS_HUGE_LEAVES) \
    $(wildcard include/config/PERSISTENT_HUGE_ZERO_FOLIO) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/vmstat.h \
    $(wildcard include/config/VM_EVENT_COUNTERS) \
    $(wildcard include/config/DEBUG_TLBFLUSH) \
    $(wildcard include/config/PER_VMA_LOCK_STATS) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/vm_event_item.h \
    $(wildcard include/config/MEMORY_BALLOON) \
    $(wildcard include/config/BALLOON_COMPACTION) \
    $(wildcard include/config/X86) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/ptrace.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/pid_namespace.h \
    $(wildcard include/config/MEMFD_CREATE) \
    $(wildcard include/config/PID_NS) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/nsproxy.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/ns_common.h \
    $(wildcard include/config/IPC_NS) \
    $(wildcard include/config/NET_NS) \
    $(wildcard include/config/TIME_NS) \
    $(wildcard include/config/UTS_NS) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/uapi/linux/ptrace.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/seccomp.h \
    $(wildcard include/config/HAVE_ARCH_SECCOMP_FILTER) \
    $(wildcard include/config/SECCOMP_FILTER) \
    $(wildcard include/config/CHECKPOINT_RESTORE) \
    $(wildcard include/config/SECCOMP_CACHE_DEBUG) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/uapi/linux/seccomp.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/asm/seccomp.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/generated/asm/unistd_compat_32.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/asm-generic/seccomp.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/uapi/linux/unistd.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/asm/unistd.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/generated/uapi/asm/unistd_64.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/asm/ftrace.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/compat.h \
    $(wildcard include/config/ARCH_HAS_SYSCALL_WRAPPER) \
    $(wildcard include/config/X86_X32_ABI) \
    $(wildcard include/config/COMPAT_OLD_SIGACTION) \
    $(wildcard include/config/ODD_RT_SIGACTION) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/sem.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/uapi/linux/sem.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/ipc.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/rhashtable-types.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/uapi/linux/ipc.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/generated/uapi/asm/ipcbuf.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/uapi/asm-generic/ipcbuf.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/generated/uapi/asm/sembuf.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/uapi/asm-generic/sembuf.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/socket.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/generated/uapi/asm/socket.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/uapi/asm-generic/socket.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/generated/uapi/asm/sockios.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/uapi/asm-generic/sockios.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/uapi/linux/sockios.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/uio.h \
    $(wildcard include/config/ARCH_HAS_COPY_MC) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/uapi/linux/uio.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/uapi/linux/socket.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/uapi/linux/if.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/uapi/linux/libc-compat.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/uapi/linux/hdlc/ioctl.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/uapi/linux/aio_abi.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/asm/syscall_wrapper.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/ftrace_regs.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/objpool.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/rethook.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/asm/kprobes.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/asm-generic/kprobes.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/asm/kgdb.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/asm-generic/cacheflush.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/kmsan.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/dma-direction.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/highmem-internal.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/net/checksum.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/asm/checksum.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/in6.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/uapi/linux/in6.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/asm-generic/checksum.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/dma-mapping.h \
    $(wildcard include/config/DMA_API_DEBUG) \
    $(wildcard include/config/HAS_DMA) \
    $(wildcard include/config/IOMMU_DMA) \
    $(wildcard include/config/DMA_NEED_SYNC) \
    $(wildcard include/config/NEED_DMA_MAP_STATE) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/device.h \
    $(wildcard include/config/GENERIC_MSI_IRQ) \
    $(wildcard include/config/ENERGY_MODEL) \
    $(wildcard include/config/PINCTRL) \
    $(wildcard include/config/ARCH_HAS_DMA_OPS) \
    $(wildcard include/config/DMA_DECLARE_COHERENT) \
    $(wildcard include/config/DMA_CMA) \
    $(wildcard include/config/SWIOTLB) \
    $(wildcard include/config/SWIOTLB_DYNAMIC) \
    $(wildcard include/config/ARCH_HAS_SYNC_DMA_FOR_DEVICE) \
    $(wildcard include/config/ARCH_HAS_SYNC_DMA_FOR_CPU) \
    $(wildcard include/config/ARCH_HAS_SYNC_DMA_FOR_CPU_ALL) \
    $(wildcard include/config/DMA_OPS_BYPASS) \
    $(wildcard include/config/PM) \
    $(wildcard include/config/OF) \
    $(wildcard include/config/DEVTMPFS) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/dev_printk.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/energy_model.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/sched/cpufreq.h \
    $(wildcard include/config/CPU_FREQ) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/sched/topology.h \
    $(wildcard include/config/SCHED_CLUSTER) \
    $(wildcard include/config/SCHED_MC) \
    $(wildcard include/config/CPU_FREQ_GOV_SCHEDUTIL) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/sched/idle.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/sched/sd_flags.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/klist.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/pm.h \
    $(wildcard include/config/VT_CONSOLE_SLEEP) \
    $(wildcard include/config/CXL_SUSPEND) \
    $(wildcard include/config/PM_CLK) \
    $(wildcard include/config/PM_GENERIC_DOMAINS) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/device/bus.h \
    $(wildcard include/config/ACPI) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/device/class.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/device/devres.h \
    $(wildcard include/config/HAS_IOMEM) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/device/driver.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/asm/device.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/pm_wakeup.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/scatterlist.h \
    $(wildcard include/config/NEED_SG_DMA_LENGTH) \
    $(wildcard include/config/NEED_SG_DMA_FLAGS) \
    $(wildcard include/config/DEBUG_SG) \
    $(wildcard include/config/SGL_ALLOC) \
    $(wildcard include/config/ARCH_NO_SG_CHAIN) \
    $(wildcard include/config/SG_POOL) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/netdev_features.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/net/flow_dissector.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/siphash.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/uapi/linux/if_ether.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/uapi/linux/pkt_cls.h \
    $(wildcard include/config/NET_CLS_ACT) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/uapi/linux/pkt_sched.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/uapi/linux/if_packet.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/page_frag_cache.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/net/flow.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/net/inet_dscp.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/netfilter/nf_conntrack_common.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/uapi/linux/netfilter/nf_conntrack_common.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/net/net_debug.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/net/dropreason-core.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/net/netmem.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/u64_stats_sync.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/generated/asm/local64.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/asm-generic/local64.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/generated/asm/local.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/asm-generic/local.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/net/genetlink.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/net.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/once.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/sockptr.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/uapi/linux/net.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/net/netlink.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/netlink.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/net/scm.h \
    $(wildcard include/config/UNIX) \
    $(wildcard include/config/SECURITY_NETWORK) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/file.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/security.h \
    $(wildcard include/config/SECURITY_INFINIBAND) \
    $(wildcard include/config/SECURITY_NETWORK_XFRM) \
    $(wildcard include/config/SECURITY_PATH) \
    $(wildcard include/config/SECURITYFS) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/kernel_read_file.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/bpf.h \
    $(wildcard include/config/CGROUP_BPF) \
    $(wildcard include/config/DEBUG_KERNEL) \
    $(wildcard include/config/FINEIBT) \
    $(wildcard include/config/BPF_LSM) \
    $(wildcard include/config/BPF_JIT_ALWAYS_ON) \
    $(wildcard include/config/INET) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/uapi/linux/bpf.h \
    $(wildcard include/config/BPF_LIRC_MODE2) \
    $(wildcard include/config/EFFICIENT_UNALIGNED_ACCESS) \
    $(wildcard include/config/CGROUP_NET_CLASSID) \
    $(wildcard include/config/IP_ROUTE_CLASSID) \
    $(wildcard include/config/BPF_KPROBE_OVERRIDE) \
    $(wildcard include/config/SOCK_CGROUP_DATA) \
    $(wildcard include/config/IPV6) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/uapi/linux/bpf_common.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/uapi/linux/filter.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/crypto/sha2.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/bpfptr.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/btf.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/bsearch.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/btf_ids.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/uapi/linux/btf.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/rcupdate_trace.h \
    $(wildcard include/config/TASKS_TRACE_RCU_READ_MB) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/static_call.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/cpu.h \
    $(wildcard include/config/GENERIC_CPU_DEVICES) \
    $(wildcard include/config/PM_SLEEP_SMP) \
    $(wildcard include/config/PM_SLEEP_SMP_NONZERO_CPU) \
    $(wildcard include/config/ARCH_HAS_CPU_FINALIZE_INIT) \
    $(wildcard include/config/CPU_MITIGATIONS) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/node.h \
    $(wildcard include/config/HMEM_REPORTING) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/cpuhotplug.h \
    $(wildcard include/config/HOTPLUG_CORE_SYNC_DEAD) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/cpu_smt.h \
    $(wildcard include/config/HOTPLUG_SMT) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/memcontrol.h \
    $(wildcard include/config/MEMCG_NMI_SAFETY_REQUIRES_ATOMIC) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/cgroup.h \
    $(wildcard include/config/DEBUG_CGROUP_REF) \
    $(wildcard include/config/CGROUP_CPUACCT) \
    $(wildcard include/config/CGROUP_DATA) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/uapi/linux/cgroupstats.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/uapi/linux/taskstats.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/seq_file.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/string_helpers.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/ctype.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/string_choices.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/user_namespace.h \
    $(wildcard include/config/INOTIFY_USER) \
    $(wildcard include/config/FANOTIFY) \
    $(wildcard include/config/BINFMT_MISC) \
    $(wildcard include/config/PERSISTENT_KEYRINGS) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/rculist_nulls.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/kernel_stat.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/cgroup-defs.h \
    $(wildcard include/config/CGROUP_NET_PRIO) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/bpf-cgroup-defs.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/psi_types.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/kthread.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/cgroup_subsys.h \
    $(wildcard include/config/CGROUP_DEVICE) \
    $(wildcard include/config/CGROUP_FREEZER) \
    $(wildcard include/config/CGROUP_PERF) \
    $(wildcard include/config/CGROUP_HUGETLB) \
    $(wildcard include/config/CGROUP_PIDS) \
    $(wildcard include/config/CGROUP_RDMA) \
    $(wildcard include/config/CGROUP_MISC) \
    $(wildcard include/config/CGROUP_DMEM) \
    $(wildcard include/config/CGROUP_DEBUG) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/cgroup_namespace.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/cgroup_refcnt.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/page_counter.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/vmpressure.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/eventfd.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/uapi/linux/eventfd.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/writeback.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/flex_proportions.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/backing-dev-defs.h \
    $(wildcard include/config/DEBUG_FS) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/blk_types.h \
    $(wildcard include/config/FAIL_MAKE_REQUEST) \
    $(wildcard include/config/BLK_CGROUP_IOCOST) \
    $(wildcard include/config/BLK_INLINE_ENCRYPTION) \
    $(wildcard include/config/BLK_DEV_INTEGRITY) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/pagevec.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/cfi.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/asm/cfi.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/arch/arm64/include/asm/rqspinlock.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/asm-generic/rqspinlock.h \
    $(wildcard include/config/QUEUED_SPINLOCKS) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/bpf_types.h \
    $(wildcard include/config/NETFILTER_BPF_LINK) \
    $(wildcard include/config/XDP_SOCKETS) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/uapi/linux/lsm.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/lsm/selinux.h \
    $(wildcard include/config/SECURITY_SELINUX) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/lsm/smack.h \
    $(wildcard include/config/SECURITY_SMACK) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/lsm/apparmor.h \
    $(wildcard include/config/SECURITY_APPARMOR) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/lsm/bpf.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/net/compat.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/uapi/linux/netlink.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/net/net_namespace.h \
    $(wildcard include/config/NF_FLOW_TABLE) \
    $(wildcard include/config/IEEE802154_6LOWPAN) \
    $(wildcard include/config/NETFILTER) \
    $(wildcard include/config/WEXT_CORE) \
    $(wildcard include/config/MPLS) \
    $(wildcard include/config/CAN) \
    $(wildcard include/config/MCTP) \
    $(wildcard include/config/CRYPTO_USER) \
    $(wildcard include/config/SMC) \
    $(wildcard include/config/DEBUG_NET_SMALL_RTNL) \
    $(wildcard include/config/NET_NS_REFCNT_TRACKER) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/net/netns/core.h \
    $(wildcard include/config/RPS) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/net/netns/mib.h \
    $(wildcard include/config/XFRM_STATISTICS) \
    $(wildcard include/config/TLS) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/net/snmp.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/uapi/linux/snmp.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/net/netns/unix.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/net/netns/packet.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/net/netns/ipv4.h \
    $(wildcard include/config/IP_ROUTE_MULTIPATH) \
    $(wildcard include/config/NET_UDP_TUNNEL) \
    $(wildcard include/config/IP_MULTIPLE_TABLES) \
    $(wildcard include/config/NET_L3_MASTER_DEV) \
    $(wildcard include/config/IP_MROUTE) \
    $(wildcard include/config/IP_MROUTE_MULTIPLE_TABLES) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/net/inet_frag.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/net/netns/ipv6.h \
    $(wildcard include/config/IPV6_MULTIPLE_TABLES) \
    $(wildcard include/config/IPV6_SUBTREES) \
    $(wildcard include/config/IPV6_MROUTE) \
    $(wildcard include/config/IPV6_MROUTE_MULTIPLE_TABLES) \
    $(wildcard include/config/NF_DEFRAG_IPV6) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/net/dst_ops.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/uapi/linux/icmpv6.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/net/netns/nexthop.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/net/netns/ieee802154_6lowpan.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/net/netns/sctp.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/net/netns/netfilter.h \
    $(wildcard include/config/LWTUNNEL) \
    $(wildcard include/config/NETFILTER_FAMILY_ARP) \
    $(wildcard include/config/NETFILTER_FAMILY_BRIDGE) \
    $(wildcard include/config/NF_DEFRAG_IPV4) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/netfilter_defs.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/uapi/linux/netfilter.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/in.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/uapi/linux/in.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/net/netns/conntrack.h \
    $(wildcard include/config/NF_CT_PROTO_SCTP) \
    $(wildcard include/config/NF_CT_PROTO_GRE) \
    $(wildcard include/config/NF_CONNTRACK_EVENTS) \
    $(wildcard include/config/NF_CONNTRACK_LABELS) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/netfilter/nf_conntrack_tcp.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/uapi/linux/netfilter/nf_conntrack_tcp.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/netfilter/nf_conntrack_sctp.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/uapi/linux/netfilter/nf_conntrack_sctp.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/uapi/linux/netfilter/nf_conntrack_tuple_common.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/net/netns/flow_table.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/net/netns/nftables.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/net/netns/xfrm.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/uapi/linux/xfrm.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/net/netns/mpls.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/net/netns/can.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/net/netns/xdp.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/net/netns/smc.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/net/netns/bpf.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/net/netns/mctp.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/hashtable.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/net/net_trackers.h \
    $(wildcard include/config/NET_DEV_REFCNT_TRACKER) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/ref_tracker.h \
    $(wildcard include/config/REF_TRACKER) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/stackdepot.h \
    $(wildcard include/config/STACKDEPOT) \
    $(wildcard include/config/STACKDEPOT_MAX_FRAMES) \
    $(wildcard include/config/STACKDEPOT_ALWAYS_INIT) \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/seq_file_net.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/uapi/linux/genetlink.h \
  tests/../include/uapi/linux/ask/ask.h \
  tests/../include/ask_internal.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/rhashtable.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/jhash.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/unaligned.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/linux/unaligned/packed_struct.h \
  /home/vyos/kernel-ls1046a-build/work/linux-6.18.26/include/vdso/unaligned.h \

tests/ask_test_genl.o: $(deps_tests/ask_test_genl.o)

$(deps_tests/ask_test_genl.o):
