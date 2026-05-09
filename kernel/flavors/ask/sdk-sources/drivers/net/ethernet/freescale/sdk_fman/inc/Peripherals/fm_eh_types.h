/*
 *  Copyright 2018 NXP
 *
 *  This program is free software; you can redistribute it and/or
 *  modify it under the terms of the GNU General Public License
 *  as published by the Free Software Foundation; either version 2
 *  of the License, or (at your option) any later version.
 *
 *  This program is distributed in the hope that it will be useful,
 *  but WITHOUT ANY WARRANTY; without even the implied warranty of
 *  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 *  GNU General Public License for more details.
 *
 *  You should have received a copy of the GNU General Public License
 *  along with this program; if not, write to the Free Software
 *  Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301, USA.
 *
 */
        
/**     
 * @file                fm_eh_types.h     
 * @description         DPAA enhanced external hash table types
 */


#ifndef FM_EH_TYPES_H
#define FM_EH_TYPES_H 1

//type field in table_info
enum {
	IPV4_UDP_TABLE,
	IPV4_TCP_TABLE,
	IPV6_UDP_TABLE,
	IPV6_TCP_TABLE,
	ESP_IPV4_TABLE,
	ESP_IPV6_TABLE,
	IPV4_MULTICAST_TABLE,
	IPV6_MULTICAST_TABLE,
	PPPOE_RELAY_TABLE,
	ETHERNET_TABLE,
	IPV4_3TUPLE_UDP_TABLE,
	IPV4_3TUPLE_TCP_TABLE,
	IPV6_3TUPLE_UDP_TABLE,
	IPV6_3TUPLE_TCP_TABLE,
	IPV4_REASSM_TABLE,
	IPV6_REASSM_TABLE,
	MAX_MATCH_TABLES
};

#define TABLE_TYPE_MASK 0xf

#endif
