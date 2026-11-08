"""F-309 (2026-11-08): WireGuard MAX_QUEUED_PACKETS 1024 -> 4096.

The WireGuard download (decrypt) path on .185 capped at ~1.9 Gbit/s with no
CPU saturated (78-85 % busy) and no counter showing a drop. Same-window
conservation (image 2026.11.08-0436, 4 TCP streams, one tunnel): eth4 MAC
received 2,748,044 frames, the P3 sinks 2,749,602, but WireGuard accepted only
2,719,192 -- 30,410 frames (1.1 %) dropped silently inside WireGuard receive;
with P3 off 47,839 (1.9 %). Not replay rejections (no 'invalid nonce' with
dynamic debug on); a kretprobe on wg_prev_queue_enqueue() saw the per-peer
queue full only ~1,000 times per 15 s, so most drops are the per-device
decrypt ptr_ring overflowing in bursts: wg_queue_enqueue_per_device_and_peer()
returns -EPIPE, the packet is queued DEAD and wg_packet_rx_poll() frees it
without touching any statistic. The ~1-2 % loss holds TCP near 1.9 Gbit/s.

Both the device rings (device.c ptr_ring_init for encrypt and decrypt) and the
per-peer queue cap (queueing.c) are sized by MAX_QUEUED_PACKETS. 4096 costs
32 KiB per ring of pointers and bounds queued skbs to a few MiB per direction
under overload. Upstream marks the constant 'TODO: replace this with DQL'.

Idempotent; exits non-zero unless the anchor matches exactly once.
"""

import sys

MARK = "F-309"
PATH = "drivers/net/wireguard/messages.h"
OLD = "\tMAX_QUEUED_PACKETS = 1024 /* TODO: replace this with DQL */\n"
NEW = ("\t/* F-309: 1024 overflowed the decrypt ring in bursts on LS1046A (silent\n"
       "\t * DEAD drops, ~1-2 % loss capping downloads near 1.9 Gbit/s). */\n"
       "\tMAX_QUEUED_PACKETS = 4096 /* TODO: replace this with DQL */\n")

try:
    with open(PATH) as f:
        s = f.read()
except FileNotFoundError:
    print(f"### F-309: FATAL: {PATH} not found")
    sys.exit(1)

if MARK in s:
    print("### F-309: already applied")
    sys.exit(0)

n = s.count(OLD)
if n != 1:
    print(f"### F-309: FATAL: anchor in {PATH} found {n} times (expected 1)")
    sys.exit(1)

with open(PATH, "w") as f:
    f.write(s.replace(OLD, NEW))
print("### F-309: WireGuard MAX_QUEUED_PACKETS 1024 -> 4096")
