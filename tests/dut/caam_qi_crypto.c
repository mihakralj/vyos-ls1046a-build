// SPDX-License-Identifier: GPL-2.0
/* Host model for the extracted CAAM crypto callers and wait helper. */
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>

struct device { struct device *parent; };
struct caam_drv_req { int unused; };
struct caam_drv_private { int era; };
struct crypto_skcipher;
struct caam_ctx {
	struct device *jrdev, *qidev;
	struct crypto_skcipher *fallback;
	bool xts_key_fallback;
};
struct crypto_aead { struct caam_ctx *ctx; };
struct crypto_skcipher { struct caam_ctx *ctx; };
struct aead_request { struct crypto_aead *tfm; };
struct skcipher_request {
	struct crypto_skcipher *tfm;
	unsigned int cryptlen;
	void *src, *dst, *iv;
	struct {
		unsigned int flags;
		void (*complete)(void *);
		void *data;
	} base;
};
struct caam_skcipher_req_ctx { struct skcipher_request fallback_req; };
struct aead_edesc { struct caam_drv_req drv_req; };
struct skcipher_edesc { struct caam_drv_req drv_req; };
struct completion { int waits, reinits; };
struct crypto_wait { struct completion completion; int err; };

#define unlikely(x) (x)
#define IS_ERR(ptr) false
#define PTR_ERR(ptr) (-ENOMEM)

static struct device device, jrdev = { .parent = &device };
static struct caam_ctx ctx = { .jrdev = &jrdev, .qidev = &device };
static struct crypto_aead aead = { .ctx = &ctx };
static struct crypto_skcipher skcipher = { .ctx = &ctx };
static struct aead_request aead_req = { .tfm = &aead };
static struct skcipher_request skcipher_req = { .tfm = &skcipher, .cryptlen = 16 };
static struct caam_drv_private priv;
static struct caam_skcipher_req_ctx fallback_ctx;
static struct aead_edesc aead_desc;
static struct skcipher_edesc skcipher_desc;
static bool caam_congested;
static int enqueue_result, enqueue_calls, aead_unmaps, skcipher_unmaps, frees;

static struct crypto_aead *crypto_aead_reqtfm(struct aead_request *req)
{
	return req->tfm;
}

static struct caam_ctx *crypto_aead_ctx_dma(struct crypto_aead *tfm)
{
	return tfm->ctx;
}

static struct crypto_skcipher *crypto_skcipher_reqtfm(struct skcipher_request *req)
{
	return req->tfm;
}

static struct caam_ctx *crypto_skcipher_ctx_dma(struct crypto_skcipher *tfm)
{
	return tfm->ctx;
}

static void *dev_get_drvdata(struct device *dev)
{
	assert(dev == &device);
	return &priv;
}

static bool xts_skcipher_ivsize(struct skcipher_request *req) { return false; }

static struct caam_skcipher_req_ctx *skcipher_request_ctx(struct skcipher_request *req)
{
	return &fallback_ctx;
}

static void skcipher_request_set_tfm(struct skcipher_request *req,
				     struct crypto_skcipher *tfm) {}
static void skcipher_request_set_callback(struct skcipher_request *req,
					  unsigned int flags,
					  void (*complete)(void *), void *data) {}
static void skcipher_request_set_crypt(struct skcipher_request *req,
				       void *src, void *dst,
				       unsigned int len, void *iv) {}
static int crypto_skcipher_encrypt(struct skcipher_request *req) { return -EIO; }
static int crypto_skcipher_decrypt(struct skcipher_request *req) { return -EIO; }

static struct aead_edesc *aead_edesc_alloc(struct aead_request *req, bool encrypt)
{
	return &aead_desc;
}

static struct skcipher_edesc *skcipher_edesc_alloc(struct skcipher_request *req,
						   bool encrypt)
{
	return &skcipher_desc;
}

static int caam_qi_enqueue(struct device *dev, struct caam_drv_req *req)
{
	assert(dev == &device);
	enqueue_calls++;
	return enqueue_result;
}

static void aead_unmap(struct device *dev, struct aead_edesc *desc,
		       struct aead_request *req)
{
	assert(dev == &device && desc == &aead_desc && req == &aead_req);
	aead_unmaps++;
}

static void skcipher_unmap(struct device *dev, struct skcipher_edesc *desc,
			   struct skcipher_request *req)
{
	assert(dev == &device && desc == &skcipher_desc && req == &skcipher_req);
	skcipher_unmaps++;
}

static void qi_cache_free(void *desc)
{
	assert(desc == &aead_desc || desc == &skcipher_desc);
	frees++;
}

static void wait_for_completion(struct completion *completion)
{
	completion->waits++;
}

static void reinit_completion(struct completion *completion)
{
	completion->reinits++;
}

#include "caam_qi_crypto_body.h"

static void reset(int result)
{
	enqueue_result = result;
	enqueue_calls = aead_unmaps = skcipher_unmaps = frees = 0;
}

static void check_request(bool cipher, int result)
{
	struct crypto_wait wait = { 0 };
	int rc;

	reset(result);
	rc = cipher ? skcipher_crypt(&skcipher_req, true) :
		      aead_crypt(&aead_req, true);
	assert(enqueue_calls == 1);
	if (result) {
		assert(rc == result && frees == 1);
		assert(aead_unmaps == !cipher && skcipher_unmaps == cipher);
	} else {
		assert(rc == -EINPROGRESS && !frees && !aead_unmaps &&
		       !skcipher_unmaps);
	}

	rc = crypto_wait_req(rc, &wait);
	if (result == -EAGAIN) {
		assert(rc == -EAGAIN && !wait.completion.waits &&
		       !wait.completion.reinits);
	} else {
		assert(!rc && wait.completion.waits == 1 &&
		       wait.completion.reinits == 1);
	}
}

int main(void)
{
	check_request(false, -EAGAIN);
	check_request(true, -EAGAIN);
	check_request(false, 0);
	check_request(true, 0);
	check_request(false, -EBUSY);
	check_request(true, -EBUSY);
	puts("CAAM crypto callers: synchronous rejection and async controls pass");
	return 0;
}
