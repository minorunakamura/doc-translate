"""Capture an owned CLI's stderr PTY and readable frames (stdlib only).

shortcut: frame projection handles Rich's line/cursor CSI, not arbitrary terminal apps;
use the raw transcript in a real terminal if the renderer starts using other controls.
"""

import argparse
import codecs
import errno
import fcntl
import html
import json
import os
import pty
import re
import selectors
import struct
import subprocess
import sys
import termios
import time
import unicodedata
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--evidence", type=Path, required=True)
parser.add_argument("--columns", type=int, default=80)
parser.add_argument("--rows", type=int, default=24)
parser.add_argument("--background", choices=("light", "dark"), default="dark")
parser.add_argument(
    "--mirror",
    action="store_true",
    help="Replay live stderr on your own terminal for direct inspection.",
)
parser.add_argument("command", nargs=argparse.REMAINDER)
args = parser.parse_args()
command = args.command[1:] if args.command[:1] == ["--"] else args.command
if not command or args.columns < 10 or args.rows < 3:
    parser.error("command and at least 10 columns / 3 rows required")
args.evidence.mkdir(parents=True, exist_ok=False)
master, slave = pty.openpty()
fcntl.ioctl(
    slave, termios.TIOCSWINSZ, struct.pack("HHHH", args.rows, args.columns, 0, 0)
)
child = None
raw = bytearray()
frames = []
# Keep the visible terminal cells; double-width continuations are empty cells.
screen = [[(" ", "")] * args.columns for _ in range(args.rows)]
color = ""
palette = [
    "#000000",
    "#800000",
    "#008000",
    "#808000",
    "#000080",
    "#800080",
    "#008080",
    "#c0c0c0",
    "#808080",
    "#ff0000",
    "#00ff00",
    "#ffff00",
    "#0000ff",
    "#ff00ff",
    "#00ffff",
    "#ffffff",
]
x = y = 0
pending = ""
decoder = codecs.getincrementaldecoder("utf-8")("replace")
csi = re.compile(r"\x1b\[([?0-9;]*)([A-Za-z])")


def ansi_color(index):
    if index < 16:
        return palette[index]
    if index >= 232:
        value = 8 + 10 * (index - 232)
        return f"#{value:02x}{value:02x}{value:02x}"
    index -= 16
    levels = [0, 95, 135, 175, 215, 255]
    return "#" + "".join(
        f"{levels[n]:02x}" for n in (index // 36, index // 6 % 6, index % 6)
    )


def project(text):
    global x, y, pending, color
    pending += text
    index = 0
    while index < len(pending):
        char = pending[index]
        if char == "\x1b":
            match = csi.match(pending, index)
            if match is None:
                break
            values, operation = match.groups()
            count = int(values or 1) if values.isdigit() or not values else 1
            if operation == "A":
                y = max(0, y - count)
            elif operation == "B":
                y = min(args.rows - 1, y + count)
            elif operation == "m":
                codes = [int(value or 0) for value in values.split(";")]
                while codes:
                    code = codes.pop(0)
                    if code in {0, 39}:
                        color = ""
                    elif 30 <= code <= 37:
                        color = ansi_color(code - 30)
                    elif 90 <= code <= 97:
                        color = ansi_color(code - 90 + 8)
                    elif code == 38 and len(codes) >= 2 and codes[0] == 5:
                        color = ansi_color(codes[1])
                        del codes[:2]
                    elif code == 38 and len(codes) >= 4 and codes[0] == 2:
                        color = "#" + "".join(f"{n:02x}" for n in codes[1:4])
                        del codes[:4]
            elif operation == "K":
                start = 0 if values == "2" else x
                screen[y][start:] = [(" ", "")] * (args.columns - start)
            elif operation == "J":
                for row in range(y, args.rows):
                    screen[row] = [(" ", "")] * args.columns
            index = match.end()
            continue
        if char == "\r":
            x = 0
        elif char == "\n":
            y += 1
        elif not unicodedata.category(char).startswith("C"):
            width = 2 if unicodedata.east_asian_width(char) in "WF" else 1
            if x + width > args.columns:
                x = 0
                y += 1
            if y >= args.rows:
                screen.pop(0)
                screen.append([(" ", "")] * args.columns)
                y = args.rows - 1
            screen[y][x] = (char, color)
            if width == 2:
                screen[y][x + 1] = ("", color)
            x += width
        if y >= args.rows:
            screen.pop(0)
            screen.append([(" ", "")] * args.columns)
            y = args.rows - 1
        index += 1
    pending = pending[index:]
    frame = "\n".join(
        "".join(char for char, _ in row).rstrip() for row in screen
    ).rstrip()
    colored = "\n".join(
        "".join(
            f'<span style="color:{fg}">{html.escape(char)}</span>'
            if fg
            else html.escape(char)
            for char, fg in row
        )
        for row in screen
    )
    if not frames or frames[-1]["text"] != frame or frames[-1]["html"] != colored:
        frames.append(
            {
                "seconds": round(time.monotonic() - started, 3),
                "text": frame,
                "html": colored,
            }
        )


started = time.monotonic()
try:
    with (args.evidence / "stdout.txt").open("wb") as stdout:
        child = subprocess.Popen(
            command, stdin=subprocess.DEVNULL, stdout=stdout, stderr=slave
        )
        os.close(slave)
        slave = -1
        with selectors.DefaultSelector() as selector:
            selector.register(master, selectors.EVENT_READ)
            while selector.get_map():
                for _, _ in selector.select(0.1):
                    try:
                        data = os.read(master, 65536)
                    except OSError as error:
                        if error.errno != errno.EIO:
                            raise
                        data = b""
                    if not data:
                        selector.unregister(master)
                        break
                    raw.extend(data)
                    project(decoder.decode(data))
                    if args.mirror:
                        sys.stderr.buffer.write(data)
                        sys.stderr.buffer.flush()
        code = child.wait(timeout=35)
finally:
    if slave >= 0:
        os.close(slave)
    os.close(master)
    if child is not None and child.poll() is None:
        child.terminate()
        try:
            child.wait(timeout=35)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()
    (args.evidence / "stderr.ansi").write_bytes(raw)
    (args.evidence / "frames.json").write_text(
        json.dumps(frames, ensure_ascii=False, indent=2)
    )

background, foreground = (
    ("#fff", "#111") if args.background == "light" else ("#111", "#eee")
)
header = (
    '<!doctype html><meta charset="utf-8"><title>Captured progress frames</title>'
    f'<body style="background:{background};color:{foreground}">'
)
for index, frame in enumerate(frames):
    (args.evidence / f"frame-{index}.html").write_text(
        header
        + f'<pre style="font-family:monospace;font-size:16px;line-height:1.3;width:{args.columns}ch">{frame["html"]}</pre>'
    )
(args.evidence / "frames.html").write_text(
    header
    + "<p>Actual stderr PTY cells and SGR colors (conventional xterm palette). Not a screenshot or full terminal emulator.</p>"
    + "".join(
        f'<h3><a href="frame-{index}.html">Frame {index}: {frame["seconds"]}s</a></h3><pre style="font-family:monospace;line-height:1.3;width:{args.columns}ch">{frame["html"]}</pre>'
        for index, frame in enumerate(frames)
    )
)
(args.evidence / "command.json").write_text(
    json.dumps(
        {
            "command": command,
            "exit": code,
            "columns": args.columns,
            "rows": args.rows,
            "background": args.background,
            "NO_COLOR_present": "NO_COLOR" in os.environ,
            "frame_projection_remainder": pending,
        },
        ensure_ascii=False,
        indent=2,
    )
)
raise SystemExit(code)
