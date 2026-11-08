/* Host regression for ENQ descriptor writes during FE context setup. */
#include <arpa/inet.h>
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef uint8_t u8;
typedef uint16_t u16;
typedef uint32_t u32;

#define __iomem
#define __maybe_unused __attribute__((unused))
#define FMAN_PCD_FE_MAX_SIZE 28
#define memcpy_toio(dst, src, size) memcpy((dst), (src), (size))
#define list_first_entry_or_null(list, type, member) ((list)->first)

struct muram_info {
	unsigned char bytes[1024] __attribute__((aligned(4)));
};

struct fman_pcd_fe_obj {
	unsigned long muram_off;
};

struct fman_pcd {
	struct muram_info *fman;
	struct {
		struct fman_pcd_fe_obj *first;
	} fe_enq;
	unsigned long fe_mux_off;
	unsigned long fe_transition_off;
	unsigned long fe_exit_off;
};

static uintptr_t watched_start, watched_end;
static unsigned int descriptor_writes;

static struct muram_info *fman_get_muram(struct muram_info *muram)
{
	return muram;
}

static void *fman_muram_offset_to_vbase(struct muram_info *muram,
					unsigned long offset)
{
	assert(offset + FMAN_PCD_FE_MAX_SIZE <= sizeof(muram->bytes));
	return muram->bytes + offset;
}

static u32 ioread32be(const void *addr)
{
	u32 word;

	memcpy(&word, addr, sizeof(word));
	return ntohl(word);
}

static void iowrite32be(u32 value, void *addr)
{
	u32 word = htonl(value);
	uintptr_t address = (uintptr_t)addr;

	if (address >= watched_start && address < watched_end)
		descriptor_writes++;
	memcpy(addr, &word, sizeof(word));
}

#include "fe_context_body.h"

int main(void)
{
	struct muram_info muram = {};
	struct fman_pcd_fe_obj enq = { .muram_off = 0x40 };
	struct fman_pcd pcd = {
		.fman = &muram,
		.fe_enq = { .first = &enq },
		.fe_mux_off = 0x100,
		.fe_transition_off = 0x140,
		.fe_exit_off = 0x180,
	};
	struct fman_pcd_fe_params params = {
		.type = FMAN_FE_TYPE_ENQ,
		.ws_offset = FMAN_FE_ENQ_CTX_OFF,
		.nia = 0x00500002,
		.next_fe_off = pcd.fe_exit_off,
	};
	struct fman_pcd_fe_context_params context_params = {
		.type = FMAN_FE_TYPE_ENQ,
		.u.enq = { .fqid = 0x1234, .rspid = 0x5a, .ppid = 0xbeef },
	};
	unsigned char original[FMAN_FE_ENQ_SIZE];
	void *descriptor = muram.bytes + enq.muram_off;
	void *context = muram.bytes + 0x200;

	fman_pcd_fe_build(&muram, enq.muram_off, &params);
	memcpy(original, descriptor, sizeof(original));
	assert(ioread32be((u32 *)descriptor + 1) == params.nia);
	assert(ioread32be((u32 *)descriptor + 3) == pcd.fe_exit_off);
	watched_start = (uintptr_t)descriptor;
	watched_end = watched_start + sizeof(original);

	for (unsigned int attempt = 0; attempt < 2; attempt++) {
		fman_pcd_fe_build_contexts(&pcd);
		if (descriptor_writes || memcmp(original, descriptor, sizeof(original))) {
			fprintf(stderr, "FAIL: context setup %u wrote ENQ descriptor "
				"(%u writes, next FE 0x%x)\n",
				attempt + 1, descriptor_writes,
				ioread32be((u32 *)descriptor + 3));
			return EXIT_FAILURE;
		}
	}

	assert(!fman_pcd_fe_context_build(context, FMAN_FE_ENQ_CTX_OFF,
					  &context_params));
	assert(ioread32be((u32 *)context + 2) == 0x5a001234);
	assert(ioread32be((u32 *)context + 3) == 0xbeef0000);
	assert(!descriptor_writes);
	puts("FE contexts: repeated setup preserves ENQ descriptor; separate context still builds");
	return EXIT_SUCCESS;
}
