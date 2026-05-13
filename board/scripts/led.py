#!/usr/bin/env python3
# led.py — Mono Gateway DK status LED control (LP5812, RGBW)
#
# Three input forms, single output: write R/G/B/W brightness 0..255 to the
# LP5812 sysfs channels, optionally fading from the current state.
#
#   led 17                       # int → index into /config/led.json palette
#   led 51 0 51 0                # four ints 0..255 → R G B W
#   led '#33003300'              # hex RRGGBBWW (with or without leading '#')
#   led 33003300                 # bare hex, 8 digits exactly
#   led off                      # alias for index 0 of the palette
#   led get                      # print current state
#   led list                     # print the palette
#   led -h | --help
#
# Fade options (apply to every form that *sets* a colour — index/decimal/
# hex/off; ignored by get/list):
#
#   --fade-ms <N>     fade duration in milliseconds (default 200)
#   --instant         alias for --fade-ms 0 (snap, no fade)
#   --fps <N>         frame rate during the fade (default 50, → ~20 ms/frame)
#
# Index vs hex disambiguation:
#   - exactly 8 hex digits (with optional '#')  -> hex form
#   - any other pure-digit token               -> palette index
#   ('99' is an index, '00000099' is the hex colour 00000099).
#
# Palette JSON layout (auto-created on first run if /config/led.json is
# missing — VyOS persists /config across reboots when vyos-union= is set):
#
#   {
#     "_comment": "Mono Gateway DK status LED palette. ...",
#     "colors": [
#       {"name": "off",   "color": "00000000"},
#       {"name": "red",   "color": "ff000000"},
#       ...
#     ]
#   }
#
# Hardware: LP5812 on i2c-15 addr 0x6c, four single-colour LEDs forming one
# logical RGBW indicator. Each channel is 8-bit PWM (0..255). The kernel
# `trigger` attribute MUST be 'none' for direct brightness writes to stick;
# this script enforces that on every write.
#
# Sysfs paths:
#   /sys/class/leds/status:red/brightness
#   /sys/class/leds/status:green/brightness
#   /sys/class/leds/status:blue/brightness
#   /sys/class/leds/status:white/brightness
#
# Fade implementation: read current R/G/B/W from sysfs, linearly interpolate
# to the target over `fade_ms` total wall-clock at `fps` Hz, writing all
# four channels per frame. For 200 ms at 50 fps → 10 frames @ 20 ms/frame.
# fade_ms == 0 (or --instant) writes target directly with no sleep. The
# final frame is always the exact target so rounding never strands the LED
# one tick off.
#
# Exit codes:
#   0  ok
#   1  LP5812 driver missing (sysfs path absent)
#   2  bad arguments / parse error
#   3  sysfs write failed (usually need root)
#   4  palette file corrupt or unreadable

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

PROG = Path(sys.argv[0]).name

LED_BASE = Path("/sys/class/leds")
CHANNELS = ("red", "green", "blue", "white")           # canonical R G B W
PALETTE_PATH = Path("/config/led.json")
PALETTE_SIZE = 32                                       # default length

# Fade defaults. 200 ms at 50 fps is the sweet spot: visibly smooth (10 frames),
# perceptibly snappy (still feels responsive on the CLI), cheap on sysfs writes
# (40 writes total: 4 channels × 10 frames).
DEFAULT_FADE_MS = 200
DEFAULT_FPS = 50
MIN_FRAME_MS = 5         # don't burn sysfs writes faster than 200 Hz

# Default 32-colour palette. Order is stable so customer scripts that hard-code
# an index keep working across upgrades. Colours stored as 8-hex strings
# RRGGBBWW (W is the white channel, NOT alpha). Brightness is intentionally
# moderate (~40% peak) — the LP5812 driving four white SMD LEDs through the
# Mono case at 100% is uncomfortably bright in an office.
DEFAULT_PALETTE = [
    {"name": "off",          "color": "00000000"},   # 0
    {"name": "red",          "color": "66000000"},   # 1
    {"name": "orange",       "color": "66330000"},   # 2
    {"name": "amber",        "color": "66440000"},   # 3
    {"name": "yellow",       "color": "66660000"},   # 4
    {"name": "chartreuse",   "color": "33660000"},   # 5
    {"name": "green",        "color": "00660000"},   # 6
    {"name": "spring-green", "color": "00663300"},   # 7
    {"name": "cyan",         "color": "00666600"},   # 8
    {"name": "azure",        "color": "00336600"},   # 9
    {"name": "blue",         "color": "00006600"},   # 10
    {"name": "violet",       "color": "33006600"},   # 11
    {"name": "magenta",      "color": "66006600"},   # 12
    {"name": "rose",         "color": "66003300"},   # 13
    {"name": "pink",         "color": "66202000"},   # 14
    {"name": "gold",         "color": "66440800"},   # 15
    {"name": "lime",         "color": "22660000"},   # 16
    {"name": "teal",         "color": "00444400"},   # 17
    {"name": "indigo",       "color": "22004400"},   # 18
    {"name": "turquoise",    "color": "00554400"},   # 19
    {"name": "salmon",       "color": "66331a00"},   # 20
    {"name": "olive",        "color": "44440000"},   # 21
    {"name": "navy",         "color": "00003300"},   # 22
    {"name": "maroon",       "color": "33000000"},   # 23
    {"name": "purple",       "color": "33003300"},   # 24
    {"name": "white-dim",    "color": "00000020"},   # 25
    {"name": "white-cool",   "color": "00000040"},   # 26
    {"name": "white-warm",   "color": "11110030"},   # 27
    {"name": "white-bright", "color": "00000066"},   # 28
    {"name": "alert-red",    "color": "ff000000"},   # 29  emergency override
    {"name": "alert-yellow", "color": "ffff0000"},   # 30  emergency override
    {"name": "alert-white",  "color": "000000ff"},   # 31  full white
]
assert len(DEFAULT_PALETTE) == PALETTE_SIZE, "default palette must be 32 long"


def die(msg: str, code: int) -> None:
    print(f"{PROG}: {msg}", file=sys.stderr)
    sys.exit(code)


def led_path(channel: str) -> Path:
    return LED_BASE / f"status:{channel}"


def verify_driver() -> None:
    for ch in CHANNELS:
        if not led_path(ch).is_dir():
            die(
                f"{led_path(ch)} not found — LP5812 driver not loaded?",
                1,
            )


def ensure_trigger_none(channel_dir: Path) -> None:
    trig_path = channel_dir / "trigger"
    try:
        current = trig_path.read_text()
    except OSError:
        return  # no trigger attribute, nothing to enforce
    # kernel prints active trigger as "[name]" in the list
    if "[none]" in current:
        return
    try:
        trig_path.write_text("none")
    except OSError as e:
        die(
            f"failed to set trigger=none on {channel_dir} ({e}); need root?",
            3,
        )


def write_brightness(channel: str, value: int) -> None:
    """Write a single channel without re-checking the trigger (caller has)."""
    p = led_path(channel)
    try:
        (p / "brightness").write_text(str(value))
    except OSError as e:
        die(
            f"failed to write {value} to {p}/brightness ({e}); need root?",
            3,
        )


def read_brightness(channel: str) -> int:
    try:
        return int((led_path(channel) / "brightness").read_text().strip())
    except OSError:
        return 0


def claim_triggers() -> None:
    """Force trigger=none on all four channels once per invocation."""
    for ch in CHANNELS:
        ensure_trigger_none(led_path(ch))


def write_rgbw_now(r: int, g: int, b: int, w: int) -> None:
    """Write target values directly, no interpolation."""
    write_brightness("red", r)
    write_brightness("green", g)
    write_brightness("blue", b)
    write_brightness("white", w)


def fade_to_rgbw(
    target: tuple[int, int, int, int],
    fade_ms: int,
    fps: int,
) -> None:
    """Linear-interpolate from current sysfs state to target over fade_ms.

    fade_ms == 0: snap to target (one write per channel).
    fade_ms  > 0: interpolate in `frames` steps at `frame_ms` cadence,
                  always landing exactly on target on the final frame.
    """
    r1, g1, b1, w1 = target
    if fade_ms <= 0:
        write_rgbw_now(r1, g1, b1, w1)
        return

    r0 = read_brightness("red")
    g0 = read_brightness("green")
    b0 = read_brightness("blue")
    w0 = read_brightness("white")

    # If we are already there, save the syscalls.
    if (r0, g0, b0, w0) == (r1, g1, b1, w1):
        return

    frame_ms = max(MIN_FRAME_MS, int(1000 / fps))
    frames = max(1, fade_ms // frame_ms)
    # frame i in 1..frames; t = i/frames in (0,1]; t==1 lands on target.
    for i in range(1, frames + 1):
        t = i / frames
        r = round(r0 + (r1 - r0) * t)
        g = round(g0 + (g1 - g0) * t)
        b = round(b0 + (b1 - b0) * t)
        w = round(w0 + (w1 - w0) * t)
        write_rgbw_now(r, g, b, w)
        if i < frames:
            time.sleep(frame_ms / 1000.0)


def set_rgbw(
    r: int,
    g: int,
    b: int,
    w: int,
    fade_ms: int = DEFAULT_FADE_MS,
    fps: int = DEFAULT_FPS,
) -> None:
    for name, val in zip(("R", "G", "B", "W"), (r, g, b, w)):
        if not (0 <= val <= 255):
            die(f"{name}={val} out of range 0..255", 2)
    verify_driver()
    claim_triggers()
    fade_to_rgbw((r, g, b, w), fade_ms, fps)
    suffix = " (instant)" if fade_ms <= 0 else f" (fade {fade_ms} ms)"
    print(
        f"LED set R={r} G={g} B={b} W={w} "
        f"(#{r:02X}{g:02X}{b:02X}{w:02X}){suffix}"
    )


def cmd_get() -> None:
    verify_driver()
    r = read_brightness("red")
    g = read_brightness("green")
    b = read_brightness("blue")
    w = read_brightness("white")
    print(
        f"R={r} G={g} B={b} W={w}  #{r:02X}{g:02X}{b:02X}{w:02X}"
    )


# ---- palette I/O ---------------------------------------------------------

def palette_template() -> dict:
    return {
        "_comment": (
            "Mono Gateway DK status LED palette. Edit `colors` to taste; "
            "preserve list length so existing integer indices keep meaning. "
            "Each entry: name (free text), color (8 hex digits RRGGBBWW where "
            "W is the white channel, NOT alpha)."
        ),
        "colors": DEFAULT_PALETTE,
    }


def load_palette() -> list[dict]:
    if not PALETTE_PATH.exists():
        # /config is the VyOS persistent overlay; create the directory only if
        # it doesn't exist (live-boot may not have it yet).
        try:
            PALETTE_PATH.parent.mkdir(parents=True, exist_ok=True)
            PALETTE_PATH.write_text(
                json.dumps(palette_template(), indent=2) + "\n"
            )
        except OSError as e:
            die(
                f"cannot create palette at {PALETTE_PATH} ({e}); "
                "is /config writable?",
                4,
            )
    try:
        data = json.loads(PALETTE_PATH.read_text())
    except (OSError, json.JSONDecodeError) as e:
        die(f"{PALETTE_PATH} unreadable or not valid JSON ({e})", 4)
    if not isinstance(data, dict) or "colors" not in data:
        die(
            f"{PALETTE_PATH} missing top-level 'colors' list; "
            "delete it to regenerate the default",
            4,
        )
    colors = data["colors"]
    if not isinstance(colors, list) or not colors:
        die(f"{PALETTE_PATH} 'colors' must be a non-empty list", 4)
    for i, entry in enumerate(colors):
        if (
            not isinstance(entry, dict)
            or "color" not in entry
            or not isinstance(entry["color"], str)
        ):
            die(
                f"{PALETTE_PATH} entry {i} missing 'color' string",
                4,
            )
    return colors


def cmd_list() -> None:
    colors = load_palette()
    width = max((len(c.get("name", "")) for c in colors), default=0)
    for i, c in enumerate(colors):
        name = c.get("name", "")
        color = c["color"].lower()
        print(f"  {i:2d}  {name:<{width}}  #{color}")


# ---- argument parsing ----------------------------------------------------

HEX8 = re.compile(r"^#?[0-9a-fA-F]{8}$")
INT_ONLY = re.compile(r"^[0-9]+$")


def parse_hex8(s: str) -> tuple[int, int, int, int]:
    s = s.strip().lstrip("#")
    if not re.fullmatch(r"[0-9a-fA-F]{8}", s):
        die(f"'{s}' is not 8 hex digits (expected RRGGBBWW)", 2)
    return (
        int(s[0:2], 16),
        int(s[2:4], 16),
        int(s[4:6], 16),
        int(s[6:8], 16),
    )


def apply_palette_index(idx: int, fade_ms: int, fps: int) -> None:
    colors = load_palette()
    if not (0 <= idx < len(colors)):
        die(
            f"palette index {idx} out of range 0..{len(colors) - 1} "
            f"({PALETTE_PATH})",
            2,
        )
    entry = colors[idx]
    r, g, b, w = parse_hex8(entry["color"])
    set_rgbw(r, g, b, w, fade_ms=fade_ms, fps=fps)


# argparse with positional pass-through. argparse alone cannot disambiguate
# between "led <int-index>" and "led <int> <int> <int> <int>" cleanly because
# argparse needs the variadic positional to be the last arg, and we also need
# subcommands `off|get|list`. We split: argparse handles flags + collects all
# positionals as one list; we route on the list ourselves (same logic as
# main() in the pre-fade version, just with fade_ms/fps threaded through).

def build_argparser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog=PROG,
        description=(
            "Set the Mono Gateway DK status LED. Accepts a palette index, "
            "four decimal R G B W bytes, or an 8-digit hex RRGGBBWW. "
            "All forms fade from the current colour by default."
        ),
        epilog=(
            "Examples:\n"
            f"  {PROG} 1                  set palette index 1, 200 ms fade\n"
            f"  {PROG} --instant 1        same, no fade\n"
            f"  {PROG} --fade-ms 1000 6   green over 1 s\n"
            f"  {PROG} 51 0 51 0          R=51 G=0 B=51 W=0\n"
            f"  {PROG} '#33003300'        same in hex\n"
            f"  {PROG} off                shorthand for index 0\n"
            f"  {PROG} get                print current R G B W\n"
            f"  {PROG} list               print palette\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        add_help=True,
    )
    ap.add_argument(
        "--fade-ms",
        type=int,
        default=DEFAULT_FADE_MS,
        metavar="N",
        help=f"fade duration in ms (default {DEFAULT_FADE_MS})",
    )
    ap.add_argument(
        "--instant",
        action="store_true",
        help="snap to target, no fade (== --fade-ms 0)",
    )
    ap.add_argument(
        "--fps",
        type=int,
        default=DEFAULT_FPS,
        metavar="N",
        help=f"fade frame rate (default {DEFAULT_FPS})",
    )
    ap.add_argument(
        "args",
        nargs="*",
        help="palette index | R G B W | '#RRGGBBWW' | off | get | list",
    )
    return ap


def main(argv: list[str]) -> int:
    ap = build_argparser()
    ns = ap.parse_args(argv)

    fade_ms = 0 if ns.instant else max(0, ns.fade_ms)
    fps = max(1, ns.fps)

    args = ns.args
    if not args:
        ap.print_help(sys.stderr)
        return 2

    head = args[0]

    # special words (no fade applies to get/list; off does fade)
    if head == "get":
        cmd_get()
        return 0
    if head == "list":
        cmd_list()
        return 0
    if head == "off":
        apply_palette_index(0, fade_ms, fps)
        return 0

    # 4-arg decimal form
    if len(args) == 4:
        try:
            r, g, b, w = (int(x, 10) for x in args)
        except ValueError:
            die("decimal form expects four 0..255 integers", 2)
        set_rgbw(r, g, b, w, fade_ms=fade_ms, fps=fps)
        return 0

    # single-token forms
    if len(args) == 1:
        token = head.strip()
        # hex8 takes priority — exactly 8 hex digits with optional '#'
        if HEX8.match(token):
            r, g, b, w = parse_hex8(token)
            set_rgbw(r, g, b, w, fade_ms=fade_ms, fps=fps)
            return 0
        # otherwise pure integer = palette index
        if INT_ONLY.match(token):
            apply_palette_index(int(token, 10), fade_ms, fps)
            return 0
        die(
            f"'{token}' is neither an integer index nor an 8-digit hex colour",
            2,
        )

    die(f"expected 1 or 4 positional arguments; got {len(args)}", 2)
    return 2  # unreachable, satisfies type checker


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))