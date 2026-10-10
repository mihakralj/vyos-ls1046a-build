"""LAN-side neighbour controls shared by UDP and persistent TCP tests.

This file is staged as source with lan_run_python or prepended to the TCP peer.
Nothing changes until configure_neighbour is explicitly called by the test.
"""


def configure_neighbour(iface, address, mac=None, arp_ignore=None, restore_after=None):
    import json
    import pathlib
    import subprocess
    import threading
    import time

    def link():
        # Netlink follows setns(); the caller's existing sysfs mount may still
        # describe its original namespace.
        return json.loads(subprocess.check_output(
            ["ip", "-j", "link", "show", "dev", iface], text=True))[0]

    link()
    ignore = pathlib.Path("/proc/sys/net/ipv4/conf") / iface / "arp_ignore"
    assert ignore.is_file()
    if restore_after is not None:
        assert arp_ignore == 8 and 0 < restore_after <= 15
    if arp_ignore is not None:
        assert arp_ignore in {0, 8}
        old = ignore.read_text()
        ignore.write_text(str(arp_ignore))
        if restore_after is not None:
            # A TCP control connection cannot restore ARP over a path whose
            # resolution has deliberately failed. Bound the fault locally;
            # the fixture also restores the original value after peer exit.
            timer = threading.Timer(restore_after, ignore.write_text, args=(old,))
            timer.daemon = True
            timer.start()
    else:
        assert restore_after is None
    if mac is not None:
        from scapy.all import ARP, Ether, sendp
        subprocess.run(["ip", "link", "set", "dev", iface, "address", mac], check=True)
        # Announce the actual new receive address using ARP, never an
        # administrative replacement of the DUT's neighbour entry.
        sendp(Ether(src=mac, dst="ff:ff:ff:ff:ff:ff") /
              ARP(op=1, hwsrc=mac, psrc=address,
                  hwdst="00:00:00:00:00:00", pdst=address),
              iface=iface, count=2, inter=0.05, verbose=False)
    return {"op": "neighbour", "mac": link()["address"],
            "arp_ignore": int(ignore.read_text()), "time": time.time(),
            "restore_after": restore_after}


def configure_ndp(iface, blocked=None, restore_after=None):
    """Suppress NS in a test-owned namespace, with an independent expiry."""
    import json
    import subprocess
    import threading
    import time

    table = 'ask_recovery_ndp'
    def listing():
        result = subprocess.run(['nft', '-j', 'list', 'table', 'ip6', table],
                                capture_output=True, text=True)
        assert result.returncode in (0, 1), result.stderr
        return json.loads(result.stdout)['nftables'] if result.returncode == 0 else None
    if restore_after is not None:
        assert blocked is True and 0 < restore_after <= 15
    present = listing()
    if blocked is True and present is None:
        rule = (f'table ip6 {table} {{ chain input {{ type filter hook input priority -10; '
                f'iifname "{iface}" icmpv6 type nd-neighbor-solicit counter drop; }}; }}\n')
        result = subprocess.run(['nft', '-f', '-'], input=rule, text=True, capture_output=True)
        assert result.returncode == 0, (rule, result.stderr)
        if restore_after is not None:
            timer = threading.Timer(restore_after, subprocess.run,
                args=(['nft', 'delete', 'table', 'ip6', table],), kwargs={'capture_output': True})
            timer.daemon = True
            timer.start()
    elif blocked is False and present is not None:
        subprocess.run(['nft', 'delete', 'table', 'ip6', table], check=True, capture_output=True)
    current = listing()
    packets = sum(expr['counter']['packets'] for item in current or []
                  for expr in item.get('rule', {}).get('expr', []) if 'counter' in expr)
    return {'blocked': current is not None, 'packets': packets, 'time': time.time(), 'restore_after': restore_after}
