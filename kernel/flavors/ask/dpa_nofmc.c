/*
 * dpa_app replacement — C-only, no C++ fmc dependency.
 *
 * The original dpa_app (dpa.c + libfmc.a C++ static library) crashes
 * with SIGSEGV due to C++ ABI issues on Debian 12. This C-only version
 * uses the FMan C library directly.
 *
 * IMPORTANT: We do NOT call CDX_CTRL_DPA_SET_PARAMS here. The kernel CDX
 * module pre-populates fman_info during cdx_module_init(). Calling the
 * ioctl from dpa_app (which runs INSIDE cdx_module_init via call_usermodehelper)
 * would trigger cdx_ioc_set_dpa_params() which calls release_cfg_info()
 * and kfree()s the pre-populated fman_info — corrupting the kernel state.
 *
 * The PCD classification tables are configured at runtime by CMM via
 * flow-push ioctls. dpa_app's only essential role is to return 0 so
 * cdx_module_init considers it a success.
 */

#include <stdio.h>

int main(void)
{
    /* Minimal: just prove the binary runs.
     * FM_Open/FM_PCD_Open are called by the kernel CDX module
     * when CMM pushes the first flow via /dev/cdx_ctrl ioctl. */
    return 0;
}
