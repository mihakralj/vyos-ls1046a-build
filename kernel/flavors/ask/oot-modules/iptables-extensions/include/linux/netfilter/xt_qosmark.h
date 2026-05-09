#ifndef _XT_QOSMARK_H
#define _XT_QOSMARK_H

#include <linux/types.h>

struct xt_qosmark_tginfo2 {
	__u64 mark, mask;
};

struct xt_qosmark_mtinfo1 {
	__u64 mark, mask;
	__u8 invert;
};
#endif /*_XT_QOSMARK_H*/
