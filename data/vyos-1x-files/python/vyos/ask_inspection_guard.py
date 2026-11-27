# Copyright (C) VyOS maintainers and contributors <maintainers@vyos.io>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2 or later as
# published by the Free Software Foundation.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
# LS1046A ASK2 G7 Track 0 (D8): commit-time guard and status reporting for
# known silent-bypass combinations between inspection (passive Suricata or
# hand-built firewall `action queue` NFQUEUE rules) and fast-path offload
# (ASK2 hardware offload and nftables flowtables).
#
# Scope note: this guard catches named, validated bypass combinations only.
# It does NOT guarantee full capture or enforcement; capture loss, E1
# software-flowtable visibility, non-monitored paths, and exclusions remain
# the operator's responsibility.

from vyos import ConfigError


def _normalize_list(val):
    if not val:
        return []
    if isinstance(val, str):
        return [val]
    return list(val)


def _parent_physical(ifname):
    """Return physical parent for a VLAN vif (e.g. eth3.10 -> eth3), else None."""
    if '.' in ifname:
        return ifname.split('.', 1)[0]
    return None


def resolve_base_ifaces(ifname, bridges=None):
    """Expand an interface name into the set of interfaces/ports it covers.

    - Physical port `eth3` -> {'eth3'}
    - VLAN vif `eth3.10` -> {'eth3.10', 'eth3'}
    - Bridge `br0` -> {'br0'} union its configured member interfaces (and their
      VLAN parents if a member is a vif).
    """
    covered = {ifname}
    parent = _parent_physical(ifname)
    if parent:
        covered.add(parent)
    br_conf = (bridges or {}).get(ifname)
    if isinstance(br_conf, dict):
        members = (br_conf.get('member') or {}).get('interface') or {}
        if isinstance(members, dict):
            member_names = members.keys()
        else:
            member_names = _normalize_list(members)
        for member in member_names:
            covered.add(member)
            mp = _parent_physical(member)
            if mp:
                covered.add(mp)
    return covered


def get_offloaded_ifaces(ethernet=None, firewall=None):
    """Collect interfaces with active ASK2 hardware offload or flowtables.

    Returns `(hw_ifaces, ft_ifaces_by_family)`:
    - `hw_ifaces`: dict mapping interface name -> sorted list of HW offload
      sources (`ethernet offload ipv4/ipv6` or `flowtable <name> (hardware)`).
    - `ft_ifaces_by_family`: `{'ipv4': {ifname: [...]}, 'ipv6': {ifname: [...]}}`
      covering BOTH software and hardware user flowtables plus the G1 auto
      hardware flowtable (`VYOS_FLOWTABLE_ASK_AUTO`).
    """
    ethernet = ethernet or {}
    firewall = firewall or {}

    hw_sources = {}
    ft_by_fam = {'ipv4': {}, 'ipv6': {}}
    user_ft_ifaces = set()

    for ft_name, ft_conf in sorted((firewall.get('flowtable') or {}).items()):
        if not isinstance(ft_conf, dict):
            continue
        is_hw = ft_conf.get('offload') == 'hardware'
        label = f'flowtable "{ft_name}" ({"hardware" if is_hw else "software"})'
        for ifname in _normalize_list(ft_conf.get('interface')):
            user_ft_ifaces.add(ifname)
            for fam in ('ipv4', 'ipv6'):
                ft_by_fam[fam].setdefault(ifname, []).append(label)
            if is_hw:
                hw_sources.setdefault(ifname, []).append(label)

    for ifname, if_conf in sorted(ethernet.items()):
        if not isinstance(if_conf, dict) or 'disable' in if_conf:
            continue
        offload = if_conf.get('offload') or {}
        fams = [f for f in ('ipv4', 'ipv6') if f in offload]
        if not fams:
            continue
        label = f'ethernet offload {"/".join(fams)}'
        hw_sources.setdefault(ifname, []).append(label)
        if ifname not in user_ft_ifaces:
            for fam in fams:
                ft_by_fam[fam].setdefault(ifname, []).append(
                    f'auto flowtable ({label})'
                )

    return hw_sources, ft_by_fam


def find_suricata_offload_conflicts(suricata, ethernet=None, firewall=None, bridges=None):
    """Return list of `(monitored_if, offload_if, sources)` for passive Suricata."""
    if not suricata or not isinstance(suricata, dict):
        return []
    monitored = _normalize_list(suricata.get('interface'))
    if not monitored:
        return []

    hw_sources, _ = get_offloaded_ifaces(ethernet, firewall)
    if not hw_sources:
        return []

    # Map each HW-offloaded interface (and its parent physical port if vif)
    # back to the concrete configured offload entries.
    conflicts = []
    seen = set()
    for mon_if in sorted(set(monitored)):
        mon_covered = resolve_base_ifaces(mon_if, bridges)
        for off_if, sources in sorted(hw_sources.items()):
            off_covered = resolve_base_ifaces(off_if, bridges)
            if mon_covered & off_covered:
                key = (mon_if, off_if)
                if key not in seen:
                    seen.add(key)
                    conflicts.append((mon_if, off_if, list(sources)))
    return conflicts


def _expand_interface_group(group_name, iface_groups, visited=None):
    if visited is None:
        visited = set()
    if group_name in visited or group_name not in iface_groups:
        return set(), True
    visited.add(group_name)
    grp = iface_groups[group_name] or {}
    names = set()
    unbounded = False
    for iface in _normalize_list(grp.get('interface')):
        if iface.startswith('!') or '*' in iface:
            unbounded = True
        else:
            names.add(iface)
    for inc in _normalize_list(grp.get('include')):
        sub_names, sub_unbounded = _expand_interface_group(inc, iface_groups, visited)
        names.update(sub_names)
        unbounded = unbounded or sub_unbounded
    return names, unbounded


def _rule_scoped_ifaces(rule_conf, iface_groups):
    """Return concrete interface names if the rule restricts ingress/egress,
    or None if the rule may match traffic on any interface."""
    scoped = set()
    has_positive_scope = False
    for direction in ('inbound_interface', 'outbound_interface'):
        clause = rule_conf.get(direction)
        if not isinstance(clause, dict):
            continue
        name = clause.get('name')
        if name:
            if name.startswith('!') or '*' in name:
                return None
            scoped.add(name)
            has_positive_scope = True
        grp = clause.get('group')
        if grp:
            if grp.startswith('!'):
                return None
            grp_ifaces, unbounded = _expand_interface_group(grp, iface_groups)
            if unbounded or not grp_ifaces:
                return None
            scoped.update(grp_ifaces)
            has_positive_scope = True
    return scoped if has_positive_scope else None


def _collect_forward_queue_rules(firewall, family):
    """Yield `(chain_label, rule_id, rule_conf)` for enabled `action queue`
    rules reachable from `<family> forward filter`."""
    fam_conf = firewall.get(family)
    if not isinstance(fam_conf, dict):
        return

    fwd_filter = (fam_conf.get('forward') or {}).get('filter')
    if not isinstance(fwd_filter, dict):
        return

    named_chains = fam_conf.get('name') or {}
    visited_names = set()

    def walk_chain(chain_label, chain_conf):
        if not isinstance(chain_conf, dict):
            return
        if chain_conf.get('default_action') == 'jump':
            target = chain_conf.get('default_jump_target')
            if target and target not in visited_names and target in named_chains:
                visited_names.add(target)
                yield from walk_chain(f'name {target}', named_chains[target])
        for rule_id, rule_conf in sorted(
            (chain_conf.get('rule') or {}).items(),
            key=lambda kv: (int(kv[0]) if str(kv[0]).isdigit() else 0, str(kv[0])),
        ):
            if not isinstance(rule_conf, dict) or 'disable' in rule_conf:
                continue
            action = rule_conf.get('action')
            if action == 'queue':
                yield (chain_label, str(rule_id), rule_conf)
            elif action == 'jump':
                target = rule_conf.get('jump_target')
                if target and target not in visited_names and target in named_chains:
                    visited_names.add(target)
                    yield from walk_chain(f'name {target}', named_chains[target])

    yield from walk_chain('forward filter', fwd_filter)


def find_queue_offload_conflicts(firewall, ethernet=None, bridges=None):
    """Return list of `(family, chain_label, rule_id, overlap_ifaces)` where a
    forward-hook `action queue` rule can be silently bypassed by an active
    software or hardware flowtable (including the G1 auto flowtable)."""
    if not firewall or not isinstance(firewall, dict):
        return []

    _, ft_by_fam = get_offloaded_ifaces(ethernet, firewall)
    iface_groups = (firewall.get('group') or {}).get('interface_group') or {}
    conflicts = []

    for family in ('ipv4', 'ipv6'):
        active_ft = ft_by_fam.get(family) or {}
        if not active_ft:
            continue
        for chain_label, rule_id, rule_conf in _collect_forward_queue_rules(firewall, family):
            scoped = _rule_scoped_ifaces(rule_conf, iface_groups)
            if scoped is None:
                overlap = sorted(active_ft.keys())
            else:
                scoped_covered = set()
                for iface in scoped:
                    scoped_covered.update(resolve_base_ifaces(iface, bridges))
                overlap = []
                for ft_if in sorted(active_ft.keys()):
                    if scoped_covered & resolve_base_ifaces(ft_if, bridges):
                        overlap.append(ft_if)
            if overlap:
                conflicts.append((family, chain_label, rule_id, overlap))

    return conflicts


def verify_suricata_offload_guard(suricata, ethernet=None, firewall=None, bridges=None):
    """Raise ConfigError if passive Suricata shares an interface with HW offload
    and `service suricata allow-offload-bypass` is not set (G7 Track 0 / D8)."""
    if not suricata or not isinstance(suricata, dict):
        return
    if 'allow_offload_bypass' in suricata:
        return
    conflicts = find_suricata_offload_conflicts(suricata, ethernet, firewall, bridges)
    if not conflicts:
        return
    details = '; '.join(
        f'{mon_if} <-> {off_if} ({", ".join(sources)})'
        for mon_if, off_if, sources in conflicts
    )
    raise ConfigError(
        f'service suricata shares monitored interface(s) with ASK2 hardware '
        f'offload: {details}. Hardware-offloaded frames bypass the CPU and are '
        f'invisible to AF_PACKET capture. Disable hardware offload on those '
        f'interface(s) (and remove them from hardware flowtables), or explicitly '
        f'acknowledge the bypass with '
        f'"set service suricata allow-offload-bypass".'
    )


def verify_queue_offload_guard(firewall, ethernet=None, bridges=None):
    """Raise ConfigError if a forward-hook `action queue` (NFQUEUE) rule
    overlaps an active flowtable or ASK2 hardware offload and
    `firewall global-options allow-queue-offload-bypass` is not set (D8)."""
    if not firewall or not isinstance(firewall, dict):
        return
    gopts = firewall.get('global_options') or {}
    if 'allow_queue_offload_bypass' in gopts:
        return
    conflicts = find_queue_offload_conflicts(firewall, ethernet, bridges)
    if not conflicts:
        return
    details = '; '.join(
        f'{fam} {chain} rule {rid} overlaps flowtable/offload on {", ".join(ifs)}'
        for fam, chain, rid, ifs in conflicts
    )
    raise ConfigError(
        f'firewall action queue (NFQUEUE) conflicts with active flowtable / '
        f'hardware offload: {details}. Established flows admitted to a '
        f'flowtable bypass the forward hook and skip NFQUEUE inspection '
        f'silently. Disable offload/flowtable on those interface(s), or '
        f'explicitly acknowledge the bypass with '
        f'"set firewall global-options allow-queue-offload-bypass".'
    )


def describe_inspection_bypass_status(suricata, ethernet=None, firewall=None, bridges=None):
    """Return human-readable status lines for `show offload config` (D8)."""
    lines = []
    suricata = suricata or {}
    firewall = firewall or {}

    suricata_conflicts = find_suricata_offload_conflicts(
        suricata, ethernet, firewall, bridges
    )
    queue_conflicts = find_queue_offload_conflicts(firewall, ethernet, bridges)

    if 'allow_offload_bypass' in suricata:
        if suricata_conflicts:
            ifaces = ', '.join(sorted({c[0] for c in suricata_conflicts}))
            lines.append(
                f'WARNING: service suricata allow-offload-bypass active on '
                f'{ifaces} (hardware-offloaded packets bypass AF_PACKET capture)'
            )
        else:
            lines.append(
                'NOTE: service suricata allow-offload-bypass is set '
                '(no monitored interface currently has hardware offload)'
            )
    elif suricata and suricata.get('interface'):
        mon = ', '.join(sorted(set(_normalize_list(suricata.get('interface')))))
        lines.append(
            f'Suricata passive IDS monitored interface(s): {mon} '
            f'(no HW offload; capture loss and SW-flowtable visibility are not guaranteed)'
        )

    gopts = firewall.get('global_options') or {}
    if 'allow_queue_offload_bypass' in gopts:
        if queue_conflicts:
            rules = ', '.join(
                f'{fam} {chain} rule {rid}' for fam, chain, rid, _ in queue_conflicts
            )
            lines.append(
                f'WARNING: firewall global-options allow-queue-offload-bypass '
                f'active for {rules} (offloaded flows bypass NFQUEUE)'
            )
        else:
            lines.append(
                'NOTE: firewall global-options allow-queue-offload-bypass is set '
                '(no forward action queue rule currently overlaps a flowtable)'
            )

    return lines
