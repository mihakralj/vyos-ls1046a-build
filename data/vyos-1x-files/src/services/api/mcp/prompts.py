import ipaddress
import logging
import os
import re

from mcp.server import Server
from mcp.types import Prompt, PromptArgument, PromptMessage, GetPromptResult, TextContent

LOG = logging.getLogger('http_api.mcp.prompts')

_CUSTOM_PROMPTS_DIR = '/config/user-data/mcp/prompts'


def _capabilities_overview_text(read_only, introspection_enabled):
    read_tools = (
        '== TOOLS ==\n'
        '  execute_operational_command(path) — Run any op-mode command. Path is an array: '
        '["show","interfaces"], ["ping","192.168.1.1"]. First element is the op-mode verb '
        '(show, ping, monitor, clear, etc.).\n'
    )
    write_tools = (
        '  modify_configuration(operations, commit_confirm_minutes?) — Stage set/delete ops '
        'and atomically commit. Always use commit_confirm_minutes for changes that could '
        'break connectivity.\n'
        '  manage_system_image(action, url?, name?) — Add/delete/set-default system images.\n'
        '  reboot_system(path?) — Reboot, defaults to ["now"].\n'
        '  poweroff_system(path?) — Power off, defaults to ["now"].\n'
        '  save_configuration(file?) — Save running config to disk.\n'
    )
    tools = read_tools + ('' if read_only else write_tools) + '\n'

    config_schema_usage = (
        'inspect valid config paths, types, defaults and constraints.\n'
        if read_only else
        'inspect valid paths, types, defaults and constraints BEFORE calling modify_configuration.\n'
    )
    schema_resources = (
        '  vyos-schema://config/{path} — JSON Schema for CLI config nodes; ' + config_schema_usage
        + '  vyos-schema://op/{path} — Valid op-mode continuations. Use BEFORE '
        'execute_operational_command to discover available sub-commands.\n'
    )
    resources = (
        '== RESOURCES ==\n'
        '  vyos-config://running/{path} — Active config subtree as JSON.\n'
        '  vyos-config://effective/{path} — Check if a node is effectively applied.\n'
        + (schema_resources if introspection_enabled else '')
        + '  vyos-state://operational/{path} — Operational state output.\n\n'
    )

    if read_only:
        mode_line = (
            'This session is READ-ONLY: only execute_operational_command and the read '
            'resources are available. Configuration-changing tools are not exposed. Do not '
            'claim to have applied changes.'
        )
    else:
        mode_line = (
            'This session is READ-WRITE: configuration, image management and reboot/poweroff '
            'tools are available. Treat as root-equivalent and prefer commit-confirm for '
            'connectivity-affecting changes.'
        )
    if not introspection_enabled:
        mode_line += (
            ' Schema introspection resources (vyos-schema://) are disabled on this session; '
            'rely on documented command paths and validation errors instead.'
        )

    patterns = '== PATTERNS ==\n'
    if introspection_enabled:
        patterns += '1. Schema-first: check vyos-schema before modifying config.\n'
    patterns += (
        '2. Check current state: read vyos-config before proposing changes.\n'
    )
    if not read_only:
        patterns += (
            '3. Commit-confirm for risky changes: WAN, routing, firewall.\n'
            '4. Atomic multi-step: batch all set/delete ops in one modify_configuration call.\n'
        )
    patterns += (
        '5. Op-mode path format: verb first, then sub-path. The server validates path '
        'prefixes against its command cache.\n\n'
    )

    return (
        'You are connected to a VyOS router via the Model Context Protocol (MCP). '
        'You can monitor and inspect this router'
        + ('.' if read_only else ', and change its configuration.') + '\n\n'
        + tools
        + resources
        + '== OTHER PROMPTS ==\n'
        '  troubleshoot_internet_connectivity — Guided home internet-down diagnosis\n'
        '  analyze_security_posture — Educational home-router security review\n'
        '  troubleshoot_asymmetric_routing — Routing-loop diagnosis workflow\n'
        + ('' if read_only or not introspection_enabled else
           '  provision_site_to_site_vpn — IPsec tunnel provisioning workflow\n')
        + '  audit_firewall_posture — Expert firewall ruleset audit\n\n'
        + patterns
        + mode_line
    )


def _troubleshoot_routing_text(_read_only, introspection_enabled):
    schema_step = (
        '   - Read vyos-schema://op/show/ip/route before running commands to confirm the live command tree.\n'
        if introspection_enabled else
        '   - Schema introspection is disabled; start with the conservative path ["show", "ip", "route"] and adapt to validation errors.\n'
    )
    return (
        'You are diagnosing asymmetric routing or a routing-loop issue on a VyOS router.\n\n'
        '1. Check the routing table:\n'
        + schema_step
        + '   - Use execute_operational_command with path ["show", "ip", "route"].\n'
        '   - Look for unexpected ECMP entries, recursive next-hops, or policy-selected routes.\n\n'
        '2. Inspect conntrack only after discovering the live op-mode path'
        + (' through vyos-schema://op/show/conntrack.\n' if introspection_enabled else '.\n')
        + '   - Look for UNREPLIED or ASSURED flows and direction/address asymmetry.\n\n'
        '3. Examine BGP if learned routes are involved:\n'
        '   - Discover the BGP show path when introspection is available; otherwise try ["show", "ip", "bgp", "summary"].\n'
        '   - Check peer churn, best-path changes, and stale routes.\n\n'
        '4. Check interface counters with ["show", "interfaces"] and correlate drops/errors with the affected direction.\n\n'
        '5. Read vyos-config://running/protocols and vyos-config://running/policy.\n'
        '   - Look for route-maps, policy routes, VRFs, or static routes that create asymmetric paths.\n\n'
        'Report observations before recommending changes; do not infer a routing loop from one table alone.'
    )


def _audit_firewall_text(_read_only, introspection_enabled):
    schema_step = (
        '1. Read vyos-schema://config/firewall and follow the live children; do not assume legacy `name` or `zone-policy` nodes exist.\n'
        if introspection_enabled else
        '1. Schema introspection is disabled. Read vyos-config://running/firewall and do not assume legacy `name` or `zone-policy` nodes exist.\n'
    )
    return (
        'You are performing a security audit of the firewall configuration on this VyOS version.\n\n'
        + schema_step
        + '2. Read vyos-config://running/firewall and identify the configured model (IPv4, IPv6, bridge, flowtable, groups, global options).\n'
        '   - Audit each discovered base chain and its default-action; prefer explicit allow rules over broad any/any permits.\n\n'
        '3. Review rule ordering and coverage:\n'
        '   - Check state handling, source/destination scope, protocol/port scope, logging, disabled rules, and shadowed or unreachable rules.\n'
        '   - Treat hardware-offload/flowtable rules separately from filtering policy; offload is not an allow rule by itself.\n\n'
        '4. Correlate policy with interfaces, VRFs, NAT/NAT66, and routing:\n'
        '   - Read vyos-config://running/interfaces, /vrf, /nat, and /nat66 where present.\n'
        '   - Do not recommend legacy interface-level firewall attachment or `zone-policy` unless the live schema exposes it.\n\n'
        '5. Verify operational state:\n'
        + ('   - Discover valid commands under vyos-schema://op/show/firewall before execution.\n' if introspection_enabled else
           '   - Try conservative show-firewall commands and adapt to validation errors.\n')
        + '   - Compare counters/hit data with the configured rules; zero counters alone do not prove a rule is ineffective.\n\n'
        'Produce a report containing observed state, evidence, risk, and schema-valid remediation steps. Do not apply changes during an audit unless explicitly requested.'
    )


def _troubleshoot_internet_text(read_only, introspection_enabled):
    schema_step = (
        '   - When unsure of a command, read vyos-schema://op/show before running it to confirm the live path.\n'
        if introspection_enabled else
        '   - Schema introspection is disabled; use the conservative paths below and adapt to validation errors.\n'
    )
    fix_note = (
        'If you find the cause, propose the exact `set` command(s) and explain them, then apply with '
        'modify_configuration using commit_confirm_minutes so a mistake auto-rolls-back.'
        if not read_only else
        'This session is READ-ONLY: report the cause and the exact `set` command the owner should run; do not claim to have fixed anything.'
    )
    return (
        'The home internet connection is down or unreliable and you are diagnosing it on this VyOS router.\n'
        'Work OUTWARD one layer at a time and stop at the first broken layer — do not guess. Explain each\n'
        'finding in plain language a non-expert can follow.\n\n'
        '1. Identify the WAN (internet-facing) interface and check the physical link:\n'
        + schema_step
        + '   - ["show", "interfaces", "summary"] to see which interface faces the ISP (it holds the default route / public-ish address).\n'
        '   - ["show", "interfaces", "ethernet", "<wan>"] — is it admin up, is carrier/link present? No carrier = cable/ISP/ONT problem, not the router.\n\n'
        '2. Confirm the WAN actually obtained an address:\n'
        '   - DHCP WAN: ["show", "dhcp", "client", "leases"] — is there a lease with a gateway?\n'
        '   - PPPoE WAN: ["show", "pppoe-client"] — is the session up? A down session is usually ISP credentials or line.\n'
        '   - Static WAN: read vyos-config://running/interfaces to confirm the address/gateway are set.\n'
        '   - No WAN address = the layers below cannot work; fix this first.\n\n'
        '3. Confirm a default route exists and points at the ISP gateway:\n'
        '   - ["show", "ip", "route", "0.0.0.0/0"] — there should be one default route via the WAN interface/gateway.\n'
        '   - No default route = the router does not know where "the internet" is.\n\n'
        '4. Test reachability BY IP to separate routing/NAT from DNS:\n'
        '   - ["ping", "<isp-gateway>"] then ["ping", "1.1.1.1"]. Use a numeric target on purpose.\n'
        '   - Gateway pings but 1.1.1.1 does not = upstream/ISP or NAT issue. Nothing pings = local link/route issue (recheck steps 1-3).\n\n'
        '5. Test DNS separately — "can ping 1.1.1.1 but websites fail" is almost always DNS:\n'
        '   - ["ping", "google.com"]. If the IP ping in step 4 worked but the name does not resolve, it is a name-resolution problem.\n'
        '   - If this router is the LAN resolver: ["show", "dns", "forwarding", "statistics"] to see if the forwarder is answering.\n\n'
        '6. Confirm outbound NAT (masquerade) is translating LAN traffic to the WAN address:\n'
        '   - ["show", "nat", "source", "statistics"] — the masquerade rule for the LAN should show its translation counter climbing.\n'
        '   - No source-NAT rule matching the WAN = LAN devices have no return path even when the router itself works.\n\n'
        '7. Rule out the firewall dropping return traffic:\n'
        '   - Read vyos-config://running/firewall and check the WAN input/local default-action and any state handling.\n'
        '   - Established/related return traffic must be allowed; an overly strict WAN rule can break browsing while ping-from-router still works.\n\n'
        'Only after locating the single broken layer, summarize: what is broken, the evidence, and the fix. ' + fix_note
    )


def _analyze_security_text(read_only, introspection_enabled):
    schema_step = (
        '   - Read vyos-schema://config/firewall to learn the exact node names THIS VyOS version uses before judging anything.\n'
        if introspection_enabled else
        '   - Schema introspection is off; read vyos-config://running/firewall directly and do not assume node names from other versions.\n'
    )
    apply_note = (
        'When the owner asks you to harden something, propose the specific `set` commands, explain in one sentence WHAT each does '
        'and WHY it helps, and apply them with modify_configuration using commit_confirm_minutes so a lockout auto-reverts.'
        if not read_only else
        'This session is READ-ONLY. For each gap, give the exact `set` commands the owner can run and explain why — but do not claim to have applied anything.'
    )
    return (
        'You are reviewing the security posture of a HOME VyOS router for an owner who knows they SHOULD be\n'
        'secured but is not a network engineer. Be educational and encouraging: for every finding, explain in\n'
        'plain language WHAT it is, WHY it matters for a home network, and the concrete fix. Never lecture; guide.\n'
        'Prioritize findings as CRITICAL / IMPORTANT / NICE-TO-HAVE so the owner knows what to fix first.\n'
        'Read only in this review; do not change config unless the owner explicitly asks.\n\n'
        'Begin by orienting yourself:\n'
        + schema_step
        + '   - Read vyos-config://running/interfaces to learn which interface is the WAN (internet) side and which are LAN.\n'
        '     Everything below hinges on "trust the LAN, distrust the WAN".\n\n'
        '=== 1. The front door: is the WAN firewalled at all? (CRITICAL) ===\n'
        '   - A home router MUST drop unsolicited traffic arriving from the internet while allowing replies to traffic\n'
        '     the LAN started. In modern VyOS this is the input/forward filter with a default-action of drop plus a rule\n'
        '     that accepts state established/related.\n'
        '   - Check the WAN "input" (traffic TO the router, e.g. its own SSH/GUI) and "forward" (traffic THROUGH it to the LAN).\n'
        '   - If either default-action is accept, or there is no established/related accept rule, explain that the home is\n'
        '     effectively exposed to the internet and this is the #1 thing to fix.\n\n'
        '=== 2. IPv6 is a real network too — check it has the SAME protection (CRITICAL, often missed) ===\n'
        '   - Home users routinely firewall IPv4 and forget IPv6, yet ISPs increasingly hand out globally-routable IPv6 to\n'
        '     every LAN device (no NAT to hide behind). An open IPv6 forward chain exposes every device directly.\n'
        '   - Verify the IPv6 input/forward filters mirror the IPv4 ones (default drop + established/related accept, and\n'
        '     permit the ICMPv6 types IPv6 needs to function). Call out any IPv4/IPv6 asymmetry explicitly.\n\n'
        '=== 3. Are management services exposed? (CRITICAL) ===\n'
        '   - Read vyos-config://running/service and vyos-config://running/system login. Look at SSH (service ssh), the HTTP\n'
        '     API/GUI (service https), SNMP, Telnet, and DNS forwarding.\n'
        '   - For each, decide: is it reachable from the WAN? SSH/GUI/SNMP open to the internet is a top cause of home-router\n'
        '     compromise. Recommend binding these to the LAN interface or restricting source addresses, disabling password\n'
        '     SSH login in favor of keys, and turning off anything unused (Telnet should never be on).\n\n'
        '=== 4. Zone-based thinking for multi-segment homes (IMPORTANT) ===\n'
        '   - Many homes now have guest Wi-Fi, IoT, or a lab VLAN. Explain the principle: untrusted zones (guest, IoT)\n'
        '     should reach the internet but NOT the trusted LAN, and should never reach the router\'s management.\n'
        '   - If multiple LAN segments/VLANs exist, check whether inter-zone rules enforce that separation. If the home is a\n'
        '     single flat LAN, say so plainly and suggest segmentation only as a NICE-TO-HAVE, not a scare tactic.\n\n'
        '=== 5. Hygiene and visibility (IMPORTANT / NICE-TO-HAVE) ===\n'
        '   - Logging: are dropped/blocked events logged so the owner can SEE attacks? Recommend logging on the WAN drop rule.\n'
        '   - Default credentials / weak login: check for a non-default admin and key-based SSH.\n'
        '   - Firmware currency: mention keeping the image updated as part of posture.\n'
        '   - Overly broad rules: flag any any/any accept, and note that hardware-offload/flowtable entries are NOT filtering rules.\n\n'
        'Finish with a short, friendly report: a one-line overall verdict, then findings grouped CRITICAL -> IMPORTANT ->\n'
        'NICE-TO-HAVE, each with a plain-language why and the exact schema-valid fix. ' + apply_note
    )


_BUILTIN_PROMPTS = {
    'vyos_capabilities_overview': {
        'description': 'System prompt explaining all VyOS MCP capabilities, tools, resources, and usage patterns',
        'text_builder': _capabilities_overview_text,
    },
    'troubleshoot_internet_connectivity': {
        'description': 'Home internet-down triage: link -> WAN address -> default route -> ping-by-IP -> DNS -> NAT -> firewall',
        'text_builder': _troubleshoot_internet_text,
    },
    'troubleshoot_asymmetric_routing': {
        'description': 'Guide through asymmetric-routing and routing-loop diagnosis (FRR, conntrack, policy)',
        'text_builder': _troubleshoot_routing_text,
    },
    'analyze_security_posture': {
        'description': 'Educational home-security review: WAN firewall, IPv4/IPv6 parity, exposed services, zone segmentation, hygiene',
        'text_builder': _analyze_security_text,
    },
    'provision_site_to_site_vpn': {
        'description': 'IPsec tunnel workflow across IKE/ESP/peer config branches',
        'write_only': True,
        'requires_introspection': True,
        'arguments': [
            PromptArgument(name='remote_ip', description='Remote peer public IP address', required=True),
            PromptArgument(name='local_subnet', description="Local private subnet, e.g. 10.0.1.0/24", required=True),
            PromptArgument(name='remote_subnet', description="Remote private subnet, e.g. 10.0.2.0/24", required=True),
            PromptArgument(name='group_name', description="Name for the IKE/ESP groups (default: SITE2SITE)", required=False),
        ],
        'template': (
            'You are provisioning a site-to-site IPsec VPN on VyOS.\n\n'
            'Parameters for this run:\n'
            '  - remote peer IP: {remote_ip}\n'
            '  - local subnet:   {local_subnet}\n'
            '  - remote subnet:  {remote_subnet}\n'
            '  - IKE/ESP group name: {group_name}\n\n'
            '1. Ask the user for the pre-shared key (do NOT guess or log it) and confirm the\n'
            '   IKE version (default: ikev2) and algorithms (default: aes256 / sha256 / dh-group 14).\n\n'
            '2. Validate the live schema for the vpn ipsec subtree before staging anything:\n'
            '   - Read vyos-schema://config/vpn/ipsec to confirm node names for THIS VyOS version\n'
            '     (the ipsec CLI has changed across releases — do not assume node paths).\n\n'
            '3. Stage the IKE group:\n'
            '   - set vpn ipsec ike-group {group_name} proposal 1 encryption aes256\n'
            '   - set vpn ipsec ike-group {group_name} proposal 1 hash sha256\n'
            '   - set vpn ipsec ike-group {group_name} proposal 1 dh-group 14\n'
            '   - set vpn ipsec ike-group {group_name} key-exchange ikev2\n\n'
            '4. Stage the ESP group:\n'
            '   - set vpn ipsec esp-group {group_name} proposal 1 encryption aes256\n'
            '   - set vpn ipsec esp-group {group_name} proposal 1 hash sha256\n\n'
            '5. Stage the peer and tunnel (substitute the PSK the user provides):\n'
            '   - set vpn ipsec site-to-site peer {remote_ip} authentication pre-shared-secret <psk>\n'
            '   - set vpn ipsec site-to-site peer {remote_ip} ike-group {group_name}\n'
            '   - set vpn ipsec site-to-site peer {remote_ip} tunnel 0 esp-group {group_name}\n'
            '   - set vpn ipsec site-to-site peer {remote_ip} tunnel 0 local prefix {local_subnet}\n'
            '   - set vpn ipsec site-to-site peer {remote_ip} tunnel 0 remote prefix {remote_subnet}\n\n'
            '6. Commit all changes atomically via modify_configuration; use commit_confirm_minutes\n'
            '   so connectivity loss auto-rolls-back.\n\n'
            '7. Verify the tunnel: execute_operational_command with path ["show", "vpn", "ipsec", "sa"].\n'
        ),
        'defaults': {'group_name': 'SITE2SITE'},
        'validators': {
            'remote_ip': '_v_ip',
            'local_subnet': '_v_cidr',
            'remote_subnet': '_v_cidr',
            'group_name': '_v_name',
        },
    },
    'audit_firewall_posture': {
        'description': 'Security audit of the firewall config, driven by the live CLI schema',
        'text_builder': _audit_firewall_text,
    }
}

_MAX_CUSTOM_FILES = 32
_MAX_CUSTOM_FILE_SIZE = 65536


def _load_custom_prompts():
    custom = {}
    prompts_dir = _CUSTOM_PROMPTS_DIR
    if not os.path.isdir(prompts_dir):
        return custom

    try:
        entries = os.listdir(prompts_dir)
        count = 0
        for entry in sorted(entries):
            if count >= _MAX_CUSTOM_FILES:
                LOG.warning(f'Maximum custom prompt files reached ({_MAX_CUSTOM_FILES})')
                break
            filepath = os.path.join(prompts_dir, entry)
            real = os.path.realpath(filepath)
            if not real.startswith(os.path.realpath(prompts_dir) + os.sep):
                LOG.warning(f'Skipping path traversal: {entry}')
                continue
            if not os.path.isfile(real):
                continue
            try:
                size = os.path.getsize(real)
                if size > _MAX_CUSTOM_FILE_SIZE:
                    LOG.warning(f'Skipping oversized prompt file: {entry} ({size} bytes)')
                    continue
            except OSError:
                continue
            try:
                with open(real, 'r', encoding='utf-8') as f:
                    text = f.read()
            except Exception:
                LOG.warning(f'Failed to read prompt file: {entry}')
                continue

            name = os.path.splitext(entry)[0]
            if not name or not name[0].isalpha():
                LOG.warning(f'Skipping invalid prompt name: {entry}')
                continue

            # Sanitize name to alphanumeric + underscore
            safe_name = ''.join(c for c in name if c.isalnum() or c == '_')
            if safe_name != name:
                LOG.warning(f'Prompt name contains unsafe characters: {name}')
                continue

            custom[safe_name] = {
                'description': f'User-defined prompt: {safe_name}',
                'messages': [
                    PromptMessage(
                        role='user',
                        content=TextContent(type='text', text=text)
                    )
                ]
            }
            count += 1

            LOG.debug(f'Loaded custom prompt: {safe_name}')
    except OSError:
        pass

    return custom


def _build_prompts():
    prompts = dict(_BUILTIN_PROMPTS)
    try:
        custom = _load_custom_prompts()
        for name, pdata in custom.items():
            if name in _BUILTIN_PROMPTS:
                LOG.warning(f'Ignoring custom prompt shadowing built-in: {name}')
                continue
            prompts[name] = pdata
    except Exception:
        LOG.warning('Failed to load custom prompts', exc_info=True)
    return prompts


def _validate_prompt_value(validator, name, value):
    try:
        if validator == '_v_ip':
            ipaddress.ip_address(value)
        elif validator == '_v_cidr':
            ipaddress.ip_network(value, strict=False)
        elif validator == '_v_name' and not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,31}', value):
            raise ValueError('must start with a letter and contain only letters, digits, underscore or hyphen')
    except ValueError as e:
        raise ValueError(f'Invalid argument {name}: {e}') from e


def _render_messages(pdata, read_only, introspection_enabled, arguments):
    arguments = arguments or {}

    text_builder = pdata.get('text_builder')
    if text_builder is not None:
        text = text_builder(read_only, introspection_enabled)
        return [PromptMessage(role='user', content=TextContent(type='text', text=text))]

    template = pdata.get('template')
    if template is not None:
        declared = {a.name for a in pdata.get('arguments', [])}
        required = {a.name for a in pdata.get('arguments', []) if a.required}
        missing = sorted(required - {k for k, v in arguments.items() if v})
        if missing:
            raise ValueError(f'Missing required argument(s): {", ".join(missing)}')
        validators = pdata.get('validators', {})
        values = dict(pdata.get('defaults', {}))
        for k, v in arguments.items():
            if k in declared and v is not None:
                if k in validators:
                    _validate_prompt_value(validators[k], k, str(v))
                values[k] = v
        # Only the declared placeholders are substituted; unknown braces are left intact.
        text = template
        for k in declared:
            text = text.replace('{' + k + '}', str(values.get(k, '{' + k + '}')))
        return [PromptMessage(role='user', content=TextContent(type='text', text=text))]

    return list(pdata.get('messages', []))


def register_prompts(server: Server, read_only: bool = True, introspection_enabled: bool = False):
    prompts = _build_prompts()

    prompt_list = []
    for name, pdata in prompts.items():
        # Write-only workflows (config provisioning) are hidden on read-only sessions
        # where the modify_configuration tool isn't even exposed.
        if read_only and pdata.get('write_only'):
            continue
        if not introspection_enabled and pdata.get('requires_introspection'):
            continue
        prompt_list.append(Prompt(
            name=name,
            description=pdata.get('description', ''),
            arguments=list(pdata.get('arguments', []))
        ))
    visible = {p.name for p in prompt_list}

    @server.list_prompts()
    async def list_prompts():
        return prompt_list

    @server.get_prompt()
    async def get_prompt(name: str, arguments: dict | None):
        if name not in prompts or name not in visible:
            raise ValueError(f'Unknown prompt: {name}')
        pdata = prompts[name]
        messages = _render_messages(pdata, read_only, introspection_enabled, arguments)
        return GetPromptResult(
            description=pdata.get('description', ''),
            messages=messages
        )
