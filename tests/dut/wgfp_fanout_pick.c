/* Host check for wgfp_fanout_pick() (dpaa_wgfp.c), extracted by tests/wgfp_fanout_pick.py. */
#include <assert.h>
#include <stdio.h>
#include "wgfp_fanout_pick_body.h"

static void share(unsigned int n, unsigned int home, unsigned int m, unsigned int batches,
		  unsigned int *cnt)
{
	for (unsigned int i = 0; i < n; i++)
		cnt[i] = 0;
	for (unsigned int b = 0; b < batches; b++) {
		unsigned int i = wgfp_fanout_pick(b, n, home, m);
		assert(i < n);
		cnt[i]++;
	}
}

int main(void)
{
	unsigned int c[8];

	/* m = 0 and m = 1-free defaults: exactly the old (rr / batch) % n */
	for (unsigned int b = 0; b < 1000; b++) {
		assert(wgfp_fanout_pick(b, 4, 0, 0) == b % 4);
		assert(wgfp_fanout_pick(b, 4, 7, 5) == b % 4);	/* home not a worker */
		assert(wgfp_fanout_pick(b, 1, 0, 5) == 0);
	}
	/* m = n: still an even split, home every n-th batch */
	share(4, 0, 4, 4000, c);
	assert(c[0] == 1000 && c[1] == 1000 && c[2] == 1000 && c[3] == 1000);
	/* m = 5 on 4 workers: home 1/5, the others share the rest evenly */
	share(4, 0, 5, 15000, c);
	assert(c[0] == 3000 && c[1] == 4000 && c[2] == 4000 && c[3] == 4000);
	/* a home other than worker 0 */
	share(4, 2, 6, 18000, c);
	assert(c[2] == 3000 && c[0] == 5000 && c[1] == 5000 && c[3] == 5000);
	/* consecutive non-home batches rotate over all other workers */
	for (unsigned int b = 1; b < 200; b++) {
		unsigned int i = wgfp_fanout_pick(b, 4, 0, 5);
		if (b % 5)
			assert(i != 0);
	}
	puts("wgfp_fanout_pick ok");
	return 0;
}
