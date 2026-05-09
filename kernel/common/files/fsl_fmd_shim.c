// SPDX-License-Identifier: GPL-2.0-only
/*
 * FMD Shim -- Minimal FMan Distribution character device for DPDK fmlib
 *
 * Creates /dev/fm0, /dev/fm0-pcd, and /dev/fm0-port-rxN character devices
 * that the DPDK DPAA PMD's fmlib userspace library expects for runtime
 * FMan KeyGen (RSS) configuration.
 *
 * Skeleton phase: only FM_IOC_GET_API_VERSION ioctl is functional.
 * PCD and PORT ioctls return -ENOSYS (reserved for future KG programming).
 *
 * This module is completely passive at boot -- it ioremaps FMan CCSR for
 * future register access but performs ZERO register writes until a
 * userspace process explicitly issues ioctls.  The kernel fsl_dpaa_mac
 * and fsl_dpa drivers are unaffected (they use MAC registers at offset
 * 0xE0000+, while this module targets KeyGen at offset 0x80000).
 *
 * Copyright (C) 2026 Mono Gateway Project
 */

#include <linux/module.h>
#include <linux/miscdevice.h>
#include <linux/fs.h>
#include <linux/io.h>
#include <linux/of.h>
#include <linux/of_address.h>
#include <linux/slab.h>
#include <linux/uaccess.h>

/* ioctl magic -- must match DPDK fmlib NCSW definitions */
#define FM_IOC_TYPE_BASE  0xe1

/* ioctl number macros */
#define FM_IOC_NUM(n)       (n)
#define FM_PCD_IOC_NUM(n)   ((n) + 20)
#define FM_PORT_IOC_NUM(n)  ((n) + 70)

/* --- FM (control) ioctls --- */
struct fmd_shim_api_version {
	__u8 major;
	__u8 minor;
	__u8 respin;
	__u8 reserved;
};

#define FM_IOC_GET_API_VERSION \
	_IOR(FM_IOC_TYPE_BASE, FM_IOC_NUM(7), struct fmd_shim_api_version)

/* --- PCD ioctls (stubs -- reserved for future KG programming) --- */
#define FM_PCD_IOC_ENABLE \
	_IO(FM_IOC_TYPE_BASE, FM_PCD_IOC_NUM(1))
#define FM_PCD_IOC_DISABLE \
	_IO(FM_IOC_TYPE_BASE, FM_PCD_IOC_NUM(2))
#define FM_PCD_IOC_NET_ENV_SET \
	_IOWR(FM_IOC_TYPE_BASE, FM_PCD_IOC_NUM(20), char[256])
#define FM_PCD_IOC_KG_SET \
	_IOWR(FM_IOC_TYPE_BASE, FM_PCD_IOC_NUM(4), char[512])
#define FM_PCD_IOC_KG_DELETE \
	_IOW(FM_IOC_TYPE_BASE, FM_PCD_IOC_NUM(6), char[8])

/* --- PORT ioctls (stubs -- reserved for future PCD binding) --- */
#define FM_PORT_IOC_SET_PCD \
	_IOW(FM_IOC_TYPE_BASE, FM_PORT_IOC_NUM(3), char[64])
#define FM_PORT_IOC_DELETE_PCD \
	_IO(FM_IOC_TYPE_BASE, FM_PORT_IOC_NUM(4))

/* API version that DPDK fmlib checks: major >= 21, minor >= 1 */
#define FMD_API_MAJOR   21
#define FMD_API_MINOR   1
#define FMD_API_RESPIN  0

/* LS1046A has 5 RX ports: MAC2, MAC5, MAC6, MAC9, MAC10 */
#define FMD_MAX_RX_PORTS  5

/* Per-file-descriptor context (allocated on open, freed on release) */
enum fmd_dev_type {
	FMD_DEV_FM,
	FMD_DEV_PCD,
	FMD_DEV_PORT,
};

struct fmd_shim_fd_ctx {
	enum fmd_dev_type type;
	int port_index;  /* only meaningful for FMD_DEV_PORT */
};

/* Module-global state */
struct fmd_shim_priv {
	void __iomem      *fman_regs;
	phys_addr_t        fman_phys;
	resource_size_t    fman_size;

	struct miscdevice  fm_dev;
	struct miscdevice  pcd_dev;
	struct miscdevice  port_devs[FMD_MAX_RX_PORTS];
	char               port_names[FMD_MAX_RX_PORTS][20];

	/* Future: KG scheme allocation bitmap (32 schemes on LS1046A) */
	unsigned long      scheme_bitmap;
};

static struct fmd_shim_priv *fmd_priv;

/* ===================================================================
 * /dev/fm0 -- FMan control device
 * =================================================================== */

static int fmd_fm_open(struct inode *inode, struct file *filp)
{
	struct fmd_shim_fd_ctx *ctx;

	ctx = kzalloc(sizeof(*ctx), GFP_KERNEL);
	if (!ctx)
		return -ENOMEM;

	ctx->type = FMD_DEV_FM;
	filp->private_data = ctx;
	return 0;
}

static int fmd_common_release(struct inode *inode, struct file *filp)
{
	kfree(filp->private_data);
	filp->private_data = NULL;
	return 0;
}

static long fmd_fm_ioctl(struct file *filp, unsigned int cmd,
			 unsigned long arg)
{
	switch (cmd) {
	case FM_IOC_GET_API_VERSION: {
		struct fmd_shim_api_version ver = {
			.major  = FMD_API_MAJOR,
			.minor  = FMD_API_MINOR,
			.respin = FMD_API_RESPIN,
		};

		if (copy_to_user((void __user *)arg, &ver, sizeof(ver)))
			return -EFAULT;
		return 0;
	}
	default:
		return -ENOTTY;
	}
}

static const struct file_operations fmd_fm_fops = {
	.owner          = THIS_MODULE,
	.open           = fmd_fm_open,
	.release        = fmd_common_release,
	.unlocked_ioctl = fmd_fm_ioctl,
	.compat_ioctl   = compat_ptr_ioctl,
};

/* ===================================================================
 * /dev/fm0-pcd -- PCD engine (KeyGen, parser, classifier)
 * =================================================================== */

static int fmd_pcd_open(struct inode *inode, struct file *filp)
{
	struct fmd_shim_fd_ctx *ctx;

	ctx = kzalloc(sizeof(*ctx), GFP_KERNEL);
	if (!ctx)
		return -ENOMEM;

	ctx->type = FMD_DEV_PCD;
	filp->private_data = ctx;
	return 0;
}

static long fmd_pcd_ioctl(struct file *filp, unsigned int cmd,
			  unsigned long arg)
{
	switch (cmd) {
	case FM_PCD_IOC_ENABLE:
	case FM_PCD_IOC_DISABLE:
	case FM_PCD_IOC_NET_ENV_SET:
	case FM_PCD_IOC_KG_SET:
	case FM_PCD_IOC_KG_DELETE:
		pr_debug("fmd_shim: pcd ioctl 0x%x not yet implemented\n",
			 cmd);
		return -ENOSYS;
	default:
		return -ENOTTY;
	}
}

static const struct file_operations fmd_pcd_fops = {
	.owner          = THIS_MODULE,
	.open           = fmd_pcd_open,
	.release        = fmd_common_release,
	.unlocked_ioctl = fmd_pcd_ioctl,
	.compat_ioctl   = compat_ptr_ioctl,
};

/* ===================================================================
 * /dev/fm0-port-rxN -- Per-port RX devices (PCD binding)
 * =================================================================== */

static int fmd_port_open(struct inode *inode, struct file *filp)
{
	int minor = iminor(inode);
	struct fmd_shim_fd_ctx *ctx;
	int i;

	ctx = kzalloc(sizeof(*ctx), GFP_KERNEL);
	if (!ctx)
		return -ENOMEM;

	ctx->type = FMD_DEV_PORT;
	ctx->port_index = -1;

	/* Identify which port device was opened by matching minor number */
	for (i = 0; i < FMD_MAX_RX_PORTS; i++) {
		if (fmd_priv->port_devs[i].minor == minor) {
			ctx->port_index = i;
			break;
		}
	}

	filp->private_data = ctx;
	return 0;
}

static long fmd_port_ioctl(struct file *filp, unsigned int cmd,
			   unsigned long arg)
{
	struct fmd_shim_fd_ctx *ctx = filp->private_data;

	switch (cmd) {
	case FM_PORT_IOC_SET_PCD:
	case FM_PORT_IOC_DELETE_PCD:
		pr_debug("fmd_shim: port rx%d ioctl 0x%x not yet implemented\n",
			 ctx->port_index, cmd);
		return -ENOSYS;
	default:
		return -ENOTTY;
	}
}

static const struct file_operations fmd_port_fops = {
	.owner          = THIS_MODULE,
	.open           = fmd_port_open,
	.release        = fmd_common_release,
	.unlocked_ioctl = fmd_port_ioctl,
	.compat_ioctl   = compat_ptr_ioctl,
};

/* ===================================================================
 * Module init / exit
 * =================================================================== */

static int __init fmd_shim_init(void)
{
	struct device_node *fman_np;
	struct resource res;
	int ret, i;

	/* Find FMan node in device tree -- absent on non-DPAA1 SoCs */
	fman_np = of_find_compatible_node(NULL, NULL, "fsl,fman");
	if (!fman_np) {
		pr_info("fmd_shim: no fsl,fman node, not a DPAA1 SoC\n");
		return -ENODEV;
	}

	ret = of_address_to_resource(fman_np, 0, &res);
	of_node_put(fman_np);
	if (ret) {
		pr_err("fmd_shim: cannot read FMan CCSR resource: %d\n", ret);
		return ret;
	}

	fmd_priv = kzalloc(sizeof(*fmd_priv), GFP_KERNEL);
	if (!fmd_priv)
		return -ENOMEM;

	fmd_priv->fman_phys = res.start;
	fmd_priv->fman_size = resource_size(&res);

	/*
	 * ioremap the entire FMan CCSR region.  In this skeleton phase
	 * we only verify the mapping succeeds; future phases will write
	 * KeyGen scheme registers (offset 0x80000) for RSS distribution.
	 *
	 * This does NOT conflict with the kernel fsl_dpaa_mac driver
	 * which ioremaps individual MAC register blocks (0xE0000+).
	 * Multiple ioremaps of overlapping physical regions are safe.
	 */
	fmd_priv->fman_regs = ioremap(fmd_priv->fman_phys, fmd_priv->fman_size);
	if (!fmd_priv->fman_regs) {
		pr_err("fmd_shim: ioremap failed at %pa (0x%llx bytes)\n",
		       &fmd_priv->fman_phys,
		       (unsigned long long)fmd_priv->fman_size);
		ret = -ENOMEM;
		goto err_free;
	}

	pr_info("fmd_shim: FMan CCSR %pa (0x%llx bytes) mapped\n",
		&fmd_priv->fman_phys,
		(unsigned long long)fmd_priv->fman_size);

	/* Register /dev/fm0 */
	fmd_priv->fm_dev.minor = MISC_DYNAMIC_MINOR;
	fmd_priv->fm_dev.name  = "fm0";
	fmd_priv->fm_dev.fops  = &fmd_fm_fops;
	ret = misc_register(&fmd_priv->fm_dev);
	if (ret) {
		pr_err("fmd_shim: /dev/fm0 register failed: %d\n", ret);
		goto err_unmap;
	}

	/* Register /dev/fm0-pcd */
	fmd_priv->pcd_dev.minor = MISC_DYNAMIC_MINOR;
	fmd_priv->pcd_dev.name  = "fm0-pcd";
	fmd_priv->pcd_dev.fops  = &fmd_pcd_fops;
	ret = misc_register(&fmd_priv->pcd_dev);
	if (ret) {
		pr_err("fmd_shim: /dev/fm0-pcd register failed: %d\n", ret);
		goto err_unreg_fm;
	}

	/* Register /dev/fm0-port-rx0 .. /dev/fm0-port-rx4 */
	for (i = 0; i < FMD_MAX_RX_PORTS; i++) {
		snprintf(fmd_priv->port_names[i],
			 sizeof(fmd_priv->port_names[i]),
			 "fm0-port-rx%d", i);
		fmd_priv->port_devs[i].minor = MISC_DYNAMIC_MINOR;
		fmd_priv->port_devs[i].name  = fmd_priv->port_names[i];
		fmd_priv->port_devs[i].fops  = &fmd_port_fops;
		ret = misc_register(&fmd_priv->port_devs[i]);
		if (ret) {
			pr_err("fmd_shim: /dev/%s register failed: %d\n",
			       fmd_priv->port_names[i], ret);
			while (--i >= 0)
				misc_deregister(&fmd_priv->port_devs[i]);
			goto err_unreg_pcd;
		}
	}

	pr_info("fmd_shim: ready /dev/fm0 /dev/fm0-pcd "
		"/dev/fm0-port-rx0..rx%d  (API %d.%d.%d)\n",
		FMD_MAX_RX_PORTS - 1,
		FMD_API_MAJOR, FMD_API_MINOR, FMD_API_RESPIN);

	return 0;

err_unreg_pcd:
	misc_deregister(&fmd_priv->pcd_dev);
err_unreg_fm:
	misc_deregister(&fmd_priv->fm_dev);
err_unmap:
	iounmap(fmd_priv->fman_regs);
err_free:
	kfree(fmd_priv);
	fmd_priv = NULL;
	return ret;
}

static void __exit fmd_shim_exit(void)
{
	int i;

	if (!fmd_priv)
		return;

	for (i = FMD_MAX_RX_PORTS - 1; i >= 0; i--)
		misc_deregister(&fmd_priv->port_devs[i]);

	misc_deregister(&fmd_priv->pcd_dev);
	misc_deregister(&fmd_priv->fm_dev);

	iounmap(fmd_priv->fman_regs);
	kfree(fmd_priv);
	fmd_priv = NULL;

	pr_info("fmd_shim: unloaded\n");
}

module_init(fmd_shim_init);
module_exit(fmd_shim_exit);

MODULE_LICENSE("GPL");
MODULE_AUTHOR("Mono Gateway Project");
MODULE_DESCRIPTION("FMD Shim -- minimal FMan chardev for DPDK fmlib RSS");