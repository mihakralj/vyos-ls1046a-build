/* test-portal-mmap.c - Minimal test for USDPAA portal ioctl + mmap */
#include <stdio.h>
#include <stdlib.h>
#include <fcntl.h>
#include <unistd.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <stdint.h>
#include <string.h>
#include <errno.h>

#define USDPAA_IOCTL_MAGIC 'u'

enum portal_type { PORTAL_QMAN, PORTAL_BMAN };

struct portal_map { void *cinh; void *cena; };

struct ioctl_portal_map {
    enum portal_type type;
    uint32_t index;
    struct portal_map addr;
    uint32_t channel;
    uint32_t pools;
};

#define IOCTL_PORTAL_MAP _IOWR(USDPAA_IOCTL_MAGIC, 0x07, struct ioctl_portal_map)

int main(void)
{
    int fd;
    struct ioctl_portal_map pm;
    void *va;
    int ret;

    printf("sizeof(struct ioctl_portal_map) = %zu\n", sizeof(pm));
    printf("IOCTL_PORTAL_MAP = 0x%lx\n", (unsigned long)IOCTL_PORTAL_MAP);

    fd = open("/dev/fsl-usdpaa", O_RDWR);
    if (fd < 0) {
        perror("open /dev/fsl-usdpaa");
        return 1;
    }
    printf("Opened /dev/fsl-usdpaa fd=%d\n", fd);

    /* Request BMan portal */
    memset(&pm, 0, sizeof(pm));
    pm.type = PORTAL_BMAN;

    ret = ioctl(fd, IOCTL_PORTAL_MAP, &pm);
    printf("ioctl ret=%d errno=%d (%s)\n", ret, errno, strerror(errno));
    if (ret < 0) {
        perror("ioctl PORTAL_MAP");
        close(fd);
        return 1;
    }

    printf("PORTAL_MAP returned:\n");
    printf("  type=%d index=%u channel=%u pools=0x%x\n",
           pm.type, pm.index, pm.channel, pm.pools);
    printf("  addr.cena=%p (phys CE)\n", pm.addr.cena);
    printf("  addr.cinh=%p (phys CI)\n", pm.addr.cinh);

    uint64_t phys_ce = (uint64_t)(uintptr_t)pm.addr.cena;
    uint64_t phys_ci = (uint64_t)(uintptr_t)pm.addr.cinh;
    printf("  phys_ce=0x%lx phys_ci=0x%lx\n", phys_ce, phys_ci);

    /* Now try mmap CE via the same fd */
    printf("\nAttempting mmap CE: fd=%d size=0x10000 offset=0x%lx\n", fd, phys_ce);
    va = mmap(NULL, 0x10000, PROT_READ | PROT_WRITE, MAP_SHARED,
              fd, (off_t)phys_ce);
    if (va == MAP_FAILED) {
        printf("mmap CE FAILED: errno=%d (%s)\n", errno, strerror(errno));
    } else {
        printf("mmap CE SUCCESS: va=%p\n", va);
        munmap(va, 0x10000);
    }

    /* Try mmap CI */
    printf("\nAttempting mmap CI: fd=%d size=0x4000 offset=0x%lx\n", fd, phys_ci);
    va = mmap(NULL, 0x4000, PROT_READ | PROT_WRITE, MAP_SHARED,
              fd, (off_t)phys_ci);
    if (va == MAP_FAILED) {
        printf("mmap CI FAILED: errno=%d (%s)\n", errno, strerror(errno));
    } else {
        printf("mmap CI SUCCESS: va=%p\n", va);
        munmap(va, 0x4000);
    }

    close(fd);
    return 0;
}
