#!/usr/bin/env python3
"""Host gate for ASK2 G7 Track 0 (D8) Suricata/NFQUEUE silent-bypass guard
and ask-check scope banner contract."""

import ast
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile
import types
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GUARD_SRC = ROOT / 'data/vyos-1x-files/python/vyos/ask_inspection_guard.py'
SHOW_OFFLOAD_SRC = ROOT / 'data/vyos-1x-files/src/op_mode/show_offload.py'
PATCH_062 = ROOT / 'data/vyos-1x-062-suricata-offload-bypass-guard.patch'
ASK_CHECK = ROOT / 'board/scripts/ask-check'
VYOS1X_REPO = Path('/mnt/builds/vyos-1x')
PINNED_COMMIT = '908f67c4f5ed46a55aeee9744a913efecd06e358'


def load_guard_module():
    class FakeConfigError(Exception):
        pass

    vyos_pkg = types.ModuleType('vyos')
    vyos_pkg.ConfigError = FakeConfigError
    sys.modules['vyos'] = vyos_pkg

    spec = importlib.util.spec_from_file_location('vyos.ask_inspection_guard', GUARD_SRC)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod, FakeConfigError


def assert_raises(exc_type, fn, *args, match=None, **kwargs):
    try:
        fn(*args, **kwargs)
    except exc_type as exc:
        if match and match not in str(exc):
            raise AssertionError(f'Expected {match!r} in error: {exc}') from exc
        return str(exc)
    raise AssertionError(f'Expected {exc_type.__name__} from {fn.__name__}')


def test_guard_logic():
    g, ConfigError = load_guard_module()

    eth_offload = {
        'eth3': {'offload': {'ipv4': {}, 'ipv6': {}}},
        'eth4': {'offload': {'ipv4': {}}},
        'eth1': {},
        'eth2': {'disable': {}, 'offload': {'ipv4': {}}},
    }
    bridges = {
        'br0': {'member': {'interface': {'eth3': {}, 'eth1': {}}}},
        'br1': {'member': {'interface': {'eth1': {}}}},
    }

    # --- Combination A: Passive Suricata vs ASK2 hardware offload ---
    # 1. Direct ethernet offload conflict rejected
    assert_raises(
        ConfigError,
        g.verify_suricata_offload_guard,
        {'interface': ['eth3']},
        ethernet=eth_offload,
        firewall={},
        bridges=bridges,
        match='allow-offload-bypass',
    )

    # 2. VLAN vif monitored by Suricata conflicts with parent physical offload
    assert_raises(
        ConfigError,
        g.verify_suricata_offload_guard,
        {'interface': ['eth3.10']},
        ethernet=eth_offload,
        firewall={},
        bridges=bridges,
        match='eth3.10 <-> eth3',
    )

    # 3. Bridge monitored by Suricata conflicts when member has offload
    assert_raises(
        ConfigError,
        g.verify_suricata_offload_guard,
        {'interface': ['br0']},
        ethernet=eth_offload,
        firewall={},
        bridges=bridges,
        match='br0 <-> eth3',
    )

    # 4. Hardware user flowtable conflict (even without ethernet offload leaves)
    fw_hw_ft = {
        'flowtable': {
            'FT_HW': {'interface': ['eth1'], 'offload': 'hardware'},
        }
    }
    assert_raises(
        ConfigError,
        g.verify_suricata_offload_guard,
        {'interface': ['eth1']},
        ethernet={'eth1': {}},
        firewall=fw_hw_ft,
        bridges={},
        match='FT_HW',
    )

    # 5. Non-offloaded port, disabled offloaded port, and software-only flowtable pass
    fw_sw_ft = {
        'flowtable': {
            'FT_SW': {'interface': ['eth1'], 'offload': 'software'},
        }
    }
    g.verify_suricata_offload_guard(
        {'interface': ['eth1', 'eth2', 'br1']},
        ethernet=eth_offload,
        firewall=fw_sw_ft,
        bridges=bridges,
    )

    # 6. Explicit override allows commit AND surfaces WARNING in show status
    suricata_override = {'interface': ['eth3'], 'allow_offload_bypass': {}}
    g.verify_suricata_offload_guard(
        suricata_override,
        ethernet=eth_offload,
        firewall={},
        bridges=bridges,
    )
    lines = g.describe_inspection_bypass_status(
        suricata_override, ethernet=eth_offload, firewall={}, bridges=bridges
    )
    assert any('WARNING:' in l and 'allow-offload-bypass' in l and 'eth3' in l for l in lines), lines

    # Non-overridden Suricata reports honest capture-loss / SW-flowtable caveat
    lines_clean = g.describe_inspection_bypass_status(
        {'interface': ['eth1']}, ethernet=eth_offload, firewall={}, bridges=bridges
    )
    assert any('no HW offload' in l and 'not guaranteed' in l for l in lines_clean), lines_clean

    # --- Combination B: Firewall `action queue` vs flowtable / HW offload ---
    # 7. Unbounded forward `action queue` conflicts with G1 auto flowtable
    fw_q_unbounded = {
        'ipv4': {
            'forward': {
                'filter': {
                    'rule': {
                        '10': {'action': 'queue', 'queue': '0-3'},
                    }
                }
            }
        }
    }
    assert_raises(
        ConfigError,
        g.verify_queue_offload_guard,
        fw_q_unbounded,
        ethernet=eth_offload,
        bridges=bridges,
        match='allow-queue-offload-bypass',
    )

    # 8. Interface-scoped `action queue` on non-offloaded port passes
    fw_q_eth1_only = {
        'ipv4': {
            'forward': {
                'filter': {
                    'rule': {
                        '10': {
                            'action': 'queue',
                            'inbound_interface': {'name': 'eth1'},
                        },
                    }
                }
            }
        }
    }
    g.verify_queue_offload_guard(fw_q_eth1_only, ethernet=eth_offload, bridges=bridges)

    # 9. Interface-scoped `action queue` on software-only flowtable port is rejected
    #    (flowtable hits bypass forward-hook NFQUEUE even in software!)
    fw_q_sw_ft = {
        'flowtable': {'FT_SW': {'interface': ['eth1'], 'offload': 'software'}},
        'ipv4': fw_q_eth1_only['ipv4'],
    }
    assert_raises(
        ConfigError,
        g.verify_queue_offload_guard,
        fw_q_sw_ft,
        ethernet={'eth1': {}},
        bridges={},
        match='eth1',
    )

    # 10. Negated or wildcard interface match treated as unbounded -> rejected
    fw_q_neg = {
        'ipv4': {
            'forward': {
                'filter': {
                    'rule': {
                        '20': {
                            'action': 'queue',
                            'inbound_interface': {'name': '!eth0'},
                        },
                    }
                }
            }
        }
    }
    assert_raises(
        ConfigError,
        g.verify_queue_offload_guard,
        fw_q_neg,
        ethernet=eth_offload,
        bridges=bridges,
        match='rule 20',
    )

    # 11. `action queue` inside a named chain reached via `jump` is caught
    fw_q_jump = {
        'ipv6': {
            'forward': {
                'filter': {
                    'rule': {
                        '5': {'action': 'jump', 'jump_target': 'IPS_CHAIN'},
                    }
                }
            },
            'name': {
                'IPS_CHAIN': {
                    'rule': {
                        '100': {
                            'action': 'queue',
                            'outbound_interface': {'name': 'eth3.10'},
                        }
                    }
                }
            },
        }
    }
    assert_raises(
        ConfigError,
        g.verify_queue_offload_guard,
        fw_q_jump,
        ethernet=eth_offload,
        bridges=bridges,
        match='ipv6 name IPS_CHAIN rule 100',
    )

    # 12. Disabled `action queue` rule is ignored
    fw_q_disabled = {
        'ipv4': {
            'forward': {
                'filter': {
                    'rule': {
                        '10': {'action': 'queue', 'disable': {}},
                    }
                }
            }
        }
    }
    g.verify_queue_offload_guard(fw_q_disabled, ethernet=eth_offload, bridges=bridges)

    # 13. Interface-group expansion (nested) catches overlap on eth3
    fw_q_grp = {
        'group': {
            'interface_group': {
                'LAN_PORTS': {'interface': ['eth3']},
                'MONITORED': {'include': ['LAN_PORTS']},
            }
        },
        'ipv4': {
            'forward': {
                'filter': {
                    'rule': {
                        '15': {
                            'action': 'queue',
                            'inbound_interface': {'group': 'MONITORED'},
                        }
                    }
                }
            }
        },
    }
    assert_raises(
        ConfigError,
        g.verify_queue_offload_guard,
        fw_q_grp,
        ethernet=eth_offload,
        bridges=bridges,
        match='eth3',
    )

    # 14. Explicit override allows commit AND surfaces WARNING in show status
    fw_q_override = dict(fw_q_unbounded)
    fw_q_override['global_options'] = {'allow_queue_offload_bypass': {}}
    g.verify_queue_offload_guard(fw_q_override, ethernet=eth_offload, bridges=bridges)
    q_lines = g.describe_inspection_bypass_status(
        {}, ethernet=eth_offload, firewall=fw_q_override, bridges=bridges
    )
    assert any('WARNING:' in l and 'allow-queue-offload-bypass' in l for l in q_lines), q_lines

    print('PASS [1]: ask_inspection_guard unit tests (14 scenarios)')


def test_ask_check_banner():
    text = ASK_CHECK.read_text()
    hw_m = re.search(r"hardware scope:.*", text)
    sw_m = re.search(r"software fallback:.*", text)
    assert hw_m and sw_m, 'Missing hardware scope / software fallback lines in ask-check'
    assert 'bridge' in hw_m.group(0), hw_m.group(0)
    assert 'NAT44' in hw_m.group(0), hw_m.group(0)
    assert not re.search(r'\bbridge\b', sw_m.group(0)), f'bridge still in software fallback: {sw_m.group(0)}'
    assert re.search(r'\bIPsec\b', sw_m.group(0)), f'IPsec missing from software fallback: {sw_m.group(0)}'
    print('PASS [2]: ask-check scope banner contract')


def test_patch_stack_apply_and_syntax():
    assert PATCH_062.is_file(), f'Missing {PATCH_062}'
    ast.parse(GUARD_SRC.read_text(), filename=str(GUARD_SRC))
    ast.parse(SHOW_OFFLOAD_SRC.read_text(), filename=str(SHOW_OFFLOAD_SRC))

    if not VYOS1X_REPO.is_dir():
        print('SKIP [3]: /mnt/builds/vyos-1x not present on host')
        return

    tmp = Path(tempfile.mkdtemp(prefix='vyos1x-g7-gate-'))
    try:
        subprocess.run(
            ['git', 'clone', '--no-checkout', str(VYOS1X_REPO), str(tmp)],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        subprocess.run(
            ['git', '-C', str(tmp), 'checkout', '-q', PINNED_COMMIT],
            check=True,
        )
        # Copy plain files just like bin/ci-setup-vyos1x.sh
        files_dir = ROOT / 'data/vyos-1x-files'
        if files_dir.is_dir():
            subprocess.run(['cp', '-a', f'{files_dir}/.', str(tmp)], check=True)
        for patch in sorted((ROOT / 'data').glob('vyos-1x-*.patch')):
            res = subprocess.run(
                ['git', '-C', str(tmp), 'apply', '--3way', '--whitespace=nowarn', str(patch)],
                capture_output=True, text=True,
            )
            if res.returncode != 0:
                raise AssertionError(f'Patch {patch.name} failed --3way apply:\n{res.stderr}')

        # Verify patched Python files parse cleanly
        for rel in (
            'src/conf_mode/service_suricata.py',
            'src/conf_mode/firewall.py',
            'src/conf_mode/interfaces_ethernet.py',
            'src/conf_mode/interfaces_bridge.py',
            'python/vyos/ask_inspection_guard.py',
            'src/op_mode/show_offload.py',
        ):
            p = tmp / rel
            ast.parse(p.read_text(), filename=rel)

        # Verify patched XML snippets are well-formed (after stripping #include lines)
        for rel in (
            'interface-definitions/service_suricata.xml.in',
            'interface-definitions/include/firewall/global-options.xml.i',
        ):
            raw = (tmp / rel).read_text()
            cleaned = '\n'.join(l for l in raw.splitlines() if not l.lstrip().startswith('#include'))
            if rel.endswith('.xml.i'):
                cleaned = f'<root>{cleaned}</root>'
            ET.fromstring(cleaned)

        print('PASS [3]: full vyos-1x patch stack (001..062) --3way apply, AST, and XML checks')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    test_guard_logic()
    test_ask_check_banner()
    test_patch_stack_apply_and_syntax()
    print('OK: G7 Track 0 Suricata/NFQUEUE bypass guard + ask-check banner gate PASS')


if __name__ == '__main__':
    main()
