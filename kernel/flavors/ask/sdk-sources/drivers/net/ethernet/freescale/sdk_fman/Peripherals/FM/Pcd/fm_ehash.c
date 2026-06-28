// SPDX-License-Identifier: GPL-2.0+
/*
 * Copyright 2018 NXP
 */

/**
 * @file                fm_ehash.c
 * @description         DPAA enhanced external hash functions
 */

#define __ERR_MODULE__  MODULE_FM_PCD

#include <linux/module.h>
#include <linux/kernel.h>
#include <linux/slab.h>
//#include <linux/fsl_dpa_offload.h>
//#include <linux/fsl_dpa_classifier.h>
#include "fm_common.h"
#include "fm_muram_ext.h"
#include "fm_ehash.h"
#include "fm_pcd.h"
#include "fm_cc.h"
#include "endian_ext.h"

//#define FM_EHASH_DEBUG 1

#ifdef FM_EHASH_DEBUG 
#define FM_EHASH_PRINT printk
#else
#define FM_EHASH_PRINT(fmt, ...)
#endif // CDX_DPA_DEBUG

#ifndef EXCLUDE_FMAN_IPR_OFFLOAD
#define REASSM_DEBUG_SIZE       128
struct en_exthash_info *ipv4_reassly_tbl_info;
struct en_exthash_info *ipv6_reassly_tbl_info;
struct ipr_context_info *IprContextMem;

extern void disp_sch_info(void *);
extern void *FmMurambaseAddr;
en_exthash_global_mem *en_global_muram_mem = NULL;

extern void get_indexed_hash_bucket(uint8_t key_size,  uint8_t *key_ptr,
	        uint8_t crc_shift, uint16_t mask, uint16_t *bucket_index);

void display_reassem_stats(uint32_t type);
static void display_reassem_params(uint32_t type);
#endif

#ifdef USE_ENHANCED_EHASH
static inline void copy_ipaddr(uint32_t *src, uint8_t *dest, uint32_t size)
{	
	uint32_t ii;
	uint32_t val;

	for (ii = 0; ii < size; ii++) {
		val = cpu_to_be32(*src);
		memcpy(dest, &val, 4);
		dest += sizeof(uint32_t);
	}
}



#if 0
static int fill_ehash_key_info(PCtEntry entry, struct en_ehash_entry *entry)
{
	unsigned char *ptr;
	
	ptr = &entry->key[0];
	switch (entry->proto) {
		case IPPROTOCOL_TCP: 
		case IPPROTOCOL_UDP:
			if (IS_IPV6_FLOW(entry))
			{
				copy_ipaddr(&entry->Saddr_v6[i], ptr, 4);
				ptr += 16;
				copy_ipaddr(&entry->Daddr_v6[i], ptr, 4);
				ptr += 16;
			} else {
				copy_ipaddr(&entry->Saddr_v4, ptr, 1);
				ptr += 4;
				copy_ipaddr(&entry->Daddr_v4, ptr, 1);
				ptr += 4;
			}
			*ptr++ = entry->proto;
			*ptr++ = (entry->Sport >> 8);
			*ptr++ = (entry->Sport & 0xff);
			*ptr++ = (entry->Dport >> 8);
			*ptr++ = (entry->Dport & 0xff);
			break;
		default:
			DPA_ERROR("%s::protocol %d not supported\n",
					__FUNCTION__, entry->proto);
			return FAILURE;

	}
#if 1//
	{
		uint32_t size;

		size = (uint32_t)(ptr - &entry->key[0]);
		printk("keysize %d\n", size);
		display_buf(&entry->key[0]), size);
#endif
	}
	return SUCCESS;
}
#endif

static inline struct en_exthash_tbl_entry *find_entry_in_bucket(struct en_exthash_tbl_entry *entry, 
		uint8_t *key, uint32_t size)
{
#ifdef NO_CUMULATIVE_ENTRY
	while(1) {
		if (!entry)
			break;
		if (memcmp(key, &entry->hashentry.key[0], size) == 0) 
			return entry; 
		entry = entry->next;
	}
#else
	struct en_cumulative_tbl_entry *cumulative_tbl_node = (struct en_cumulative_tbl_entry *)entry;
	struct en_cumulative_entry *cumulative_node;
	int ii;
	if (!entry)
		return NULL;
	cumulative_node = &cumulative_tbl_node->cumulative_entry;
	if (cumulative_node->flags & EN_CUMULATIVE_NODE)
	{
		while (cumulative_node)
		{
			for(ii=0; ii<cumulative_node->num_key_entries; ii++)
				if (memcmp(key, &cumulative_node->data[ii*cumulative_node->key_size], size) == 0)
					return entry;
			if (cumulative_tbl_node->next_entry)
			{
				cumulative_tbl_node = cumulative_tbl_node->next_entry;
				cumulative_node = &cumulative_tbl_node->cumulative_entry;
			}
			else
				cumulative_node = NULL;
		}
	}
	else if(memcmp(key, &entry->hashentry.key[0], size) == 0) 
		return entry; 
#endif // NO_CUMULATIVE_ENTRY
	return NULL;
}

void *ExternalHashTableAllocEntry(t_Handle h_HashTbl) 
{
	void *entry;
	struct en_exthash_info *info;
	
	entry = NULL;
	info = (struct en_exthash_info *)h_HashTbl;
	if (info) {
		//allocate new entry
		entry = XX_MallocSmart(sizeof(struct en_exthash_tbl_entry),
                        0 /*info->dataMemId not used */, EN_EHASH_ENTRY_ALIGN);
		if (entry) 
			memset(entry, 0, sizeof(struct en_exthash_tbl_entry));
		else {
        		REPORT_ERROR(MAJOR, E_NO_MEMORY,
                		     ("en_ext_hash_entry"));
		}
	}
	return entry;
}
EXPORT_SYMBOL(ExternalHashTableAllocEntry); 

void ExternalHashTableEntryFree(void *entry)
{
	XX_FreeSmart(entry);
}
EXPORT_SYMBOL(ExternalHashTableEntryFree); 

void *ExternalHashTableAllocCumulativeEntry(t_Handle h_HashTbl)
{
	void *entry;
	struct en_exthash_info *info;

	info = (struct en_exthash_info *) h_HashTbl;

	entry = XX_MallocSmart(sizeof(struct en_cumulative_tbl_entry), 0 /*info->dataMemId not used */, EN_EHASH_ENTRY_ALIGN);
	if (entry)
	        memset(entry, 0, sizeof(struct en_cumulative_tbl_entry));
	else
	{
	        REPORT_ERROR(MAJOR, E_NO_MEMORY, ("en_cumulative_entry"));
	}
	return entry;
}
void ExternalHashTableCumulativeEntryFree(void *entry)
{
	XX_FreeSmart(entry);
}



#define MAX_HIST_SIZE	15
void EhashTableWalk(void *h_HashTbl)
{
	uint32_t ii;
	struct en_exthash_info *info;
	struct en_exthash_tbl_entry *entry;
	struct en_exthash_bucket *bucket;
	uint32_t num_entries;
	uint32_t bucket_entries;
	uint32_t max_collisions;
	uint32_t min_collisions;
	uint32_t histo[MAX_HIST_SIZE + 2];
	

	num_entries = 0;
	max_collisions = 0;
	min_collisions = 0xffffffff;
	memset(&histo[0], 0, (sizeof(uint32_t) * (MAX_HIST_SIZE + 2)));
	info = (struct en_exthash_info *)h_HashTbl;
	bucket = (struct en_exthash_bucket *)info->table_base;
	printk("%s::tbl %p, num buckets %d base %p\n",
		__FUNCTION__, info, (info->hashmask + 1), bucket);
	for (ii = 0; ii <= info->hashmask; ii++) {
		if (bucket->h) {
			bucket_entries = 0;
			entry = XX_PhysToVirt(SwapUint64(bucket->h));
			while(entry) {
				bucket_entries++;
				entry = entry->next;
			}
			num_entries += bucket_entries;
			if (bucket_entries > max_collisions)
				max_collisions = bucket_entries;
			if (bucket_entries < min_collisions)
				min_collisions = bucket_entries;
			if (max_collisions > MAX_HIST_SIZE)
				histo[MAX_HIST_SIZE + 1]++;
			else 
				histo[max_collisions]++;
		} else {
			histo[0]++;
		}	
		bucket++;
	}
	printk("%s::entries %d, max colls %d, min colls %d\n",
			__FUNCTION__, num_entries, max_collisions, 
			min_collisions);
	printk("num collisions\t	num_buckets\n");	
	for (ii = 0; ii < MAX_HIST_SIZE; ii++) {
		printk("%d\t%d\n", ii, histo[ii]);
	}
	printk(">15\t%d\n", histo[ii]);
}
EXPORT_SYMBOL(EhashTableWalk); 

int ExternalHashTableEntryGetStatsAndTS(void *tbl_entry,
				struct en_tbl_entry_stats *stats)
{
	struct en_exthash_tbl_entry *hash_node;
	struct en_ehash_entry *entry;
//	uint32_t val;
	uint16_t flags;

	hash_node = (struct en_exthash_tbl_entry *)tbl_entry;
	if(!hash_node)
		return -1;
	entry = &hash_node->hashentry;
	flags = cpu_to_be16(entry->flags);
	stats->flags = 0;
        if (GET_TIMESTAMP_ENABLE(flags)) {
		stats->flags |= TIMESTAMP_VALID;
		stats->timestamp = cpu_to_be32(entry->timestamp);
#ifdef FM_EHASH_DEBUG 
                printk("external  timestamp %08x\n",
                	 stats->timestamp);
#endif // FM_EHASH_DEBUG
        }
	if (GET_STATS_ENABLE(flags))
	{
		stats->flags |= STATS_VALID;
		stats->pkts = be64_to_cpu(entry->packet_count);
		stats->bytes = be64_to_cpu(entry->packet_bytes);
#ifdef  FM_EHASH_DEBUG 
		printk("%s(%d) stats pkts %lld , bytes %lld \n",
			__FUNCTION__, __LINE__, stats->pkts, stats->bytes);
#endif // FM_EHASH_DEBUG 
	}
#ifdef FM_EHASH_DEBUG 
	printk("%s::stats flags %x\n", __FUNCTION__, stats->flags);
#endif
	return 0;
}
EXPORT_SYMBOL(ExternalHashTableEntryGetStatsAndTS);

int ExternalHashTableAddKey(void *h_HashTbl, uint8_t keySize,
                                      void *tbl_entry)
{
	struct en_exthash_info *info;
	struct en_exthash_tbl_entry *first_entry;
	struct en_exthash_tbl_entry *new_entry;
	uint16_t index;
	struct en_exthash_bucket *bucket;
	t_Handle *h_Spinlock;
	uint32_t intFlags;
	int retval;
	uint64_t phyaddr;
#ifndef NO_CUMULATIVE_ENTRY
	uint8_t key_align, flags;
//	uint32_t ii;
	struct en_cumulative_entry *cumulative_entry, *tmp;
	struct en_cumulative_tbl_entry *tmp_tbl_entry, *cumulative_tbl_entry;
	uint64_t bucket_phyaddr;
#endif // NO_CUMULATIVE_ENTRY

	SANITY_CHECK_RETURN_ERROR(h_HashTbl, E_INVALID_HANDLE);
    	SANITY_CHECK_RETURN_ERROR(tbl_entry, E_NULL_POINTER);

	new_entry = (struct en_exthash_tbl_entry *)tbl_entry;
	info = (struct en_exthash_info *)h_HashTbl;
#ifdef FM_EHASH_DEBUG 
	printk("%s::keysize %d, shift %d, mask %x key::\n",
		__FUNCTION__, keySize, info->hashshift,
		info->hashmask);
	disp_buf(&new_entry->hashentry.key[0], keySize);
#endif
	get_indexed_hash_bucket(keySize, &new_entry->hashentry.key[0],
                                info->hashshift,
                                (uint16_t)info->hashmask, &index);
	bucket = ((struct en_exthash_bucket *)(info->table_base) + index);
#ifdef FM_EHASH_DEBUG 
	printk("%s::index %x\n", __FUNCTION__, index);
	printk("%s::table base %p , basePhyAddr %p \n bucket %p, bucket phy %p spinlock %p \n", __FUNCTION__,
		info->table_base, XX_VirtToPhys(info->table_base), bucket, XX_VirtToPhys(bucket), info->pSpinlock);
#endif

	h_Spinlock = *(info->pSpinlock + index);
   	intFlags = XX_LockIntrSpinlock(h_Spinlock);

	retval = index;
	phyaddr = bucket->h;
#ifdef NO_CUMULATIVE_ENTRY
	if (phyaddr) {
		first_entry = XX_PhysToVirt(SwapUint64(phyaddr));
		if (find_entry_in_bucket(first_entry, &new_entry->hashentry.key[0], 
				keySize)) {
        		REPORT_ERROR(MAJOR, E_ALREADY_EXISTS,
                	     ("en_ext_hash_entry"));
			retval = -1;
			goto func_ret;
		}
		first_entry->prev = new_entry;
	} else
		first_entry = NULL;
	//fill next pointer info and link into chain
	new_entry->next = first_entry;
	//adjust the prev pointer in the old entry
	//fill next pointer physaddr for uCode
	// physical address was swapped before adding to bucket, so reverse it and add
	phyaddr = SwapUint64(phyaddr);
	new_entry->hashentry.next_entry_hi = cpu_to_be16((phyaddr >> 32) & 0xffff);
	new_entry->hashentry.next_entry_lo = cpu_to_be32((phyaddr & 0xffffffff));
	//change the head pointer in the bucket
	phyaddr = XX_VirtToPhys(new_entry);
	bucket->h = SwapUint64(phyaddr);
#else
	/* key should be aligned to  8 byte boundary if size > 4*/
	if (keySize <= 2)
		key_align =  keySize;
	else if (keySize <= 4)
		key_align =  4;
	else
		key_align =  (keySize + 7) & 0x38;

	FM_EHASH_PRINT("%s(%d) tbl_entry %p, key size %d, key-align %d\n",
		__FUNCTION__,__LINE__,tbl_entry, keySize, key_align);
	key_align -= keySize;

	// if no nodes in bucket, just add the node as it is
	if (!phyaddr)
	{
		new_entry->next =  NULL;
		//change the head pointer in the bucket
		phyaddr = XX_VirtToPhys(new_entry);
		bucket->h = SwapUint64(phyaddr);
	}
	else
	{
		FM_EHASH_PRINT("%s(%d)\n",__FUNCTION__,__LINE__);
		first_entry = XX_PhysToVirt(SwapUint64(phyaddr));
		if (find_entry_in_bucket(first_entry, &new_entry->hashentry.key[0], 
				keySize)) {
        		REPORT_ERROR(MAJOR, E_ALREADY_EXISTS,
                	     ("en_ext_hash_entry"));
			retval = -1;
			goto func_ret;
		}
		FM_EHASH_PRINT("%s(%d)\n",__FUNCTION__,__LINE__);
		// check if node is cumulative or not
		// if not cumulative, allocate cumulative node 
		// add the existing node and the current node to the cumulative node
		cumulative_tbl_entry = XX_PhysToVirt(SwapUint64(phyaddr));
		cumulative_entry = &cumulative_tbl_entry->cumulative_entry;
		FM_EHASH_PRINT("%s(%d)\n",__FUNCTION__,__LINE__);
		if (cumulative_entry->flags & EN_CUMULATIVE_NODE)
		{
			FM_EHASH_PRINT("%s(%d)\n",__FUNCTION__,__LINE__);
			if (((cumulative_entry->key_size+EN_CU_HASH_TABLE_ENTRY_ADDR_SIZE) * 
				    (cumulative_entry->num_key_entries+1) + EN_CU_FIXED_ELEMENTS_SIZE) <= EN_CUMULATIVE_NODE_MAX_SIZE)
			{
				tmp_tbl_entry = ExternalHashTableAllocCumulativeEntry (h_HashTbl);
				if (!tmp_tbl_entry)
				{
					REPORT_ERROR(MAJOR, E_NO_MEMORY,
								 ("en_cumulative_entry"));
					retval = -1;
					goto func_ret;
				}
				FM_EHASH_PRINT("%s(%d)\n",__FUNCTION__,__LINE__);
				tmp = &tmp_tbl_entry->cumulative_entry;
				tmp->flags = cumulative_entry->flags; 
				tmp->key_size =  cumulative_entry->key_size;
				tmp->next_entry_addr = cumulative_entry->next_entry_addr;
				tmp->num_key_entries = cumulative_entry->num_key_entries+1;
				tmp->tbl_entry_index = cumulative_entry->tbl_entry_index + cumulative_entry->key_size;
				memcpy(tmp->data, new_entry->hashentry.key, keySize);
				memset(tmp->data+keySize, 0, key_align);
				memcpy(tmp->data+keySize+key_align, cumulative_entry->data,
							1+cumulative_entry->num_key_entries*cumulative_entry->key_size);
				phyaddr = XX_VirtToPhys(new_entry);
				phyaddr = SwapUint64(phyaddr);
				memcpy(tmp->data+(cumulative_entry->num_key_entries+1)*cumulative_entry->key_size+1, &phyaddr,
					EN_CU_HASH_TABLE_ENTRY_ADDR_SIZE);
				memcpy(tmp->data+(cumulative_entry->num_key_entries+1)*cumulative_entry->key_size+1+EN_CU_HASH_TABLE_ENTRY_ADDR_SIZE, 
					cumulative_entry->data+1+cumulative_entry->num_key_entries*cumulative_entry->key_size, 
					8*cumulative_entry->num_key_entries);
				tmp_tbl_entry->next_entry = cumulative_tbl_entry->next_entry;
				if (cumulative_tbl_entry->next_entry)
					cumulative_tbl_entry->next_entry->prev_entry = tmp_tbl_entry;
				// initiate host command
				FM_EHASH_PRINT("%s(%d)\n",__FUNCTION__,__LINE__);
				if (cumulative_tbl_entry->prev_entry)
				{
					phyaddr =  XX_VirtToPhys(tmp_tbl_entry);
					phyaddr =  SwapUint64(phyaddr);
					cumulative_tbl_entry->prev_entry->cumulative_entry.next_entry_addr =  phyaddr;
					cumulative_tbl_entry->prev_entry->next_entry = tmp_tbl_entry;
					tmp_tbl_entry->prev_entry =  cumulative_tbl_entry->prev_entry;
				}
				phyaddr =  XX_VirtToPhys(cumulative_tbl_entry);
				phyaddr =  SwapUint64(phyaddr);
				bucket_phyaddr = bucket->h;
				if (bucket_phyaddr == phyaddr)
				{
					phyaddr =  XX_VirtToPhys(tmp_tbl_entry);
					phyaddr =  SwapUint64(phyaddr);
					bucket->h = phyaddr;
				}
				flags = cumulative_entry->flags;
				flags = flags | EN_INVALID_CUMULATIVE_NODE;
				cumulative_entry->flags = flags;
				info = (struct en_exthash_info *)h_HashTbl;
				XX_UnlockIntrSpinlock(h_Spinlock, intFlags);
				if (FmPcdHcSync(info->pcd)) {
					printk("%s::FmPcdHcSync failed\n", __FUNCTION__);
					retval = -1;
					goto func_ret;
				}
				ExternalHashTableCumulativeEntryFree(cumulative_tbl_entry);
#ifdef FM_EHASH_DEBUG 
				printk("new cumulative entry phyaddr : %p bucket content value : 0x%lx\n",
							(void *)XX_VirtToPhys(cumulative_entry), (long unsigned int)bucket->h);
				cumulative_entry = (struct en_cumulative_entry *)XX_PhysToVirt((physAddress_t)SwapUint64(bucket->h));
				while (cumulative_entry)
				{
					printk("cumulative entry : %p \n", cumulative_entry);
							
						
					printk("flags: 0x%x, key-size %d, key_entries: %d, nodes_index: %d, next_entry 0x%lx\n", 
							cumulative_entry->flags, 
							cumulative_entry->key_size, cumulative_entry->num_key_entries, cumulative_entry->tbl_entry_index,
							(long unsigned int)cumulative_entry->next_entry_addr);
					printk("cumulative key: \n");
					for (ii=0; ii<((cumulative_entry->key_size*cumulative_entry->num_key_entries)+1); ii++)
					{
						if ((ii % 16) == 0)
							printk("\n");
						printk("%02x ", cumulative_entry->data[ii]);
					}
					printk("\n table entries (%d) : \n",cumulative_entry->num_key_entries);
					for (ii = 0; ii < cumulative_entry->num_key_entries; ii++) {
						phyaddr = *((uint64_t *)(&cumulative_entry->data[cumulative_entry->tbl_entry_index-12+ii*8]));
						printk("table_entry ptr[%d]: %p \n", ii, (void *)XX_PhysToVirt((physAddress_t)SwapUint64(phyaddr)));
					}
					printk("\n");
					if (cumulative_entry->flags & EN_NEXT_CUMULATIVE_NODE)
					{
						cumulative_entry = XX_PhysToVirt(SwapUint64(cumulative_entry->next_entry_addr));
					}
					else
						cumulative_entry = NULL;
				}
#endif
				return retval;
			}
			else
			{
				FM_EHASH_PRINT("%s(%d)\n",__FUNCTION__,__LINE__);
				tmp_tbl_entry = ExternalHashTableAllocCumulativeEntry (h_HashTbl);
				if (!tmp_tbl_entry)
				{
					REPORT_ERROR(MAJOR, E_NO_MEMORY,
								 ("en_cumulative_entry"));
					retval = -1;
					return retval;
				}
				tmp = &tmp_tbl_entry->cumulative_entry;
				phyaddr = XX_VirtToPhys(cumulative_tbl_entry);
				tmp->flags = EN_NEXT_CUMULATIVE_NODE | EN_CUMULATIVE_NODE;
				tmp->next_entry_addr = SwapUint64(phyaddr);
				tmp_tbl_entry->next_entry =  cumulative_tbl_entry;
				cumulative_tbl_entry->prev_entry =  tmp_tbl_entry;
				cumulative_entry = tmp;
				cumulative_entry->key_size = keySize+key_align;
				cumulative_entry->num_key_entries = 1;
				memcpy(cumulative_entry->data, new_entry->hashentry.key, keySize);
				memset(cumulative_entry->data+keySize, 0, key_align);
				cumulative_entry->data[keySize+key_align] = 0;
				cumulative_entry->tbl_entry_index =  EN_CU_FIXED_ELEMENTS_SIZE+1+cumulative_entry->num_key_entries*cumulative_entry->key_size;
				phyaddr = XX_VirtToPhys(new_entry);
				phyaddr = SwapUint64(phyaddr);
				memcpy(&cumulative_entry->data[cumulative_entry->key_size+1], &phyaddr, EN_CU_HASH_TABLE_ENTRY_ADDR_SIZE);
				
				//change the head pointer in the bucket
				phyaddr = XX_VirtToPhys(tmp_tbl_entry);
				bucket->h = SwapUint64(phyaddr);
			}
		}
		else
		{
			FM_EHASH_PRINT("%s(%d)\n",__FUNCTION__,__LINE__);
			cumulative_tbl_entry = ExternalHashTableAllocCumulativeEntry (h_HashTbl);
			if (!cumulative_tbl_entry)
			{
				REPORT_ERROR(MAJOR, E_NO_MEMORY,
							 ("en_cumulative_entry"));
				retval = -1;
				return retval;
				
			}
			cumulative_entry = &cumulative_tbl_entry->cumulative_entry;
			cumulative_entry->flags = EN_CUMULATIVE_NODE;
			cumulative_entry->key_size = keySize+key_align;
			cumulative_entry->num_key_entries = 2;
			FM_EHASH_PRINT("%s(%d)\n",__FUNCTION__,__LINE__);
			memcpy(cumulative_entry->data, new_entry->hashentry.key, keySize);
			memset(cumulative_entry->data+keySize, 0, key_align);
			FM_EHASH_PRINT("%s(%d)\n",__FUNCTION__,__LINE__);
			memcpy(cumulative_entry->data+keySize+key_align, first_entry->hashentry.key, keySize);
			memset(cumulative_entry->data+2*keySize+key_align, 0, key_align);
			cumulative_entry->data[2*(keySize+key_align)] = 0;
			cumulative_entry->tbl_entry_index =  EN_CU_FIXED_ELEMENTS_SIZE+1+cumulative_entry->num_key_entries*(keySize+key_align);
			phyaddr = XX_VirtToPhys(new_entry);
			phyaddr = SwapUint64(phyaddr);
			*((uint64_t *)(cumulative_entry->data+2*(keySize+key_align)+1)) = phyaddr;
			phyaddr = XX_VirtToPhys(first_entry);
			phyaddr = SwapUint64(phyaddr);
			*((uint64_t *)(cumulative_entry->data+2*(keySize+key_align)+1+EN_CU_HASH_TABLE_ENTRY_ADDR_SIZE)) = phyaddr;
			//change the head pointer in the bucket
			FM_EHASH_PRINT("%s(%d)\n",__FUNCTION__,__LINE__);
			phyaddr = XX_VirtToPhys(cumulative_tbl_entry);
			bucket->h = SwapUint64(phyaddr);
			FM_EHASH_PRINT("%s(%d)\n",__FUNCTION__,__LINE__);
		}
		
#ifdef FM_EHASH_DEBUG 
		printk("new cumulative entry phyaddr : %p bucket content value : 0x%lx\n",
			(void *)XX_VirtToPhys(cumulative_entry), (long unsigned int)bucket->h);
		cumulative_entry = (struct en_cumulative_entry *)XX_PhysToVirt((physAddress_t)SwapUint64(bucket->h));
		while (cumulative_entry)
		{
			printk("cumulative entry : %p \n", cumulative_entry);
			
//				uint32_t ii;
			
			printk("flags: 0x%x, key-size %d, key_entries: %d, nodes_index: %d, next_entry 0x%lx\n", 
					cumulative_entry->flags, 
					cumulative_entry->key_size, cumulative_entry->num_key_entries, cumulative_entry->tbl_entry_index,
					(long unsigned int)cumulative_entry->next_entry_addr);
			printk("cumulative key: \n");
			for (ii=0; ii<((cumulative_entry->key_size*cumulative_entry->num_key_entries)+1); ii++)
			{
				if ((ii % 16) == 0)
					printk("\n");
				printk("%02x ", cumulative_entry->data[ii]);
			}
			printk("\n table entries (%d) : \n",cumulative_entry->num_key_entries);
			for (ii = 0; ii < cumulative_entry->num_key_entries; ii++) {
				phyaddr = *((uint64_t *)(&cumulative_entry->data[cumulative_entry->tbl_entry_index-12+ii*8]));
				printk("table_entry ptr[%d]: %p \n", ii, (void *)XX_PhysToVirt((physAddress_t)SwapUint64(phyaddr)));
			}
			printk("\n");
			if (cumulative_entry->flags & EN_NEXT_CUMULATIVE_NODE)
			{
				cumulative_entry = XX_PhysToVirt(SwapUint64(cumulative_entry->next_entry_addr));
			}
			else
				cumulative_entry = NULL;
		}
#endif
	}	
#endif //NO_CUMULATIVE_ENTRY
#ifdef FM_EHASH_DEBUG 
	printk("new entry phyaddr : %p bucket content value : 0x%lx\n", phyaddr, bucket->h);
	{
		uint32_t ii;
		uint8_t *ptr;

		printk("hash entry::%p, next %p, prev %p\n hashentry %p::\n", 
			new_entry, 
			new_entry->next, new_entry->prev, &new_entry->hashentry);
		ptr = (uint8_t *)&new_entry->hashentry;
		for (ii = 0; ii < sizeof(struct en_ehash_entry); ii++) {
			if ((ii % 16) == 0)
				printk("\n");
			printk("%02x ", *ptr);
			ptr++;
		}
		printk("\n");
	}
#endif
func_ret:
    	XX_UnlockIntrSpinlock(h_Spinlock, intFlags);
	return retval;	
}
EXPORT_SYMBOL(ExternalHashTableAddKey); 

//called from ExternalHashTableSet only when table init fails
static void Delete_EnEhashInfo(t_Handle handle)
{
	struct en_exthash_info *info;
	uint32_t ii;
	
	info = (struct en_exthash_info *)handle;
	if (info) {
		if (info->pSpinlock) {
			//free all bucket locks
			for (ii = 0; ii <= info->hashmask; ii++) {
				if (*(info->pSpinlock + ii)) {
					XX_FreeSpinlock(*(info->pSpinlock + ii));
				} else 
					break;
			}	
			//free lock handle array
			XX_FreeSmart(info->pSpinlock);
			info->pSpinlock = NULL;
		}
		//free table info
		kfree(info);
	}
}

#ifndef EXCLUDE_FMAN_IPR_OFFLOAD
extern void disp_sch_info(void *);
void ipr_update_timestamp(void)
{
	uint32_t ii;

	if (ipv4_reassly_tbl_info) {
		ii = cpu_to_be32(ipv4_reassly_tbl_info->ip_reassem_info->ipr_timer);
		ii++;
		ipv4_reassly_tbl_info->ip_reassem_info->ipr_timer =
			cpu_to_be32(ii);
	}
	if (ipv6_reassly_tbl_info)
		ipv4_reassly_tbl_info->ip_reassem_info->ipr_timer =
			cpu_to_be32(ii);

}

static int GetMuramIprContextMem(t_Handle h_FmPcd)
{
	int ii;
	uint32_t *ptr;
	uint32_t prev_addr;

	if (!IprContextMem) {
		//allocate memory for IPR context in muram and make a free list
		IprContextMem = (struct ipr_context_info *)
			FM_MURAM_AllocMem(FmPcdGetMuramHandle(h_FmPcd),
					sizeof(struct ipr_context_info), IPR_CTX_ALIGN);
		if (!IprContextMem)
		{
			REPORT_ERROR(MAJOR, E_NO_MEMORY, ("MURAM allocation for reassem context"));
			return -1;
		}
		//make a free list
		prev_addr = IPR_CONTEXT_EOL;
		ptr = (uint32_t *)&IprContextMem->context_data[0][0];
		for (ii = 0; ii < IPR_MAX_SESSIONS; ii++) {
			*ptr = cpu_to_be32(prev_addr);
			if (prev_addr == IPR_CONTEXT_EOL)
				prev_addr =
					(uint32_t)((uint8_t *)IprContextMem - (uint8_t *)FmMurambaseAddr);
			else
				prev_addr += IPR_MAX_SESSSIZE;
			ptr += (IPR_MAX_SESSSIZE / sizeof(uint32_t));
		}
		IprContextMem->next_free_ctx = cpu_to_be32(prev_addr);
		ii = (int)((uint8_t *)IprContextMem - (uint8_t *)FmMurambaseAddr);
		{
			uint32_t *ptr;
			uint32_t count;

			prev_addr = cpu_to_be32(IprContextMem->next_free_ctx);
			printk("%s::IprContextMem %p, %08x, next free ctx %08x\n",
					__FUNCTION__, IprContextMem, ii, prev_addr);
			count = 0;
			while(1) {
				if (prev_addr == IPR_CONTEXT_EOL)
					break;
#if 0
				printk("memaddr %08x\n", prev_addr);
#endif
				ptr = (uint32_t *)(FmMurambaseAddr + prev_addr);
				prev_addr = cpu_to_be32(*ptr);
				count++;
			}
			printk("%d free contexts initialized\n", count);
		}
	} else {
		ii = (int)((uint8_t *)IprContextMem - (uint8_t *)FmMurambaseAddr);
	}
	//return offset in muram of IPR context in muram
	return ii;
}
#endif

extern void disp_sch_info(void *);
t_Handle ExternalHashTableSet(t_Handle h_FmPcd, t_FmPcdHashTableParams *p_Param)
{
	struct en_exthash_info *info;
	t_Handle h_FmMuram;
	uint32_t ii;
	uint8_t num_of_zeroes = 0;
	struct en_exthash_node *node;
	uint64_t tblphysaddr;
	t_FmPcd *p_FmPcd = (t_FmPcd*)h_FmPcd;
#ifndef EXCLUDE_FMAN_IPR_OFFLOAD
	uint32_t gpp_table;
	uint32_t table_type;

	p_Param->table_type &= TABLE_TYPE_MASK;
	switch (p_Param->table_type) {
		case IPV4_REASSM_TABLE:
			if (ipv4_reassly_tbl_info) {
#ifdef FM_EHASH_DEBUG 
				printk("%s::ipv4_reassly_tbl_info already created %p\n",
						__FUNCTION__, ipv4_reassly_tbl_info);
#endif
				return (t_Handle)ipv4_reassly_tbl_info;
			}
			gpp_table = 0;
			table_type = REASSEMBLY_TABLE;
			//override xml definitions
			p_Param->hashResMask = (MAX_REASSM_BUCKETS - 1);
			break;
		case IPV6_REASSM_TABLE:
			if (ipv6_reassly_tbl_info) {
#ifdef FM_EHASH_DEBUG 
				printk("%s::ipv6_reassly_tbl_info already created %p\n",
						__FUNCTION__, ipv6_reassly_tbl_info);
#endif
				return (t_Handle)ipv6_reassly_tbl_info;
			}
			table_type = REASSEMBLY_TABLE;
			//override xml definitions
			p_Param->hashResMask = (MAX_REASSM_BUCKETS - 1);
			gpp_table = 0;
			break;
		case IPV4_UDP_TABLE:
		case IPV4_TCP_TABLE:
		case ESP_IPV4_TABLE:
		case IPV6_UDP_TABLE:
		case IPV6_TCP_TABLE:
		case ESP_IPV6_TABLE:
		case IPV4_3TUPLE_UDP_TABLE:
		case IPV4_3TUPLE_TCP_TABLE:
		case IPV6_3TUPLE_UDP_TABLE:
		case IPV6_3TUPLE_TCP_TABLE:
			table_type = L4_TABLE;
			gpp_table = 1;
			break;

		case IPV4_MULTICAST_TABLE:
		case IPV6_MULTICAST_TABLE:
			table_type = L3_TABLE;
			gpp_table = 1;
			break;
		default:
			table_type = L2_TABLE;
			gpp_table = 1;
			break;

	}
#endif
	//allocate table info structure
	info = kzalloc(sizeof(struct en_exthash_info), GFP_KERNEL);
	if (!info) {
        	REPORT_ERROR(MAJOR, E_NO_MEMORY,
                	     ("allocation for en_ext_hash_info"));
        	return NULL;
	}
#ifdef FM_EHASH_DEBUG 
	printk("%s(%d) info %p \n",__FUNCTION__,__LINE__,info);
#endif
	memset(info, 0, sizeof(struct en_exthash_info));
		
	//determine number of bits set in the hash mask
	ii = p_Param->hashResMask;
#if defined(CONFIG_ARM) || defined(CONFIG_ARM64)
        __asm__ ("clz %0,%1\n"
                        : "=r"(num_of_zeroes)
                        : "r"(ii));
#else
        __asm__ ("cntlzw %0,%1\n"
                        : "=r"(num_of_zeroes)
                        : "r"(ii));
#endif
	h_FmMuram = FmPcdGetMuramHandle(h_FmPcd);
    	if (!h_FmMuram) {
        	REPORT_ERROR(MAJOR, E_INVALID_HANDLE,
                	     ("muram"));
		goto err_ret;
	}
	info->hashmask = p_Param->hashResMask;
	info->pcd = h_FmPcd;
#ifndef EXCLUDE_FMAN_IPR_OFFLOAD 
	info->type = p_Param->table_type;
	if (gpp_table) 
#endif
	{
		//spin locks are required for tables filled by GPP
		//not required for reassembly tables, used by uCode only
		//allocate memory for bucket lock handles
		info->pSpinlock = XX_MallocSmart((sizeof(t_Handle *) * (info->hashmask + 1)),
							0, sizeof(t_Handle));
		if (!info->pSpinlock) {
			REPORT_ERROR(MAJOR, E_NO_MEMORY,
				("spin lock array"));
			goto err_ret;
		}
		//allocate and initialize spin locks on all buckets
		for (ii = 0; ii <= info->hashmask; ii++) {
			*(info->pSpinlock + ii) = XX_InitSpinlock();
			if (!*(info->pSpinlock + ii)) {
				REPORT_ERROR(MAJOR, E_NO_MEMORY,
						("spinlock for en_ext_hash"));
				goto err_ret;
			}
		}
	}
		info->tablesize = (sizeof(struct en_exthash_bucket) << (64 - num_of_zeroes) );
    	info->table_base = XX_MallocSmart(info->tablesize, 0 /*p_Param->externalHashParams.dataMemId not used */, 
			EN_EXTHASH_TBL_ALIGNMENT);
    	if (!info->table_base)
    	{
        	REPORT_ERROR(MAJOR, E_NO_MEMORY,
                	     ("en_ext_hash_table"));
		goto err_ret;
    	}
	//clear table
	memset(info->table_base, 0, info->tablesize);
	//allocate node for table in muram
// following code may not be rqd -- TBD
//	{
//		t_FmPcd *p_FmPcd = (t_FmPcd *)h_FmPcd;
 //       	ii = (uint32_t)((XX_VirtToPhys(info->h_Ad)
 //                               - p_FmPcd->physicalMuramBase));
//	}
	info->keysize = p_Param->matchKeySize;       
        info->hashshift = p_Param->hashShift;
        //info->dataMemId = p_Param->externalHashParams.dataMemId;
//        info->dataLiodnOffset = p_Param->externalHashParams.dataMemId;
//	if (p_Param->agingSupport) 
		info->flags |= TIMESTAMP_EN;
	if (p_Param->statisticsMode) 
		info->flags |= STATS_EN;
	//fill AD
	node = &info->node;
	memset(node, 0, sizeof(struct en_exthash_node));
	node->key_size = info->keysize;		
	node->hash_bytes_offset = info->hashshift;	
	/* convert the hash mask value to hash mask bits */
	if (info->hashmask > 0x7fff)
	{
		REPORT_ERROR(MAJOR, E_INVALID_VALUE, 
			("unsupported hash mask value"));
		goto err_ret;
	}
	for (ii =0; ii <15; ii++)
	{
		if ((info->hashmask + 1) & (1 << ii))
			break;
	}

	if ((1 << ii) != (info->hashmask +1))
	{
		REPORT_ERROR(MAJOR, E_INVALID_VALUE, 
			("unsupported hash mask value"));
		goto err_ret;
	}
	node->hash_mask_bits = ii;
	node->int_buf_pool_addr = p_FmPcd->InternalBufMgmtMuramArea;
	node->global_mem_offset = EN_INTERNAL_BUFF_POOL_SIZE >> 8; /* value compressed */
	/* assign the MURAM reserved space of global params/stats to global muram pointer */
	if (!en_global_muram_mem)
	{
		en_global_muram_mem = (en_exthash_global_mem *)
			((uint8_t *)(p_FmPcd->pIntMuramPtr)+EN_INTERNAL_BUFF_POOL_SIZE);
	}
#ifdef FM_EHASH_DEBUG 
	printk("%s::table type %d, word_1 0x%x, hashmask+1 0x%x, hashmask bits 0x%x, global mem offset 0x%x\n",
	__FUNCTION__, p_Param->table_type, node->word_1, info->hashmask+1, node->hash_mask_bits,
	node->global_mem_offset);
#endif
	tblphysaddr = XX_VirtToPhys(info->table_base);
	node->table_base_hi = ((tblphysaddr >> 32) & 0xffff);	
	node->table_base_lo = (tblphysaddr & 0xffffffff);		
#ifndef EXCLUDE_FMAN_IPR_OFFLOAD
	node->table_type = table_type;
#endif
	//check next engine for miss action
#ifndef EXCLUDE_FMAN_IPR_OFFLOAD
	if (gpp_table) 
#endif
	{
		switch (p_Param->ccNextEngineParamsForMiss.nextEngine) {
			case e_FM_PCD_KG:
			{
				//Keygen
				t_FmPcdCcNextKgParams *kgparams;
				kgparams =
					&p_Param->ccNextEngineParamsForMiss.params.kgParams;
#ifdef FM_EHASH_DEBUG 
				printk("%s::Nextengine = KG, kgparams->h_DirectScheme %p\n", 
					__FUNCTION__, kgparams->h_DirectScheme);
#endif
				node->nia = (NIA_ENG_KG | NIA_KG_DIRECT);
				if (kgparams->overrideFqid) 
                    			node->nia |= (kgparams->newFqid | NIA_KG_CC_EN);
                		node->nia |= FmPcdKgGetSchemeId(kgparams->h_DirectScheme);
#ifdef FM_EHASH_DEBUG 
				disp_sch_info(kgparams->h_DirectScheme);
#endif
				node->miss_action_type = EN_EHASH_MISS_ACTION_NIA;
				break;
		}
		case e_FM_PCD_DONE:
		{
				//BMI
				t_FmPcdCcNextEnqueueParams *enqparams;
				enqparams = 
					&p_Param->ccNextEngineParamsForMiss.params.enqueueParams;
#ifdef FM_EHASH_DEBUG
                               printk("%s:: EN_EHASH_MISS_ACTION_NIA %d , ovrdfqid %d\n",
                                       __FUNCTION__, EN_EHASH_MISS_ACTION_NIA,enqparams->overrideFqid);
#endif
				if (enqparams->overrideFqid)
					node->fqid = enqparams->newFqid;
				if (enqparams->overrideFqid)
				{
					node->miss_action_type = EN_EHASH_MISS_ACTION_ENQUE;
				}
				else
					node->miss_action_type = EN_EHASH_MISS_ACTION_DONE;
#ifdef FM_EHASH_DEBUG
printk("node->fqid : %d \n", node->fqid);
#endif
				break;
		}
		default:
			node->miss_action_type = EN_EHASH_MISS_ACTION_DROP;
#ifdef FM_EHASH_DEBUG
			printk("%s::Nextengine type %d not supported, drop case\n", __FUNCTION__,
				p_Param->ccNextEngineParamsForMiss.nextEngine);
#endif
			break;
		}
	}
#ifndef EXCLUDE_FMAN_IPR_OFFLOAD
	else {
		//ipr table
		uint32_t mem_size;
		int iprctxinfo;

		iprctxinfo = GetMuramIprContextMem(h_FmPcd);
		if (iprctxinfo == -1)
			goto err_ret;

		mem_size = (sizeof(struct ip_reassembly_params) +
							REASSM_DEBUG_SIZE);
				//reassembly table, allocate stats and other info
				info->ip_reassem_info = (struct ip_reassembly_params *)
						PTR_TO_UINT(FM_MURAM_AllocMem(FmPcdGetMuramHandle(h_FmPcd),
							mem_size, sizeof(uint64_t)));
				if (!info->ip_reassem_info)
				{
					REPORT_ERROR(MAJOR, E_NO_MEMORY, ("MURAM allocation for reassem info"));
					goto err_ret;
				}
				node->reassm_param =
						(uint32_t)((uint8_t *)info->ip_reassem_info - (uint8_t *)FmMurambaseAddr);
				info->ip_reassem_info->type = (p_Param->table_type);
				info->ip_reassem_info->table_base_hi =
						cpu_to_be32(tblphysaddr >> 32);
				info->ip_reassem_info->table_base_lo = cpu_to_be32(tblphysaddr & 0xffffffff);
				info->ip_reassem_info->table_mask = cpu_to_be32(p_Param->hashResMask);
				info->ip_reassem_info->timeout_val = cpu_to_be32(p_Param->timeout_val);
				info->ip_reassem_info->timeout_fqid = cpu_to_be32(p_Param->timeout_fqid);
				info->ip_reassem_info->max_frags = cpu_to_be32(p_Param->max_frags);
				info->ip_reassem_info->min_frag_size = cpu_to_be32(p_Param->min_frag_size);
				info->ip_reassem_info->max_con_reassm = cpu_to_be32(p_Param->max_sessions);
				info->ip_reassem_info->timer_tnum = 0xffffffff; //no task assigned yet
				info->ip_reassem_info->curr_sessions = 0;
				info->ip_reassem_info->reassly_dbg =
						cpu_to_be32(node->reassm_param + sizeof(struct ip_reassembly_params));
		info->ip_reassem_info->context_info = cpu_to_be32(iprctxinfo);
		memset(&info->ip_reassem_info->stats, 0, sizeof(struct ip_reassembly_stats));
		for (ii = 0; ii < MAX_REASSM_BUCKETS; ii++) {
			info->ip_reassem_info->bucket_lock[ii] = 0;
			info->ip_reassem_info->bucket_head[ii] = (uint64_t)0;
		}
#ifdef FM_EHASH_DEBUG
		if (p_Param->table_type == IPV4_REASSM_TABLE)
			printk("%s::ipv4 reassem param ", __FUNCTION__);
		else
			printk("%s::ipv6 reassem param ", __FUNCTION__);
		printk("%x, size %d dbg %x\n",
				node->reassm_param,
				(uint32_t)(sizeof(struct ip_reassembly_params) +
				REASSM_DEBUG_SIZE),
				cpu_to_be32(info->ip_reassem_info->reassly_dbg));
		printk(KERN_INFO "%s::en ext hash reassem table created, handle %p\n",
				__FUNCTION__, info);
#endif
	}
#endif

#ifdef FM_EHASH_DEBUG
	printk(KERN_INFO "%s::en ext hash table created, handle %p\n",
			__FUNCTION__, info);
	printk("ad_word_0::%08x\n", node->word_0);
	printk("ad_word_1::%08x\n", node->table_base_lo);
	printk("ad_word_2::%08x\n", node->word_1);
	printk("ad_word_3::%08x\n", node->word_2);
#endif //FM_EHASH_DEBUG

#ifndef EXCLUDE_FMAN_IPR_OFFLOAD
	switch (p_Param->table_type) {
		case IPV4_REASSM_TABLE:
			ipv4_reassly_tbl_info = info;
#ifdef FM_EHASH_DEBUG
			printk("%s::saving ipv4_reassly_tbl_info %p\n",
					__FUNCTION__, ipv4_reassly_tbl_info);
#endif
			break;
		case IPV6_REASSM_TABLE:
			ipv6_reassly_tbl_info = info;
#ifdef FM_EHASH_DEBUG
			printk("%s::saving ipv6_reassly_tbl_info %p\n",
					__FUNCTION__, ipv6_reassly_tbl_info);
#endif
			break;
		default:
			break;
	}
#endif
#ifdef FM_EHASH_DEBUG
	//display_ehashtbl_info(info, __FUNCTION__);
	printk("%s::handle %p\n", __FUNCTION__, info);
#endif //FM_EHASH_DEBUG
	return (t_Handle)info;
err_ret:
	Delete_EnEhashInfo(info);	
	return NULL;
}


t_Error ExternalHashTableModifyMissNextEngine(t_Handle h_HashTbl, t_FmPcdCcNextEngineParams *p_FmPcdCcNextEngineParams)
{
	struct en_exthash_info *info;
	struct en_exthash_node node;

	info = (struct en_exthash_info *)h_HashTbl;

	node.word_0 = GET_UINT32(((struct en_exthash_node *)info->h_Ad)->word_0);
	node.word_2 = GET_UINT32(((struct en_exthash_node *)info->h_Ad)->word_2);

	//set action type to miss temporarily
	node.miss_action_type = EN_EHASH_MISS_ACTION_DROP;
	WRITE_UINT32(((struct en_exthash_node *)info->h_Ad)->word_0, node.word_0);

	//set new miss action
	switch (p_FmPcdCcNextEngineParams->nextEngine) {
		case e_FM_PCD_KG:
		{
			//Keygen
			t_FmPcdCcNextKgParams *kgparams;
			kgparams = &p_FmPcdCcNextEngineParams->params.kgParams;
#ifdef FM_EHASH_DEBUG
			printk("%s::Nextengine = KG, kgparams->h_DirectScheme %p\n",
				__FUNCTION__, kgparams->h_DirectScheme);
#endif
			node.nia = (NIA_ENG_KG | NIA_KG_DIRECT | NIA_KG_CC_EN);
			node.nia |= FmPcdKgGetSchemeId(kgparams->h_DirectScheme);
#ifdef FM_EHASH_DEBUG
			disp_sch_info(kgparams->h_DirectScheme);
#endif
			node.miss_action_type = EN_EHASH_MISS_ACTION_NIA;
			break;
		}
               case e_FM_PCD_DONE:
		{
			//BMI
			t_FmPcdCcNextEnqueueParams *enqparams;
			enqparams =
				&p_FmPcdCcNextEngineParams->params.enqueueParams;
#ifdef FM_EHASH_DEBUG
			printk("%s:: EN_EHASH_MISS_ACTION_NIA %d , ovrdfqid %d\n",
				__FUNCTION__, EN_EHASH_MISS_ACTION_NIA,enqparams->overrideFqid);
#endif
			if (enqparams->overrideFqid)
				node.fqid = enqparams->newFqid;
			if (enqparams->overrideFqid)
			{
				node.miss_action_type = EN_EHASH_MISS_ACTION_ENQUE;
			}
			else
				node.miss_action_type = EN_EHASH_MISS_ACTION_DONE;
			break;
		}
		case e_FM_PCD_PLCR:
		{
			t_FmPcdCcNextPlcrParams *plcrparams;

			plcrparams =
				&p_FmPcdCcNextEngineParams->params.plcrParams;
			node.miss_action_type = EN_EHASH_MISS_ACTION_NIA;
			if (plcrparams->sharedProfile)
				node.nia = (NIA_ENG_PLCR | NIA_PLCR_ABSOLUTE | plcrparams->newRelativeProfileId);
			else
				node.nia = (NIA_ENG_PLCR | plcrparams->newRelativeProfileId);
			break;
		}

		default:
			node.miss_action_type = EN_EHASH_MISS_ACTION_DROP;
#ifdef FM_EHASH_DEBUG
			printk("%s::Nextengine type %d not supported, drop case\n", __FUNCTION__,
				p_FmPcdCcNextEngineParams->nextEngine);
#endif
			break;
	}
	//write the new params
	WRITE_UINT32(((struct en_exthash_node *)info->h_Ad)->word_2, node.word_2);
	WRITE_UINT32(((struct en_exthash_node *)info->h_Ad)->word_0, node.word_0);
#ifdef FM_EHASH_DEBUG
	node.word_0 = GET_UINT32(((struct en_exthash_node *)info->h_Ad)->word_0);
	node.table_base_lo = GET_UINT32(((struct en_exthash_node *)info->h_Ad)->table_base_lo);
	printk("ad_word_0::%p, %08x\n", &(((struct en_exthash_node *)info->h_Ad)->word_0),
                                       node.word_0);
	printk("ad_word_1::%p, %08x\n", &(((struct en_exthash_node *)info->h_Ad)->table_base_lo),
                                       node.table_base_lo);
	printk("ad_word_2::%p, %08x\n", &(((struct en_exthash_node *)info->h_Ad)->word_1),
                                       node.word_1);
	printk("ad_word_3::%p, %08x\n", &(((struct en_exthash_node *)info->h_Ad)->word_2),
                                       node.word_2);
#endif
	return E_OK;
}

int ExternalHashTableFmPcdHcSync(void *h_HashTbl)
{
	struct en_exthash_info *info;
	info = (struct en_exthash_info *)h_HashTbl;
	if (FmPcdHcSync(info->pcd)) {
		printk("%s::FmPcdHcSync failed\n", __FUNCTION__);
		return -1;
	}
	return 0;
}
EXPORT_SYMBOL(ExternalHashTableFmPcdHcSync); 

int ExternalHashTableDeleteKey(void *h_HashTbl, uint16_t index, void *tbl_entry)
{
	t_Handle *h_Spinlock;
	uint32_t intFlags;
	uint64_t phyaddr;
	struct en_exthash_info *info;
	struct en_exthash_bucket *bucket;
	struct en_exthash_tbl_entry *entry;
	struct en_exthash_tbl_entry *temp_entry;
#ifndef NO_CUMULATIVE_ENTRY
	struct en_cumulative_entry *cumulative_entry, *tmp;
	struct en_cumulative_tbl_entry *cumulative_tbl_entry, *tmp_tbl_entry = NULL;
	uint8_t ii, match=0, flags;
#else
	uint64_t update_entry; 
#endif // NO_CUMULATIVE_ENTRY

#ifdef FM_EHASH_DEBUG
	printk("%s::tbl %p, index %x\n", __FUNCTION__, h_HashTbl, index);
#endif
	info = (struct en_exthash_info *)h_HashTbl;
	bucket = ((struct en_exthash_bucket *)info->table_base + index);
	entry = (struct en_exthash_tbl_entry *)tbl_entry;
	h_Spinlock = *(info->pSpinlock + index);
	intFlags = XX_LockIntrSpinlock(h_Spinlock);
#ifdef NO_CUMULATIVE_ENTRY
	//SET_INVALID_ENTRY(entry->hashentry.flags); // setting invalid flag
        update_entry = SwapUint64(entry->hashentry.next_entry);
        SET_INVALID_ENTRY_64BIT(update_entry);
        entry->hashentry.next_entry = SwapUint64(update_entry);
	if (entry->prev) {
                //adjust software links
		temp_entry = entry->prev;
		temp_entry->next = entry->next;
		if (entry->next)
			(entry->next)->prev = temp_entry;
		//temp_entry->hashentry.next_entry_lo = entry->hashentry.next_entry_lo;
		//temp_entry->hashentry.next_entry_hi = entry->hashentry.next_entry_hi;
                update_entry = (entry->hashentry.next_entry & (~0xffff));
                temp_entry->hashentry.next_entry = (update_entry | (temp_entry->hashentry.flags));
	} else {
		phyaddr = XX_VirtToPhys(entry->next);
		bucket->h = SwapUint64(phyaddr);
//                phyaddr = SwapUint64(entry->hashentry.next_entry_lo |
  //                      (entry->hashentry.next_entry_hi << 32));
                //remove from head
                //zero prev of next entry
		if (entry->next)
			(entry->next)->prev = NULL;
	}
	XX_UnlockIntrSpinlock(h_Spinlock, intFlags);
	if (FmPcdHcSync(info->pcd)) {
		printk("%s::FmPcdHcSync failed\n", __FUNCTION__);
		return -1;
	}
	return 0;
#else
	FM_EHASH_PRINT("%s(%d) tbl_entry %p\n", __FUNCTION__,__LINE__,tbl_entry);
	// check if it normal node or cumulative node 
	// no cumulative node, its only one node in hash bucket, delete
	phyaddr = XX_VirtToPhys(entry);
	if (bucket->h == SwapUint64(phyaddr))
	{
		FM_EHASH_PRINT("%s(%d)\n",__FUNCTION__,__LINE__);
		bucket->h = 0;
		XX_UnlockIntrSpinlock(h_Spinlock, intFlags);
		if (FmPcdHcSync(info->pcd)) {
			printk("%s::FmPcdHcSync failed\n", __FUNCTION__);
			return -1;
		}
		return 0;
	}
	else
	{
		FM_EHASH_PRINT("%s(%d)\n",__FUNCTION__,__LINE__);
		// get cumulative entry node address.
		phyaddr = bucket->h;
		cumulative_tbl_entry = XX_PhysToVirt(SwapUint64(phyaddr));
		cumulative_entry = &cumulative_tbl_entry->cumulative_entry;
		if (!(cumulative_entry->flags & EN_CUMULATIVE_NODE))
		{
			FM_EHASH_PRINT("%s(%d)\n",__FUNCTION__,__LINE__);
       		REPORT_ERROR(MAJOR, E_INVALID_STATE,
                	     ("Invalid state"));
			XX_UnlockIntrSpinlock(h_Spinlock, intFlags);
			return -1;
		}
		// find matching cumulative entry
		do
		{
			for (ii=0; ii<cumulative_entry->num_key_entries; ii++)
			{
				FM_EHASH_PRINT("%s(%d)\n",__FUNCTION__,__LINE__);
				phyaddr = *((uint64_t *)(&cumulative_entry->data[cumulative_entry->tbl_entry_index - 12 + ii*8]));
				temp_entry = XX_PhysToVirt(SwapUint64(phyaddr));
				if (temp_entry == tbl_entry)
				{
					match = 1;
					break;
				}
			}
			if (match)
				break;
			if (cumulative_tbl_entry->next_entry)
			{
				cumulative_tbl_entry = cumulative_tbl_entry->next_entry;
				cumulative_entry = &cumulative_tbl_entry->cumulative_entry;
			}
			else
				cumulative_entry =  NULL;
		}while (cumulative_entry);
		if (match) // if found
		{
			FM_EHASH_PRINT("%s(%d)\n",__FUNCTION__,__LINE__);
			// cumulative entry might have the next entry, in that case, get the last table entry in last cumulative entry
			// and overwrite it with to-be-deleted table entry
			flags =  cumulative_entry->flags | EN_INVALID_CUMULATIVE_NODE;
			cumulative_entry->flags = flags;
			if ((cumulative_entry->num_key_entries > 2) ||
				((cumulative_entry->num_key_entries == 2) &&
				  ((cumulative_tbl_entry->prev_entry) || (cumulative_tbl_entry->next_entry))))
			{
				tmp_tbl_entry = ExternalHashTableAllocCumulativeEntry (h_HashTbl);
				if (!tmp_tbl_entry)
				{
					REPORT_ERROR(MAJOR, E_NO_MEMORY,
								 ("en_cumulative_entry"));
					XX_UnlockIntrSpinlock(h_Spinlock, intFlags);
					return -1;
				}
				FM_EHASH_PRINT(" case 1 num keys > 1 %s(%d)\n",__FUNCTION__,__LINE__);
				tmp = &tmp_tbl_entry->cumulative_entry;
				tmp->flags = cumulative_entry->flags & 0xbf; 
				tmp->key_size =  cumulative_entry->key_size;
				tmp->next_entry_addr = cumulative_entry->next_entry_addr;
				tmp->num_key_entries = cumulative_entry->num_key_entries-1;
				tmp->tbl_entry_index = cumulative_entry->tbl_entry_index - cumulative_entry->key_size;
				FM_EHASH_PRINT("%s(%d)\n",__FUNCTION__,__LINE__);
				if (ii)
				{
					FM_EHASH_PRINT("%s(%d)\n",__FUNCTION__,__LINE__);
					memcpy(tmp->data, &cumulative_entry->data, (ii*cumulative_entry->key_size));
					memcpy(&tmp->data[tmp->num_key_entries*tmp->key_size+1], 
						&cumulative_entry->data[cumulative_entry->num_key_entries*tmp->key_size+1], ii*EN_CU_HASH_TABLE_ENTRY_ADDR_SIZE);
				}
				if (ii < cumulative_entry->num_key_entries-1)
				{
					FM_EHASH_PRINT("%s(%d)\n",__FUNCTION__,__LINE__);
					memcpy(tmp->data+(ii*cumulative_entry->key_size), 
						&cumulative_entry->data[(ii+1)*cumulative_entry->key_size],
					(cumulative_entry->num_key_entries-ii-1)*cumulative_entry->key_size+1);
					memcpy(&tmp->data[(tmp->num_key_entries*tmp->key_size)+1+(ii*EN_CU_HASH_TABLE_ENTRY_ADDR_SIZE)], 
						&cumulative_entry->data[cumulative_entry->num_key_entries*tmp->key_size+1+((ii+1)*EN_CU_HASH_TABLE_ENTRY_ADDR_SIZE)],
						(tmp->num_key_entries - ii)*EN_CU_HASH_TABLE_ENTRY_ADDR_SIZE);
				}
				if (cumulative_tbl_entry->next_entry)
				{
					FM_EHASH_PRINT("%s(%d)\n",__FUNCTION__,__LINE__);
					tmp_tbl_entry->next_entry = cumulative_tbl_entry->next_entry;
					cumulative_tbl_entry->next_entry->prev_entry = tmp_tbl_entry;
				}
				phyaddr = SwapUint64(XX_VirtToPhys(tmp_tbl_entry));
				if (cumulative_tbl_entry->prev_entry)
				{
					FM_EHASH_PRINT("%s(%d)\n",__FUNCTION__,__LINE__);
					cumulative_tbl_entry->prev_entry->cumulative_entry.next_entry_addr = phyaddr;
					tmp_tbl_entry->prev_entry = cumulative_tbl_entry->prev_entry;
					cumulative_tbl_entry->prev_entry->next_entry =  tmp_tbl_entry;
				}
				else
				{
					FM_EHASH_PRINT("%s(%d)\n",__FUNCTION__,__LINE__);
					bucket->h = phyaddr;
				}
				XX_UnlockIntrSpinlock(h_Spinlock, intFlags);
				if (FmPcdHcSync(info->pcd)) {
					printk("%s::FmPcdHcSync failed\n", __FUNCTION__);
					return -1;
				}
				ExternalHashTableCumulativeEntryFree(cumulative_tbl_entry);
			}
			else  // if only one entry in list and there is next pointer
			{
				FM_EHASH_PRINT("%s(%d)\n",__FUNCTION__,__LINE__);
				if ((!(cumulative_tbl_entry->next_entry) &&
					  !(cumulative_tbl_entry->prev_entry) &&
					 (cumulative_entry->num_key_entries == 2)) || 
					((!cumulative_tbl_entry->prev_entry) && 
					 (cumulative_tbl_entry->next_entry) && 
					 (cumulative_tbl_entry->next_entry->cumulative_entry.num_key_entries == 1) &&
					 (!cumulative_tbl_entry->next_entry->next_entry)) ||
					((cumulative_tbl_entry->prev_entry) && 
					 !(cumulative_tbl_entry->next_entry) && 
					 (cumulative_tbl_entry->prev_entry->cumulative_entry.num_key_entries == 1) &&
					 (!cumulative_tbl_entry->prev_entry->prev_entry))) 
				{
					// special case where only one entry in list exists
					if (cumulative_tbl_entry->next_entry)
					{
						FM_EHASH_PRINT("%s(%d) special case where only one entry in list exists\n",__FUNCTION__,__LINE__);
						tmp_tbl_entry = cumulative_tbl_entry->next_entry;
						flags = tmp_tbl_entry->cumulative_entry.flags | EN_INVALID_CUMULATIVE_NODE;
						tmp_tbl_entry->cumulative_entry.flags =	flags;
						phyaddr = *((uint64_t *)
							(&tmp_tbl_entry->cumulative_entry.data[tmp_tbl_entry->cumulative_entry.tbl_entry_index-EN_CU_FIXED_ELEMENTS_SIZE]));
						bucket->h = phyaddr;
					}
					else if (cumulative_tbl_entry->prev_entry)
					{
						FM_EHASH_PRINT("%s(%d) special case where only one entry in list exists\n",__FUNCTION__,__LINE__);
						tmp_tbl_entry = cumulative_tbl_entry->prev_entry;
						flags = tmp_tbl_entry->cumulative_entry.flags | EN_INVALID_CUMULATIVE_NODE;
						tmp_tbl_entry->cumulative_entry.flags =	flags;
						phyaddr = *((uint64_t *)
							(&tmp_tbl_entry->cumulative_entry.data[tmp_tbl_entry->cumulative_entry.tbl_entry_index-EN_CU_FIXED_ELEMENTS_SIZE]));
						bucket->h = phyaddr;
					}
					else
					{
						tmp_tbl_entry = NULL;
						if (ii)
							phyaddr = *((uint64_t *)(&cumulative_entry->data[cumulative_entry->tbl_entry_index-EN_CU_FIXED_ELEMENTS_SIZE]));
						else					
							phyaddr = *((uint64_t *)(&cumulative_entry->data[cumulative_entry->tbl_entry_index-EN_CU_FIXED_ELEMENTS_SIZE+EN_CU_HASH_TABLE_ENTRY_ADDR_SIZE]));
						bucket->h = phyaddr;
						FM_EHASH_PRINT("%s(%d) special case where only one entry %p in list exists\n",__FUNCTION__,__LINE__,
							XX_PhysToVirt(SwapUint64(phyaddr)));
					}
					XX_UnlockIntrSpinlock(h_Spinlock, intFlags);
					if (FmPcdHcSync(info->pcd)) {
						printk("%s::FmPcdHcSync failed\n", __FUNCTION__);
						return -1;
					}
					if (tmp_tbl_entry)
						ExternalHashTableCumulativeEntryFree(tmp_tbl_entry);
					ExternalHashTableCumulativeEntryFree(cumulative_tbl_entry);
				}
				else if (!cumulative_tbl_entry->prev_entry) // this is the first cumulative entry in list
				{
					if (cumulative_tbl_entry->next_entry)
					{
						// update the bucket with next cumulative node
						FM_EHASH_PRINT("%s(%d) update the bucket with next cumulative node as no prev entry \n",__FUNCTION__,__LINE__);
						cumulative_tbl_entry->next_entry->prev_entry =  NULL;
						phyaddr = SwapUint64(XX_VirtToPhys(cumulative_tbl_entry->next_entry));
						bucket->h = phyaddr;
						XX_UnlockIntrSpinlock(h_Spinlock, intFlags);
						if (FmPcdHcSync(info->pcd)) {
							printk("%s::FmPcdHcSync failed\n", __FUNCTION__);
							return -1;
						}
						ExternalHashTableCumulativeEntryFree(cumulative_tbl_entry);						
					}
					else // no next node , no prev node , only table entry in node ==> invalid case
					{
						FM_EHASH_PRINT("%s(%d)no next node , no prev node , only table entry in node ==> invalid case\n",__FUNCTION__,__LINE__);
						REPORT_ERROR(MAJOR, E_INVALID_STATE,
									 ("Invalid state"));
						XX_UnlockIntrSpinlock(h_Spinlock, intFlags);
						return -1;
					}
				}
				else // this node is not the first node
				{
					FM_EHASH_PRINT("%s(%d) this node is not the first node \n",__FUNCTION__,__LINE__);
					if (cumulative_tbl_entry->next_entry)
					{
						// update the bucket with next cumulative node
						FM_EHASH_PRINT("%s(%d) update the next cumulative node with prev entry\n",__FUNCTION__,__LINE__);
						cumulative_tbl_entry->next_entry->prev_entry =  cumulative_tbl_entry->prev_entry;
						cumulative_tbl_entry->prev_entry->next_entry =	cumulative_tbl_entry->next_entry;
						cumulative_tbl_entry->prev_entry->cumulative_entry.next_entry_addr = cumulative_tbl_entry->cumulative_entry.next_entry_addr ;
					}
					else
					{
						flags = cumulative_tbl_entry->prev_entry->cumulative_entry.flags & ~EN_NEXT_CUMULATIVE_NODE;
						FM_EHASH_PRINT("%s(%d) update the next cumulative node with prev entry\n",__FUNCTION__,__LINE__);
						cumulative_tbl_entry->prev_entry->cumulative_entry.flags = flags;
						cumulative_tbl_entry->prev_entry->next_entry =	NULL;
						cumulative_tbl_entry->prev_entry->cumulative_entry.next_entry_addr = 0;
					}
					XX_UnlockIntrSpinlock(h_Spinlock, intFlags);
					if (FmPcdHcSync(info->pcd)) {
						printk("%s::FmPcdHcSync failed\n", __FUNCTION__);
						return -1;
					}
					ExternalHashTableCumulativeEntryFree(cumulative_tbl_entry);						
				}
			}
		}// match not found
		else
		{
			XX_UnlockIntrSpinlock(h_Spinlock, intFlags);
			REPORT_ERROR(MAJOR, E_INVALID_STATE,
						 ("No matching node"));
			return -1;
		}
		
	}

#ifdef FM_EHASH_DEBUG
	printk("%s(%d)\n",__FUNCTION__,__LINE__);
	cumulative_entry = XX_PhysToVirt(SwapUint64(bucket->h));
	if (!cumulative_entry)
		return 0;
	if (!(cumulative_entry->flags & EN_CUMULATIVE_NODE))
	{
		printk("head ptr: %p\n", cumulative_entry);
	}
	while (cumulative_entry && (cumulative_entry->flags & EN_CUMULATIVE_NODE))
	{
		printk("cumulative entry : %p \n", cumulative_entry);
		{
			uint32_t ii;
		
			printk("flags: 0x%x, key-size %d, key_entries: %d, nodes_index: %d, next_entry 0x%lx\n", 
				cumulative_entry->flags, 
				cumulative_entry->key_size, cumulative_entry->num_key_entries, cumulative_entry->tbl_entry_index,
				(long unsigned int)(cumulative_entry->next_entry_addr));
			printk("cumulative key: \n");
			for (ii=0; ii<((cumulative_entry->key_size*cumulative_entry->num_key_entries)+1); ii++)
			{
				if ((ii % 16) == 0)
					printk("\n");
				printk("%02x ", cumulative_entry->data[ii]);
			}
			printk(" table entries (%d) : \n",cumulative_entry->num_key_entries);
			for (ii = 0; ii < cumulative_entry->num_key_entries; ii++) {
				phyaddr = *((uint64_t *)(&cumulative_entry->data[cumulative_entry->tbl_entry_index-12+ii*8]));
				printk("table_entry ptr[%d]: %p\n", ii, XX_PhysToVirt(SwapUint64(phyaddr)));
			}
			printk("\n");
		}
		if (cumulative_entry->flags & EN_NEXT_CUMULATIVE_NODE)
		{
			cumulative_entry = XX_PhysToVirt(SwapUint64(cumulative_entry->next_entry_addr));
		}
		else
			cumulative_entry = NULL;
	}
#endif // FM_EHASH_DEBUG
#endif // NO_CUMULATIVE_ENTRY
#ifdef NO_CUMULATIVE_ENTRY
	XX_UnlockIntrSpinlock(h_Spinlock, intFlags);
	if (FmPcdHcSync(info->pcd)) {
		printk("%s::FmPcdHcSync failed\n", __FUNCTION__);
		return -1;
	}
#endif //NO_CUMULATIVE_ENTRY
	return 0;
}

#ifndef EXCLUDE_FMAN_IPR_OFFLOAD
int ExternalHashSetReasslyPool(uint32_t type, uint32_t ctx_bpid, 
		uint32_t ctx_bsize, uint32_t frag_bpid, uint32_t frag_bsize, 
		uint32_t txc_fqid, uint32_t ipr_timer_freq)
{
	uint32_t ii;

	switch (type)
	{
		case IPV4_REASSM_TABLE:
			if (!ipv4_reassly_tbl_info) {
				printk("%s::ipv4 frag table not setup\n", __FUNCTION__);
				return -1;
			}
			printk("ipv4 reassembly ctx_bpid %d, frag_bpid %d timerfreq %d\n", 
					ctx_bpid, frag_bpid, ipr_timer_freq);
			ipv4_reassly_tbl_info->ip_reassem_info->reassem_bpid = 
				cpu_to_be32(ctx_bpid);
			ipv4_reassly_tbl_info->ip_reassem_info->reassem_bsize = 
				cpu_to_be32(ctx_bsize);
			ipv4_reassly_tbl_info->ip_reassem_info->frag_bpid = 
				cpu_to_be32(frag_bpid);
			ipv4_reassly_tbl_info->ip_reassem_info->frag_bsize = 
				cpu_to_be32(frag_bsize);
			ii = cpu_to_be32(ipv4_reassly_tbl_info->ip_reassem_info->timeout_val);  
			ipv4_reassly_tbl_info->ip_reassem_info->timeout_val = 
				cpu_to_be32(ii / ipr_timer_freq);  
			ipv4_reassly_tbl_info->ip_reassem_info->txc_fqid = 
				cpu_to_be32(txc_fqid);
			break;
		case IPV6_REASSM_TABLE:
			if (!ipv6_reassly_tbl_info) {
				printk("%s::ipv6 frag table not setup\n", __FUNCTION__);
				return -1;
			}
			printk("ipv6 reassembly ctx_bpid %d, frag_bpid %d timerfreq %d\n", 
					ctx_bpid, frag_bpid, ipr_timer_freq);
			ipv6_reassly_tbl_info->ip_reassem_info->reassem_bpid = 
				cpu_to_be32(ctx_bpid);
			ipv6_reassly_tbl_info->ip_reassem_info->reassem_bsize = 
				cpu_to_be32(ctx_bsize);
			ipv6_reassly_tbl_info->ip_reassem_info->frag_bpid = 
				cpu_to_be32(frag_bpid);
			ipv6_reassly_tbl_info->ip_reassem_info->frag_bsize = 
				cpu_to_be32(frag_bsize);
			ii = cpu_to_be32(ipv4_reassly_tbl_info->ip_reassem_info->timeout_val);  
			ipv6_reassly_tbl_info->ip_reassem_info->timeout_val = 
				cpu_to_be32(ii / ipr_timer_freq);  
			ipv6_reassly_tbl_info->ip_reassem_info->txc_fqid = 
				cpu_to_be32(txc_fqid);
			break;


		default:
			printk("%s::invalid frag table type %d\n", __FUNCTION__, type);
			return -1;
	}
	return 0;
}

static void display_debug_info(struct ip_reassembly_params *params)
{
	char *sbuf;
	char *buf;
	uint8_t *ptr;
	uint32_t ii;

	buf = (char *)kzalloc(1024, GFP_KERNEL);
	sbuf = buf;
	if (sbuf) {	
		sbuf += sprintf(sbuf, "debug info::\n");
		ptr = ((uint8_t *)params + sizeof(struct ip_reassembly_params));
		for (ii = 0; ii < REASSM_DEBUG_SIZE; ii++) {
			if ((ii % 16) == 15)
				sbuf += sprintf(sbuf, "%02x\n", *ptr);
			else
				sbuf += sprintf(sbuf, "%02x ", *ptr);
			ptr++;
		}
	}
	sbuf += sprintf(sbuf, "\n");
	printk("%s", buf);
	kfree(buf);	
}

static void display_reassem_params(uint32_t type)
{
	struct ip_reassembly_params *params;

	switch (type)
	{
		case IPV4_REASSM_TABLE:
			if (!ipv4_reassly_tbl_info) {
				printk("%s::ipv4 frag table not setup\n", __FUNCTION__);
				return;
			}
			printk("ipv4 reassembly params::");
			params = ipv4_reassly_tbl_info->ip_reassem_info; 
			break;
		case IPV6_REASSM_TABLE:
			if (!ipv6_reassly_tbl_info) {
				printk("%s::ipv6 frag table not setup\n", __FUNCTION__);
				return;
			}
			printk("ipv6 reassembly params::");
			params = ipv6_reassly_tbl_info->ip_reassem_info; 
			break;

		default:
			printk("%s::invalid frag table type %d\n", __FUNCTION__, type);
			return;
	}
	printk("%p\n", params);
	{
		uint64_t ii;

		ii = ((uint64_t)(cpu_to_be32(params->table_base_hi)) << 32);
		ii |= cpu_to_be32(params->table_base_lo);
		printk("table base\t%p\n", (void *)ii);
	}
	printk("type\t%x\n", cpu_to_be32(params->type));
	printk("ipr_timer\t%d\n",
			cpu_to_be32(params->ipr_timer));
	printk("timeoutval\t%d\n",
			cpu_to_be32(params->timeout_val));
	printk("timeout_fqid\t%x(%d)\n",
			cpu_to_be32(params->timeout_fqid),
			cpu_to_be32(params->timeout_fqid));
	printk("max_frags\t%d\n",
			cpu_to_be32(params->max_frags));
	printk("min_frag_size\t%d\n",
			cpu_to_be32(params->min_frag_size));
	printk("max_con_reassm\t%d\n",
			cpu_to_be32(params->max_con_reassm));
	printk("reassem_bpid\t%d\n",
			cpu_to_be32(params->reassem_bpid));
	printk("table mask\t%d\n",
			cpu_to_be32(params->table_mask));
	printk("reassly_dbg\t%x\n",
			cpu_to_be32(params->reassly_dbg));
	printk("timer_info\t%x\n",
			cpu_to_be32(params->timer_tnum));
	printk("txc_fqid\t0x%x\n",
			(cpu_to_be32(params->txc_fqid) & 0xffffff));
#if 0
	{
		uint32_t ii;
		uint8_t *ptr;

		ptr = (uint8_t *)params;
		for (ii = 0; ii < sizeof(struct ip_reassembly_params); ii++) {
			if (ii % 16 == 15)
				printk("%02x\n", *(ptr + ii));
			else
				printk("%02x ", *(ptr + ii));
		}
		printk("\n");
	}
#endif
}

int get_ip_reassem_info(uint32_t type, struct ip_reassembly_info *info)
{
	struct ip_reassembly_params *params;

	switch (type)
	{
		case IPV4_REASSM_TABLE:
			if (!ipv4_reassly_tbl_info) {
				printk("%s::ipv4 frag table not setup\n", __FUNCTION__);
				return -1;
			}
			params = ipv4_reassly_tbl_info->ip_reassem_info; 
			break;
		case IPV6_REASSM_TABLE:
			if (!ipv6_reassly_tbl_info) {
				printk("%s::ipv6 frag table not setup\n", __FUNCTION__);
				return -1;
			}
			params = ipv6_reassly_tbl_info->ip_reassem_info; 
			break;
		default:
			printk("%s::invalid frag table type %d\n", __FUNCTION__, type);
			return -1;
	}
	info->num_frag_pkts = params->stats.num_frag_pkts;
	info->num_reassemblies = params->stats.num_reassemblies;
	info->num_completed_reassly = params->stats.num_completed_reassly;
	info->num_sess_matches = params->stats.num_sess_matches;
	info->num_frags_too_small = params->stats.num_frags_too_small;
	info->num_reassm_timeouts = params->stats.num_reassm_timeouts;
	info->num_overlapping_frags = params->stats.num_overlapping_frags;
	info->num_too_many_frags = params->stats.num_too_many_frags;
	info->num_failed_bufallocs = params->stats.num_failed_bufallocs;
	info->num_failed_ctxallocs = params->stats.num_failed_ctxallocs;
	info->num_fatal_errors = params->stats.num_fatal_errors;
	info->num_failed_ctxdeallocs = params->stats.num_failed_ctxdeallocs;
	info->table_mask = params->table_mask;
	info->ipr_timer = params->ipr_timer;
	info->timeout_val = params->timeout_val;
	info->timeout_fqid = params->timeout_fqid;
	info->max_frags = params->max_frags; 
	info->min_frag_size = params->min_frag_size;
	info->max_con_reassm = params->max_con_reassm;
	info->reassem_bpid = params->reassem_bpid;
	info->reassem_bsize = params->reassem_bsize;
	info->frag_bpid = params->frag_bpid; 
	info->frag_bsize = params->frag_bsize;
	info->timer_tnum = params->timer_tnum;
	info->reassly_dbg = params->reassly_dbg;
	info->curr_sessions = params->curr_sessions;
	info->txc_fqid = params->txc_fqid; 
	display_debug_info(params);
	return 0;
}

void display_reassem_stats(uint32_t type)
{
	struct ip_reassembly_stats *stats;

	display_reassem_params(type);
	switch (type)
	{
		case IPV4_REASSM_TABLE:
			if (!ipv4_reassly_tbl_info) {
				printk("%s::ipv4 frag table not setup\n", __FUNCTION__);
				return;
			}
			stats = &ipv4_reassly_tbl_info->ip_reassem_info->stats;
			printk("ipv4 reassembly statistics::\n");
			break;
		case IPV6_REASSM_TABLE:
			if (!ipv6_reassly_tbl_info) {
				printk("%s::ipv6 frag table not setup\n", __FUNCTION__);
				return;
			}
			stats = &ipv6_reassly_tbl_info->ip_reassem_info->stats;
			printk("ipv6 reassembly statistics::\n");
			break;

		default:
			printk("%s::invalid frag table type %d\n", __FUNCTION__, type);
			return;
	}

	printk("num_frag_pkts\t%p\n",
			(void *)(cpu_to_be64(stats->num_frag_pkts)));
	printk("num_reassemblies\t%p\n",
			(void *)(cpu_to_be64(stats->num_reassemblies)));
	printk("num_completed_reassly\t%p\n",
			(void *)(cpu_to_be64(stats->num_completed_reassly)));
	printk("num_sess_matches\t%p\n",
			(void *)(cpu_to_be64(stats->num_sess_matches)));
	printk("frags too small\t%p\n",
			(void *)(cpu_to_be64(stats->num_frags_too_small)));
	printk("num_reassm_failures\t%p\n",
			(void *)(cpu_to_be64(stats->num_reassm_timeouts)));
	printk("overlapping fragments\t%p\n",
			(void *)(cpu_to_be64(stats->num_overlapping_frags)));
	printk("too many frags\t%p\n",
			(void *)(cpu_to_be64(stats->num_too_many_frags)));
	printk("num_failed_bufallocs\t%p\n",
			(void *)(cpu_to_be64(stats->num_failed_bufallocs)));
	printk("num_failed_ctxallocs\t%p\n",
			(void *)(cpu_to_be64(stats->num_failed_ctxallocs)));
	printk("reassm_count\t%p\n",
			(void *)(cpu_to_be64(stats->reassm_count)));
	printk("num_failed_ctxdeallocs\t%p\n",
			(void *)(cpu_to_be64(stats->num_failed_ctxdeallocs)));
	printk("num_fatal_errors\t%p\n",
			(void *)(cpu_to_be64(stats->num_fatal_errors)));

}
EXPORT_SYMBOL(get_ip_reassem_info);
EXPORT_SYMBOL(display_reassem_stats);
EXPORT_SYMBOL(ipv4_reassly_tbl_info);
EXPORT_SYMBOL(ipv6_reassly_tbl_info);
EXPORT_SYMBOL(ExternalHashSetReasslyPool);
EXPORT_SYMBOL(ipr_update_timestamp);
#endif
EXPORT_SYMBOL(ExternalHashTableDeleteKey); 

int32_t ExternalHashGetSECfailureStats(en_SEC_failure_stats *stats)
{
	if (!stats)
		return -1;

	stats->anti_replay_late_errs =
		GET_UINT32(en_global_muram_mem->SEC_failure_stats.anti_replay_late_errs);
		

	stats->anti_replay_replay_errs =
		GET_UINT32(en_global_muram_mem->SEC_failure_stats.anti_replay_replay_errs);
	
	stats->buff_pool_depletion_errs =
		GET_UINT32(en_global_muram_mem->SEC_failure_stats.buff_pool_depletion_errs);
	
	stats->buff_too_small_errs =
		GET_UINT32(en_global_muram_mem->SEC_failure_stats.buff_too_small_errs);
	
	stats->cmpnd_frame_read_errs =
		GET_UINT32(en_global_muram_mem->SEC_failure_stats.cmpnd_frame_read_errs);
	
	stats->cmpnd_frame_write_errs =
		GET_UINT32(en_global_muram_mem->SEC_failure_stats.cmpnd_frame_write_errs);
	
	stats->DECO_watchdog_timer_timedout_errs =
		GET_UINT32(en_global_muram_mem->SEC_failure_stats.DECO_watchdog_timer_timedout_errs);
	
	stats->DMA_errs =
		GET_UINT32(en_global_muram_mem->SEC_failure_stats.DMA_errs);
	
	stats->hw_errs =
		GET_UINT32(en_global_muram_mem->SEC_failure_stats.hw_errs);
	
	stats->icv_failures =
		GET_UINT32(en_global_muram_mem->SEC_failure_stats.icv_failures);
	
	stats->input_frame_read_errs =
		GET_UINT32(en_global_muram_mem->SEC_failure_stats.input_frame_read_errs);
	
	stats->ipsec_pad_chk_failures =
		GET_UINT32(en_global_muram_mem->SEC_failure_stats.ipsec_pad_chk_failures);

	stats->ipsec_ttl_zero_errs =
		GET_UINT32(en_global_muram_mem->SEC_failure_stats.ipsec_ttl_zero_errs);
	
	stats->other_errs =
		GET_UINT32(en_global_muram_mem->SEC_failure_stats.other_errs);
	
	stats->output_frame_length_rollover_errs =
		GET_UINT32(en_global_muram_mem->SEC_failure_stats.output_frame_length_rollover_errs);
	
	stats->output_frame_too_large_errs =
		GET_UINT32(en_global_muram_mem->SEC_failure_stats.output_frame_too_large_errs);
	
	stats->output_frame_write_errs =
		GET_UINT32(en_global_muram_mem->SEC_failure_stats.output_frame_write_errs);
	
	stats->prehdr_read_errs = 
		GET_UINT32(en_global_muram_mem->SEC_failure_stats.prehdr_read_errs);
	
	stats->protocol_format_errs =
		GET_UINT32(en_global_muram_mem->SEC_failure_stats.protocol_format_errs);
	
	stats->seq_num_overflows =
		GET_UINT32(en_global_muram_mem->SEC_failure_stats.seq_num_overflows);
	
	stats->tbl_buff_pool_depletion_errs =
		GET_UINT32(en_global_muram_mem->SEC_failure_stats.tbl_buff_pool_depletion_errs);
	
	stats->tbl_buff_too_small_errs =
		GET_UINT32(en_global_muram_mem->SEC_failure_stats.tbl_buff_too_small_errs);

	return 0;
}
EXPORT_SYMBOL(ExternalHashGetSECfailureStats);

int32_t ExternalHashResetSECfailureStats(void)
{
	if (!en_global_muram_mem)
		return -1;
	
	WRITE_UINT32(en_global_muram_mem->SEC_failure_stats.anti_replay_late_errs, 0);
		
	WRITE_UINT32(en_global_muram_mem->SEC_failure_stats.anti_replay_replay_errs, 0);
	
	WRITE_UINT32(en_global_muram_mem->SEC_failure_stats.buff_pool_depletion_errs, 0);
	
	WRITE_UINT32(en_global_muram_mem->SEC_failure_stats.buff_too_small_errs, 0);
	
	WRITE_UINT32(en_global_muram_mem->SEC_failure_stats.cmpnd_frame_read_errs, 0);
	
	WRITE_UINT32(en_global_muram_mem->SEC_failure_stats.cmpnd_frame_write_errs, 0);
	
	WRITE_UINT32(en_global_muram_mem->SEC_failure_stats.DECO_watchdog_timer_timedout_errs, 0);
	
	WRITE_UINT32(en_global_muram_mem->SEC_failure_stats.DMA_errs, 0);
	
	WRITE_UINT32(en_global_muram_mem->SEC_failure_stats.hw_errs, 0);
	
	WRITE_UINT32(en_global_muram_mem->SEC_failure_stats.icv_failures, 0);
	
	WRITE_UINT32(en_global_muram_mem->SEC_failure_stats.input_frame_read_errs, 0);
	
	WRITE_UINT32(en_global_muram_mem->SEC_failure_stats.ipsec_pad_chk_failures, 0);

	WRITE_UINT32(en_global_muram_mem->SEC_failure_stats.ipsec_ttl_zero_errs, 0);
	
	WRITE_UINT32(en_global_muram_mem->SEC_failure_stats.other_errs, 0);
	
	WRITE_UINT32(en_global_muram_mem->SEC_failure_stats.output_frame_length_rollover_errs, 0);
	
	WRITE_UINT32(en_global_muram_mem->SEC_failure_stats.output_frame_too_large_errs, 0);
	
	WRITE_UINT32(en_global_muram_mem->SEC_failure_stats.output_frame_write_errs, 0);
	
	WRITE_UINT32(en_global_muram_mem->SEC_failure_stats.prehdr_read_errs, 0);
	
	WRITE_UINT32(en_global_muram_mem->SEC_failure_stats.protocol_format_errs, 0);
	
	WRITE_UINT32(en_global_muram_mem->SEC_failure_stats.seq_num_overflows, 0);
	
	WRITE_UINT32(en_global_muram_mem->SEC_failure_stats.tbl_buff_pool_depletion_errs, 0);
	
	WRITE_UINT32(en_global_muram_mem->SEC_failure_stats.tbl_buff_too_small_errs, 0);

	return 0;
}

EXPORT_SYMBOL(ExternalHashResetSECfailureStats);

int32_t ExternalHashSetDscpVlanpcpMapCfg(en_dscp_vlanpcp_map_cfg *map)
{
	int32_t index;

	if ((!en_global_muram_mem) || (!map))
		return -1;

	for(index = 0; index <= MAX_VLAN_PCP; index++)
		WRITE_UINT8(en_global_muram_mem->dscp_vlanpcp_map.dscp_vlanpcp[index], map->dscp_vlanpcp[index]);

	return 0;
}
EXPORT_SYMBOL(ExternalHashSetDscpVlanpcpMapCfg);

int32_t ExternalHashGetDscpVlanpcpMapCfg(en_dscp_vlanpcp_map_cfg *map)
{
	int32_t index;

	if ((!en_global_muram_mem) || (!map))
		return -1;

	for(index = 0; index <= MAX_VLAN_PCP; index++)
		map->dscp_vlanpcp[index] = GET_UINT8(en_global_muram_mem->dscp_vlanpcp_map.dscp_vlanpcp[index]);

	return 0;
}
EXPORT_SYMBOL(ExternalHashGetDscpVlanpcpMapCfg);

#endif
