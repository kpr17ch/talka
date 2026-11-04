#!/usr/bin/env python3
"""Generate the "Fenna" avatar frame pack (original character, flat vector style).

Every frame is built from plain SVG shapes in this file, written to ./svg/ and
rendered to PNG with headless Google Chrome. No external images are used.

Frames (contract shared by talka web, talka-ios and the talkaz frame-pack export):
  avatar_main, avatar_smiling,
  avatar_speak_ae, avatar_speak_ltsch, avatar_speak_o, avatar_speak_pmn,
  avatar_work_a, avatar_work_b, avatar_work_c, avatar_work_d

Usage:
  python3 generate.py --out <dir> --format png    # transparent RGBA PNG (talka-ios)
  python3 generate.py --out <dir> --format jpeg   # #00FF00 green screen JPEG (talka web)

Requires Python 3 + Pillow and Google Chrome (override with $CHROME).
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image

SIZE = 2048  # output pixels (square)
VIEW = 1024  # SVG viewBox units
PREFIX = "avatar"
HERE = Path(__file__).resolve().parent
DEFAULT_CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

# Palette: deliberately free of green so the web chroma key never eats the character.
INK = "#2A2140"
SKIN = "#F6C9A6"
SKIN_SHADE = "#E8AE8A"
CHEEK = "#F2958A"
HAIR = "#4A3478"
HAIR_LIGHT = "#6A50A0"
HOODIE = "#F2765A"
HOODIE_SHADE = "#D95C43"
HEADSET = "#2F3A56"
CUP = "#F4B63F"
MOUTH = "#7A2E3E"
TONGUE = "#EE7C86"
LAPTOP = "#CBD0DE"
LAPTOP_SHADE = "#A9B1C6"
WHITE = "#FFFFFF"

STROKE = f'stroke="{INK}" stroke-width="7" stroke-linejoin="round" stroke-linecap="round"'


def eyes(looking_down: bool) -> str:
    out = []
    for cx in (448, 576):
        cy = 424
        if looking_down:
            out.append(f'<ellipse cx="{cx}" cy="{cy + 8}" rx="19" ry="21" fill="{INK}"/>')
            out.append(f'<circle cx="{cx + 6}" cy="{cy + 3}" r="6" fill="{WHITE}"/>')
            # upper lid: skin cap + lid line (focused, looking at the laptop)
            out.append(
                f'<path d="M {cx - 30} {cy - 2} Q {cx} {cy - 12} {cx + 30} {cy - 2} '
                f'L {cx + 30} {cy - 40} L {cx - 30} {cy - 40} Z" fill="{SKIN}"/>'
            )
            out.append(
                f'<path d="M {cx - 26} {cy} Q {cx} {cy - 10} {cx + 26} {cy}" fill="none" {STROKE}/>'
            )
        else:
            out.append(f'<ellipse cx="{cx}" cy="{cy}" rx="20" ry="26" fill="{INK}"/>')
            out.append(f'<circle cx="{cx + 7}" cy="{cy - 9}" r="7" fill="{WHITE}"/>')
            out.append(f'<circle cx="{cx - 6}" cy="{cy + 10}" r="3" fill="{WHITE}" opacity="0.7"/>')
    brow_y = 372 if not looking_down else 380
    out.append(f'<path d="M 420 {brow_y + 4} Q 446 {brow_y - 8} 472 {brow_y}" fill="none" stroke="{HAIR}" stroke-width="10" stroke-linecap="round"/>')
    out.append(f'<path d="M 552 {brow_y} Q 578 {brow_y - 8} 604 {brow_y + 4}" fill="none" stroke="{HAIR}" stroke-width="10" stroke-linecap="round"/>')
    return "\n".join(out)


def mouth(shape: str) -> str:
    if shape == "main":
        return f'<path d="M 484 500 Q 512 520 540 500" fill="none" stroke="{MOUTH}" stroke-width="8" stroke-linecap="round"/>'
    if shape == "smiling":
        return (
            f'<path d="M 468 492 Q 512 500 556 492 Q 548 548 512 550 Q 476 548 468 492 Z" fill="{MOUTH}" stroke="{MOUTH}" stroke-width="5" stroke-linejoin="round"/>'
            f'<path d="M 474 496 Q 512 503 550 496 L 547 510 Q 512 516 477 510 Z" fill="{WHITE}"/>'
            f'<path d="M 488 538 Q 512 520 536 538 Q 524 548 512 548 Q 500 548 488 538 Z" fill="{TONGUE}"/>'
        )
    if shape == "ae":
        return (
            f'<path d="M 478 494 Q 512 486 546 494 Q 545 542 512 546 Q 479 542 478 494 Z" fill="{MOUTH}" stroke="{MOUTH}" stroke-width="5" stroke-linejoin="round"/>'
            f'<path d="M 483 496 Q 512 490 541 496 L 539 506 Q 512 502 485 506 Z" fill="{WHITE}"/>'
            f'<path d="M 490 536 Q 512 518 534 536 Q 524 545 512 545 Q 500 545 490 536 Z" fill="{TONGUE}"/>'
        )
    if shape == "ltsch":
        return (
            f'<rect x="472" y="494" width="80" height="26" rx="12" fill="{MOUTH}" stroke="{MOUTH}" stroke-width="5"/>'
            f'<rect x="478" y="497" width="68" height="10" rx="4" fill="{WHITE}"/>'
            f'<rect x="480" y="508" width="64" height="8" rx="4" fill="{WHITE}" opacity="0.9"/>'
        )
    if shape == "o":
        return (
            f'<circle cx="512" cy="512" r="19" fill="{MOUTH}" stroke="{MOUTH}" stroke-width="6"/>'
            f'<ellipse cx="512" cy="522" rx="10" ry="6" fill="{TONGUE}"/>'
        )
    if shape == "pmn":
        return (
            f'<path d="M 486 506 Q 512 512 538 506" fill="none" stroke="{MOUTH}" stroke-width="9" stroke-linecap="round"/>'
            f'<path d="M 498 520 Q 512 524 526 520" fill="none" stroke="{SKIN_SHADE}" stroke-width="5" stroke-linecap="round"/>'
        )
    if shape == "focus":
        return f'<path d="M 494 508 Q 512 514 530 508" fill="none" stroke="{MOUTH}" stroke-width="8" stroke-linecap="round"/>'
    raise ValueError(shape)


def body() -> str:
    return f"""
  <!-- back hair (bob) -->
  <path d="M 318 410 C 312 230 420 168 512 168 C 604 168 712 230 706 410 L 712 566
           C 712 596 682 604 660 588 L 364 588 C 342 604 312 596 312 566 Z" fill="{HAIR}" {STROKE}/>
  <!-- neck -->
  <path d="M 466 540 L 466 650 L 558 650 L 558 540 Z" fill="{SKIN_SHADE}" {STROKE}/>
  <!-- hoodie -->
  <path d="M 196 1040 C 196 830 280 700 430 668 L 594 668 C 744 700 828 830 828 1040 Z" fill="{HOODIE}" {STROKE}/>
  <path d="M 300 1040 C 300 900 320 820 360 770" fill="none" stroke="{HOODIE_SHADE}" stroke-width="8" stroke-linecap="round"/>
  <path d="M 724 1040 C 724 900 704 820 664 770" fill="none" stroke="{HOODIE_SHADE}" stroke-width="8" stroke-linecap="round"/>
  <!-- hood collar -->
  <path d="M 380 676 C 400 640 470 630 512 650 C 554 630 624 640 644 676 C 610 716 560 726 512 704 C 464 726 414 716 380 676 Z" fill="{HOODIE_SHADE}" {STROKE}/>
  <!-- drawstrings -->
  <path d="M 482 708 L 474 810" stroke="{WHITE}" stroke-width="8" stroke-linecap="round"/>
  <path d="M 542 708 L 550 810" stroke="{WHITE}" stroke-width="8" stroke-linecap="round"/>
  <circle cx="474" cy="816" r="9" fill="{WHITE}" {STROKE}/>
  <circle cx="550" cy="816" r="9" fill="{WHITE}" {STROKE}/>
  <!-- chest badge: small sound-wave mark -->
  <circle cx="640" cy="860" r="40" fill="{WHITE}" {STROKE}/>
  <g stroke="{HOODIE_SHADE}" stroke-width="8" stroke-linecap="round">
    <line x1="620" y1="852" x2="620" y2="868"/><line x1="632" y1="840" x2="632" y2="880"/>
    <line x1="644" y1="846" x2="644" y2="874"/><line x1="656" y1="854" x2="656" y2="866"/>
  </g>
  <!-- face -->
  <ellipse cx="512" cy="412" rx="162" ry="172" fill="{SKIN}" {STROKE}/>
  <!-- fringe -->
  <path d="M 348 392 C 344 270 430 214 520 214 C 612 214 684 272 678 388
           C 650 330 604 300 566 308 C 548 282 500 270 470 296
           C 432 300 380 334 348 392 Z" fill="{HAIR}" {STROKE}/>
  <path d="M 470 236 C 500 226 540 228 566 240" fill="none" stroke="{HAIR_LIGHT}" stroke-width="10" stroke-linecap="round"/>
  <!-- cowlick -->
  <path d="M 512 172 C 520 140 552 128 572 140 C 552 142 536 152 530 172" fill="{HAIR}" {STROKE}/>
  <!-- cheeks + nose -->
  <ellipse cx="414" cy="476" rx="26" ry="14" fill="{CHEEK}" opacity="0.6"/>
  <ellipse cx="610" cy="476" rx="26" ry="14" fill="{CHEEK}" opacity="0.6"/>
  <path d="M 503 452 Q 512 464 521 452" fill="none" stroke="{SKIN_SHADE}" stroke-width="7" stroke-linecap="round"/>
  <!-- headset band + ear cups + mic -->
  <path d="M 326 400 C 300 150 724 150 698 400" fill="none" stroke="{HEADSET}" stroke-width="24" stroke-linecap="round"/>
  <rect x="300" y="352" width="56" height="116" rx="26" fill="{CUP}" {STROKE}/>
  <rect x="668" y="352" width="56" height="116" rx="26" fill="{CUP}" {STROKE}/>
  <rect x="318" y="376" width="20" height="68" rx="10" fill="{HEADSET}"/>
  <rect x="686" y="376" width="20" height="68" rx="10" fill="{HEADSET}"/>
  <path d="M 330 462 C 336 520 370 548 412 552" fill="none" stroke="{HEADSET}" stroke-width="12" stroke-linecap="round"/>
  <ellipse cx="424" cy="552" rx="18" ry="14" fill="{HEADSET}" {STROKE}/>
"""


def hand(cx: float, cy: float) -> str:
    return (
        f'<ellipse cx="{cx}" cy="{cy}" rx="38" ry="28" fill="{SKIN}" {STROKE}/>'
        f'<path d="M {cx - 14} {cy - 18} L {cx - 14} {cy - 4} M {cx + 2} {cy - 22} L {cx + 2} {cy - 6} M {cx + 18} {cy - 18} L {cx + 18} {cy - 4}" '
        f'stroke="{SKIN_SHADE}" stroke-width="5" stroke-linecap="round"/>'
    )


def laptop(left: tuple[float, float], right: tuple[float, float], led_on: bool) -> str:
    lx, ly = left
    rx, ry = right
    led = CUP if led_on else LAPTOP_SHADE
    return f"""
  <!-- sleeves -->
  <path d="M 226 1070 Q 222 {ly + 6} {lx} {ly}" fill="none" stroke="{INK}" stroke-width="78" stroke-linecap="round"/>
  <path d="M 226 1070 Q 222 {ly + 6} {lx} {ly}" fill="none" stroke="{HOODIE}" stroke-width="64" stroke-linecap="round"/>
  <path d="M 798 1070 Q 802 {ry + 6} {rx} {ry}" fill="none" stroke="{INK}" stroke-width="78" stroke-linecap="round"/>
  <path d="M 798 1070 Q 802 {ry + 6} {rx} {ry}" fill="none" stroke="{HOODIE}" stroke-width="64" stroke-linecap="round"/>
  <!-- laptop base -->
  <path d="M 268 968 L 756 968 L 788 1010 L 236 1010 Z" fill="{LAPTOP_SHADE}" {STROKE}/>
  <!-- laptop lid (back side facing the viewer) -->
  <rect x="318" y="716" width="388" height="256" rx="22" fill="{LAPTOP}" {STROKE}/>
  <circle cx="512" cy="840" r="34" fill="{WHITE}" {STROKE}/>
  <g stroke="{HEADSET}" stroke-width="7" stroke-linecap="round">
    <line x1="496" y1="834" x2="496" y2="846"/><line x1="507" y1="824" x2="507" y2="856"/>
    <line x1="518" y1="829" x2="518" y2="851"/><line x1="529" y1="836" x2="529" y2="844"/>
  </g>
  <circle cx="680" cy="950" r="6" fill="{led}"/>
  {hand(lx, ly)}
  {hand(rx, ry)}
"""


FRAMES: dict[str, dict] = {
    "main": {"mouth": "main"},
    "smiling": {"mouth": "smiling"},
    "speak_ae": {"mouth": "ae"},
    "speak_ltsch": {"mouth": "ltsch"},
    "speak_o": {"mouth": "o"},
    "speak_pmn": {"mouth": "pmn"},
    # work idle loop: a -> b -> c are typing frames, d is the hand-lift transition frame
    "work_a": {"mouth": "focus", "work": ((300, 958), (724, 966), True)},
    "work_b": {"mouth": "focus", "work": ((304, 970), (720, 952), False)},
    "work_c": {"mouth": "focus", "work": ((298, 962), (726, 962), True)},
    "work_d": {"mouth": "focus", "work": ((300, 962), (716, 926), False)},
}


def build_svg(frame: str) -> str:
    spec = FRAMES[frame]
    work = spec.get("work")
    parts = [body(), eyes(looking_down=work is not None), mouth(spec["mouth"])]
    if work:
        parts.append(laptop(*work))
    inner = "\n".join(parts)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {VIEW} {VIEW}" width="{VIEW}" height="{VIEW}">\n'
        f"<!-- Fenna, original talka avatar. Frame: {frame}. Generated by generate.py -->\n"
        f"{inner}\n</svg>\n"
    )


def render(svg_path: Path, png_path: Path, chrome: str, workdir: Path) -> None:
    html = workdir / f"{svg_path.stem}.html"
    html.write_text(
        "<!doctype html><html><head><style>html,body{margin:0;padding:0;background:transparent;overflow:hidden}"
        f"img{{display:block;width:{SIZE}px;height:{SIZE}px}}</style></head>"
        f'<body><img src="{svg_path.as_uri()}"></body></html>'
    )
    subprocess.run(
        [
            chrome, "--headless=new", "--disable-gpu", "--hide-scrollbars",
            "--force-device-scale-factor=1", "--default-background-color=00000000",
            "--allow-file-access-from-files", f"--user-data-dir={workdir / 'profile'}",
            f"--window-size={SIZE},{SIZE}", f"--screenshot={png_path}", html.as_uri(),
        ],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    with Image.open(png_path) as im:
        if im.size != (SIZE, SIZE):
            im.crop((0, 0, SIZE, SIZE)).save(png_path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--format", choices=("png", "jpeg"), default="png")
    parser.add_argument("--svg-dir", type=Path, default=HERE / "svg")
    args = parser.parse_args()

    chrome = os.environ.get("CHROME", DEFAULT_CHROME)
    args.out.mkdir(parents=True, exist_ok=True)
    args.svg_dir.mkdir(parents=True, exist_ok=True)
    workdir = Path(tempfile.mkdtemp(prefix="avatar-render-"))
    try:
        for frame in FRAMES:
            name = f"{PREFIX}_{frame}"
            svg_path = args.svg_dir / f"{name}.svg"
            svg_path.write_text(build_svg(frame))
            raw = workdir / f"{name}.png"
            render(svg_path.resolve(), raw, chrome, workdir)
            with Image.open(raw) as im:
                rgba = im.convert("RGBA")
                if args.format == "png":
                    rgba.save(args.out / f"{name}.png", optimize=True)
                else:
                    bg = Image.new("RGBA", rgba.size, (0, 255, 0, 255))
                    bg.alpha_composite(rgba)
                    bg.convert("RGB").save(args.out / f"{name}.jpeg", quality=92)
            print(f"wrote {name}")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    main()
