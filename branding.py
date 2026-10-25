"""The SmoothTransfer logo.

Put "SmoothTransfer Education Path Logo.png" next to app.py (or in an assets/ folder, or call it logo.png).
If the file is not there, the app falls back to the 🎓 emoji and nothing breaks.

How it is shown depends on the picture, which this module checks itself:
  - a wide logo (about 2.2 times wider than tall, or more) is assumed to contain the name, so it is shown alone;
  - a squarer logo is a symbol, so the name "SmoothTransfer" is written next to it, in the same row.
Set LOGO_SHOW_NAME=1 or LOGO_SHOW_NAME=0 in .env to override that.
The logo sits on a white rounded tile, so it stays readable on the dark theme even if it has a transparent background.
"""
import base64
import io
import os
from pathlib import Path

import streamlit as st

HERE = Path(__file__).resolve().parent
LOGO_DIRS = [HERE, HERE / "assets"]
LOGO_NAMES = ["SmoothTransfer Education Path Logo.png", "logo.png"]
WIDE_RATIO = 2.2        # width / height at or above this: the logo already contains the name
HEADER_HEIGHT = 56      # pixels tall in the page header
ICON_SIZE = 128         # the browser-tab icon is a square of this size


def find_logo():
    """Path of the logo file, or None."""
    for d in LOGO_DIRS:
        for n in LOGO_NAMES:
            p = Path(d) / n
            if p.is_file():
                return p
    return None


def _open(path):
    from PIL import Image
    img = Image.open(path)
    img.load()
    return img.convert("RGBA")


def show_name(path):
    """Whether to write the name next to the logo. LOGO_SHOW_NAME=1/0 overrides the guess."""
    forced = os.getenv("LOGO_SHOW_NAME", "").strip().lower()
    if forced in ("1", "true", "yes"):
        return True
    if forced in ("0", "false", "no"):
        return False
    try:
        w, h = _open(path).size
    except Exception:
        return True
    return (w / h) < WIDE_RATIO if h else True


@st.cache_resource(show_spinner=False)
def _header_html(path_str, mtime):
    """The logo as a small image inside a white tile. Shrunk first, so the page does not carry a big file.
    `mtime` is only there so a replaced file is picked up."""
    from PIL import Image
    img = _open(path_str)
    h = HEADER_HEIGHT * 2                                   # twice the height, so it stays sharp on retina screens
    if img.height > h:
        img = img.resize((max(1, round(img.width * h / img.height)), h), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    b64 = base64.b64encode(buf.getvalue()).decode()
    return ('<div style="display:inline-block;background:#ffffff;border-radius:12px;padding:6px 10px;line-height:0;">'
            f'<img src="data:image/png;base64,{b64}" alt="SmoothTransfer" style="height:{HEADER_HEIGHT}px;width:auto;"></div>')


def header_html():
    """The HTML for the logo tile, or None when there is no logo."""
    p = find_logo()
    return _header_html(str(p), p.stat().st_mtime) if p else None


def page_icon():
    """What to give st.set_page_config(page_icon=...): a square version of the logo, or the emoji."""
    p = find_logo()
    if not p:
        return "🎓"
    try:
        from PIL import Image
        img = _open(p)
        side = max(img.size)
        canvas = Image.new("RGBA", (side, side), (255, 255, 255, 0))      # a wide logo is centred, not stretched
        canvas.paste(img, ((side - img.width) // 2, (side - img.height) // 2), img)
        return canvas.resize((ICON_SIZE, ICON_SIZE), Image.LANCZOS)
    except Exception:
        return "🎓"


def render_header():
    """Draw the page header: the logo (and the name when the logo does not contain it), else the old emoji title."""
    p = find_logo()
    try:
        html = header_html() if p else None
    except Exception:                                       # a damaged picture must never break the page
        html = None
    if not html:
        st.title("🎓 SmoothTransfer")
        return
    if show_name(p):
        # the symbol and the name in one row, so there is no big gap between them
        st.markdown('<div role="heading" aria-level="1" style="display:flex;align-items:center;gap:16px;margin:0 0 .5rem 0;">'
                    f'{html}<span style="font-size:2.75rem;font-weight:700;line-height:1.2;">SmoothTransfer</span></div>',
                    unsafe_allow_html=True)
    else:
        st.markdown(html, unsafe_allow_html=True)
