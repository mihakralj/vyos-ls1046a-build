/* Copyright 2014 Freescale Semiconductor, Inc.
 *
 * Redistribution and use in source and binary forms, with or without
 * modification, are permitted provided that the following conditions are met:
 *     * Redistributions of source code must retain the above copyright
 *       notice, this list of conditions and the following disclaimer.
 *     * Redistributions in binary form must reproduce the above copyright
 *       notice, this list of conditions and the following disclaimer in the
 *       documentation and/or other materials provided with the distribution.
 *     * Neither the name of Freescale Semiconductor nor the
 *       names of its contributors may be used to endorse or promote products
 *       derived from this software without specific prior written permission.
 *
 *
 * ALTERNATIVELY, this software may be distributed under the terms of the
 * GNU General Public License ("GPL") as published by the Free Software
 * Foundation, either version 2 of that License or (at your option) any
 * later version.
 *
 * THIS SOFTWARE IS PROVIDED BY Freescale Semiconductor ``AS IS'' AND ANY
 * EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
 * WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
 * DISCLAIMED. IN NO EVENT SHALL Freescale Semiconductor BE LIABLE FOR ANY
 * DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
 * (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
 * LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND
 * ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
 * (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
 * SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
 */

#ifndef DPA_SYS_ARM64_H
#define DPA_SYS_ARM64_H

#include <asm/cacheflush.h>
#include <asm/barrier.h>

/* ASK-edit (mainline-6.18-port): the NXP SDK arch tree provided
 * ioremap_cache_ns() to map the QBMan portal Cache-Enabled (CENA) region as
 * Normal / WriteBack / Non-Shareable, via an SDK-added MT_NORMAL_NS MAIR entry
 * in arch/arm64. Mainline 6.18 has no such helper and we deliberately do NOT
 * patch arch/arm64 (keep mainline files untouched). Mainline's own
 * drivers/soc/fsl/qbman maps the same CENA region with memremap(MEMREMAP_WB)
 * (Normal / WriteBack / Shareable) and works on LS1046A, and this driver does
 * its own explicit cacheline maintenance (dcbf/dcbi) on the portal, which stays
 * correct under a shareable mapping. Fall back to ioremap_cache() (Normal /
 * WriteBack); the only attribute difference is shareability, which is safe. */
#ifndef ioremap_cache_ns
#define ioremap_cache_ns(addr, size) ioremap_cache((addr), (size))
#endif

/* ASK-edit (mainline-6.18-port): companion to ioremap_cache_ns() above for the
 * userspace mmap() path (fsl_usdpaa.c). The SDK arch tree provided
 * pgprot_cached_ns() to map the QBMan portal Cache-Enabled (CENA) region into
 * user space as Normal / WriteBack / Non-Shareable (its MT_NORMAL_NS MAIR
 * entry). Mainline 6.18 has no such pgprot helper and we do NOT patch
 * arch/arm64. Build it from the standard cacheable memory type (MT_NORMAL,
 * Normal / WriteBack) using mainline's own __pgprot_modify() — the same
 * primitive pgprot_noncached()/pgprot_writecombine() are built from. The only
 * attribute difference from the SDK original is shareability; the driver does
 * its own explicit dcbf/dc-ivac maintenance on the portal, which stays correct
 * under a shareable mapping, so this is safe. PXN|UXN match the device-style
 * pgprot helpers (portal is data, never executed). */
#ifndef pgprot_cached_ns
#define pgprot_cached_ns(prot)						\
	__pgprot_modify((prot), PTE_ATTRINDX_MASK,			\
			PTE_ATTRINDX(MT_NORMAL) | PTE_PXN | PTE_UXN)
#endif

/* Implementation of ARM 64 bit specific routines */

/* TODO: NB, we currently assume that hwsync() and lwsync() imply compiler
 * barriers and that dcb*() won't fall victim to compiler or execution
 * reordering with respect to other code/instructions that manipulate the same
 * cacheline. */
#define hwsync() { asm volatile("dmb st" : : : "memory"); }
#define lwsync() { asm volatile("dmb st" : : : "memory"); }
#define dcbf(p) { asm volatile("dc cvac, %0;" : : "r" (p) : "memory"); }
#define dcbt_ro(p) { asm volatile("prfm pldl1keep, [%0, #0]" : : "r" (p)); }
#define dcbt_rw(p) { asm volatile("prfm pstl1keep, [%0, #0]" : : "r" (p)); }
#define dcbi(p) { asm volatile("dc ivac, %0" : : "r"(p) : "memory"); }
#define dcbz(p) { asm volatile("dc zva, %0" : : "r" (p) : "memory"); }

#define dcbz_64(p) \
	do { \
		dcbz(p);	\
	} while (0)

#define dcbf_64(p) \
	do { \
		dcbf(p); \
	} while (0)
/* Commonly used combo */
#define dcbit_ro(p) \
	do { \
		dcbi(p); \
		dcbt_ro(p); \
	} while (0)

static inline u32 in_be32(volatile void *addr)
{
	return be32_to_cpu(*((volatile u32 *) addr));
}

static inline void out_be32(void *addr, u32 val)
{
	*((u32 *) addr) = cpu_to_be32(val);
}


static inline void set_bits(unsigned long mask, volatile unsigned long *p)
{
	*p |= mask;
}
static inline void clear_bits(unsigned long mask, volatile unsigned long *p)
{
	*p &= ~mask;
}

static inline void flush_dcache_range(unsigned long start, unsigned long stop)
{
	dcache_clean_inval_poc(start, stop - start);
}

#define hard_smp_processor_id() raw_smp_processor_id()



#endif
