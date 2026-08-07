"""Generate docs/assets/siamcart_architecture.png (deterministic PIL drawing)."""
import os
from PIL import Image, ImageDraw, ImageFont

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "docs", "assets", "siamcart_architecture.png")
W, H = 1760, 1240
BG = "#ffffff"

FONT_DIR = "C:/Windows/Fonts"
def F(size, bold=False):
    path = os.path.join(FONT_DIR, "segoeuib.ttf" if bold else "segoeui.ttf")
    return ImageFont.truetype(path, size)

BLUE = "#1f6feb"; BLUE_L = "#dbe9fd"
GRAY = "#57606a"; GRAY_L = "#eef1f5"
GREEN = "#1a7f37"; GREEN_L = "#dafbe1"
AMBER = "#9a6700"; AMBER_L = "#fff8c5"
PURPLE = "#8250df"; PURPLE_L = "#f0e7fb"
DARK = "#24292f"

img = Image.new("RGB", (W, H), BG)
d = ImageDraw.Draw(img)

def box(x, y, w, h, label, sub=None, fill=GRAY_L, border=GRAY, font=22, sub_font=15, rounded=True):
    r = 14 if rounded else 0
    d.rounded_rectangle([x, y, x + w, y + h], radius=r, fill=fill, outline=border, width=2)
    f = F(font, bold=True)
    lw = d.textlength(label, font=f)
    ty = y + (h - font) / 2 - (8 if sub else 0)
    d.text((x + (w - lw) / 2, ty), label, fill=DARK, font=f)
    if sub:
        sf = F(sub_font)
        sw = d.textlength(sub, font=sf)
        d.text((x + (w - sw) / 2, ty + font - 2), sub, fill=GRAY, font=sf)
    return (x, y, x + w, y + h)

def center_label(cx, cy, text, font=20, fill=DARK, bold=True):
    f = F(font, bold=bold)
    tw = d.textlength(text, font=f)
    d.text((cx - tw / 2, cy - font / 2), text, fill=fill, font=f)

def arrow(x1, y1, x2, y2, color=GRAY, width=3):
    d.line([x1, y1, x2, y2], fill=color, width=width)
    import math
    ang = math.atan2(y2 - y1, x2 - x1)
    L = 13
    for da in (2.7, -2.7):
        d.line([x2, y2, x2 - L * math.cos(ang - da), y2 - L * math.sin(ang - da)],
               fill=color, width=width)

def down(x1, y1, x2, y2, color=GRAY, width=3):
    arrow(x1, y1, x2, y2, color, width)

# ── Title ──────────────────────────────────────────────────────────────
t = F(34, bold=True)
tw = d.textlength("SiamCart — Multi-Agent Architecture", font=t)
d.text(((W - tw) / 2, 26), "SiamCart — Multi-Agent Architecture", fill=DARK, font=t)
center_label(W / 2, 78, "Thai e-commerce customer support research prototype", 17)

# ── Section 1: request flow ────────────────────────────────────────────
cx = W / 2
browser = box(cx - 150, 110, 300, 56, "Customer Browser", fill=GRAY_L, border=GRAY)
down(cx, 166, cx, 196)
store = box(cx - 220, 196, 440, 64, "SiamCart Storefront", "HTML / CSS / Vanilla JavaScript", fill=BLUE_L, border=BLUE)
down(cx, 260, cx, 290)
api = box(cx - 200, 290, 400, 56, "FastAPI API Layer", fill=BLUE_L, border=BLUE)
down(cx, 346, cx, 376)
router = box(cx - 230, 376, 460, 56, "Intelligent Router", fill=PURPLE_L, border=PURPLE)

# router -> two agents
arrow(cx - 30, 432, cx - 330, 472, PURPLE)
arrow(cx + 30, 432, cx + 330, 472, PURPLE)

tracker = box(cx - 560, 472, 460, 62, "Transaction Tracker", "order / payment / shipment facts", fill=GRAY_L, border=GRAY)
policy = box(cx + 100, 472, 460, 62, "Store Policy Evaluator", "RAG retrieval over policy docs", fill=GRAY_L, border=GRAY)

down(cx - 330, 534, cx - 330, 566, GRAY)
sqlite = box(cx - 560, 566, 460, 56, "SQLite", "orders.db  ·  read-only evidence", fill=GRAY_L, border=GRAY)
down(cx + 330, 534, cx + 330, 566, GRAY)
chroma = box(cx + 100, 566, 460, 56, "ChromaDB / Policy Docs", "data/policies", fill=GRAY_L, border=GRAY)

# merge back to Thai Response
arrow(cx - 330, 622, cx - 120, 668, GRAY)
arrow(cx + 330, 622, cx + 120, 668, GRAY)
thai = box(cx - 220, 668, 440, 56, "Thai Response", "deterministic, polite, Thai-first", fill=GREEN_L, border=GREEN)

# ── Section 2: demo extension + optional LLM ───────────────────────────
demo = box(70, 480, 400, 84, "Product Catalog Lookup", "Demo Extension — Deterministic Handler", fill=GREEN_L, border=GREEN)
down(270, 564, 270, 600, GREEN)
box(70, 600, 400, 52, "SQLite", "products table", fill=GRAY_L, border=GRAY)
center_label(270, 672, "not an evaluated agent", 15, GRAY)

llm = box(1290, 480, 400, 84, "Optional DeepSeek", "response generation / fallback — OPTIONAL", fill=AMBER_L, border=AMBER)
arrow(1490, 440, 1490, 480, AMBER)
center_label(1490, 458, "DEEPSEEK_ENABLED=false by default", 15, AMBER)

# ── Section 3: deployment layer ────────────────────────────────────────
down(cx, 724, cx, 780, GRAY)
dep = box(cx - 260, 780, 520, 56, "Deployment — Docker → Railway", fill=GRAY_L, border=GRAY)
down(cx, 836, cx, 866, GRAY)
vol = box(cx - 380, 866, 760, 64, "Persistent Volume  /data", "writable SQLite · survives restarts", fill=BLUE_L, border=BLUE)
db1 = box(cx - 330, 952, 300, 52, "/data/orders.db", "SQLite runtime DB", fill=GRAY_L, border=GRAY)
db2 = box(cx + 30, 952, 300, 52, "/data/chroma", "policy index", fill=GRAY_L, border=GRAY)

# ── Footer ─────────────────────────────────────────────────────────────
center_label(cx, 1050, "Evaluated framework: Intelligent Router · Transaction Tracker · Store Policy Evaluator", 16, DARK)
center_label(cx, 1082, "Product Catalog Lookup is a demo extension, not a fourth evaluated agent", 15, GRAY)

os.makedirs(os.path.dirname(OUT), exist_ok=True)
img.save(OUT, "PNG")
print("saved", OUT, img.size)
