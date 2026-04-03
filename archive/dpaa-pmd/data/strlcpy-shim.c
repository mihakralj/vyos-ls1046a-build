/* strlcpy/strlcat shim for glibc < 2.38 (e.g. Debian bookworm).
 * CI runners (Ubuntu 24.04) have glibc 2.38+ where strlcpy/strlcat
 * exist in libc. Static archives (libfdt.a, librte_*.a) compiled on
 * the runner reference these symbols, but the VyOS target (bookworm,
 * glibc 2.36) lacks them. This shim provides BSD-compatible
 * implementations linked into dpdk_plugin.so to resolve the gap.
 */
#include <string.h>
#include <stddef.h>

size_t strlcpy(char *dst, const char *src, size_t size)
{
    size_t srclen = strlen(src);
    if (size > 0) {
        size_t copylen = srclen < size - 1 ? srclen : size - 1;
        memcpy(dst, src, copylen);
        dst[copylen] = '\0';
    }
    return srclen;
}

size_t strlcat(char *dst, const char *src, size_t size)
{
    size_t dstlen = strnlen(dst, size);
    if (dstlen == size)
        return size + strlen(src);
    return dstlen + strlcpy(dst + dstlen, src, size - dstlen);
}