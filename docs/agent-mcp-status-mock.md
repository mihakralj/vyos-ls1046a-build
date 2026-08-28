# Agent × VyOS MCP — Network Status (mock chat)

> Mock transcript. The agent answers a status request by calling the `vyos` MCP
> server (`ssh -i ~/.ssh/vyos_key vyos@192.168.1.185 vyos-mcp`), tool
> `vyos_execute_operational_command`, then renders the same data shape as the
> Mono dashboard.

---

**👤 User**

Give me full router status — system health, WAN addressing, per-interface links, live WAN throughput, firewall zones and DHCP leases. Pull it live from the board.

---

**🤖 Agent**

Collecting live state from the board. Batching independent operational commands over the `vyos` MCP.

🔧 **`vyos_execute_operational_command`** · `{ "path": ["show","system","uptime"] }`

```
10:42:07 up 6 days, 4:11, 1 user, load average: 0.34, 0.28, 0.22
```

🔧 **`vyos_execute_operational_command`** · `{ "path": ["show","interfaces"] }`

```
Interface    IP Address           S/L   Description
---------    ----------           ---   -----------
eth0         -                    u/D
eth1         192.168.1.1/24       u/u   lan
eth2         192.168.30.1/24      u/u   iot
eth3         -                    u/u
eth4         198.51.100.155/32    u/u   WAN
pppoe0       198.51.100.155/32    u/u
```

🔧 **`vyos_execute_operational_command`** · `{ "path": ["show","interfaces","ethernet","eth4"] }`

```
eth4: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc fq
    link/ether 68:05:ca:16:00:03
    Speed: 10000Mb/s  Duplex: Full  Link detected: yes
    RX: 18.4 GB  14.2M pkts  0 err     TX: 2.1 GB  9.8M pkts  0 err
```

🔧 **`vyos_execute_operational_command`** · `{ "path": ["monitor","interfaces","ethernet","eth4"], "samples": 2 }`

```
eth4   rx/s 142.6 Mbps   tx/s 11.3 Mbps
```

🔧 **`vyos_execute_operational_command`** · `{ "path": ["show","dhcp","server","leases"] }`

```
IP             MAC                Hostname        Expires
192.168.1.104  a4:83:e7:2b:19:0c  Gaming-PC       11h 12m
192.168.1.140  dc:a6:32:44:8f:2e  Living-Room-TV   9h 03m
192.168.1.156  f0:18:98:1d:77:a1  family-laptop    6h 44m
192.168.1.181  b8:e6:0c:5a:3f:d2  Alices-iPhone    2h 20m
192.168.20.44  9e:2f:11:c4:08:5b  (unknown)          30m
```

🔧 **`vyos_execute_operational_command`** · `{ "path": ["show","firewall"] }` _(+ show system memory/storage, show ipv6 dhcpv6-client, fan-check)_

```
Zone    Subnet           Default-Action   Members
lan     192.168.1.0/24   accept           7 hosts
guest   192.168.20.0/24  drop (isolated)  2 hosts
iot     192.168.30.0/24  drop (isolated)  3 hosts
mem: 41% of 8GB ECC   storage: 22% of 32GB   temp: 52C   fan: 1980 rpm
```

All sections collected. Here is the status report.

---

## Your network is **healthy** ✅

_source: vyos-mcp @ 192.168.1.185 · live_

| Internet | Security | Load (1m) | Uptime |
|---|---|---|---|
| ✅ Connected | ✅ Firewall on | 0.34 | 6d 4h |
| PPPoE · up 2h 14m | 3 zones | 4 cores · 1.6 GHz | 6.18.44-vyos |

### 🖥️ System — hardware · live

| Field | Value |
|---|---|
| Model | Mono Gateway Development Kit (LS1046A) |
| Firmware | VyOS rolling 2026.08.22-0031 |
| Kernel | Linux 6.18.44-vyos |
| CPU / Memory | 4×A72 · 41% of 8 GB ECC |
| Storage | 22% of 32 GB eMMC |
| Temp / Fan | 52 °C Normal · 1980 rpm |
| Power / Sensors | 8 power · 2 thermal |

### 🌐 WAN & IP — eth4 · PPPoE

| Field | Value |
|---|---|
| IPv4 | 198.51.100.155/32 · gw 198.51.100.1 |
| IPv4 DNS | 203.0.113.10, 203.0.113.11 |
| IPv6 prefix | 2001:db8:2d00:2e00::/56 |
| IPv6 addr | 2001:db8:f02c:242:1a2b:3c4d:5e6f:7a8b/64 |
| IPv6 gw / DNS | fe80::1a2b:3cff:fe4d:5e6f · 2001:db8:d::23 |
| Lease expires | 0h 10m |

### 🔌 Interfaces — 5 ports

| Port | Link | Speed | RX / TX today |
|---|---|---|---|
| eth0 | ⚫ No link | — | — |
| eth1 | 🟢 Up | 1 Gbps full | 4.1 / 0.9 GB |
| eth2 | 🟢 Up | 1 Gbps full | 0.3 / 0.1 GB |
| eth3 | 🟢 Up | 10 Gbps | 2.7 / 1.2 GB |
| **eth4 (WAN)** | 🟢 Up | 10 Gbps | 18.4 / 2.1 GB |

### 📈 Internet traffic — live · WAN eth4

| Direction | Rate | Plan |
|---|---|---|
| Download | **142.6 Mbps** | of 300 Mbps |
| Upload | **11.3 Mbps** | of 40 Mbps |

### 🛡️ Firewall zones — 3 zones

| Zone | Subnet | DHCP range | Policy | Hosts |
|---|---|---|---|---|
| lan | 192.168.1.0/24 | .100–.200 | accept | 7 |
| guest | 192.168.20.0/24 | .100–.150 | isolated | 2 |
| iot | 192.168.30.0/24 | .100–.180 | isolated | 3 |

### 📶 DHCP leases — 5 active

| Device | MAC | IP | Expires |
|---|---|---|---|
| Gaming PC | a4:83:e7:2b:19:0c | 192.168.1.104 | 11h 12m |
| Living Room TV | dc:a6:32:44:8f:2e | 192.168.1.140 | 9h 03m |
| family-laptop | f0:18:98:1d:77:a1 | 192.168.1.156 | 6h 44m |
| Alice's iPhone | b8:e6:0c:5a:3f:d2 | 192.168.1.181 | 2h 20m |
| _Unknown device_ | 9e:2f:11:c4:08:5b | 192.168.20.44 | 30m |

### ⚪ Not available on VyOS

| Field | Reason |
|---|---|
| Wi-Fi bands | n/a — no radio on LS1046A |
| Per-device usage | n/a — no accounting |
| Update available | n/a — firmware version reported above |

---

_8 sections rendered from 6 batched `vyos_execute_operational_command` calls · unavailable fields marked n/a_
