/*
 * proc_cleanup.c — Remove orphaned CDX procfs entries
 *
 * The CDX module (cdx.ko) creates /proc/fqid_stats/* and /proc/oh* entries
 * but doesn't remove them on module unload. This module cleans them up.
 *
 * Build: make -C /opt/vyos-dev/linux M=$PWD modules
 * Usage: insmod proc_cleanup.ko  (loads, cleans, auto-fails to unload itself)
 */
#include <linux/module.h>
#include <linux/proc_fs.h>

static int __init proc_cleanup_init(void)
{
    /* Remove the entire /proc/fqid_stats tree */
    remove_proc_subtree("fqid_stats", NULL);
    pr_info("proc_cleanup: removed /proc/fqid_stats\n");

    /* Remove /proc/oh1 and /proc/oh2 if they exist */
    remove_proc_entry("oh1", NULL);
    pr_info("proc_cleanup: removed /proc/oh1\n");

    remove_proc_entry("oh2", NULL);
    pr_info("proc_cleanup: removed /proc/oh2\n");

    /* Return error so module doesn't stay loaded */
    return -ECANCELED;
}

module_init(proc_cleanup_init);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Cleanup orphaned CDX procfs entries");