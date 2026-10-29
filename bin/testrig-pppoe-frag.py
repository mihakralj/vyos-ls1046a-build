#!/usr/bin/env python3
"""1500 -> 1492 fragmentation probe across the PPPoE session (ASK2 or vendor DUT).

dell2 sends N UDP datagrams that are exactly 1500 bytes of IP (LAN MTU), through the DUT, into the
PPPoE session toward dell1 (MTU 1492). The DUT must fragment (or punt). Three cases:
  v4-dfclear  IPv4, DF clear: expect fragmentation, in hardware if offloaded
  v4-dfset    IPv4, DF set: expect nothing delivered and an ICMP destination-unreachable per packet
  v6          IPv6: hardware fragmentation (vendor behaviour) or ICMPv6 Packet Too Big
Hardware means: nearly all datagrams delivered and the DUT kernel saw under 10 % of the frames
(eth3 + eth4 `rx packets [TOTAL]`, the same proof testrig-offload-quick.sh uses on the vendor).
The receiver answers every datagram with 1 byte so conntrack sees an established flow.
DUT FragCreates is printed for information only: the vendor's patched kernel counts hardware
fragmentation there too, so it does not separate software from hardware.

Concurrent flows: run two copies with INSTANCE=0 and INSTANCE=1 (distinct ports and 5-tuples).
Usage: bin/testrig-pppoe-frag.py ask2|vendor [count] [pps] [case,case,...]
Cases: v4-j2000/3000/3500 and v6-j2000/3000/3500 (jumbo ingress; set MTU 3600 on the LAN port and gen2 first), ctl-v4-1000 ctl-v6-1000 (controls that need no fragmentation), v4-dfclear v4-dfset v6, and the
-warm variants, which first offload the flow with small datagrams so the oversize ones hit a live record.
Needs the PPPoE session up (ASK2: dell1 root ns, or RIG_NS=<netns> for the second DUT; vendor:
bin/testrig-pppoe-vendor.sh up).
Hosts and key: bin/testrig-env.sh (GEN1, GEN2, ASK2_DUT, VENDOR_DUT, RIG_KEY, VENDOR_NS); RUN_ON=gen2 runs
the gen2 side locally.
"""
import subprocess
import sys
import time
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import testrig_env  # noqa: E402

KIND = sys.argv[1] if len(sys.argv) > 1 else ""
N = int(sys.argv[2]) if len(sys.argv) > 2 else 6000
PPS = int(sys.argv[3]) if len(sys.argv) > 3 else 500
if KIND not in ("ask2", "vendor"):
    sys.exit(__doc__)

CFG = testrig_env.load()
SSHO = ["-n", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8"] + testrig_env.key_args(CFG)
# RUN_ON=gen2 (alias dell2): run on gen2: gen2 commands run locally, gen1 and the DUT go over ssh.
LOCAL_D2 = os.environ.get("RUN_ON") in ("gen2", "dell2")
D1 = testrig_env.require(CFG, "GEN1")
D2 = ["gen2-local"] if LOCAL_D2 else testrig_env.require(CFG, "GEN2")
if KIND == "vendor":
    NS = f"sudo ip netns exec {CFG['VENDOR_NS']} "
    DUT = testrig_env.require(CFG, "VENDOR_DUT")
else:
    # RIG_NS: the generator ports facing this DUT live in that netns (second ASK2 DUT)
    NS = f"sudo ip netns exec {os.environ['RIG_NS']} " if os.environ.get("RIG_NS") else ""
    DUT = testrig_env.require(CFG, "ASK2_DUT")
# INSTANCE=n shifts the receiver port and the warm source ports so several instances can run at once
# (concurrent-flow case); the per-instance share/reasm figures then include the other instances' frames.
INST = int(os.environ.get("INSTANCE", "0"))
PORT = 5999 - 10 * INST

RECV = r'''
import socket, sys, time, json
fam, plen, dur, addr = sys.argv[1], int(sys.argv[2]), float(sys.argv[3]), sys.argv[4]
s = socket.socket(socket.AF_INET6 if fam == "6" else socket.AF_INET, socket.SOCK_DGRAM)
if fam == "6":
    s.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 8 << 20)
s.bind((addr, %d))
s.settimeout(0.5)
end, n, other = time.time() + dur, 0, {}
while time.time() < end:
    try:
        data, peer = s.recvfrom(65535)
    except socket.timeout:
        continue
    if len(data) == plen:
        n += 1
    else:
        other[len(data)] = other.get(len(data), 0) + 1
    # a small reply per datagram makes the conntrack entry established, which offload requires
    try:
        s.sendto(b"x", peer)
    except OSError:
        pass
print(n, json.dumps(other))
''' % PORT

SEND = r'''
import socket, sys, time
fam, mode, dst, n, pps, plen = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4]), int(sys.argv[5]), int(sys.argv[6])
sp = int(sys.argv[7]) if len(sys.argv) > 7 else 0
v6 = fam == "6"
s = socket.socket(socket.AF_INET6 if v6 else socket.AF_INET, socket.SOCK_DGRAM)
if sp:
    s.bind(("", sp))
if v6:
    s.setsockopt(socket.IPPROTO_IPV6, 23, 3)  # IPV6_PMTUDISC_PROBE: ignore a learned path MTU, always send 1500 B
else:
    s.setsockopt(socket.IPPROTO_IP, 10, 3 if mode == "dfset" else 0)
buf = bytes(plen)
t0 = time.perf_counter()
sent = 0
for i in range(n):
    while time.perf_counter() < t0 + i / pps:
        pass
    try:
        s.sendto(buf, (dst, %d)); sent += 1
    except OSError:
        pass
print(sent)
''' % PORT


def ssh(dest, cmd, stdin=None, timeout=120):
    argv = ["bash", "-c", cmd] if LOCAL_D2 and dest == D2 else \
        ["ssh"] + (SSHO if stdin is None else SSHO[1:]) + dest + [cmd]
    p = subprocess.run(argv, input=stdin, capture_output=True, text=True, timeout=timeout, check=True)
    return p.stdout.strip()


def counters(dest, ns=""):
    raw = ssh(dest, f"{ns}cat /proc/net/snmp /proc/net/snmp6")
    out, hdr = {}, {}
    for line in raw.splitlines():
        parts = line.split()
        if not parts:
            continue
        if parts[0].endswith(":"):
            if parts[0] in hdr:
                out.update({parts[0][:-1] + k: int(v) for k, v in zip(hdr.pop(parts[0])[1:], parts[1:])})
            else:
                hdr[parts[0]] = parts
        elif len(parts) == 2 and parts[1].lstrip("-").isdigit():
            out[parts[0]] = int(parts[1])
    return out


def krx():
    sudo = "" if KIND == "vendor" else "sudo "
    cmd = f"for i in eth3 eth4; do {sudo}ethtool -S $i; done | awk '/rx packets \\[TOTAL\\]/{{s+=$NF}} END{{print s+0}}'"
    return int(ssh(DUT, cmd) or 0)


def clean_pmtu(dst):
    """Drop dell2's learned path MTU for dst: the DF-set case makes the DUT send ICMP frag-needed, and a
    sender that knows the path MTU pre-fragments, so the DUT would never see a 1500-byte datagram."""
    pre = NS if NS else "sudo "
    fam = "-6 " if ":" in dst else ""
    ssh(D2, f"{pre}ip {fam}route flush cache")
    left = ssh(D2, f"{pre}ip {fam}route get {dst}")
    if "mtu" in left:
        sys.exit(f"dell2 still has a cached path MTU for {dst}: {left}")


def run_case(name, fam, mode, dst, plen, warm_port=0):
    """warm_port != 0: first establish and offload the flow with small datagrams from that fixed source
    port, then send the oversize ones on the same 5-tuple (otherwise the kernel drops the first oversize
    v6 packet, conntrack never confirms the flow and it is never offloaded)."""
    clean_pmtu(dst)
    warm = 1500 if warm_port else 0
    dur = N / PPS + 6 + (warm / PPS + 2 if warm else 0)
    rx = subprocess.Popen(["ssh"] + SSHO[1:] + D1 + [f"{NS}python3 - {fam} {plen} {dur} {dst}"],
                          stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    rx.stdin.write(RECV)
    rx.stdin.close()
    time.sleep(2)
    sp = f" {warm_port - 10 * INST}" if warm_port else ""
    if warm:
        ssh(D2, f"{NS}python3 - {fam} {mode} {dst} {warm} {PPS} 100{sp}", stdin=SEND, timeout=int(dur) + 60)
        time.sleep(1)
    r0, d0, k0 = counters(D1, NS), counters(DUT), krx()
    sent = ssh(D2, f"{NS}python3 - {fam} {mode} {dst} {N} {PPS} {plen}{sp}", stdin=SEND, timeout=int(dur) + 60)
    got = rx.stdout.read().strip().split(" ", 1)
    rx.wait()
    r1, d1, k1 = counters(D1, NS), counters(DUT), krx()
    k = (lambda a, b, key: b.get(key, 0) - a.get(key, 0))
    reasm = k(r0, r1, "IpReasmOKs" if fam == "4" else "Ip6ReasmOKs")
    frag = k(d0, d1, "IpFragCreates" if fam == "4" else "Ip6FragCreates")
    icmp = k(d0, d1, "IcmpOutDestUnreachs" if fam == "4" else "Icmp6OutPktTooBigs")
    dl = int(got[0] or 0)
    other = got[1] if len(got) > 1 else "{}"
    kernel_frames = k1 - k0
    # a datagram that reaches the DUT kernel is one frame in, plus its 1-byte reply in
    share = kernel_frames / max(1, 2 * int(sent or 1))
    sent_n = int(sent or 0)
    window = 2 * PPS  # packets the kernel may carry before the flow is offloaded
    if warm_port:
        window = 0  # the flow is already offloaded when the oversize datagrams start
    if name.startswith("ctl") and share < 0.1 and dl >= sent_n - window:
        verdict = "offloaded (control: no fragmentation needed)"
    elif share < 0.1 and reasm >= 0.9 * sent_n and dl < 0.5 * sent_n:
        verdict = "hardware-fragmented, but the receiver's UDP rejects the datagrams (corrupt L4 header)"
    elif share < 0.1 and dl >= sent_n - window and dl > 0:
        verdict = "hardware-fragmented, datagrams intact"
    elif share >= 0.5 and dl > 0 and icmp == 0:
        verdict = "kernel path (kernel fragments)"
    elif icmp > 0 and dl < 0.5 * sent_n:
        verdict = "host path: oversize frames punted, ICMP/PTB sent, nothing delivered"
    elif icmp > 0:
        verdict = f"kernel path: {icmp} ICMP/PTB sent, sender then pre-fragments"
    else:
        verdict = "unclassified"
    print(f"{name:11s} sent {sent:>5s} delivered(1472/1452 B) {dl:>5d} other sizes {other}  dell1 reasm +{reasm}  "
          f"DUT kernel rx {kernel_frames} ({share:.0%}) fragcreates +{frag} icmp +{icmp} -> {verdict}", flush=True)


print(f"# {KIND}: {N} datagrams at {PPS} pps, 1500-byte IP packets into the 1492 PPPoE session")
ONLY = set(sys.argv[4].split(",")) if len(sys.argv) > 4 else None
CASES = [("ctl-v4-1000", "4", "dfclear", "10.99.50.1", 972),  # control: fits the PPPoE MTU, no fragmentation
         ("ctl-v6-1000", "6", "default", "fd99:50::1", 952),
         ("v4-dfclear", "4", "dfclear", "10.99.50.1", 1472),
         ("v4-dfset", "4", "dfset", "10.99.50.1", 1472),
         ("v6", "6", "default", "fd99:50::1", 1452),
         ("v4-dfclear-warm", "4", "dfclear", "10.99.50.1", 1472, 5990),  # flow already offloaded
         ("v6-warm", "6", "default", "fd99:50::1", 1452, 5991),
         # R1 jumbo ingress: the LAN port and gen2 need MTU >= the IP size first (ASK range is 1280-3600)
         ("v4-j2000", "4", "dfclear", "10.99.50.1", 1972), ("v6-j2000", "6", "default", "fd99:50::1", 1952),
         ("v4-j3000", "4", "dfclear", "10.99.50.1", 2972), ("v6-j3000", "6", "default", "fd99:50::1", 2952),
         ("v4-j3500", "4", "dfclear", "10.99.50.1", 3472), ("v6-j3500", "6", "default", "fd99:50::1", 3452)]
for c in CASES:
    if ONLY is None or c[0] in ONLY:
        run_case(*c)
clean_pmtu("10.99.50.1")
clean_pmtu("fd99:50::1")
