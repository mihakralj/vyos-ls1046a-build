#!/usr/bin/env python3
# led.py — Mono Gateway DK status LED control (LP5812, RGBW)
#
# Spec (frozen):
#   no args                  -> print current LED state as 4 ints AND as
#                               quad-tuple hex; exit 0
#   off                      -> fade to 00 00 00 00
#   <int>                    -> fade to PALETTE[int]   (single token, decimal)
#   R G B W (4 decimals)     -> fade to (R,G,B,W),  each 0..255
#   R G B   (3 decimals)     -> fade to (R,G,B,0)   (W forced to 0)
#   RRGGBBWW (8 hex)         -> fade to that colour
#   RRGGBB   (6 hex)         -> fade to (RR,GG,BB,00)
#   leading '#' on hex tokens is accepted and ignored.
#
# All transitions fade from the current LED state to the target over
# FADE_MS milliseconds (linear interpolation in raw 8-bit PWM space).
# There are no command-line flags — fade time and palette are baked in
# as constants below.
#
# Hardware: LP5812 on i2c-15 addr 0x6c, four single-colour LEDs forming
# one logical RGBW indicator. Each channel is 8-bit PWM (0..255). The
# kernel `trigger` attribute MUST be 'none' for direct brightness writes
# to stick; this script forces that on every set.
#
# Sysfs paths:
#   /sys/class/leds/status:red/brightness
#   /sys/class/leds/status:green/brightness
#   /sys/class/leds/status:blue/brightness
#   /sys/class/leds/status:white/brightness
#
# Exit codes:
#   0  ok
#   1  LP5812 driver missing (sysfs path absent)
#   2  bad arguments / parse error
#   3  sysfs write failed (usually need root)

from __future__ import annotations

import re
import sys
import time
from pathlib import Path

PROG = Path(sys.argv[0]).name

LED_BASE = Path("/sys/class/leds")
CHANNELS = ("red", "green", "blue", "white")   # canonical R G B W

# ---- baked-in constants --------------------------------------------------

# Total fade duration, in milliseconds, applied to every colour change.
# Linear interpolation in raw PWM space at FADE_FPS Hz.
FADE_MS = 200
FADE_FPS = 50          # -> 20 ms/frame, 10 frames per default fade
MIN_FRAME_MS = 5       # hard floor on per-frame sleep (200 Hz cap on writes)

# Baked-in 32-entry palette. Indices are stable: customer scripts that
# hard-code an index keep working across upgrades. Each entry is an
# 8-hex-digit string RRGGBBWW (W = white channel, NOT alpha). Brightness
# is intentionally moderate (~40% peak on coloured entries) — driving
# four white SMD LEDs through the Mono case at 100% is uncomfortably
# bright in an office.
PALETTE = (
    "00000000",   #  0  off
    "66000000",   #  1  red
    "66330000",   #  2  orange
    "66440000",   #  3  amber
    "66660000",   #  4  yellow
    "33660000",   #  5  chartreuse
    "00660000",   #  6  green
    "00663300",   #  7  spring-green
    "00666600",   #  8  cyan
    "00336600",   #  9  azure
    "00006600",   # 10  blue
    "33006600",   # 11  violet
    "66006600",   # 12  magenta
    "66003300",   # 13  rose
    "66202000",   # 14  pink
    "66440800",   # 15  gold
    "22660000",   # 16  lime
    "00444400",   # 17  teal
    "22004400",   # 18  indigo
    "00554400",   # 19  turquoise
    "66331a00",   # 20  salmon
    "44440000",   # 21  olive
    "00003300",   # 22  navy
    "33000000",   # 23  maroon
    "33003300",   # 24  purple
    "00000020",   # 25  white-dim
    "00000040",   # 26  white-cool
    "11110030",   # 27  white-warm
    "00000066",   # 28  white-bright
    "ff000000",   # 29  alert-red       (emergency override)
    "ffff0000",   # 30  alert-yellow    (emergency override)
    "000000ff",   # 31  alert-white     (full white)
)
assert all(re.fullmatch(r"[0-9a-fA-F]{8}", c) for c in PALETTE), \
    "palette entries must be 8 hex digits each"


# ---- low-level sysfs -----------------------------------------------------

def die(msg: str, code: int) -> None:
    print(f"{PROG}: {msg}", file=sys.stderr)
    sys.exit(code)


def led_path(channel: str) -> Path:
    return LED_BASE / f"status:{channel}"


def verify_driver() -> None:
    for ch in CHANNELS:
        if not led_path(ch).is_dir():
            die(f"{led_path(ch)} not found — LP5812 driver not loaded?", 1)


def ensure_trigger_none(channel_dir: Path) -> None:
    trig = channel_dir / "trigger"
    try:
        current = trig.read_text()
    except OSError:
        return  # no trigger attribute — nothing to enforce
    if "[none]" in current:
        return
    try:
        trig.write_text("none")
    except OSError as e:
        die(f"failed to set trigger=none on {channel_dir} ({e}); need root?", 3)


def claim_triggers() -> None:
    for ch in CHANNELS:
        ensure_trigger_none(led_path(ch))


def write_brightness(channel: str, value: int) -> None:
    p = led_path(channel)
    try:
        (p / "brightness").write_text(str(value))
    except OSError as e:
        die(f"failed to write {value} to {p}/brightness ({e}); need root?", 3)


def read_brightness(channel: str) -> int:
    try:
        return int((led_path(channel) / "brightness").read_text().strip())
    except OSError:
        return 0


def write_rgbw_now(r: int, g: int, b: int, w: int) -> None:
    write_brightness("red",   r)
    write_brightness("green", g)
    write_brightness("blue",  b)
    write_brightness("white", w)


# ---- fade engine ---------------------------------------------------------

def fade_to(target: tuple[int, int, int, int]) -> None:
    """Linear-fade from the current LED state to `target` over FADE_MS.

    Always lands exactly on `target` on the final frame so rounding
    never strands the LED one tick off. Skips entirely if already there.
    """
    r1, g1, b1, w1 = target

    if FADE_MS <= 0:
        write_rgbw_now(r1, g1, b1, w1)
        return

    r0 = read_brightness("red")
    g0 = read_brightness("green")
    b0 = read_brightness("blue")
    w0 = read_brightness("white")

    if (r0, g0, b0, w0) == (r1, g1, b1, w1):
        return

    frame_ms = max(MIN_FRAME_MS, int(1000 / FADE_FPS))
    frames = max(1, FADE_MS // frame_ms)

    for i in range(1, frames + 1):
        t = i / frames
        r = round(r0 + (r1 - r0) * t)
        g = round(g0 + (g1 - g0) * t)
        b = round(b0 + (b1 - b0) * t)
        w = round(w0 + (w1 - w0) * t)
        write_rgbw_now(r, g, b, w)
        if i < frames:
            time.sleep(frame_ms / 1000.0)


def set_rgbw(r: int, g: int, b: int, w: int) -> None:
    for name, val in zip(("R", "G", "B", "W"), (r, g, b, w)):
        if not (0 <= val <= 255):
            die(f"{name}={val} out of range 0..255", 2)
    verify_driver()
    claim_triggers()
    fade_to((r, g, b, w))


# ---- input parsing -------------------------------------------------------

HEX8 = re.compile(r"^[0-9a-fA-F]{8}$")
HEX6 = re.compile(r"^[0-9a-fA-F]{6}$")
INT_ONLY = re.compile(r"^[0-9]+$")


def strip_hash(s: str) -> str:
    return s[1:] if s.startswith("#") else s


def parse_hex8(s: str) -> tuple[int, int, int, int]:
    return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16), int(s[6:8], 16))


def parse_hex6(s: str) -> tuple[int, int, int, int]:
    return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16), 0)


def cmd_get() -> None:
    """No-args path: print current LED state as four ints AND as quad-tuple
    hex on a single line, machine-friendly:
        R G B W  #RRGGBBWW
    """
    verify_driver()
    r = read_brightness("red")
    g = read_brightness("green")
    b = read_brightness("blue")
    w = read_brightness("white")
    print(f"{r} {g} {b} {w}  #{r:02X}{g:02X}{b:02X}{w:02X}")


def apply_palette_index(idx: int) -> None:
    if not (0 <= idx < len(PALETTE)):
        die(f"palette index {idx} out of range 0..{len(PALETTE) - 1}", 2)
    r, g, b, w = parse_hex8(PALETTE[idx])
    set_rgbw(r, g, b, w)


# ---- main dispatcher -----------------------------------------------------

USAGE = (
    f"usage: {PROG}                  print current LED state\n"
    f"       {PROG} off              fade to 0 0 0 0\n"
    f"       {PROG} <index>          fade to baked-in palette entry (0..{len(PALETTE) - 1})\n"
    f"       {PROG} R G B W          fade to four decimal channel values\n"
    f"       {PROG} R G B            fade to (R,G,B,0) — white channel zeroed\n"
    f"       {PROG} RRGGBBWW         fade to 8-hex-digit colour (optional leading #)\n"
    f"       {PROG} RRGGBB           fade to 6-hex-digit colour, white channel zeroed\n"
    f"\n"
    f"All set/fade transitions take {FADE_MS} ms (baked in)."
)


def main(argv: list[str]) -> int:
    # Bare --help / -h short-circuit (we don't use argparse — flags are not
    # part of the spec, but a help token is still polite).
    if argv and argv[0] in ("-h", "--help", "help"):
        print(USAGE)
        return 0

    # No args: print current state.
    if not argv:
        cmd_get()
        return 0

    # Single-token forms.
    if len(argv) == 1:
        tok = argv[0].strip()
        low = tok.lower()
        if low == "off":
            set_rgbw(0, 0, 0, 0)
            return 0

        bare = strip_hash(tok)

        # Hex 8 (RRGGBBWW) — checked before HEX6 so '00000000' is hex,
        # not palette index 0 (palette index 0 is the literal string '0').
        if HEX8.match(bare):
            r, g, b, w = parse_hex8(bare)
            set_rgbw(r, g, b, w)
            return 0

        # Hex 6 (RRGGBB) -> W := 0. Only matches when '#' was present
        # OR the token contains a non-decimal hex digit; a pure 6-digit
        # decimal token like "123456" is treated as a palette index and
        # will fail the range check (palette is 32 long). That's the
        # documented disambiguation: a bare 6-decimal-digit token cannot
        # name a hex colour — prefix it with '#' to force hex.
        if HEX6.match(bare) and (tok.startswith("#") or not INT_ONLY.match(tok)):
            r, g, b, w = parse_hex6(bare)
            set_rgbw(r, g, b, w)
            return 0

        # Pure integer -> palette index.
        if INT_ONLY.match(tok):
            apply_palette_index(int(tok, 10))
            return 0

        die(
            f"'{tok}' is not a palette index, 'off', or a 6/8-digit hex colour",
            2,
        )

    # 3 decimals -> R G B 0
    if len(argv) == 3:
        try:
            r, g, b = (int(x, 10) for x in argv)
        except ValueError:
            die("3-arg form expects three decimal integers (R G B)", 2)
        set_rgbw(r, g, b, 0)
        return 0

    # 4 decimals -> R G B W
    if len(argv) == 4:
        try:
            r, g, b, w = (int(x, 10) for x in argv)
        except ValueError:
            die("4-arg form expects four decimal integers (R G B W)", 2)
        set_rgbw(r, g, b, w)
        return 0

    die(f"expected 0, 1, 3, or 4 arguments; got {len(argv)}\n\n{USAGE}", 2)
    return 2  # unreachable


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))