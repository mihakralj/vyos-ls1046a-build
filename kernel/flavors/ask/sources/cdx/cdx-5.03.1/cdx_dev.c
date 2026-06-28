/*
 *  Copyright 2014-2016 Freescale Semiconductor, Inc.
 *  Copyright 2017,2021 NXP
 *
 * SPDX-License-Identifier:    GPL-2.0+
 * The GPL-2.0+ license for this file can be found in the COPYING.GPL file
 * included with this distribution or at http://www.gnu.org/licenses/gpl-2.0.html
 *
 */
 
/**     
 * @file                cdx_dev.c     
 * @description         cdx driver open,r,w,ioctl call implemnetations 
 */

#include <linux/device.h>
#include "linux/ioctl.h"
#include <linux/compat.h>
#include <linux/kernel.h>
#include <linux/uaccess.h>
#include <linux/slab.h>
#include <linux/fdtable.h>
#include <linux/capability.h>

#include "portdefs.h"
#include "misc.h"
#include "cdx.h"
#include "cdx_ioctl.h"
#include "lnxwrp_fm.h"

static int cdx_ctrl_cdev_major = -1;
static struct class *cdx_ctrl_class;
static struct device *cdx_ctrl_dev;
static atomic_t cdx_ctrl_open_count;

int cdx_ctrl_open(struct inode *inode, struct file *filp);
int cdx_ctrl_release(struct inode *inode, struct file *filp);
long cdx_ctrl_ioctl(struct file *filp, unsigned int cmd,
                unsigned long args);
#ifdef CONFIG_COMPAT
long cdx_ctrl_compat_ioctl(struct file *filp, unsigned int cmd,
                unsigned long args);
#endif


/* cdx device file ops */
static const struct file_operations cdx_dev_fops = {
        .owner                  = THIS_MODULE,
        .open                   = cdx_ctrl_open,
        .unlocked_ioctl         = cdx_ctrl_ioctl,
#ifdef CONFIG_COMPAT
        .compat_ioctl           = cdx_ctrl_compat_ioctl,
#endif /* CONFIG_COMPAT */
        .release                = cdx_ctrl_release
};

int cdx_ctrl_open(struct inode *inode, struct file *filp)
{
	//allow only one open instance
	if (atomic_cmpxchg(&cdx_ctrl_open_count, 1, 0) != 1)
		return -EBUSY;
	return 0;
}

int cdx_ctrl_release(struct inode *inode, struct file *filp)
{
	//release open instance
	//TBD - recover resources here
	atomic_set(&cdx_ctrl_open_count, 1);
	return 0;
}

#ifdef DPAA_DEBUG_ENABLE
extern void *get_muram_data(uint32_t *size);
static long cdx_get_muram_data(unsigned long args)
{
	long retval;
	uint8_t *pdata;
	uint32_t size;
	struct muram_data data_in;

	if(copy_from_user(&data_in, (void *)args,
				sizeof(struct muram_data))) {
		DPA_ERROR("%s::unable to copy struct get_muram_data\n", __FUNCTION__);
		return (-EIO);
	}
	pdata = get_muram_data(&size);
	if (!pdata) {
		DPA_ERROR("%s::get_muram_data failed\n", __FUNCTION__);
		return (-EIO);
	}
	if (size > data_in.size) {
		DPA_ERROR("%s::muram data size is %d,does not fit\n", __FUNCTION__, size);
		retval = -EINVAL;
		goto func_ret;
	}
	data_in.size = size;
	if(copy_to_user(data_in.buff, pdata, size)) {
		DPA_ERROR("%s::unable to copy muram data\n", __FUNCTION__);
		retval = -EIO;
		goto func_ret;
	}
	if (copy_to_user((void *)args, &data_in, sizeof(struct muram_data))) {
		DPA_ERROR("%s::unable to copy result\n", __FUNCTION__);
		retval = -EIO;
		goto func_ret;
	}
	retval = 0;
func_ret:
	kfree(pdata);
	return retval;
}
#endif

static int __maybe_unused disp_muram(void)
{
#ifdef DPAA_DEBUG_ENABLE
	int ii;
	uint8_t *pdata;
	uint32_t size;

	printk("%s::\n", __FUNCTION__);
	pdata = get_muram_data(&size);
	if (!pdata) {
		DPA_ERROR("%s::get_muram_data failed\n", __FUNCTION__);
		return (-EIO);
	}

	printk("%s::muram data size %d\n", __FUNCTION__, size);
	for (ii = 0; ii < size; ii++) {
		if (!(ii % 16))
			printk("\n%04x:%02x ", ii, *pdata);
		else
			printk("%02x ", *pdata);
		pdata++;
	}
#else
	printk("%s::\n", __FUNCTION__);
#endif
	return 0;
}

struct cdx_ioctl_spec {
	unsigned int cmd;
	long (*handle)(unsigned long args);
};

static long cdx_ioc_set_dpa_params_wrap(unsigned long args)
{
	return cdx_ioc_set_dpa_params(args);
}

static const struct cdx_ioctl_spec cdx_ioctl_table[] = {
	{ CDX_CTRL_DPA_SET_PARAMS, cdx_ioc_set_dpa_params_wrap },
#ifdef DPAA_DEBUG_ENABLE
	{ CDX_CTRL_DPA_GET_MURAM_DATA, cdx_get_muram_data },
#endif
};

long cdx_ctrl_ioctl(struct file *filp, unsigned int cmd,
                unsigned long args) 
{
	size_t i;

	if (!capable(CAP_NET_ADMIN))
		return -EPERM;

	for (i = 0; i < ARRAY_SIZE(cdx_ioctl_table); i++) {
		if (cdx_ioctl_table[i].cmd == cmd)
			return cdx_ioctl_table[i].handle(args);
	}

	DPA_ERROR("%s::unsupported ioctl cmd %x\n", __func__, cmd);
	return -ENOTTY;
}

#ifdef CONFIG_COMPAT
long cdx_ctrl_compat_ioctl(struct file *filp, unsigned int cmd,
                unsigned long args)
{
	DPA_INFO("%s::\n", __FUNCTION__);
	if (!capable(CAP_NET_ADMIN))
		return -EPERM;

	return -ENOTTY;
}
#endif

static void cdx_driver_deinit(void)
{
	device_destroy(cdx_ctrl_class, MKDEV(cdx_ctrl_cdev_major, 0));
	class_destroy(cdx_ctrl_class);
	unregister_chrdev(cdx_ctrl_cdev_major, CDX_CTRL_CLS_CDEVNAME);
	return;
}

int cdx_driver_init(void)
{
	/* Cannot initialize the wrapper twice */
	if (cdx_ctrl_cdev_major >= 0)
		return 0;

	//initialize driver usage count
	atomic_set(&cdx_ctrl_open_count, 1);
	cdx_ctrl_cdev_major = register_chrdev(0,CDX_CTRL_CDEVNAME,&cdx_dev_fops);
	if (cdx_ctrl_cdev_major < 0) {
		DPA_ERROR("%s::Could not register dev %s\n", 
				__FUNCTION__, CDX_CTRL_CDEVNAME);
		return -1;
	}

	cdx_ctrl_class = class_create(CDX_CTRL_CLS_CDEVNAME);
	if (IS_ERR(cdx_ctrl_class)) {
		DPA_ERROR("%s::Failed to create %s class device\n",
				__FUNCTION__, CDX_CTRL_CLS_CDEVNAME);
		unregister_chrdev(cdx_ctrl_cdev_major, CDX_CTRL_CLS_CDEVNAME);
		cdx_ctrl_class = NULL;
		return -1;
	}

	cdx_ctrl_dev = device_create( cdx_ctrl_class,NULL,
			MKDEV(cdx_ctrl_cdev_major, 0),NULL,CDX_CTRL_CLS_CDEVNAME);
	if (IS_ERR(cdx_ctrl_dev)) {
		DPA_ERROR("%s::Failed to create %s device\n",
				__FUNCTION__, CDX_CTRL_CLS_CDEVNAME);
		class_destroy(cdx_ctrl_class);
		unregister_chrdev(cdx_ctrl_cdev_major, CDX_CTRL_CLS_CDEVNAME);
		cdx_ctrl_cdev_major = -1;
		return -1;
	}
	register_cdx_deinit_func(cdx_driver_deinit);
	return 0;
}

