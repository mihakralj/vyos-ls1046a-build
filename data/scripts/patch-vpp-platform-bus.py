#!/usr/bin/env python3
"""
Programmatic patcher for VPP platform-bus (DPAA1) support on NXP LS1046A.

Replaces the fragile unified diff patch (vyos-1x-010-vpp-platform-bus.patch)
with resilient Python-based modifications that search for patterns rather
than relying on exact line offsets.

Applied by pre_build_hook in auto-build.yml after git checkout.

Modifications:
  src/conf_mode/vpp.py
    - Add 'import json' (for DPAA unbound state persistence)
    - Add 'import os' (for sysfs access in DPAA helpers)
    - Add 'fsl_dpa' to not_pci_drv list
    - Add 'fsl_dpa' to SUPPORTED_DRIVERS tuple
    - Add DPAA1 kernel-to-DPDK handoff helper functions
    - Inject original_driver into iface_config for template use
    - Add _dpaa_unbind_ifaces() call before VPP start

  data/templates/vpp/startup.conf.j2
    - Add af_xdp_plugin.so to enabled plugins
    - Replace dpdk{} block with platform-bus-aware version

  python/vyos/vpp/config_verify.py
    - Lower main heap minimum from 1G to 256M

  python/vyos/vpp/config_resource_checks/resource_defaults.py
    - Lower min_memory from 8G to 7G
    - Lower min_cpus from 4 to 2
    - Lower reserved_cpu_cores from 2 to 1
"""

import re
import sys
import os


def patch_file(path, description, func):
    """Apply a patching function to a file, with error reporting."""
    if not os.path.exists(path):
        print(f'WARNING: {path} not found — skipping {description}')
        return False
    with open(path, 'r') as f:
        original = f.read()
    try:
        result = func(original)
    except Exception as e:
        print(f'ERROR: {description} failed: {e}')
        return False
    if result == original:
        print(f'NOTE: {description} — no changes needed (already applied?)')
        return True
    with open(path, 'w') as f:
        f.write(result)
    print(f'OK: {description}')
    return True


# ---------------------------------------------------------------------------
# vpp.py patches
# ---------------------------------------------------------------------------

DPAA_HELPERS = '''
# ---------------------------------------------------------------------------
# DPAA1 kernel-to-DPDK handoff helpers (LS1046A platform-bus)
#
# At boot ALL FMan MACs are owned by the kernel:
#   fsl_dpaa_mac -> hardware MAC (PHY, link, PHYLINK)
#   fsl_dpa      -> Linux netdevs (dpaa-ethernet.N -> ethN)
#
# DPDK DPAA PMD accesses FMan frame queues directly via /dev/mem and
# /dev/fsl-usdpaa. If fsl_dpa is still bound to the same MAC when DPDK
# initialises, both drivers drain the same FQs -> kernel panic.
#
# Solution: unbind the dpaa-ethernet.N sysfs device from fsl_dpa before
# starting VPP. On VPP stop a systemd ExecStopPost (dpaa-rebind.conf)
# reads the persisted state and rebinds, restoring kernel ownership
# without requiring a reboot.
# ---------------------------------------------------------------------------

_DPAA_DRIVER_PATH = '/sys/bus/platform/drivers/fsl_dpa'
_DPAA_UNBOUND_STATE = '/run/vpp-dpaa-unbound.json'


def _dpaa_find_platform_dev(iface: str):
    """Return the dpaa-ethernet.N sysfs device name backing a netdev.

    Walks /sys/bus/platform/drivers/fsl_dpa/dpaa-ethernet.*/net/<iface>
    to identify which platform device belongs to the named interface.
    Returns None on non-DPAA1 hardware (fsl_dpa driver absent).
    """
    if not os.path.isdir(_DPAA_DRIVER_PATH):
        return None
    for dev in sorted(os.listdir(_DPAA_DRIVER_PATH)):
        if not dev.startswith('dpaa-ethernet.'):
            continue
        if os.path.exists(os.path.join(_DPAA_DRIVER_PATH, dev, 'net', iface)):
            return dev
    return None


def _dpaa_unbind_ifaces(ifaces):
    """Unbind interfaces from fsl_dpa so DPDK DPAA PMD can claim them.

    Iterates the given interface names, finds each one's dpaa-ethernet.N
    platform device, and writes it to the fsl_dpa 'unbind' sysfs file.
    The resulting {iface: device} mapping is persisted to
    _DPAA_UNBOUND_STATE so the companion rebind script can restore
    kernel ownership when VPP stops.

    Silently skips interfaces that are not DPAA (no fsl_dpa binding),
    making this safe to call on any VyOS platform.
    """
    unbound = {}
    unbind_path = os.path.join(_DPAA_DRIVER_PATH, 'unbind')
    for iface in ifaces:
        dev = _dpaa_find_platform_dev(iface)
        if not dev:
            continue
        try:
            with open(unbind_path, 'w') as fh:
                fh.write(dev)
            unbound[iface] = dev
        except OSError as exc:
            print(f'W: vpp: could not unbind {iface} ({dev}) from fsl_dpa: {exc}')
    # Persist so the systemd ExecStopPost can rebind without reboot.
    try:
        with open(_DPAA_UNBOUND_STATE, 'w') as fh:
            json.dump(unbound, fh)
    except OSError as exc:
        print(f'W: vpp: could not write DPAA unbound state: {exc}')
    return unbound

'''

DPAA_UNBIND_BLOCK = '''
            # Unbind fsl_dpa from any DPAA interfaces assigned to VPP.
            # Must happen before systemctl start vpp so DPDK DPAA PMD can
            # claim the FMan frame queues without conflicting with fsl_dpa.
            _dpaa_ifaces = [
                iface
                for iface, icfg in config['settings']['interface'].items()
                if icfg.get('original_driver') == 'fsl_dpa'
            ]
            if _dpaa_ifaces:
                _dpaa_unbind_ifaces(_dpaa_ifaces)

'''


def patch_vpp_py(content):
    """Apply all DPAA1 modifications to src/conf_mode/vpp.py."""
    changes = 0

    # 1. Add 'import json' and 'import os' if not present
    if 'import json' not in content:
        # Insert after the last 'from vyos...' import block, before airbag.enable()
        # Actually, simpler: insert right after the first line (shebang) + license block
        # Find the first non-comment, non-blank import line
        if 'from pathlib import Path' in content:
            content = content.replace(
                'from pathlib import Path',
                'import json\nimport os\n\nfrom pathlib import Path',
                1
            )
            changes += 1
        else:
            # Fallback: insert after license block
            content = re.sub(
                r'(# 51 Franklin Street.*?USA.\n)',
                r'\1\nimport json\nimport os\n',
                content, count=1, flags=re.DOTALL
            )
            changes += 1

    if 'import os' not in content:
        if 'import json' in content:
            content = content.replace('import json', 'import json\nimport os', 1)
            changes += 1

    # 2. Add 'fsl_dpa' to not_pci_drv list
    if "'fsl_dpa'" not in content or 'fsl_dpa' not in content.split('not_pci_drv')[0] + content.split('not_pci_drv')[1].split('\n')[0]:
        # More precise: check if fsl_dpa is in the not_pci_drv line
        not_pci_match = re.search(r"(not_pci_drv:\s*list\[str\]\s*=\s*\[)(.*?)(\])", content)
        if not_pci_match and 'fsl_dpa' not in not_pci_match.group(2):
            old = not_pci_match.group(0)
            inner = not_pci_match.group(2).rstrip()
            if inner:
                new_inner = inner + ", 'fsl_dpa'"
            else:
                new_inner = "'fsl_dpa'"
            new = f"{not_pci_match.group(1)}{new_inner}{not_pci_match.group(3)}"
            content = content.replace(old, new, 1)
            changes += 1

    # 3. Add 'fsl_dpa' to SUPPORTED_DRIVERS tuple
    if 'fsl_dpa' not in content.split('SUPPORTED_DRIVERS')[1].split(')')[0] if 'SUPPORTED_DRIVERS' in content else '':
        # Find SUPPORTED_DRIVERS tuple and add fsl_dpa before closing paren
        supp_match = re.search(
            r"(SUPPORTED_DRIVERS\s*=\s*\(.*?)(^\))",
            content, re.MULTILINE | re.DOTALL
        )
        if supp_match:
            before_paren = supp_match.group(1)
            if 'fsl_dpa' not in before_paren:
                # Add fsl_dpa entry before the closing paren
                content = content.replace(
                    supp_match.group(0),
                    before_paren + "    'fsl_dpa',    # NXP DPAA1 FMan Ethernet (platform bus, DPDK DPAA PMD)\n)\n",
                    1
                )
                changes += 1

    # 4. Add DPAA helper functions after SUPPORTED_DRIVERS block
    if '_DPAA_DRIVER_PATH' not in content:
        # Insert after the SUPPORTED_DRIVERS closing paren
        supp_end = re.search(r"SUPPORTED_DRIVERS\s*=\s*\(.*?^\)\n", content, re.MULTILINE | re.DOTALL)
        if supp_end:
            insert_pos = supp_end.end()
            content = content[:insert_pos] + DPAA_HELPERS + content[insert_pos:]
            changes += 1

    # 5. Inject original_driver into effective_config loop
    # Find: "for iface_config in effective_config['settings']['interface'].values():"
    # Replace with items() and add original_driver injection
    eff_loop_pat = re.search(
        r"( +)(for )(iface_config)( in effective_config\['settings'\]\['interface'\]\.)(values)\(\):",
        content
    )
    if eff_loop_pat:
        indent = eff_loop_pat.group(1)
        old_line = eff_loop_pat.group(0)
        new_line = f"{indent}for _ename, iface_config in effective_config['settings']['interface'].items():"
        content = content.replace(old_line, new_line, 1)
        # After "iface_config['driver'] = 'dpdk'" in this block, add original_driver
        # Find the driver assignment that follows
        driver_assign = f"{indent}    iface_config['driver'] = 'dpdk'"
        # We need to find the one right after our modified line
        pos = content.find(new_line)
        next_driver = content.find("iface_config['driver'] = 'dpdk'", pos)
        if next_driver >= 0:
            end_of_line = content.find('\n', next_driver)
            inject = (
                f"\n{indent}    # Inject original_driver into iface_config for template use\n"
                f"{indent}    _edrv = eth_ifaces_persist.get(_ename, {{}}).get('original_driver', '')\n"
                f"{indent}    iface_config['original_driver'] = _edrv"
            )
            content = content[:end_of_line] + inject + content[end_of_line:]
            changes += 1

    # 6. Inject original_driver into the main interface loop in get_config()
    # Find the block: "for iface, iface_config in config['settings']['interface'].items():"
    #                  "    iface_config['driver'] = 'dpdk'"
    # This is in the 'if settings in config:' block
    main_loop_pat = re.search(
        r"( +)(for iface, iface_config in config\['settings'\]\['interface'\]\.items\(\):)\n"
        r"(\s+)(iface_config\['driver'\] = 'dpdk')\n"
        r"\n"
        r"(\s+)(# old_driver = leaf_node_changed\()",
        content
    )
    if main_loop_pat:
        indent2 = main_loop_pat.group(3)
        old_block = main_loop_pat.group(0)
        inject_orig_drv = (
            f"\n{indent2}# Inject original_driver so startup.conf.j2 distinguishes\n"
            f"{indent2}# platform-bus (DPAA) from PCI NICs.\n"
            f"{indent2}_orig_drv = eth_ifaces_persist.get(iface, {{}}).get('original_driver', '')\n"
            f"{indent2}if not _orig_drv:\n"
            f"{indent2}    try:\n"
            f"{indent2}        _orig_drv = EthtoolGDrvinfo(iface).driver\n"
            f"{indent2}    except Exception:\n"
            f"{indent2}        _orig_drv = ''\n"
            f"{indent2}iface_config['original_driver'] = _orig_drv\n"
        )
        new_block = old_block.replace(
            f"{main_loop_pat.group(4)}\n\n{main_loop_pat.group(5)}{main_loop_pat.group(6)}",
            f"{main_loop_pat.group(4)}{inject_orig_drv}\n{main_loop_pat.group(5)}{main_loop_pat.group(6)}",
            1
        )
        content = content.replace(old_block, new_block, 1)
        changes += 1

    # 7. Add _dpaa_unbind_ifaces call before 'systemctl restart vpp'
    if '_dpaa_unbind_ifaces' not in content or '_dpaa_ifaces' not in content.split('def apply')[1] if 'def apply' in content else '':
        # Find "call('systemctl daemon-reload')" and insert DPAA unbind before it
        daemon_reload = "call('systemctl daemon-reload')"
        if daemon_reload in content:
            pos = content.find(daemon_reload)
            # Find the indentation
            line_start = content.rfind('\n', 0, pos) + 1
            indent = ''
            for ch in content[line_start:]:
                if ch in ' \t':
                    indent += ch
                else:
                    break
            content = content[:line_start] + (
                f"{indent}# Unbind fsl_dpa from any DPAA interfaces assigned to VPP.\n"
                f"{indent}# Must happen before systemctl start vpp so DPDK DPAA PMD can\n"
                f"{indent}# claim the FMan frame queues without conflicting with fsl_dpa.\n"
                f"{indent}_dpaa_ifaces = [\n"
                f"{indent}    iface\n"
                f"{indent}    for iface, icfg in config['settings']['interface'].items()\n"
                f"{indent}    if icfg.get('original_driver') == 'fsl_dpa'\n"
                f"{indent}]\n"
                f"{indent}if _dpaa_ifaces:\n"
                f"{indent}    _dpaa_unbind_ifaces(_dpaa_ifaces)\n"
                f"\n"
            ) + content[line_start:]
            changes += 1

    if changes == 0:
        print('  (all vpp.py changes appear to be already applied)')
    else:
        print(f'  ({changes} modification(s) applied to vpp.py)')

    return content


# ---------------------------------------------------------------------------
# startup.conf.j2 patches
# ---------------------------------------------------------------------------

STARTUP_DPDK_BLOCK = """{# Classify interfaces: PCI-based DPDK vs platform-bus DPDK (DPAA auto-discovers) #}
{% set ns = namespace(has_pci_dpdk=false, has_platform_dpdk=false) %}
{% for iface, iface_config in interface.items() %}
{%     if iface_config.driver == 'dpdk' %}
{%         if iface_config.original_driver is defined and iface_config.original_driver in ['fsl_dpa'] %}
{%             set ns.has_platform_dpdk = true %}
{%         else %}
{%             set ns.has_pci_dpdk = true %}
{%         endif %}
{%     endif %}
{% endfor %}
{% if ns.has_pci_dpdk or ns.has_platform_dpdk %}
dpdk {
{%     if not ns.has_pci_dpdk %}
    {# Platform-bus only (DPAA) - skip PCI scan entirely #}
    no-pci
{%     else %}
    {# PCI whitelist anchor - prevents auto-claiming all PCI devices #}
    dev 0000:00:00.0
{%     endif %}
{%     for iface, iface_config in interface.items() %}
{%         if iface_config.driver == 'dpdk' and (iface_config.original_driver is not defined or iface_config.original_driver not in ['fsl_dpa']) %}
    dev {{ iface_config.dpdk_options.dev_id }} {
        name {{ iface }}
{%             if iface_config.num_rx_desc is vyos_defined %}
        num-rx-desc {{ iface_config.num_rx_desc }}
{%             endif %}
{%             if iface_config.num_tx_desc is vyos_defined %}
        num-tx-desc {{ iface_config.num_tx_desc }}
{%             endif %}
{%             if iface_config.num_rx_queues is vyos_defined %}
        num-rx-queues {{ iface_config.num_rx_queues }}
{%             endif %}
{%             if iface_config.num_tx_queues is vyos_defined %}
        num-tx-queues {{ iface_config.num_tx_queues }}
{%             endif %}
        }
{%         endif %}
{%         if iface_config.driver == 'dpdk' and iface_config.original_driver is defined and iface_config.original_driver in ['fsl_dpa'] %}
    {# Platform-bus NIC (DPAA) - auto-discovered by dpaa_bus, no explicit dev entry #}
    {# Interface {{ iface }} handed off via /dev/fsl-usdpaa DPAA PMD #}
{%         endif %}
{%     endfor %}
{%     if ns.has_pci_dpdk %}
    uio-bind-force
{%     endif %}
}
{% endif %}"""


def patch_startup_conf(content):
    """Apply DPAA1 modifications to startup.conf.j2."""
    changes = 0

    # 1. Add af_xdp_plugin.so to plugins block (after 'plugin default { disable }')
    if 'af_xdp_plugin.so' not in content:
        content = content.replace(
            '    plugin avf_plugin.so { enable }',
            '    plugin af_xdp_plugin.so { enable }\n    plugin avf_plugin.so { enable }',
            1
        )
        changes += 1

    # 2. Replace the entire dpdk { ... } block with platform-bus-aware version
    if 'has_pci_dpdk' not in content:
        # Match the dpdk { ... } block — starts with "dpdk {" and ends with "}"
        # followed by blank line or {% if ipsec
        dpdk_block_pat = re.search(
            r'^dpdk \{.*?^\}\n',
            content, re.MULTILINE | re.DOTALL
        )
        if dpdk_block_pat:
            content = content[:dpdk_block_pat.start()] + STARTUP_DPDK_BLOCK + '\n' + content[dpdk_block_pat.end():]
            changes += 1

    if changes == 0:
        print('  (all startup.conf.j2 changes appear to be already applied)')
    else:
        print(f'  ({changes} modification(s) applied to startup.conf.j2)')

    return content


# ---------------------------------------------------------------------------
# config_verify.py patches
# ---------------------------------------------------------------------------

def patch_config_verify(content):
    """Lower main heap minimum from 1G to 256M."""
    changes = 0

    # Change: if main_heap_size < 1 << 30:
    # To:     if main_heap_size < 1 << 28:
    if '1 << 30' in content:
        content = content.replace('1 << 30', '1 << 28', 1)
        changes += 1

    if 'greater than or equal to 1G' in content:
        content = content.replace(
            'greater than or equal to 1G',
            'greater than or equal to 256M',
            1
        )
        changes += 1

    if changes == 0:
        print('  (all config_verify.py changes appear to be already applied)')
    else:
        print(f'  ({changes} modification(s) applied to config_verify.py)')

    return content


# ---------------------------------------------------------------------------
# resource_defaults.py patches
# ---------------------------------------------------------------------------

def patch_resource_defaults(content):
    """Lower VPP resource requirements for embedded ARM64."""
    changes = 0

    if "'min_memory': '8G'" in content:
        content = content.replace("'min_memory': '8G'", "'min_memory': '7G'", 1)
        changes += 1

    if "'min_cpus': 4" in content:
        content = content.replace("'min_cpus': 4", "'min_cpus': 2", 1)
        changes += 1

    if "'reserved_cpu_cores': 2" in content:
        content = content.replace("'reserved_cpu_cores': 2", "'reserved_cpu_cores': 1", 1)
        changes += 1

    if changes == 0:
        print('  (all resource_defaults.py changes appear to be already applied)')
    else:
        print(f'  ({changes} modification(s) applied to resource_defaults.py)')

    return content


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    # Determine the vyos-1x source root (script is run from the vyos-1x checkout dir)
    root = os.getcwd()

    # Verify we're in a vyos-1x tree
    if not os.path.exists(os.path.join(root, 'src', 'conf_mode', 'vpp.py')):
        # Try parent directories or explicit path
        for candidate in [root, os.path.dirname(root)]:
            if os.path.exists(os.path.join(candidate, 'src', 'conf_mode', 'vpp.py')):
                root = candidate
                break
        else:
            print(f'ERROR: Cannot find vyos-1x source tree (cwd={root})')
            sys.exit(1)

    print(f'Patching VPP platform-bus support in {root}')

    results = []
    results.append(patch_file(
        os.path.join(root, 'src', 'conf_mode', 'vpp.py'),
        'vpp.py: DPAA1 platform-bus support',
        patch_vpp_py
    ))
    results.append(patch_file(
        os.path.join(root, 'data', 'templates', 'vpp', 'startup.conf.j2'),
        'startup.conf.j2: platform-bus DPDK block',
        patch_startup_conf
    ))
    results.append(patch_file(
        os.path.join(root, 'python', 'vyos', 'vpp', 'config_verify.py'),
        'config_verify.py: lower heap minimum to 256M',
        patch_config_verify
    ))
    results.append(patch_file(
        os.path.join(root, 'python', 'vyos', 'vpp', 'config_resource_checks', 'resource_defaults.py'),
        'resource_defaults.py: lower resource requirements',
        patch_resource_defaults
    ))

    if all(results):
        print('All VPP platform-bus patches applied successfully')
    else:
        print('WARNING: Some patches did not apply — check output above')
        sys.exit(1)


if __name__ == '__main__':
    main()