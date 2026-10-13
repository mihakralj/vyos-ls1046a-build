# CLI Commands & Bundled Tools — Mono Gateway DK (LS1046A)

Everything this build adds on top of stock VyOS: new configuration commands,
operational (`show`) commands, shell diagnostics, and third-party tools baked
into the ISO. Sources: `data/vyos-1x-*.patch`, `data/vyos-1x-files/`,
`board/scripts/`, `bin/ci-build-iso.sh`, `bin/ci-install-extra-packages.sh`.

Hardware-control recipes (LED, fan sysfs) are in [HWCTL.md](HWCTL.md);
installation is in [INSTALL.md](INSTALL.md).

```mermaid
flowchart LR
    A[Configuration mode<br/>set / commit] --> B[Hardware offload<br/>ASK2 FMan]
    A --> D[Policer, firewall, conntrack, HTTPS API/MCP]
    E[Operational mode<br/>show ...] --> F[offload / hardware]
    G[Shell tools<br/>/usr/local/bin] --> H[*-check, led, support-bundle]
```

## 1. Configuration commands

All are entered in configuration mode (`configure` … `commit` / `save`).

### 1.1 Hardware routing offload (ASK2)

Patches: `031`, `036`, `039`, `053`/`054` (final form), `051`, `052`.

```
set interfaces ethernet eth<n> offload ipv4
set interfaces ethernet eth<n> offload ipv6
```

| Item | Detail |
|------|--------|
| Meaning | Engages FMan hardware forwarding on that port for the given IP family. Each family is independent. |
| Ports | `eth0`–`eth4` (eth0 is management by convention; avoid it). |
| Automatic parts | VLAN, NAT44/NAT66/PAT, PPPoE (on the PPPoE source port) and bridge offload follow an engaged port. There are **no** `offload vlan` / `offload pppoe` leaves (removed by patch `054`), and no `ask` keyword. |
| Side effects | Forces RPS on the port and enables `hw-tc-offload`. |
| Validation | Commit fails if the helper `/usr/local/bin/vyos-offload-ask` is missing/errors, or if the MTU is outside **1280–3600**. |

Engaging a port only arms it; flows are learned **only** from a hardware
flowtable (next section). An engaged port without a flowtable offloads nothing.

#### Flow learning: hardware flowtable

```
set firewall flowtable FT interface eth3
set firewall flowtable FT interface eth4
set firewall flowtable FT offload hardware
set firewall ipv4 forward filter rule 10 action offload
set firewall ipv4 forward filter rule 10 offload-target FT
set firewall ipv4 forward filter rule 10 state established
set firewall ipv4 forward filter rule 10 state related
set firewall global-options hardware-offload-delay 500   # verify path with: set firewall global-options ?
```

* `action offload` is non-terminal: an earlier `accept` rule matching established
  traffic in the same chain shadows it (commit warns for global state-policy accepts, patch `038`).
* `hardware-offload-delay` (patch `055`): minimum flow age in ms, `0–60000`, default `500`.
* Flowtable MTU guard (`037`) mirrors the 1280–3600 range; VLAN sub-interfaces are handled when the parent is listed (`043`).
* Add the same flowtable rule for `ipv6` for IPv6 flows.

#### Disable

```
delete interfaces ethernet eth3 offload
```

### 1.2 Hardware ingress policer (FMan Policer)

Patches `024`, `025`.

```
set interfaces ethernet eth3 ingress-policer bandwidth 1gbit
set interfaces ethernet eth3 ingress-policer burst 256kb
set interfaces ethernet eth3 ingress-policer peak-bandwidth 2gbit
```

| Leaf | Meaning |
|------|---------|
| `bandwidth` | **Required.** CIR; `<n>`, `bit`, `kbit`, `mbit`, `gbit`, `tbit`. |
| `burst` | Burst bytes (`<n>`, `kb`, `mb`, `gb`). |
| `peak-bandwidth` | Optional PIR; upgrades srTCM to two-rate trTCM. |

Auto-enables `hw-tc-offload` on the port and installs a matchall→police filter
(`skip_sw`/`in_hw`). Verify with `tc -s filter show dev eth3 ingress`.
Software `tc police` counters stay 0 because the policer is fully offloaded.
Test with a few pings, **never a flood** (open issue in the policer-removal path).

### 1.3 Conntrack: WireGuard bypass

Patches `056`, `057`. WireGuard listen-port UDP flows are **not tracked by default** (fast path).

```
set system conntrack disable-wireguard-bypass     # track them again
```

### 1.4 HTTPS API and MCP server (default off)

Patch `041` (+ `vyos-http-api-tools` rebuild by `bin/ci-build-http-api-tools.sh`).

```
set service https api keys id mykey key '<secret>'
set service https api mcp
set service https api mcp mode read-only        # default; or read-write
set service https api mcp introspection         # expose schema resources
```

Local stdio access for an agent: `ssh vyos@<board> vyos-mcp` (exits non-zero
with "MCP is not enabled" until `api mcp` is configured).

### 1.5 Console / system tweaks

* `set system console device ttyS0 speed 115200` — console is `ttyS0` (patch `001`).
* `set interfaces ethernet eth<n> hw-id <mac>` — pins a port name; always wins over the DT-based ordering (patch `027`/`047`).
* Hidden: LRO/TSO ethtool offload leaves are not offered (hardware-impossible, patch `034`). Supported: `gro gso sg rps`.
* Jumbo frames: kernel parameter `fsl_dpaa_fman.fsl_fm_max_frm=9600` (RJ45 MTU up to 9578).

### 1.6 Image management (behaviour changes, same commands)

| Command | LS1046A behaviour |
|---------|-------------------|
| `add system image latest` / `<url>` | Copies kernel, DTB, writes `/boot/vyos.env` and re-asserts the U-Boot boot order (`006`, `011`, `028`). |
| `show system image` | Works under U-Boot (live-boot detection `009`). |
| `install image` | USB-live only; never on an installed system. |

## 2. Operational commands

| Command | Shows |
|---------|-------|
| `show offload config` | Per-port IPv4/IPv6 offload intent vs. live FMan armed state. |
| `show offload flow` | Flows the FMan has learned (via `ynl --family ask`). |
| `show interfaces ethernet eth<n> offload ask flows` | Offloaded flows for one port. |
| `show hardware dpaa` / `… ports` / `… fman` / `… raw` | DPAA1 complex, MEMAC↔interface map, FMan + MURAM, JSON. |
| `show hardware crypto` / `… all` / `… raw` | CAAM SEC 5.4 status, all registered algorithms, JSON. |
| `show hardware fan` | Thermal zones and EMC2305 fan controller. |
| `show hardware firmware` | Boot firmware and FMan microcode inventory. |
| `show interfaces ethernet eth<n> physical offload` | ethtool offload feature state (LRO/TSO hidden). |
| `show cpu` / `show hardware` | ARM64-aware CPU and hardware info (`015`, `029`). |

## 3. Shell tools shipped by this build

Installed in `/usr/local/bin/`. Diagnostics use exit codes **0** healthy,
**1** fault, **2** wrong board / feature absent. Full table: [HWCTL.md §3](HWCTL.md).

| Tool | Purpose |
|------|---------|
| `dpaa1-check` | DPAA1 networking posture (FMan/QMan/BMan, drivers, ports). |
| `sfp-check` | Decode SFP/SFP+ EEPROM, print quirk line for rollball modules. |
| `fan-check` | Thermal zones, PWM/RPM, `fan-pid` health. |
| `caam-check` | CAAM crypto engine, job rings, hwrng. |
| `ask-check` | ASK2 datapath health, layer by layer. Use `sudo`. |
| `firmware-check` | U-Boot, QSPI, FMan microcode, `vyos.env`. Use `sudo`. |
| `support-bundle` | One paste-ready report: `sudo support-bundle > bundle.txt`. |
| `pcd-snapshot capture\|diff\|show` | Capture/diff FMan PCD register state (reversibility gate; mutate eth3 only, never eth0). |
| `vyos-offload-ask` | Low-level ASK engage/disengage helper called by the CLI; avoid direct use. |
| `ynl --family ask …` | Vendored YNL client talking straight to `ask.ko`. |
| `led` | RGBW status LED: `led 17`, `led R G B W`, `led '#33003300'`, `led off`. See [HWCTL.md §1.6](HWCTL.md). |
| `fan-pid` / `monoledd` / `fman-fq-qdisc` | Daemons (systemd): PID fan control, load-driven LED, `fq` qdisc on 10G ports. |
| `vyos-postinstall` | Writes U-Boot env and `/boot/vyos.env` (runs automatically). |
| `vyos-mcp` | MCP stdio endpoint (see 1.4). |

## 4. Third-party tools in the image

**Debian packages** (`--custom-package` in `bin/ci-build-iso.sh`):

| Category | Packages |
|----------|----------|
| Editors / shell | `vim-tiny`, `tmux`, `tree`, `jq`, `ripgrep`, `ncdu`, `btop` |
| Network testing | `iperf3`, `netperf`, `nuttcp`, `flent`, `hping3`, `fping`, `mtr-tiny`, `socat`, `ngrep`, `iftop` |
| Network inspection | `ethtool`, `iproute2`, `nftables`, `conntrack`, `wget` |
| DDoS detection | `fastnetmon` |
| Debugging | `strace`, `lsof`, `psmisc`, `sysstat`, `binutils` |
| Boot / firmware | `u-boot-tools`, `libubootenv-tool` (`fw_printenv`/`fw_setenv`), `grub-efi-arm64-signed` |
| Containers | `containernetworking-plugins`, `skopeo`, `catatonit`, `uidmap`, `fuse-overlayfs` |

**Static binaries** (`bin/ci-install-extra-packages.sh`, best-effort): Ookla
`speedtest`, `bandwhich`, `trippy`, `gping`, `doggo`.

**Built from source in CI:** `accel-ppp-ng` (PPPoE/IPoE daemon plus `ipoe.ko`/`vlan_mon.ko`), rebuilt `vyos-http-api-tools` (MCP SDK), patched `vyos-1x`, and the Linux kernel with signed out-of-tree modules (`ask.ko`, LP5812 LED driver).

**Hardware related:** `libatomic1`; `fancontrol.service` is masked in favour of `fan-pid`.