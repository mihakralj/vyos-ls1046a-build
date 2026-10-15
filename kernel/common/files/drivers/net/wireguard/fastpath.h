/* SPDX-License-Identifier: GPL-2.0 */
#ifndef _WG_FASTPATH_H
#define _WG_FASTPATH_H

#include <linux/kconfig.h>

#if IS_ENABLED(CONFIG_FSL_DPAA_ETH)
void wg_fastpath_uninit(void);
#else
static inline void wg_fastpath_uninit(void) { }
#endif

#endif /* _WG_FASTPATH_H */
