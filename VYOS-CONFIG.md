# Configuring VyOS on a Mono Gateway Development Kit

The primary source of truth for configuring VyOS is the [upstream 'rolling' documentation](https://docs.vyos.io/en/rolling/introducing/about.html). 'Rolling' in this context refers to the nightly release provided by the VyOS project ([more info](https://docs.vyos.io/en/rolling/installation/install.html)) - this is the main development branch of VyOS.

**The documentation provides:** 
- A quick orientation and overview of how VyOS configuration works
- A walk through an example VyOS configuration
- A tutorial to create a basic HW-offloaded VyOS config for the Mono Gateway Development Kit.

By the end, you'll understand basic VyOS configuration, and how to use it to configure VyOS + ASK2 on the Mono Gateway Development Kit.

This document will heavily link to external content (wikipedia etc) for clarity/context, to remain concise. For more information - explore the links.

# 0. Orientation - VyOS 101
[VyOS](https://en.wikipedia.org/wiki/VyOS) is a [declarative](https://en.wikipedia.org/wiki/Declarative_programming), open-source [network operating system](https://en.wikipedia.org/wiki/Network_operating_system) based on [Debian Linux](https://en.wikipedia.org/wiki/Debian). It began as a fork of [Vyatta](https://en.wikipedia.org/wiki/Vyatta) in 2013 ([brief history](https://docs.vyos.io/en/rolling/introducing/history.html)), and has since become a widely deployed, enterprise-grade solution. 

Interaction with VyOS occurs primarily via the Command Line Interface (CLI). VyOS is extremely configurable, but the range of config options are so vast that it genuinely makes creating a (general) web Graphical User Interface (GUI) extremely challenging. Many have tried and failed. One more recent and promising option is [VyMANAGER](VYMANAGER.md), but to use this, you still need to understand how VyOS config works.
# 1. Configuration: Overview
This extreme TL;DR of how configuration is performed in VyOS. It is a memory aid, not a substitute for [reading the docs](https://docs.vyos.io/en/rolling/cli.html).

There are two 'modes' (Operational and Configuration), and three 'configuration states' (Running, Working, Saved). Together these enable well-managed changes to a VyOS instance.

```mermaid
flowchart LR
    A((Power<br/>On)) -->|U-Boot| B((VyOS boots)) -->|load from:<br/>/config/config.boot| C -->|Loaded<br/>at boot| G
    B --> D
    subgraph H ["Configuration states"]
      direction LR
      C(Saved Configuration)
      F(Working Configuration)
      G(Active Configuration)
    end
    
    D[Operational mode] -.->|Enter via:<br/>'configure'| E[Configuration mode] -.->|Define using:<br/>`set` commands| F -.->|Apply using:<br/>'commit'| G -.->|Persist using:<br/>'save'| C
    E -.->|Return via:<br/>'exit'| D
    style A fill:#222,stroke:#333,color:#fff
    style B fill:#48a,stroke:#333,color:#fff
    style C fill:#48c,stroke:#333,color:#fff
    style D fill:#4a7,stroke:#333,color:#fff
    style E fill:#340,stroke:#333,color:#aaa
    style F fill:#963,stroke:#333,color:#fff
    style G fill:#4a9,stroke:#333,color:#fff 
```
This diagram provides a a high-level picture of how 'Modes' and 'Configuration states' interact. The boot process is truncated for clarity.
# 1.1 Modes
Modes dictate how you can interact with a VyOS instance. The separation is deliberate and ensures changes are well-managed, and recovery is straightforward.
## Two 'modes'
- **Operational** - (Default) - Allows querying status, info and Read-Only actions (generally).
- **Configuration** - Entered via the `configure` command - Enables configuration changes. 

>**NOTE:** The scope of a change made in Configuration mode depends on the configuration state that is modified - see below. **Understanding this is critical to using VyOS**.

>**NOTE:** A good visual indicator of which CLI mode you are in can be found in the prompt itself, which will change from `$` to `#` when moving from Operational to Configuration mode.

# 2. Configuration states
VyOS configuration exists in a unified config file, located in `/config/config.boot` by default. However, in VyOS there are three configurational states that you MUST familiarise yourself with.

>**NOTE:** This matters, and exists to save you when you make mistakes, which you will. If you understand this, and use it as designed, you should never lock yourself out, or make a breaking change. Used correctly, you will never need to touch your device after initial setup.

## 2.1 Three 'configuration states'
The configuration states define the context to which you apply a change. When you make a change that breaks stuff - this is how VyOS can save you and provide options to 'roll-back' a change.

- **Active / running Configuration** - This is the current 'live' config that dictates how the device runs, right now. Using `commit` within  Configuration mode will change this, and all changes live in RAM.

>**NOTE:** Configuration changes made, via `commit` to the active configuration are lost on reboot, by design. This is good for testing changes.

- **Working / staged Configuration** - When you first enter Configuration mode, any changes you make exist here. This is a 'staged' configuration context, that does not yet apply. When executing the `commit` command, the working configuration becomes the Active configuration.

>**NOTE:** Any changes that are made in configuration mode, can be abandoned, by using `exit` to return to Operational mode. This will NOT change the Active configuration.

- **Saved Configuration** - This is what exists on-disk, by default in `/config/config.boot` and is loaded at boot. In configuration mode, the `save` command writes the active configuration to disk.

>**NOTE:** Any unsaved changes in the Active configuration are lost at reboot. Only using `save` will make a change persistent across a reboot. Use wisely. Use only after a change has been proven stable, effective AND non-breaking via the earlier `commit`.

## 2.2 Testing changes: commit-confirm

```bash
commit-confirm <minutes>          # e.g. 'commit-confirm 5' for 5 minutes
```
If making a change to the working config which you suspect may break something when applying via 'commit', including breaking your access to the VyOS instance, consider using `commit-confirm`. This provides an automated roll-back action, if you do not enter `confirm` within the time interval stated.

The roll-back action itself can be defined via:
`set system config-management commit-confirm action`
Options: 
`reboot` (Default) or `reload` (the most recent previous config)

If this doesn't yet make sense - don't worry, read on. Just know this can, and will, save you at some point!

# 3. Configuration: Structure
## 3.1 The configuration 'tree' & nodes
The previous section covers 'how' configuration occurs in VyOS; this section describes 'what' the VyOS configuration file actually looks like, and how to use the information it contains.

>**NOTE:** You should never edit- or otherwise modify the `config.boot` file directly. **Use configuration mode.** It exists to provide you with safeguards for when things break!

To view the current configuration, from operational mode:
```bash
show configuration
```

Example section of the VyOS config, describing several basic interfaces.
```
interfaces {
    ethernet eth0 {
        address "dhcp"
        description "LAN"
        }
    }
    ethernet eth1 {
        address "dhcp"
        description "WAN"
        }
    }
    loopback lo {
    }
}
```
>**NOTE:** This isn't xml, yaml or json, but exporting in various formats is an option, if desired. e.g `show configuration json` or `show configuration json pretty` (if preferred)

The unified and hierarchical configuration system used by VyOS provides a structured 'tree' of nodes, where each 'branch' has well-defined child-nodes and values. This uses the set-style format shown above, and this 'tree' view shows how the config knits together.

You may see references to 'config nodes' these are the individual sub-branches that contain assigned values (e.g. the content in the quotes in the above example).

## 3.2 Declaratively recreating configuration
In VyOS, the config is evaluated every time you use 'commit'. One consequence of this is that you can readily establish what commands are required to re-establish the same state, and you can output this as a series of config commands.

Using:
```bash
show configuration commands
```

Will print out every VyOS config command required to re-create the present configuration. 

e.g.
```bash
...
set interfaces ethernet eth0 address 'dhcp'
set system host-name 'mono-vyos'
...
```
These are the commands you can copy and paste into a terminal to return to this configured state.

>**NOTE:** Your configuration, exported in this way, *will* contain private info, e.g. SSH keys, password hashes etc. Be mindful of this. If you intended to share a configuration use `show configuration commands | strip-private` instead, which will omit your private info.

## 3.3 Use Tab-completion 
The configuration shell is based on [bash](https://en.wikipedia.org/wiki/Bash_(Unix_shell)). When exploring the CLI, in either CLI mode, you can always press `tab` to view the valid and available options to complete the next section of a command. Where there is only one valid option, it will autocomplete, OR where there are multiple options, these will then be listed.

**For example:**
If typing 'show' and then pressing tab, the following is shown.
```bash
test@mono-vyos:~$ show [tab]
Possible completions:
  arp             Show Address Resolution Protocol (ARP) information
  bridge          Show bridging information
  cluster         Show clustering information
  configuration   Show running configuration
  conntrack       Show conntrack entries in the conntrack table
  conntrack-sync  Show connection syncing information
  date            Show system date and time
  dhcp            Show Dynamic Host Configuration Protocol (DHCP) information
  dhcpv6          Show status related to DHCPv6
  disk            Show status of disk device
  dns             Show Domain Name Server (DNS) information
  file            Show files for a particular image
  firewall        Show firewall information
  flow-accounting Show flow accounting statistics
  hardware        Show system hardware details
  history         Show command history
  host            Show host information
  incoming        Show ethernet input-policy information
  ...
```

This means you never need to memorise commands, and will always have context cues for how you can complete a command, or line of config that you are entering.
# 4. Configuration: Example
This is an example VyOS configuration file, which we will now walk through. 

>**NOTE:** To be clear - this is a demo. It is neither 'complete', nor something you can just copy and reuse. It exists to show how VyOS works. We will apply what you learn here and create a basic VyOS config you can customise in the next section. If you skip this bit though, that will be a lot harder!

**The goal:** Understanding what this basic config does, and what I does not do! Both are important.

You may find it helpful to copy this and open it in a text editor to follow along.
## 4.1 config.boot 'tree view'
```
firewall {
    flowtable ft01 {
        interface "eth3"
        interface "eth4"
        interface "eth3.6"
        interface "eth4.8"
        offload "hardware"
    }
    ipv4 {
        forward {
            filter {
                rule 10 {
                    action "offload"
                    offload-target "ft01"
                    state "established"
                    state "related"
                }
            }
        }
    }
    ipv6 {
        forward {
            filter {
                rule 11 {
                    action "offload"
                    offload-target "ft01"
                    state "established"
                    state "related"
                }
            }
        }
    }
}
interfaces {
    ethernet eth0 {
        address "dhcp"
        address "10.64.0.32/24"
        description "MGMT (RJ45 left)"
        hw-id "<SNIP>"
        offload {
            gro
            gso
            sg
        }
    }
    ethernet eth1 {
        address "dhcp"
        description "RJ45 center"
        hw-id "<SNIP>"
        offload {
            gro
            gso
            sg
        }
    }
    ethernet eth2 {
        address "dhcp"
        description "WAN"
        hw-id "<SNIP>"
        offload {
            gro
            gso
            sg
        }
    }
    ethernet eth3 {
        address "dhcp"
        address "10.100.1.1/24"
        address "fd36:dead:beef:3::0a64:0101/64"
        description "SFP+ left"
        hw-id "<SNIP>"
        offload {
            gro
            gso
            hw-tc-offload
            ipv4
            ipv6
            sg
        }
        vif 6 {
            address "10.150.1.1/24"
            address "fd36:dead:beef:6::0a64:0101/64"
        }
    }
    ethernet eth4 {
        address "dhcp"
        address "10.200.1.1/24"
        address "fd36:dead:beef:4::0ac8:0101/64"
        description "SFP+ right"
        hw-id "<SNIP>"
        offload {
            gro
            gso
            hw-tc-offload
            ipv4
            ipv6
            sg
        }
        vif 8 {
            address "10.250.1.1/24"
            address "fd36:dead:beef:8::0ac8:0101/64"
        }
    }
    loopback lo {
    }
}
nat {
    source {
        rule 100 {
            outbound-interface {
                name "eth4"
            }
            source {
                address "10.100.1.0/24"
            }
            translation {
                address "masquerade"
            }
        }
    }
}
nat66 {
    source {
        rule 110 {
            outbound-interface {
                name "eth4"
            }
            source {
                prefix "fd36:dead:beef:3::/64"
            }
            translation {
                address "masquerade"
            }
        }
        rule 130 {
            outbound-interface {
                name "eth4.8"
            }
            source {
                prefix "fd36:dead:beef:6::/64"
            }
            translation {
                address "masquerade"
            }
        }
    }
}
protocols {
    static {
        route 0.0.0.0/0 {
            next-hop 10.1.9.1 {
            }
        }
    }
}
service {
    https {
        api {
            graphql {
                authentication {
                    type "key"
                }
            }
            keys {
                id vymanager {
                    key "<SNIP>"
                }
            }
            rest {
            }
        }
    }
    ntp {
        allow-client {
            address "127.0.0.0/8"
            address "169.254.0.0/16"
            address "10.0.0.0/8"
            address "172.16.0.0/12"
            address "192.168.0.0/16"
            address "::1/128"
            address "fe80::/10"
            address "fc00::/7"
        }
        server time.apple.com {
        }
        server time.cloudflare.com {
        }
        server time.google.com {
        }
    }
    ssh {
        disable-password-authentication
    }
}
system {
    config-management {
        commit-revisions "100"
    }
    conntrack {
        modules {
            ftp
            h323
            nfs
            pptp
            sip
            sqlnet
            tftp
        }
    }
    console {
        device ttyS0 {
            kernel
            speed "115200"
        }
    }
    domain-name "home.local"
    host-name "mono-vyos"
    login {
        user test {
            authentication {
                encrypted-password "<SNIP>"
                public-keys test@monovm {
                    key "<SNIP>"
                    type "ssh-ed25519"
                }
                public-keys vymanager {
                    key "<SNIP>"
                    type "ssh-ed25519"
                }
            }
        }
    }
    name-server "eth0"
    name-server "eth1"
    name-server "eth2"
    name-server "eth3"
    name-server "eth4"
    name-server "10.1.8.8"
    name-server "10.1.4.4"
    syslog {
        local {
            facility all {
                level "info"
            }
            facility local7 {
                level "debug"
            }
        }
    }
    time-zone "Europe/London"
    update-check {
        auto-check
        url "https://raw.githubusercontent.com/mihakralj/vyos-ls1046a-build/refs/heads/main/version.json"
    }
    watchdog {
        reboot-timeout "60"
        shutdown-timeout "60"
    }
}


// Warning: Do not remove the following line.
// vyos-config-version: "bgp@8:broadcast-relay@1:cluster@2:config-management@2:conntrack@6:conntrack-sync@2:container@3:dhcp-relay@2:dhcp-server@11:dhcpv6-server@6:dns-dynamic@5:dns-forwarding@4:firewall@20:flow-accounting@3:https@7:ids@2:interfaces@36:ipoe-server@4:ipsec@14:isis@3:l2tp@10:lldp@3:mdns@1:monitoring@2:nat@8:nat66@3:nhrp@1:ntp@3:openconnect@3:openvpn@6:ospf@3:pim@1:pki@1:policy@9:pppoe-server@13:pptp@6:qos@3:quagga@12:reverse-proxy@3:rip@1:rpki@2:snmp@3:ssh@3:sstp@7:system@33:vpp@6:vrf@4:vrrp@4:vyos-accel-ppp@2:wanloadbalance@4:webproxy@2"
// Release version: 2026.10.30-2012-rolling
```

## 4.2 List of commands
Just to reiterate: This same configuration can be turned into a list of declarative config commands that will re-create it. You can generate this by using `show configuration commands`. For this demo example configuration that looks like this:

```
set firewall flowtable ft01 interface 'eth3'
set firewall flowtable ft01 interface 'eth4'
set firewall flowtable ft01 interface 'eth3.6'
set firewall flowtable ft01 interface 'eth4.8'
set firewall flowtable ft01 offload 'hardware'
set firewall ipv4 forward filter rule 10 action 'offload'
set firewall ipv4 forward filter rule 10 offload-target 'ft01'
set firewall ipv4 forward filter rule 10 state 'established'
set firewall ipv4 forward filter rule 10 state 'related'
set firewall ipv6 forward filter rule 11 action 'offload'
set firewall ipv6 forward filter rule 11 offload-target 'ft01'
set firewall ipv6 forward filter rule 11 state 'established'
set firewall ipv6 forward filter rule 11 state 'related'
set interfaces ethernet eth0 address 'dhcp'
set interfaces ethernet eth0 address '10.64.0.32/24'
set interfaces ethernet eth0 description 'MGMT (RJ45 left)'
set interfaces ethernet eth0 hw-id '<SNIP>'
set interfaces ethernet eth0 offload gro
set interfaces ethernet eth0 offload gso
set interfaces ethernet eth0 offload sg
set interfaces ethernet eth1 address 'dhcp'
set interfaces ethernet eth1 description 'RJ45 center'
set interfaces ethernet eth1 hw-id '<SNIP>'
set interfaces ethernet eth1 offload gro
set interfaces ethernet eth1 offload gso
set interfaces ethernet eth1 offload sg
set interfaces ethernet eth2 address 'dhcp'
set interfaces ethernet eth2 description 'WAN'
set interfaces ethernet eth2 hw-id '<SNIP>'
set interfaces ethernet eth2 offload gro
set interfaces ethernet eth2 offload gso
set interfaces ethernet eth2 offload sg
set interfaces ethernet eth3 address 'dhcp'
set interfaces ethernet eth3 address '10.100.1.1/24'
set interfaces ethernet eth3 address 'fd36:dead:beef:3::0a64:0101/64'
set interfaces ethernet eth3 description 'SFP+ left'
set interfaces ethernet eth3 hw-id '<SNIP>'
set interfaces ethernet eth3 offload gro
set interfaces ethernet eth3 offload gso
set interfaces ethernet eth3 offload hw-tc-offload
set interfaces ethernet eth3 offload ipv4
set interfaces ethernet eth3 offload ipv6
set interfaces ethernet eth3 offload sg
set interfaces ethernet eth3 vif 6 address '10.150.1.1/24'
set interfaces ethernet eth3 vif 6 address 'fd36:dead:beef:6::0a64:0101/64'
set interfaces ethernet eth4 address 'dhcp'
set interfaces ethernet eth4 address '10.200.1.1/24'
set interfaces ethernet eth4 address 'fd36:dead:beef:4::0ac8:0101/64'
set interfaces ethernet eth4 description 'SFP+ right'
set interfaces ethernet eth4 hw-id '<SNIP>'
set interfaces ethernet eth4 offload gro
set interfaces ethernet eth4 offload gso
set interfaces ethernet eth4 offload hw-tc-offload
set interfaces ethernet eth4 offload ipv4
set interfaces ethernet eth4 offload ipv6
set interfaces ethernet eth4 offload sg
set interfaces ethernet eth4 vif 8 address '10.250.1.1/24'
set interfaces ethernet eth4 vif 8 address 'fd36:dead:beef:8::0ac8:0101/64'
set interfaces loopback lo
set nat source rule 100 outbound-interface name 'eth4'
set nat source rule 100 source address '10.100.1.0/24'
set nat source rule 100 translation address 'masquerade'
set nat66 source rule 110 outbound-interface name 'eth4'
set nat66 source rule 110 source prefix 'fd36:dead:beef:3::/64'
set nat66 source rule 110 translation address 'masquerade'
set nat66 source rule 130 outbound-interface name 'eth4.8'
set nat66 source rule 130 source prefix 'fd36:dead:beef:6::/64'
set nat66 source rule 130 translation address 'masquerade'
set protocols static route 0.0.0.0/0 next-hop 10.1.9.1
set service https api graphql authentication type 'key'
set service https api keys id vymanager key '<SNIP>'
set service https api rest
set service ntp allow-client address '127.0.0.0/8'
set service ntp allow-client address '169.254.0.0/16'
set service ntp allow-client address '10.0.0.0/8'
set service ntp allow-client address '172.16.0.0/12'
set service ntp allow-client address '192.168.0.0/16'
set service ntp allow-client address '::1/128'
set service ntp allow-client address 'fe80::/10'
set service ntp allow-client address 'fc00::/7'
set service ntp server time.apple.com
set service ntp server time.cloudflare.com
set service ntp server time.google.com
set service ssh disable-password-authentication
set system config-management commit-revisions '100'
set system conntrack modules ftp
set system conntrack modules h323
set system conntrack modules nfs
set system conntrack modules pptp
set system conntrack modules sip
set system conntrack modules sqlnet
set system conntrack modules tftp
set system console device ttyS0 kernel
set system console device ttyS0 speed '115200'
set system domain-name 'home.local'
set system host-name 'mono-vyos'
set system login user test authentication encrypted-password '<SNIP>'
set system login user test authentication public-keys test@monovm key '<SNIP>'
set system login user test authentication public-keys test@monovm type 'ssh-ed25519'
set system login user test authentication public-keys vymanager key '<SNIP>'
set system login user test authentication public-keys vymanager type 'ssh-ed25519'
set system name-server 'eth0'
set system name-server 'eth1'
set system name-server 'eth2'
set system name-server 'eth3'
set system name-server 'eth4'
set system name-server '10.1.8.8'
set system name-server '10.1.4.4'
set system syslog local facility all level 'info'
set system syslog local facility local7 level 'debug'
set system time-zone 'Europe/London'
set system update-check auto-check
set system update-check url 'https://raw.githubusercontent.com/mihakralj/vyos-ls1046a-build/refs/heads/main/version.json'
set system watchdog reboot-timeout '60'
set system watchdog shutdown-timeout '60'
```

## 4.3 Configuration: Interpretation
### 4.3.1 First impressions & and what they tell you

Even if you just scanned down through the config and the `set ... ` commands, you'll likely notice a few useful things:

- The config is long, but the list of commands to create it is shorter
	- This is because in the absence of config statements, defaults are assumed, and knowing that is important.
- The list of commands, and the structure of the config have a lot in common. The first 2-3 words in the command nicely group the commands together.
	- These groupings are the 'branches' of the config 'tree' which organise these different elements of the configuration based on what they are, or what they define. Often areas are linked, as you'll soon see.
- The words towards the start of the config commands, and the menu structures you see in the web graphical user-interfaces of other firewall operating systems look similar.
	- They look similar, because they are. 'system', 'service', 'interfaces', and 'firewall' are self-explanatory, but some others may not be intuitive and may relate to functionality that is less familiar as VyOS can be configured to do a lot more than most Firewall OS options.
- The ordering of the config and the commands may seem a bit arbitrary, or even backwards.
	- Because it largely is, at least to a human reader. We'll break this down a bit more logically in the next section.

### 4.3.2 A structure emerges
If we re-order this config, we can start to deconstruct it as follows:

```bash
System				# Defines the VyOS instance itself
> host-name
...

Interfaces			# Defines the network interfaces, and 'arming' them for offload
> ethernet
...

Service				# Defines the services the VyOS instance offers
> dhcp-server
...

Firewall			# Defines the firewall policies, and enables offloads
> ipv4 
...

'Routing'			# Collective define routing policy, either learned, or static
> protocols			# NB: Not a config branch, but a collection of branches
...

Containers			# Defines the podman containers (if any) running on the device
...

'Mono HW-offloading'# A recap of the Mono-specific HW-offloading config
> ARMED
> ENABLED
> TRIGGERED
```

For the sake of simplicity - we will deconstruct this config based on the declarative commands and what they express actually. This avoids the ambiguity of assumed defaults in how the config tree is otherwise interpreted.

### 4.3.3 System
**Upstream VyOS docs:** [System](https://docs.vyos.io/en/rolling/configuration/system/index.html) + [DNS](https://docs.vyos.io/en/rolling/configuration/system/name-server.html) + [Watchdog](https://docs.vyos.io/en/rolling/configuration/system/watchdog.html)

Starting with the 'system' branch, whose related commands all start with the prefix `set system ...`, and re-ordering for readability:

```bash
# This first block defines the hostname, domain and timezone
# host-name + domain-name = fully qualified domain name (FQDN) e.g mono-vyos.home.local in this example.
# NB: '.local' is a non-routable pseudo top-level domain (TLD), and using 'home.local' can help avoid mDNS collisons for '.local'. Other viable TLD options include '.internal' or '.test.', or any routable domain that you own.
set system domain-name 'home.local'
set system host-name 'mono-vyos'
set system time-zone 'Europe/London'

# The defines the serial console - don't change this, it's your lifeline.
set system console device ttyS0 kernel
set system console device ttyS0 speed '115200'

# This section defines the users and their permitted (local) auth methods
# In this instance there is a password, and two sets of SSH public keys:
# - The first password and pubkey are for the user 'test'
# - The second SSH key is for use with VyManager - see VYMANAGER.md.
set system login user test authentication encrypted-password '<SNIP>'
set system login user test authentication public-keys test@monovm key '<SNIP>'
set system login user test authentication public-keys test@monovm type 'ssh-ed25519'
set system login user test authentication public-keys vymanager key '<SNIP>'
set system login user test authentication public-keys vymanager type 'ssh-ed25519'

# This section defines the DNS name-servers
# By default, this assumes whatever is connected as the next-hop on eth0-4 is a DNS server and/or this will be provided in the DHCP reponses to these interfaces. This may not be the case for you in a typical home scenario
# The last two are two customer name-servers I've defined, which exist one-hop from this test network. If you had to define a custom DNS server, like a pihole or similar, this is how you'd do it.
set system name-server 'eth0'
set system name-server 'eth1'
set system name-server 'eth2'
set system name-server 'eth3'
set system name-server 'eth4'
set system name-server '10.1.8.8'
set system name-server '10.1.4.4'

# This section defined where this VyOS instance looks for updates, and enables auto-check
set system update-check auto-check
set system update-check url 'https://raw.githubusercontent.com/mihakralj/vyos-ls1046a-build/refs/heads/main/version.json'

# This section designates which Conntrack protocol modules should be loaded.
# Context: Conntrack is a linux kernel feature that tracks connections, their state, and the packets associated with them. These statements tell it what other types of connection to track beyond the standard set, which includes TCP/IP and UDP/IP.
set system conntrack modules ftp
set system conntrack modules h323
set system conntrack modules nfs
set system conntrack modules pptp
set system conntrack modules sip
set system conntrack modules sqlnet
set system conntrack modules tftp

# This defines how many 'commit' actions VyOS should locally store/track. This provides a simplistic local config management system.
set system config-management commit-revisions '100'

# This defines the level and verbosity of local logging
set system syslog local facility all level 'info'
set system syslog local facility local7 level 'debug'

# The configures the hardware 'watchdog' to 'pause' for '60' seconds whilst 'reboot' or 'shutdown' actions are occuring.
# Context: A hardware watchdog enables rebooting the device when it detects a system, process or component has becomes unresponsive.
# Further config is needed if the watchdog is to be practically used.
set system watchdog reboot-timeout '60'
set system watchdog shutdown-timeout '60'
```

### 4.3.4 Interfaces
**Upstream VyOS docs:** [Interfaces](https://docs.vyos.io/en/rolling/configuration/interfaces/index.html) + [Ethernet ](https://docs.vyos.io/en/rolling/configuration/interfaces/ethernet.html)

Commands all start with the prefix `set interfaces ...`, and re-ordering for readability:
```bash
# Most of this section is self-explanatory, and elements repeat. We will discuss only the first instances
# Config statements are grouped by the interface they relate to
# Address can be manually set in the format IP/CIDR, or obtained via dhcp.
# Descriptions remind you what things are later - make a habit of setting this, and future you will appreciate it.
# Hw-id is the MAC address of the ethernet device e.g. XX:XX:XX:XX:XX:XX in hex
# Offload statements explicitly enable types of offload behaviour, which may be impossible, software based, or hardware based.
# GRO = Generic Receive Offload - SW/HW - on (default)
# GSO = Generic Segmentation Offload - SW/HW - on (default)
# SG = Scatter-Gather - SW/HW - on (default)
# RPS = Receive Packet Steering - SW/HW - off (default)
# LRO = Large Receive Offload - impossible when HW-offload enabled
# TSO = TCP Segmentation Offload - impossible when HW-offload enabled

# These first three blocks are for the 1G RJ45 network interfaces eth0-2
set interfaces ethernet eth0 address 'dhcp'
set interfaces ethernet eth0 address '10.64.0.32/24'
set interfaces ethernet eth0 description 'MGMT (RJ45 left)'
set interfaces ethernet eth0 hw-id '<SNIP>'
set interfaces ethernet eth0 offload gro
set interfaces ethernet eth0 offload gso
set interfaces ethernet eth0 offload sg

set interfaces ethernet eth1 address 'dhcp'
set interfaces ethernet eth1 description 'RJ45 center'
set interfaces ethernet eth1 hw-id '<SNIP>'
set interfaces ethernet eth1 offload gro
set interfaces ethernet eth1 offload gso
set interfaces ethernet eth1 offload sg

set interfaces ethernet eth2 address 'dhcp'
set interfaces ethernet eth2 description 'WAN'
set interfaces ethernet eth2 hw-id '<SNIP>'
set interfaces ethernet eth2 offload gro
set interfaces ethernet eth2 offload gso
set interfaces ethernet eth2 offload sg

# These last blocks are for the 10G SFP+ network interfaces eth3-4
# NB: IPv4 and IPv6 addresses share the same syntax
# Three new offload types are defined here:
# hw-tc-offload = tc flower - a flow classifier, offloaded to HW
# ipv4 = 'arms' this interface for HW-offload of IPv4 traffic
# ipv6 = 'arms' this interface for HW-offload of IPv6 traffic
set interfaces ethernet eth3 address 'dhcp'
set interfaces ethernet eth3 address '10.100.1.1/24'
set interfaces ethernet eth3 address 'fd36:dead:beef:3::0a64:0101/64'
set interfaces ethernet eth3 description 'SFP+ left'
set interfaces ethernet eth3 hw-id '<SNIP>'
set interfaces ethernet eth3 offload gro
set interfaces ethernet eth3 offload gso
set interfaces ethernet eth3 offload hw-tc-offload
set interfaces ethernet eth3 offload ipv4
set interfaces ethernet eth3 offload ipv6
set interfaces ethernet eth3 offload sg

# These two statements define addresses for VLAN '6' on eth3
set interfaces ethernet eth3 vif 6 address '10.150.1.1/24'
set interfaces ethernet eth3 vif 6 address 'fd36:dead:beef:6::0a64:0101/64'

set interfaces ethernet eth4 address 'dhcp'
set interfaces ethernet eth4 address '10.200.1.1/24'
set interfaces ethernet eth4 address 'fd36:dead:beef:4::0ac8:0101/64'
set interfaces ethernet eth4 description 'SFP+ right'
set interfaces ethernet eth4 hw-id '<SNIP>'
set interfaces ethernet eth4 offload gro
set interfaces ethernet eth4 offload gso
set interfaces ethernet eth4 offload hw-tc-offload
set interfaces ethernet eth4 offload ipv4
set interfaces ethernet eth4 offload ipv6
set interfaces ethernet eth4 offload sg

set interfaces ethernet eth4 vif 8 address '10.250.1.1/24'
set interfaces ethernet eth4 vif 8 address 'fd36:dead:beef:8::0ac8:0101/64'
```

### 4.3.5 Services
**Upstream VyOS docs:** [Services](https://docs.vyos.io/en/rolling/configuration/service/index.html)

Commands all start with the prefix `set service ...`, and re-ordering for readability:
```bash
# This is a short section, but in most common configs will be a lot longer as this is where DHCP Server, and DNS forwarding etc along with other services are defined 
# 'set service dhcp-server ...` commands are a notable omission here, that most will define as DHCP remains a common router-hosted service.
# This test config needed neither DHCP nor DNS forwarding, so neither exists here.

# This explicitly disables the use of passwords for SSH auth to VyOS
set service ssh disable-password-authentication

# This section enables REST API access via HTTPS, an defines a key to do so for use with VyManager. See VYMANAGER.md for more.
set service https api graphql authentication type 'key'
set service https api keys id vymanager key '<SNIP>'
set service https api rest

# This section defines upstream NTP servers to sync with
set service ntp server time.apple.com
set service ntp server time.cloudflare.com
set service ntp server time.google.com

# This section defines downstream address ranges who can query VyOS for NTP
set service ntp allow-client address '127.0.0.0/8'
set service ntp allow-client address '169.254.0.0/16'
set service ntp allow-client address '10.0.0.0/8'
set service ntp allow-client address '172.16.0.0/12'
set service ntp allow-client address '192.168.0.0/16'
set service ntp allow-client address '::1/128'
set service ntp allow-client address 'fe80::/10'
set service ntp allow-client address 'fc00::/7'
```

### 4.5.6 Firewall
**Upstream VyOS docs:** [Firewall](https://docs.vyos.io/en/rolling/configuration/firewall/index.html) + [Flowtables](https://docs.vyos.io/en/rolling/configuration/firewall/flowtables.html)

This is easily the most complex areas of config, but this demo config focuses near-exclusively on where firewall config and HW-offloading intersect. Commands all start with the prefix `set firewall ...`, and re-ordering for readability:
```bash
# A flowtable is a 'fastpath' that can exist in Software, or Hardware which processes the first packet in a flow, and if permitted, enables subsequent packets in the same flow to bypass further packet processing. Even in software, this can see a 2-3x throughput increase in packets-per-second, and HW-offload massively increases this further, as the CPU never has to process anything besides the first packet.

# This first block (implicitly) defines a flowtable named 'ft01', and adds eth3, eth4, eth3 VLAN 6, and eth4 VLAN 8.
# Membership of the flowtable already provides ~2-3x throughput by reducing CPU packet processing. On the Mono GW this can provide ~5 Gbps goodput even in software.
set firewall flowtable ft01 interface 'eth3'
set firewall flowtable ft01 interface 'eth4'
set firewall flowtable ft01 interface 'eth3.6'
set firewall flowtable ft01 interface 'eth4.8'

# This line of config moves the flowtable into HW-offload, and ENABLES HW-offload for all 'armed' interfaces, as defined in the interfaces branch.
set firewall flowtable ft01 offload 'hardware'

# However, 'Arming' interfaces, and 'Enabling' HW-offload of the flowtable will do nothing unless there are also firewall rules that trigger a flow to offload.
# Firewall rules are critical to functional HW-offload, and it's important to understand what they mean, and how they work.

# 1) Firewall rules are evaluated in strict order, lowest rule index [number] first.
# 2) First, you need a firewall rule to define 'ACCEPT' or 'DROP'. These are your normal firewall rules.
# 3) Second you need a firewall rule, like those below, that triggers after the first packet has been evaluated, and enable a flow to move into HW-offload.

# Read the rules as this: 
# Rule 10: if an IPv4 <packet> is related to-, or part of an established flow, then 'offload' it via flowtable 'ft01'
set firewall ipv4 forward filter rule 10 state 'established'
set firewall ipv4 forward filter rule 10 state 'related'
set firewall ipv4 forward filter rule 10 action 'offload'
set firewall ipv4 forward filter rule 10 offload-target 'ft01'

# Rule 11 duplicates rule 10, but applies to IPv6 traffic
set firewall ipv6 forward filter rule 11 state 'established'
set firewall ipv6 forward filter rule 11 state 'related'
set firewall ipv6 forward filter rule 11 action 'offload'
set firewall ipv6 forward filter rule 11 offload-target 'ft01'
```

>**NOTE:** You should ***NEVER*** have state 'accept' in a firewall rule with action 'offload'. This may see flows move into HW-offload before later firewall rules are evaluated, with would otherwise then block it. This is unsafe, and insecure.
 
### 4.3.7 'Routing'
**Upstream VyOS docs:** [Protocols](https://docs.vyos.io/en/rolling/configuration/protocols/index.html) + [Policy](https://docs.vyos.io/en/rolling/configuration/policy/index.html) + [NAT](https://docs.vyos.io/en/rolling/configuration/nat/index.html)

Unlike other sections, there are multiple config branches and their equivalents command prefixes  that impact how a VyOS instance actively routes traffic. Applicable commands from the demo config are grouped here, but do note this is very simplistic in this regard. Config for other active routing protocols (BGP, OSPF, OSPFv3, IS-IS etc) is found in the protocols branch, whilst NAT, policer, QoS and other routing and shaping config lives in separate named branches.

```bash
# This first statement defines a static default route
# Context: a default route is always used, when the router has no other defined route to a target address. It is the 'catch all' route of last resort, here in this test environment, that points towards another router, and WAN beyond it.
set protocols static route 0.0.0.0/0 next-hop 10.1.9.1

# This block defines an outbound dynamic IPv4 'Source NAT' rule 100, where any traffic from the 10.100.1.0/24 network leaving via eth4 has it's source IP ammended to use the address of VyOS on eth4.
# 'NAT' is implicitly IPv4-IPv4
set nat source rule 100 source address '10.100.1.0/24'
set nat source rule 100 outbound-interface name 'eth4'
set nat source rule 100 translation address 'masquerade'

# 'NAT66' is IPv6-IPv6
# This block defines an NAT66 rule for interface eth4
set nat66 source rule 110 outbound-interface name 'eth4'
set nat66 source rule 110 source prefix 'fd36:dead:beef:3::/64'
set nat66 source rule 110 translation address 'masquerade'

# This block defines an NAT66 rule for the VLAN subinterface eth4 VLAN 8
set nat66 source rule 130 outbound-interface name 'eth4.8'
set nat66 source rule 130 source prefix 'fd36:dead:beef:6::/64'
set nat66 source rule 130 translation address 'masquerade'
```
NB: IPv6 done right should never require NAT66...

### 4.3.8 Containers
**Upstream VyOS docs:** [Container](https://docs.vyos.io/en/rolling/configuration/container/index.html) + [examples](https://docs.vyos.io/en/rolling/configuration/container/index.html#example-configurations)

```bash
# The demo config contains no config elements in the container VyOS config branch
```

### 4.3.9 Mono HW-offloading

HW-offloading is like a gun. In order to fire, several conditions must all be met at the same time.

**ARMED** (interfaces) + **ENABLED** (HW-offload of flowtable) + **TRIGGERED** (by firewall rules) = HW-offload fires.

You have now seen: 
- How the interface branch of VyOS config 'ARMS' an interface for HW-offload 
- How the firewall flowtables config 'ENABLES' the HW-offload of a flowtable
- How the firewall rules themselves then 'TRIGGER' HW-offload behaviour for accepted flows

>**CRITICAL:** For HW-offloading to function, ***ALL*** of the above must be defined and apply. 

# 5. Configuration: Getting started
**Upstream VyOS docs:** [Quick-Start](https://docs.vyos.io/en/rolling/quick-start.html)

WIP