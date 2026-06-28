// SPDX-License-Identifier: GPL-2.0+
/*
 * Copyright 2018 NXP
 */


/******************************************************************************
 @File          fm_cc_dbg.h

 @Description   FM Coarse Classifier debug
 *//***************************************************************************/

#include <linux/slab.h>
#include "fm_pcd_ext.h"
//enable prints from FMD
//#define FM_CC_DEBUG 		1

//enable prints from host commands
#define FM_CC_MURAM_DEBUG 	1
	
//table descriptor structure
struct generic_5_off_ic_gmask {
	union {
		struct {
			uint32_t cc_adbase:24;
			uint32_t key_length:6;
			uint32_t type:2;
		}__attribute__((packed));
		uint32_t word_0;
	};
	union {
		struct {
			uint32_t match_table_ptr:23;
			uint32_t LM:1;
			uint32_t match_table_entries_num:8;	
		}__attribute__((packed));
		uint32_t word_1;
	};
	union {	
		struct {
			uint32_t op_code:8;
			uint32_t reserved:8;
			uint32_t offset:8;
			uint32_t offset_from_parse_result:8;
		}__attribute__((packed));
		uint32_t word_2;
	};
	uint32_t age_mask;
}__attribute__((packed));

//new class result descriptor structure
struct new_class_res_desc {
	union {
		uint32_t word_0;
		struct {
			uint32_t fqid:24;
			uint32_t rspid:6;
			uint32_t type:2;
		}__attribute__((packed));
	};
	union {
		uint32_t word_1;
		struct {
			uint32_t next_ad_index:16;	
			uint32_t policer_profile:8;	
			uint32_t vspe:1;	
			uint32_t resv:1;	
			uint32_t nenq:1;	
			uint32_t cwd:1;	
			uint32_t nl:1;	
			uint32_t fwd:1;	
			uint32_t ebad:1;	
			uint32_t ebd:1;	

		}__attribute__((packed));
	};
	union {
		uint32_t word_2;
		struct {
			uint32_t fpm_nia:24;
			uint32_t ovom:1;
			uint32_t no_cspen:1;
			uint32_t resv_2:1;
			uint32_t fr:1;
			uint32_t resv_1:1;
			uint32_t naden:1;
			uint32_t stats_en:1;
			uint32_t extended_mode:1;
		}__attribute__((packed));
	};
	uint32_t word_3;
};

//statistics table descriptor structure
struct stats_table_desc {
	union {
		uint32_t word_0;
		struct {
			uint32_t stats_prof_tbl_addr:24;
			uint32_t reserved:8;
			uint32_t type:2;
		}__attribute__((packed));
	};
	uint32_t word_1;
	union {
		uint32_t word_2;
		struct {
			uint32_t opcode:8;
			uint32_t reserved_1:5;
			uint32_t cond_en:1;
			uint32_t flr_en:1;
			uint32_t nad_en:1;
			uint32_t next_ad_index:16;
		}__attribute__((packed));
	};
	union {
		uint32_t word_3;
		struct {
			uint32_t stats_tbl_addr:24;
			uint32_t reserved_2:8;
		}__attribute__((packed));
	};
};

//keep classification results descriptor structure
struct keep_class_res_desc_ad {
	union {
		struct {
			uint32_t reserved_1:29;
			uint32_t PD:1;
			uint32_t type:2;
		}__attribute__((packed));
		uint32_t word_0;
	};
	union {
		struct {
			uint32_t next_action_desc_index:16;
			uint32_t reserved_2:16;
		}__attribute__((packed));
		uint32_t word_1;
	};
	union {
		struct {
			uint32_t fpm_nia:24;
			uint32_t reserved_3:1;
			uint32_t no_vspe:1;
			uint32_t reserved_4:3;
			uint32_t naden:1;
			uint32_t statistics_enable:1;
			uint32_t extended_mode:1;
		}__attribute__((packed));
		uint32_t word_2;
	};
	uint32_t statistic_counter;
};

//header modification table desc
struct hmt_desc {
	union {
		struct {
			uint32_t reserved_2:21;
			uint32_t naden:1;
			uint32_t pahm:1;
			uint32_t reserved_1:7;
			uint32_t type:2;
		}__attribute__((packed));
		uint32_t word_0;
	};
	union {
		struct {
			uint32_t internal_hmt_ptr:24;
			uint32_t reserved_3:8;
		}__attribute__((packed));
		uint32_t word_1;
	};
	union {
		struct {
			uint32_t opcode:8;
			uint32_t reserved_4:8;
			uint32_t nextdescindex:16;
		}__attribute__((packed));
		uint32_t word_2;
	};
	uint32_t word_3;
};

//header removal header manipulation command
struct hdr_removal_hmc {
	union {
		uint32_t word;
		struct {
			uint32_t hdrrmvoffset:8;
			uint32_t hdrrmvsize:8;
			uint32_t reserved:7;
			uint32_t last:1;
			uint32_t opcode:8;
		}__attribute__((packed));
	};
};

//header insert header manipulation command
struct hdr_insert_hmc {
	union {
		uint32_t word;
		struct {
			uint32_t hdrinsoffset:8;
			uint32_t hdrinssize:8;
			uint32_t reserved:7;
			uint32_t last:1;
			uint32_t opcode:8;
		}__attribute__((packed));
	};
};

//header replace header manipulation command
#define hdr_replace_hmc hdr_insert_hmc

//internal header insert header manipulation command
struct internal_hdr_insert_hmc {
	union {
		uint32_t word_0;
		struct {
			uint32_t hdrinsoffset:8;
			uint32_t hdrinssize:8;
			uint32_t reserved:7;
			uint32_t last:1;
			uint32_t opcode:8;
		}__attribute__((packed));
	};
	union {
		uint32_t word_1;
		struct {
			uint32_t hdrinsptr:24;
			uint32_t reserved_1:8;
		}__attribute__((packed));
	};
};
//internal header replace header manipulation command
#define internal_hdr_replace_hmc internal_hdr_insert_hmc

//protocol specific header removal header manipulation command
struct proto_specific_hdr_removal_hmc {
	union {
                uint32_t word;
                struct {
                        uint32_t reserved_1:16;
                        uint32_t protospec_hdr_rmv_mode:4;
                        uint32_t reserved:3;
                        uint32_t last:1;
                        uint32_t opcode:8;
                }__attribute__((packed));
        };
};

//protocol specific header insert header manipulation command
struct proto_specific_hdr_insert_hmc {
        union {
                uint32_t word_0;
                struct {
                        uint32_t reserved_1:16;
                        uint32_t protospec_hdr_ins_mode:4;
                        uint32_t reserved:3;
                        uint32_t last:1;
                        uint32_t opcode:8;
                }__attribute__((packed));
        };
        union {
                uint32_t word_1;
                struct {
                        uint32_t inthdrptr:24;
                        uint32_t hdrinssize:8;
                }__attribute__((packed));
        };


};

//vlan priority update header manipulation command
struct vlan_priority_update_hmc {
        union {
                uint32_t word_0;
	        struct {
                        uint32_t vpri_def_value:3;
                        uint32_t reserved_1:13;
                        uint32_t vprihdr_rep_mode:3;
                        uint32_t reserved:4;
                        uint32_t last:1;
                        uint32_t opcode:8;
                }__attribute__((packed));
        };
        union {
                uint32_t word_1;
	        struct {
			uint32_t int_hdr_rep_ptr:24;
			uint32_t reserved_2:8;
                }__attribute__((packed));
        };
};

//local ipv4 update header manipulation command
struct local_ipv4_update_hmc {
        union {
                uint32_t word;
                struct {
                        uint32_t ip_ttl:1;
                        uint32_t ip_tos_mode:2;
                        uint32_t reserved_1:2;
                        uint32_t ip_dst:1;
                        uint32_t ip_src:1;
                        uint32_t ip_id_mode:1;
                        uint32_t ip_tos:8;
                        uint32_t reserved:7;
                        uint32_t last:1;
                        uint32_t opcode:8;
                }__attribute__((packed));
        };
};


//local tcp udp update header manipulation command
struct local_tcp_udp_update_hmc {
        union {
                uint32_t word_0;
                struct {
                        uint32_t reserved:14;
                        uint32_t tcp_udp_dst:1;
                        uint32_t tcp_udp_src:1;
                        uint32_t reserved_1:7;
                        uint32_t last:1;
                        uint32_t opcode:8;
                }__attribute__((packed));
        };
        union {
                uint32_t word_1;
		struct {
			uint32_t dst_port:16;
			uint32_t src_port:16;
		}__attribute__((packed));
	};
};


//ipv6 header update header manipulation command
struct ipv6_update_hmc {
	union {
		uint32_t word;
		struct {
			int32_t ip_hop_limit:1;
			uint32_t ip_traffic_class_mode:2;
			uint32_t reserved_1:3;
			uint32_t ipdst:1;
			uint32_t ipsrc:1;
			uint32_t iptraffic_class:8;
			uint32_t reserved:7;
			uint32_t last:1;
			uint32_t opcode:8;
		}__attribute__((packed));;
	};
};

//internal ipheader replace header manipulation command
struct internal_iphdr_replace_hmc {
	union {
		uint32_t word_0;
		struct {
			uint32_t reserved:16;
			uint32_t inl3smode:2;
			uint32_t reserved_1:3;
			int32_t ttl_hop_limit:1;
			uint32_t ipid_mode:1;
			uint32_t last:1;
			uint32_t opcode:8;
		}__attribute__((packed));
	};
	union {
		uint32_t word_1;
		struct {
			uint32_t l3hdr_ptr:24;
			uint32_t l3hdr_ins_size:8;
		}__attribute__((packed));;
	};
	union {
		uint32_t word_2;
		struct {
			uint32_t id_hdr_ptr:24;
			uint32_t reserved_2:8;
		}__attribute__((packed));
	};
};

extern void *FmMurambaseAddr;
static void display_pcd_cc_ad(void *ad);
void display_cc_node(void *handle, const char *func_name);
static void display_generic_5_off_ic_gmask(void *ptr);

static void display_buf_data(void *dataptr, uint32_t size)
{
	uint32_t ii;
	uint8_t *ptr;

	ptr = (uint8_t *)dataptr;	
	for (ii = 0; ii < size; ii++) {
		if ((ii % 16) == 15) 
			printk("%02x\n", *ptr);
		else
			printk("%02x ", *ptr);
		ptr++;
	}
        if (ii % 16)
		printk("\n");
}

static inline uint32_t swap_word(uint32_t *ptr)
{
	uint32_t val;
	val =  ((*ptr >> 24) | (*ptr << 24) | 
			((*ptr >> 8) & 0x0000ff00) |
			((*ptr << 8) & 0xff0000));
	return val;
}

#ifdef FM_CC_DEBUG 
static void display_global_mask(t_FmPcdCcNode *ccnode)
{
	printk(KERN_CRIT "globalmask \t%p\n, size \t%d\n", 
		ccnode->p_GlblMask, ccnode->glblMaskSize);
	if (ccnode->p_GlblMask) {
		display_buf_data (ccnode->p_GlblMask, ccnode->glblMaskSize);
	}
}
	
static void display_keymatch_table(t_FmPcdCcNode *ccnode)
{
	
	uint32_t ii;
	uint32_t keysize;
	uint8_t *ptr;
	t_FmPcd *p_FmPcd;

	p_FmPcd = (t_FmPcd *)ccnode->h_FmPcd;
	printk(KERN_CRIT "keymatchtable \t%p\n", ccnode->h_KeysMatchTable);
	ii = (uint32_t)(XX_VirtToPhys(ccnode->h_KeysMatchTable) - 
		p_FmPcd->physicalMuramBase);
	printk(KERN_CRIT "keymatchtable in muram %08x\n", ii);
	printk(KERN_CRIT "keysMatchTableMaxSize \t%d\n", 
			ccnode->keysMatchTableMaxSize);
	printk(KERN_CRIT "maxNumOfKeys \t%d\n", ccnode->maxNumOfKeys);
	if (ccnode->lclMask)
		printk(KERN_CRIT "local mask enabled\n");
	else
		printk(KERN_CRIT "No local mask\n");
	keysize = (ccnode->ccKeySizeAccExtraction * sizeof(uint8_t));
	printk(KERN_CRIT "keymatchtable in muram %08x, keysize %d, numkeys %d\n", 
			ii, keysize, ccnode->numOfKeys);
	ptr = (uint8_t *)ccnode->h_KeysMatchTable;
	for (ii = 0; ii < ccnode->numOfKeys; ii++) {
		printk("key %d::\n", ii);
		display_buf_data((void *)ptr, keysize); 
		if (ccnode->lclMask)
			ptr += (2 * keysize);
		else
			ptr += keysize;
	}
}

static void disp_ht_params(struct t_FmPcdHashTableParams *params)
{
	t_FmPcdCcNextEngineParams *missparams;
	printk(KERN_CRIT "maxNumOfKeys\t%d\n", params->maxNumOfKeys);
	printk(KERN_CRIT "statisticsMode\t%d\n", params->statisticsMode);
	printk(KERN_CRIT "kgHashShift\t%d\n", params->kgHashShift);
	printk(KERN_CRIT "hashResMask\t%d\n", params->hashResMask);
	printk(KERN_CRIT "hashShift\t%d\n", params->hashShift);
	printk(KERN_CRIT "matchKeySize\t%d\n", params->matchKeySize);
	missparams = &params->ccNextEngineParamsForMiss;
	printk(KERN_CRIT "h_Manip\t%p\n", missparams->h_Manip);
	printk(KERN_CRIT "statisticsEn\t%d\n", missparams->statisticsEn);
	switch(missparams->nextEngine) {
		case e_FM_PCD_CC:
			printk(KERN_CRIT "next eng - e_FM_PCD_CC\n"); 
			printk(KERN_CRIT "next ccnode %p\n",
				missparams->params.ccParams.h_CcNode);
			break; 
		case e_FM_PCD_PLCR:
			printk(KERN_CRIT "next eng - e_FM_PCD_PLCR\n"); 
			printk(KERN_CRIT "overrideParams %d\n",
				missparams->params.plcrParams.overrideParams);
			printk(KERN_CRIT "sharedProfile %d\n",
				missparams->params.plcrParams.sharedProfile);
			printk(KERN_CRIT "newFqid %d\n",
				missparams->params.plcrParams.newFqid);
			printk(KERN_CRIT "newRelativeStorageProfileId %d\n",
			missparams->params.plcrParams.newRelativeStorageProfileId);
			printk(KERN_CRIT "newFqid %d\n",
				missparams->params.plcrParams.newFqid);
			break; 
		case e_FM_PCD_KG:
			printk(KERN_CRIT "next eng - e_FM_PCD_KG\n"); 
			printk(KERN_CRIT "overrideFqid %d\n",
				missparams->params.kgParams.overrideFqid);
			printk(KERN_CRIT "newFqid %d\n",
				missparams->params.kgParams.newFqid);
			printk(KERN_CRIT "newRelativeStorageProfileId %d\n",
		         missparams->params.kgParams.newRelativeStorageProfileId);
			printk(KERN_CRIT "h_DirectScheme %p\n",
				missparams->params.kgParams.h_DirectScheme);
			break; 
		case e_FM_PCD_PRS:
			printk(KERN_CRIT "next eng - e_FM_PCD_PRS\n"); 
			break; 
		case e_FM_PCD_FR:
			printk(KERN_CRIT "next eng - e_FM_PCD_FR\n"); 
			break; 
		case e_FM_PCD_HASH:
			printk(KERN_CRIT "next eng - e_FM_PCD_HASH\n"); 
			break; 
		case e_FM_PCD_DONE:
			printk(KERN_CRIT "next eng - e_FM_PCD_DONE\n"); 
			printk(KERN_CRIT "action %d\n",
				missparams->params.enqueueParams.action);
			printk(KERN_CRIT "overrideFqid %d\n",
				missparams->params.enqueueParams.overrideFqid);
			printk(KERN_CRIT "newFqid %d\n",
				missparams->params.enqueueParams.newFqid);
			printk(KERN_CRIT "newRelativeStorageProfileId %d\n",
			missparams->params.enqueueParams.newRelativeStorageProfileId);
			break;
		default:
			printk(KERN_CRIT "next eng %d is invalid\n",
				missparams->nextEngine); 
			break;
	}
}

static void display_adtable(t_FmPcdCcNode *ccnode)
{
	uint32_t ii;
	t_FmPcd *p_FmPcd;
	uint8_t *ptr;

	p_FmPcd = (t_FmPcd *)ccnode->h_FmPcd;
	printk(KERN_CRIT "adtable \t%p\n", ccnode->h_AdTable);
	ii = (uint32_t)((XX_VirtToPhys(ccnode->h_AdTable) - 
		p_FmPcd->physicalMuramBase));
	printk(KERN_CRIT "adtable in muram %08x\n", ii);
	ptr = (uint8_t *)ccnode->h_AdTable;
	for (ii = 0; ii < ccnode->numOfKeys; ii++) {
		printk("ADENTRY %d::\n", ii);
		display_pcd_cc_ad((uint32_t *)ptr);
		ptr += FM_PCD_CC_AD_ENTRY_SIZE;
	}
}

static void display_enq_params(t_FmPcdCcNextEnqueueParams *enq)
{
	printk(KERN_CRIT "action %08x\n", enq->action);
	if (enq->overrideFqid)
		printk(KERN_CRIT "Fqid override, new fqid %d(%08x)\n", 
			enq->newFqid, enq->newFqid);
	else 
		printk(KERN_CRIT "No Fqid override\n");
	printk(KERN_CRIT "newRelativeStorageProfileId %08x\n", 
			enq->newRelativeStorageProfileId);
}

static void display_kg_params(t_FmPcdCcNextKgParams *kg)
{
	if (kg->overrideFqid)
		printk(KERN_CRIT "Fqid override, new fqid %d(%08x)\n", 
			kg->newFqid, kg->newFqid);
	else 
		printk(KERN_CRIT "No Fqid override\n");
	printk(KERN_CRIT "newRelativeStorageProfileId %08x\n", 
			kg->newRelativeStorageProfileId);
	printk(KERN_CRIT "newscheme handle %p\n", 
			kg->h_DirectScheme);
}

static void display_cc_params(t_FmPcdCcNextCcParams *cc)
{
	printk(KERN_CRIT "h_CcNode %p::\n", cc->h_CcNode);
	display_cc_node((void *)cc->h_CcNode, __FUNCTION__);
	
}

static void display_plcr_params(t_FmPcdCcNextPlcrParams *plcr)
{
	printk(KERN_CRIT "%s::fill this... plcr %p!!!!\n", __FUNCTION__, plcr);
}

static void display_next_engine(t_FmPcdCcNextEngineParams *nexteng)
{
	printk(KERN_CRIT "nextEngine %08x -> ", nexteng->nextEngine);

	switch (nexteng->nextEngine) {
		case e_FM_PCD_DONE:
			{
				t_FmPcdCcNextEnqueueParams *enq;
				printk(KERN_CRIT "pcd done\n");
				enq = (t_FmPcdCcNextEnqueueParams *)
					&nexteng->params.enqueueParams;
				display_enq_params(enq);
				break;
			}
		case e_FM_PCD_KG:
			{
				t_FmPcdCcNextKgParams *kg;
				printk(KERN_CRIT "keygen\n");
				kg = &nexteng->params.kgParams;
				display_kg_params(kg);
				break;
			}
		case e_FM_PCD_CC:
			{
				t_FmPcdCcNextCcParams *cc;
				printk(KERN_CRIT "coarse classification\n");
				cc = &nexteng->params.ccParams;
				display_cc_params(cc);
				break;
			}
		case e_FM_PCD_PLCR:
			{
				t_FmPcdCcNextPlcrParams *plcr;
				printk(KERN_CRIT "policer\n");
				plcr = &nexteng->params.plcrParams;
				display_plcr_params(plcr);
				break;
			}
		case e_FM_PCD_PRS:
			{
				printk(KERN_CRIT "parser\n");
				break;
			}
		default:
			printk(KERN_CRIT "unknown type\n");
			break;
	}
	printk(KERN_CRIT "h_Manip %p\n", nexteng->h_Manip);
	printk(KERN_CRIT "statisticsEn %d\n", nexteng->statisticsEn);

}

static void display_next_engine_param(t_FmPcdCcNode *ccnode)
{
	uint32_t ii;
	t_FmPcdCcKeyAndNextEngineParams *nextengparams;

	nextengparams = &ccnode->keyAndNextEngineParams[0];	
	for (ii = 0; ii < ccnode->numOfKeys; ii++) {
		printk(KERN_CRIT "next eng param id %d\n", ii);
		printk("key %d::", ii);
		display_buf_data (&nextengparams->key[0], 
			ccnode->ccKeySizeAccExtraction);
		printk("mask %d::", ii);
		display_buf_data (&nextengparams->mask[0], 
			ccnode->ccKeySizeAccExtraction);
		printk(KERN_CRIT "requiredAction %x, shadowAction %x\n",
			nextengparams->requiredAction,
			nextengparams->shadowAction);
		display_next_engine(&nextengparams->nextEngineParams);
		nextengparams++;
	}
}

void display_cc_node(void *handle, const char *func_name)
{
	t_FmPcdCcNode *ccnode;

	ccnode = (t_FmPcdCcNode *)handle;
	printk(KERN_CRIT ">>>>>>>>\n%s::ccnode %p\n", func_name, ccnode);
	if (!ccnode->externalHash) {
		if (ccnode->maskSupport)
			printk(KERN_CRIT "mask support enabled\n");
		else
			printk(KERN_CRIT "mask support disabled\n");
		printk(KERN_CRIT "statisticsMode \t%d\n", ccnode->statisticsMode);
		printk(KERN_CRIT "countersArraySize \t%d\n", ccnode->countersArraySize);
		if (ccnode->isHashBucket)
			printk(KERN_CRIT "node is a bucket of a hash table\n");
		if (ccnode->glblMaskUpdated) {
			printk(KERN_CRIT "global mask updated\n");
			display_global_mask(ccnode);
		} else
			printk(KERN_CRIT "global mask not updated\n");
		printk(KERN_CRIT "parseCode \t%d\n", ccnode->parseCode);
		printk(KERN_CRIT "offset \t%d\n", ccnode->offset);
		printk(KERN_CRIT "prsArrayOffset \t%d\n", ccnode->prsArrayOffset);
		printk(KERN_CRIT "ccKeySizeAccExtraction \t%d\n", ccnode->ccKeySizeAccExtraction);
		printk(KERN_CRIT "sizeOfExtraction \t%d\n", ccnode->sizeOfExtraction);

		printk(KERN_CRIT "shadowAction %08x\n", ccnode->shadowAction);
		printk(KERN_CRIT "userSizeOfExtraction %08x\n", ccnode->userSizeOfExtraction);
		printk(KERN_CRIT "userOffset %08x\n", ccnode->userOffset);
		printk(KERN_CRIT "kgHashShift %08x\n", ccnode->kgHashShift);

		if (ccnode->h_KeysMatchTable) {
			display_keymatch_table(ccnode);
		} else {
			printk(KERN_CRIT "No keymatch table\n");
		}
		if (ccnode->h_AdTable) {
			display_adtable(ccnode);
		} else {
			printk(KERN_CRIT "No ad table\n");
		}
		display_next_engine_param(ccnode);
		printk(KERN_CRIT "not ext hash >???\n");
	} else {
		printk(KERN_CRIT "ext hash\n");
	}
}
#else
#define display_cc_node(x, y)
#define disp_ht_params(x)
#endif


#ifdef FM_CC_MURAM_DEBUG 
static void *display_new_class_res_desc(void *ad)
{
	struct new_class_res_desc desc; 
	uint32_t *ptr;

	ptr = (uint32_t *)ad;
	desc.word_0 = swap_word(ptr);
	desc.word_1 = swap_word(ptr + 1);
	desc.word_2 = swap_word(ptr + 2);
	desc.word_3 = swap_word(ptr + 3);
	printk("rspid %x, ", desc.rspid);
	printk("fqid %x, ", desc.fqid);
	printk("policer profile %x, ", desc.policer_profile);
	printk("Fpm_nia %x\n", desc.fpm_nia);
	printk("operational mode bits:: ebd %d, ebad %d, fwd %d, nl %d, cwd %d, nenq %d, vspe %d\n", 
		desc.ebd,
		desc.ebad,
		desc.fwd,
		desc.nl,
		desc.cwd,
		desc.nenq,
		desc.vspe);
	printk("ext mode %d, stats en %d, naden %d, FR %d, NO_CSPEN %d, OVOM %d\n",
		desc.extended_mode,
		desc.stats_en,
		desc.naden,
		desc.fr,
		desc.no_cspen,
		desc.ovom);
	printk("Frindex/stats counter %x\n", desc.word_3);
	if (desc.extended_mode && desc.naden) {
		printk("next action desc %x\n", desc.next_ad_index);
		return ((void *)((uint8_t *)FmMurambaseAddr + (desc.next_ad_index << 4)));	
	}
	return NULL;  
}


static void display_stats_ad(uint32_t *ptr)
{

	struct stats_table_desc desc;

	desc.word_0 = swap_word(ptr);
	desc.word_1 = swap_word(ptr + 1);
	desc.word_2 = swap_word(ptr + 2);
	desc.word_3 = swap_word(ptr + 3);
	printk("SSSSSSSS\nstats table descriptor %p::\n", ptr);
	printk("cond_en %d\n", desc.cond_en);
	printk("stats_tbl_addr %x\n", desc.stats_tbl_addr);
	if (desc.flr_en) {
		printk("flr_stats_prof_tbl_addr %x\n", desc.stats_prof_tbl_addr);
	}
	if (desc.nad_en) {
		void *ad;

		printk("next action desc %x\n", desc.next_ad_index);
		ad = (void *)((uint8_t *)FmMurambaseAddr + 
				(desc.next_ad_index << 4));
		printk("SSSSSSSS\n");
		display_pcd_cc_ad(ad);
	} else
		printk("SSSSSSSS\n");
}


static uint32_t *display_hdr_removal_hmc(uint32_t *ptr)
{
	struct hdr_removal_hmc desc;

	printk("HMC::header removal command:: muram ptr %x\n", 
		(uint32_t)((uint8_t *)ptr - (uint8_t *)FmMurambaseAddr));
	display_buf_data(ptr, sizeof(struct hdr_removal_hmc));
	desc.word = swap_word(ptr);
	printk("header removal offset %d, ", desc.hdrrmvoffset);
	printk("header removal size %d\n", desc.hdrrmvsize);
	ptr += (sizeof(struct hdr_removal_hmc) / sizeof(uint32_t));
	return ptr;
}

static uint32_t *display_hdr_insert_hmc(uint32_t *ptr)
{
	struct hdr_insert_hmc desc;

	printk("HMC::local header insert command:: muram ptr %x\n", 
		(uint32_t)((uint8_t *)ptr - (uint8_t *)FmMurambaseAddr));
	desc.word = swap_word(ptr);
	printk("header insert offset %d, ", desc.hdrinsoffset);
	printk("header insert size %d\n", desc.hdrinssize);
	printk("header to insert::\n");
        display_buf_data((void *)((uint32_t *)&desc + 1),
		desc.hdrinssize);
	ptr += (sizeof(struct hdr_insert_hmc) + 
		(desc.hdrinssize / sizeof(uint32_t)));
	return ptr;
}


static uint32_t *display_internal_hdr_insert_hmc(uint32_t *ptr)
{
	struct internal_hdr_insert_hmc desc;

	printk("HMC::internal header insert command:: muram ptr %x\n", 
		(uint32_t)((uint8_t *)ptr - (uint8_t *)FmMurambaseAddr));
	desc.word_0 = swap_word(ptr);
	desc.word_1 = swap_word(ptr + 1);
	printk("header insert offset %d, ", desc.hdrinsoffset);
	printk("header insert size %d\n", desc.hdrinssize);
	printk("header ins ptr %d\n", desc.hdrinsptr);
	printk("header to insert::\n");
        display_buf_data(((void *)((uint8_t *)FmMurambaseAddr + 
			desc.hdrinsptr)), desc.hdrinssize);
	ptr += (sizeof(struct internal_hdr_insert_hmc) / sizeof(uint32_t));
	return ptr;
}

static uint32_t *display_hdr_replace_hmc(uint32_t *ptr)
{
	struct hdr_replace_hmc desc;

	printk("HMC::local header replace command:: muram ptr %x\n", 
		(uint32_t)((uint8_t *)ptr - (uint8_t *)FmMurambaseAddr));
	desc.word = swap_word(ptr);
	printk("header replace offset %d, ", desc.hdrinsoffset);
	printk("header replace size %d\n", desc.hdrinssize);
	printk("header to replace::\n");
        display_buf_data((void *)((uint8_t *)ptr + 
		sizeof(struct hdr_replace_hmc)),
		desc.hdrinssize);
	ptr += ((sizeof(struct hdr_replace_hmc) + desc.hdrinssize) 
			/ sizeof(uint32_t));
	return ptr;
}

static uint32_t *display_internal_hdr_replace_hmc(uint32_t *ptr)
{
        struct internal_hdr_replace_hmc desc;

        printk("HMC::internal header replace command:: muram ptr %x\n", 
		(uint32_t)((uint8_t *)ptr - (uint8_t *)FmMurambaseAddr));
        desc.word_0 = swap_word(ptr);
        desc.word_1 = swap_word(ptr + 1);
        printk("header replace offset %d, ", desc.hdrinsoffset);
        printk("header replace size %d\n", desc.hdrinssize);
        printk("header replace ptr %d\n", desc.hdrinsptr);
        printk("header to replace::\n");
        display_buf_data(((void *)((uint8_t *)FmMurambaseAddr +
                        desc.hdrinsptr)), desc.hdrinssize);
        ptr += (sizeof(struct internal_hdr_replace_hmc) / sizeof(uint32_t));
        return ptr;
}

static uint32_t *display_protosprc_hdr_remove_hmc(uint32_t *ptr)
{
        struct proto_specific_hdr_removal_hmc desc;

        printk("HMC::protocol specific header removal command:: muram ptr %x\n", 
		(uint32_t)((uint8_t *)ptr - (uint8_t *)FmMurambaseAddr));
        desc.word = swap_word(ptr);
	switch (desc.protospec_hdr_rmv_mode) {
		case 0:
			printk("remove Ethernet/802.3 MAC header\n");
			break;
		case 1:
			printk("remove stacked QTags\n");
			break;
		case 2:
			printk("remove Ethernet/802.3 MAC header + MPLS hdr\n");
			break;
		case 3:
			printk("remove MPLS hdr\n");
			break;
		default:
			printk("reserved remove mode %x\n",
				desc.protospec_hdr_rmv_mode);
			break;
	}
        ptr += (sizeof(struct proto_specific_hdr_removal_hmc) / 
		sizeof(uint32_t));
        return ptr;
}

static uint32_t *display_protosprc_hdr_insert_hmc(uint32_t *ptr)
{
        struct proto_specific_hdr_insert_hmc desc;

        printk("HMC::protocol specific header insert command:: muram ptr %x\n",
		(uint32_t)((uint8_t *)ptr - (uint8_t *)FmMurambaseAddr));
        desc.word_0 = swap_word(ptr);
        desc.word_1 = swap_word(ptr + 1);
        switch (desc.protospec_hdr_ins_mode) {
                case 0:
                        printk("insert MPLS hdr\n");
                        break;
                case 1:
                        printk("insert and update MPLS hdr\n");
                        break;
		case 2:
                        printk("insert and update PPPoE hdr\n");
                        break;
                default:
                        printk("reserved insert mode %x\n",
                                desc.protospec_hdr_ins_mode);
			goto update_ptr;
        }
	display_buf_data(((void *)((uint8_t *)FmMurambaseAddr +
                        desc.inthdrptr)), desc.hdrinssize);
update_ptr:	
        ptr += (sizeof(struct proto_specific_hdr_insert_hmc) /
                sizeof(uint32_t));
        return ptr;
}

static uint32_t *display_vlan_pri_hdr_update_hmc(uint32_t *ptr)
{
        struct vlan_priority_update_hmc desc;
	uint32_t ii;
	uint32_t val;
	uint32_t *intptr;

        printk("HMC::vlan pri header update command::muram ptr %x\n",
		(uint32_t)((uint8_t *)ptr - (uint8_t *)FmMurambaseAddr));
        desc.word_0 = swap_word(ptr);
        desc.word_1 = swap_word(ptr + 1);
        printk("default vpri value %x\n", desc.vpri_def_value);
        switch (desc.vprihdr_rep_mode) {
                case 0:
                        printk("replace outermost vlan tag with defa value\n");
                        break;
                case 1:
                        printk("translate dscp to vlan pri\n");
                        printk("int hdr rep ptr %x\n", desc.int_hdr_rep_ptr);
			intptr = (uint32_t *)((uint8_t *)FmMurambaseAddr +
                        		desc.int_hdr_rep_ptr);
			for (ii = 0; ii < 16; ii++) {
				val = swap_word(intptr);	
				printk("%02x %02x %02x %02x\n",		
					(val >> 24), ((val >> 16) & 0xff),
					((val >> 8) & 0xff), (val & 0xff));
				intptr++;
			}
                        break;
                default:
                        printk("reserved vlan pri insert mode %x\n",
                                desc.vprihdr_rep_mode);
                        break;
        }
        ptr += (sizeof(struct vlan_priority_update_hmc) /
                sizeof(uint32_t));
        return ptr;
}

static uint32_t *display_ipv4_update_hmc(uint32_t *ptr)
{
        struct local_ipv4_update_hmc desc;
	uint32_t size;
        uint32_t val;
        uint32_t *intptr;

        printk("HMC::ipv4 update command:: muram ptr %x\n",
		(uint32_t)((uint8_t *)ptr - (uint8_t *)FmMurambaseAddr));
	size = (sizeof(struct local_ipv4_update_hmc) /
                sizeof(uint32_t)); 
        desc.word = swap_word(ptr);
	intptr = (ptr + 1);
 
	if (desc.ip_ttl)
		printk("decrement TTL by 1\n");
	switch (desc.ip_tos_mode) {
		case 0:
			break;
		case 1:
			printk("replace tos value with %x\n", desc.ip_tos);
			break;
		default:
			printk("reserved value %x for tos mode\n", desc.ip_tos_mode);
			break;
	}
	if (desc.ip_id_mode) {
		val = swap_word(intptr);
		printk("replace ipid field, muram ptr %x\n", (val & 0xffffff));
		intptr++;
		size++;
	}
	if (desc.ip_src) {
		val = swap_word(intptr);
		printk("replace sip field, ipv4 addr %08x\n", val);
		intptr++;
		size++;
	}
	if (desc.ip_dst) {
		val = swap_word(intptr);
		printk("replace dip field, ipv4 addr %08x\n", val);
		size++;
	}
        return (ptr + size);
}

static uint32_t *display_tcp_udp_update_hmc(uint32_t *ptr)
{
        struct local_tcp_udp_update_hmc desc;

        printk("HMC::tcp udp update command:: muram ptr %x\n", 
		(uint32_t)((uint8_t *)ptr - (uint8_t *)FmMurambaseAddr));
        desc.word_0 = swap_word(ptr);
        desc.word_1 = swap_word(ptr + 1);
	if (desc.tcp_udp_src) {
		printk("new sport value %04x\n", desc.src_port);
	}
	if (desc.tcp_udp_dst) {
		printk("new dport value %04x\n", desc.dst_port);
	}
        return (ptr + (sizeof(struct local_tcp_udp_update_hmc) /
                sizeof(uint32_t)));
}


static uint32_t *display_ipv6_update_hmc(uint32_t *ptr)
{
        struct ipv6_update_hmc desc;
	uint8_t *intptr;
	uint32_t size;

	size = (sizeof(struct ipv6_update_hmc) / sizeof(uint32_t));
	desc.word = swap_word(ptr);
	intptr = (uint8_t *)(ptr + 1);
	printk("HMC::Local IPv6 update command\n");
	if (desc.ip_hop_limit)
		printk("decrement hop limit\n");
	switch (desc.ip_traffic_class_mode) {
		case 0:	
			break;
		case 1:
			printk("replace traffic class with %x\n", desc.iptraffic_class);
			break;
		default:
			printk("reserved traffic class mode %x\n", 
				desc.ip_traffic_class_mode);
			break;
	}
	if (desc.ipsrc) {
		printk("new sip::\n");	
	        display_buf_data((void *)intptr, 16);	
		intptr += 16;
		size += 4;
	}
	if (desc.ipdst) {
		printk("new dip::\n");
	        display_buf_data((void *)intptr, 16);	
		size += 4;
	}
	return (ptr + size);
}


static uint32_t *display_internal_iphdr_replace_hmc(uint32_t *ptr)
{
        struct internal_iphdr_replace_hmc desc;

	printk("HMC::Internal IP Header Replace command::\n");
	desc.word_0 = swap_word(ptr);
	desc.word_1 = swap_word(ptr + 1);
	desc.word_2 = swap_word(ptr + 2);
	if (desc.ipid_mode) {
		printk("replace ipid, ipid ptr in muram %x\n",
			desc.id_hdr_ptr);
	}
	switch (desc.inl3smode) {
		case 0:
			printk("replace ipv4 with ipv6 hdr, size %d::\n",
				desc.l3hdr_ins_size);
        		display_buf_data((void *)(desc.l3hdr_ptr + 
					(uint8_t *)FmMurambaseAddr), 
					desc.l3hdr_ins_size);
			if (desc.ttl_hop_limit) 
				printk("duplicate ttl from hop limit and decr\n");
			break;
		case 1:
			printk("replace ipv6 with ipv4 hdr::\n");
        		display_buf_data((void *)(desc.l3hdr_ptr + 
					(uint8_t *)FmMurambaseAddr), 
					desc.l3hdr_ins_size);
			if (desc.ttl_hop_limit) 
				printk("duplicate hop limit from ttl and decr\n");
			break;
		default:
			printk("reserved insl3mode %d\n", desc.inl3smode);
			break;
	}
	return (ptr + (sizeof(struct internal_iphdr_replace_hmc) / sizeof(uint32_t)));
}


static void display_hmt_entries(uint32_t hmtd_offset)
{
	uint32_t *ptr;
	uint32_t opcode;

	ptr = (uint32_t *)(hmtd_offset + (uint8_t *)FmMurambaseAddr);
next_hmc:
	printk("%s::ptr %p\n", __FUNCTION__, ptr);
	opcode = swap_word(ptr);
	switch (opcode >> 24) {
		case 1:
			ptr = display_hdr_removal_hmc(ptr);
			break;
		case 2:
			ptr = display_hdr_insert_hmc(ptr);
			break;
		case 3:
			ptr = display_internal_hdr_insert_hmc(ptr);
			break;
		case 5:
			ptr = display_hdr_replace_hmc(ptr);
			break;
		case 6:
			ptr = display_internal_hdr_replace_hmc(ptr);
			break;
		case 8:
			ptr = display_protosprc_hdr_remove_hmc(ptr);
			break;
		case 9:
			ptr = display_protosprc_hdr_insert_hmc(ptr);
			break;
		case 0xb:
			ptr = display_vlan_pri_hdr_update_hmc(ptr);
			break;
		case 0xc:
			ptr = display_ipv4_update_hmc(ptr);
			break;
		case 0xe:
			ptr = display_tcp_udp_update_hmc(ptr);
			break;
		case 0x10:
			ptr = display_ipv6_update_hmc(ptr);
			break;
		case 0x12:
			ptr = display_internal_iphdr_replace_hmc(ptr);
			break;
		case 0x14:
			printk("UDP/TCP checksum update command\n");
			ptr++;
			break;
		default:
			printk("unknown opcode %x, cannot continue\n", opcode);
			return;
	}
	if (!(opcode & 0x00800000))
		goto next_hmc;
}

static void *display_hmtd(uint32_t *ptr)
{
	struct hmt_desc *desc;
	void *nextad;

	printk("Header manip table desc::\n");
	nextad = NULL;
	desc = kzalloc (sizeof(struct hmt_desc), GFP_KERNEL);
	if (desc) {
		desc->word_0 = swap_word(ptr);
		desc->word_1 = swap_word((ptr + 1));
		desc->word_2 = swap_word((ptr + 2));
		desc->word_3 = swap_word((ptr + 3));
		printk("pahm\t%d, ", desc->pahm);
		printk("internal hmt ptr\t%x\n", desc->internal_hmt_ptr);
		if (desc->naden) {
			printk("nextdescidx\t%x\n", (desc->nextdescindex << 4));
			nextad = (void *)((uint8_t *)FmMurambaseAddr + 
					(desc->nextdescindex << 4));
		}
		printk("HMC_HMC_HMC<<<\n");
		display_hmt_entries(desc->internal_hmt_ptr);
		printk("HMC_HMC_HMC>>>\n");
		kfree(desc);
	} else {
		printk("kzalloc failed, cannot HM table descriptor\n");
	}
	return (nextad);
}

static void display_table_desc(uint32_t *ptr)
{
	uint32_t opcode;
	
	opcode = swap_word(ptr);
	if ((opcode & 0xc0000000) == 0x40000000) {
		opcode = swap_word(ptr + 2);
		switch (opcode & 0xff) {
			case 0x35:
				display_hmtd(ptr);
				break;
			case 0x36:
				display_stats_ad(ptr);
				break;
			case 0x2b:
				display_generic_5_off_ic_gmask(ptr);
				break;
			default:
				printk("unsupported opcode %x\n",
					opcode);
				break;
		}
	}
}

static void display_ad_table_using_offset(uint32_t ad_offset, 
		uint32_t kt_offset, uint32_t num_entries,
		uint32_t keylen)
{
	uint32_t ii;
	uint8_t *ad_ptr;
	uint8_t *kt_ptr;
	uint32_t adj_keylen;

	ad_ptr = (ad_offset + (uint8_t *)FmMurambaseAddr);
	kt_ptr = (kt_offset + (uint8_t *)FmMurambaseAddr);	
	keylen++;
	if (keylen % 16) {
		adj_keylen = (keylen + 16);
		adj_keylen &= ~0xf;
	} else
		adj_keylen = keylen;
	printk("keylen %d, adjkeylen %d\n", keylen, adj_keylen);
	for (ii = 0; ii < (num_entries + 1); ii++) {
		printk("......................\n");
		//printk("ADENTRY %d::\n", ii);
		display_buf_data(ad_ptr, FM_PCD_CC_AD_ENTRY_SIZE);
		if (ii < num_entries) {
			printk("key::\n");
			display_buf_data((void *)kt_ptr, keylen);	
			kt_ptr += adj_keylen;
			printk("mask::\n");
			display_buf_data((void *)kt_ptr, keylen);	
			kt_ptr += adj_keylen;
		}
		display_pcd_cc_ad(ad_ptr);
		ad_ptr += FM_PCD_CC_AD_ENTRY_SIZE;
	}
	printk("......................\n");
}

static void display_generic_5_off_ic_gmask(void *ad)
{
	struct generic_5_off_ic_gmask *desc;
	uint32_t *ptr;

	ptr = (uint32_t *)ad;
	desc = kzalloc (sizeof(struct generic_5_off_ic_gmask), GFP_KERNEL);
	if (desc) {
		desc->word_0 = swap_word(ptr);
		desc->word_1 = swap_word((ptr + 1));
		desc->word_2 = swap_word((ptr + 2));
		desc->age_mask = swap_word((ptr + 3));
		printk("keylen\t%d\n", (desc->key_length + 1));
		printk("cc_adbase\t%x\n", desc->cc_adbase);
		printk("match_table_entries_num\t%d\n", 
				desc->match_table_entries_num);
		printk("local mask\t%d\n", desc->LM);
		printk("match_table_ptr\t%x\n", desc->match_table_ptr);
		printk("offset_from_parse_result\t%d\n", 
			desc->offset_from_parse_result);
		printk("offset\t%d\n", desc->offset);
		printk("op_code\t%x\n", desc->op_code);
		display_ad_table_using_offset(desc->cc_adbase,
		desc->match_table_ptr, desc->match_table_entries_num,
		desc->key_length);
		kfree(desc);
	} else {
		printk("kzalloc failed, cannot display table descriptor\n");
	}
}

static void *display_keep_class_result(void *ad)
{
	struct keep_class_res_desc_ad *desc;
	uint32_t *ptr;
	uint32_t next_ad_index;

        ptr = (uint32_t *)ad;
        desc = kzalloc (sizeof(struct keep_class_res_desc_ad), GFP_KERNEL);
        if (desc) {
                desc->word_0 = swap_word(ptr);
                desc->word_1 = swap_word((ptr + 1));
                desc->word_2 = swap_word((ptr + 2));
                desc->statistic_counter = swap_word((ptr + 3));
		if (desc->PD)
                	printk("Policer disabled, ");
		else
                	printk("Policer enabled, ");
                printk("extended mode %d,\n", desc->extended_mode);
                printk("naden %d, ", desc->naden);
                printk("novspe %d\n", desc->no_vspe);
                printk("statistics_enable\t%d\n", desc->statistics_enable);
                printk("fpm_nia\t%x\n", desc->fpm_nia);	
		if (desc->naden) {
                	printk("nextADindex\t%x\n", desc->next_action_desc_index);
			next_ad_index = (desc->next_action_desc_index << 4);
			return ((void *)((uint8_t *)FmMurambaseAddr + next_ad_index));  
		}
                kfree(desc);

        } else {
                printk("kzalloc failed, cannot display keep class descriptor\n");
        }
	return NULL;
}

static void display_pcd_cc_ad(void *ad)
{
	uint32_t val;

	printk("============\n");
disp_nextad:
	val = swap_word((uint32_t *)ad);
	switch (val >> 30) {
		case 0:
			printk("New Classification result type:: %p\n", ad);
			ad = display_new_class_res_desc(ad);
			break;
		case 1:
			printk("table desc type:: %p\n", ad);
			display_table_desc(ad);
			ad = NULL;
			break;
		case 2:
			printk("Keep Classification result type:: %p\n", ad);
			ad = display_keep_class_result(ad);
			break;
		case 3:
			printk("reserved for dynamic update of CC tables\n");
			ad = NULL;
			break;
	} 
	if (ad) {
		printk("next ad %p\n", ad);
		display_buf_data(ad, FM_PCD_CC_AD_ENTRY_SIZE);
		goto disp_nextad;
	}
	printk("============\n");
}

static void display_pcd_cc_hc(t_FmPcd *p_FmPcd, uint32_t oldAdAddrOffset,
                        uint32_t newAdAddrOffset)
{
	void *ad;

	ad = (void *)((uint8_t *)FmMurambaseAddr + oldAdAddrOffset);
	printk("###### old ad::%x, ad %p\n", oldAdAddrOffset, ad);
	display_pcd_cc_ad(ad);
	printk("###### old ad end\n");
	ad = (void *)((uint8_t *)FmMurambaseAddr + newAdAddrOffset);
	printk("###### new ad::%x ad %p\n", newAdAddrOffset, ad);
	display_pcd_cc_ad(ad);
	printk("###### new ad end\n");
}
#else
#define display_pcd_cc_hc(x, y, z);
#endif

