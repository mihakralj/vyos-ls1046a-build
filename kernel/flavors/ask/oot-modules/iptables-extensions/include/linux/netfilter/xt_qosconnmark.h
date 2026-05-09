#ifndef _XT_QOSCONNMARK_H
#define _XT_QOSCONNMARK_H

#include <linux/types.h>

/* Copyright (C) 2002,2004 MARA Systems AB <http://www.marasystems.com>
 * by Henrik Nordstrom <hno@marasystems.com>
 *
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation; either version 2 of the License, or
 * (at your option) any later version.
 */

enum {
	XT_QOSCONNMARK_SET = 0,
	XT_QOSCONNMARK_SAVE_QOSMARK,
	XT_QOSCONNMARK_RESTORE_QOSMARK
};

struct xt_qosconnmark_tginfo1 {
	__u64 mark, ctmask, nfmask;
	__u8 mode;
};

struct xt_qosconnmark_mtinfo1 {
	__u64 mark, mask;
	__u8 invert;
};
#endif /*_XT_QOSCONNMARK_H*/
