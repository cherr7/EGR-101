"""Generate Oral Presentation 2 figures from repo screenshots and raw test data."""
import csv, glob, json, statistics as st, sys
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch, Rectangle
from PIL import Image

P = Path(sys.argv[1])
A, D, SCR = P / "assets", P / "assets/data", P / "assets/screens"
OUT = A / "figures"; OUT.mkdir(parents=True, exist_ok=True)

INK, INK2, MUTED, GRID, SURF = "#0b0b0b", "#52514e", "#8a8984", "#e4e3df", "#fcfcfb"
BLUE, ORANGE, AQUA, VIOLET = "#2a78d6", "#eb6834", "#1baf7a", "#6250d6"
GOOD, CRIT, WARN = "#008300", "#e34948", "#eda100"
TEAL = "#1f4e4a"   # app's own dark teal, used for "final" emphasis
HW = "#9a9a94"     # hardware placeholder gray
plt.rcParams.update({"font.family": ["Arial", "Arial Unicode MS", "DejaVu Sans"], "font.size": 14, "axes.edgecolor": MUTED,
                     "axes.labelcolor": INK2, "xtick.color": INK2, "ytick.color": INK2,
                     "figure.facecolor": "white", "axes.facecolor": "white"})

def save(fig, name):
    fig.savefig(OUT / name, dpi=200, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print("wrote", name)

def box(ax, x, y, w, h, text, fc="white", ec=INK2, tc=INK, fs=13, bold=False, ls="-", r=0.02):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle=f"round,pad=0,rounding_size={r}",
                                fc=fc, ec=ec, lw=1.6, ls=ls))
    ax.text(x + w / 2, y + h / 2, text, ha="center", va="center", color=tc, fontsize=fs,
            weight="bold" if bold else "normal", wrap=True, linespacing=1.35)

def arrow(ax, x1, y1, x2, y2, c=INK2, lw=1.8, ls="-"):
    ax.add_patch(FancyArrowPatch((x1, y1), (x2, y2), arrowstyle="-|>", mutation_scale=16,
                                 color=c, lw=lw, ls=ls, shrinkA=0, shrinkB=0))

def img(ax, path, x, y, w, border=INK2):
    """Place an image with its top-left at (x, y+h) in data coords, keeping aspect (axes aspect=equal)."""
    im = Image.open(path).convert("RGB")
    h = w * im.height / im.width
    ax.imshow(im, extent=(x, x + w, y, y + h), zorder=2, interpolation="lanczos")
    ax.add_patch(Rectangle((x, y), w, h, fill=False, ec=border, lw=1.2, zorder=3))
    return h

def canvas(w, h):
    fig = plt.figure(figsize=(w, h))
    ax = fig.add_axes([0, 0, 1, 1]); ax.set_xlim(0, w); ax.set_ylim(0, h); ax.axis("off")
    ax.set_aspect("equal")
    return fig, ax

# ---------------- F1 decision process funnel ----------------
fig, ax = canvas(16, 5.2)
stages = [
    ("Decompose", "3 functional blocks\nID medium · Scanning ·\nDatabase", "#eef4fb"),
    ("Brainstorm", "17 concepts\n6 ID · 6 scanning ·\n5 database", "#eef4fb"),
    ("Pugh screening", "3 screening matrices\nvs. baselines (+/0/−)\n→ 2 ID · 3 scan · 1 DB", "#eef4fb"),
    ("Morphological chart", "Combine survivors\n→ 5 integrated\nsolutions (A–E)", "#eef4fb"),
    ("Weighted Pugh", "4 weighted criteria\nE = 4.65 (1st)\nA = 2.85 (4th)", "#eef4fb"),
    ("Decision", "Advance A + E\nin parallel\n(>90% shared software)", "#e6f2ee"),
]
x = 0.2
for i, (t, body, fc) in enumerate(stages):
    ax.text(x + 1.15, 4.55, t, ha="center", fontsize=15, weight="bold", color=INK)
    box(ax, x, 1.95, 2.3, 2.0, body, fc=fc, ec=BLUE if i < 5 else AQUA, fs=13)
    if i < 5: arrow(ax, x + 2.33, 2.85, x + 2.6, 2.85)
    x += 2.63
ax.text(0.2, 0.75, "Weights from pairwise comparison: first-scan accuracy 40%, speed 30%, durability 25%, cost 5%.  "
        "Database was standardized across all designs (local SQL + sync) and removed as a variable.",
        fontsize=12.5, color=INK2)
ax.text(0.2, 0.25, "Source: Design Evaluation Memo, 28 Sept 2026", fontsize=11, color=MUTED)
save(fig, "fig-01-decision-process.png")

# ---------------- F2 weighted Pugh ----------------
crit = [("First-scan accuracy", 40), ("Speed", 30), ("Durability", 25), ("Cost", 5)]
ratings = {"A": [3, 3, 2, 5], "B": [2, 2, 4, 5], "C": [5, 4, 3, 3], "D": [2, 5, 5, 1], "E": [5, 5, 4, 3]}
names = {"A": "A  Barcode\nhandheld", "B": "B  Barcode\nfixed", "C": "C  RFID\nhandheld",
         "D": "D  RFID\nwalk-through", "E": "E  RFID\nfixed pad"}
totals = {k: round(sum(r * w / 100 for r, (_, w) in zip(v, crit)), 2) for k, v in ratings.items()}
assert totals == {"A": 2.85, "B": 2.65, "C": 4.10, "D": 3.60, "E": 4.65}, totals
rank = {k: i + 1 for i, k in enumerate(sorted(totals, key=totals.get, reverse=True))}
fig, ax = canvas(16, 7.4)
cols = ["Criterion", "Weight"] + list(ratings)
cx = [0.3, 4.3, 6.0, 8.0, 10.0, 12.0, 14.0]; cw = [4.0, 1.7] + [2.0] * 5
y0 = 6.0; rh = 0.78
for j, k in enumerate(cols):
    label = names.get(k, k)
    hl = k in ("A", "E")
    if hl: ax.add_patch(Rectangle((cx[j], 0.55), cw[j], 6.75, fc="#e6f2ee" if k == "E" else "#eef4fb", ec="none"))
    ax.text(cx[j] + (cw[j] / 2 if j > 0 else 0.1), y0 + 0.62, label, ha="center" if j > 0 else "left", va="center",
            fontsize=14, weight="bold", color=INK)
ax.plot([0.3, 16], [y0 + 0.08] * 2, color=INK2, lw=1.5)
for i, (c, w) in enumerate(crit):
    y = y0 - rh * (i + 0.5)
    ax.text(cx[0] + 0.1, y, c, va="center", fontsize=14, color=INK)
    ax.text(cx[1] + cw[1] / 2, y, f"{w}%", ha="center", va="center", fontsize=14, color=INK2)
    for j, k in enumerate(ratings):
        r = ratings[k][i]
        ax.text(cx[j + 2] + cw[j + 2] / 2, y, f"{r}  ({r * w / 100:.2f})", ha="center", va="center", fontsize=13.5, color=INK)
    ax.plot([0.3, 16], [y - rh / 2] * 2, color=GRID, lw=1)
yt = y0 - rh * 4.55
ax.plot([0.3, 16], [yt + 0.38] * 2, color=INK2, lw=1.5)
ax.text(cx[0] + 0.1, yt, "Weighted total (of 5.00)", va="center", fontsize=14.5, weight="bold")
for j, k in enumerate(ratings):
    ax.text(cx[j + 2] + cw[j + 2] / 2, yt, f"{totals[k]:.2f}", ha="center", va="center", fontsize=17, weight="bold",
            color=TEAL if k in ("A", "E") else INK)
yr = yt - 0.75
ax.text(cx[0] + 0.1, yr, "Rank  ·  Advance?", va="center", fontsize=14, color=INK2)
for j, k in enumerate(ratings):
    adv = "Yes" if k in ("A", "E") else "No"
    ax.text(cx[j + 2] + cw[j + 2] / 2, yr, f"#{rank[k]}  ·  {adv}", ha="center", va="center", fontsize=14,
            color=GOOD if adv == "Yes" else MUTED, weight="bold" if adv == "Yes" else "normal")
ax.text(0.3, 0.2, "Ratings 1–5; score = rating × weight.  E won on accuracy and speed; A kept for cost (5/5) and build speed. "
        "Source: Design Evaluation Memo, Table 1.1", fontsize=11.5, color=MUTED)
save(fig, "fig-02-weighted-pugh.png")

# ---------------- F3 decision evolution ----------------
rows = [
    ("Identification", "Passive UHF RFID (E) + barcode (A),\nbuilt in parallel",
     "2D Data Matrix printed label (A); RFID not built.\nOptional NFC tag reader kept as an add-on",
     "Barcode was built first as planned and met\nTest 1 targets; RFID left as a future upgrade"),
    ("What a label means", "One label on top of a stack =\nthe whole stack",
     "One permanent number per specimen,\nlinked to its stack (scan any piece)",
     "Staff remove part of a stack; audit needs\nper-specimen ENTRY/EXIT"),
    ("Database", "Local SQLite cache + cloud SQL sync;\n24 h offline queue",
     "One PostgreSQL server on a Mac host;\nPi stations show OFFLINE, no offline writes",
     "Outline clarification: show NOT AVAILABLE\ninstead of queueing; no sync conflicts in audit"),
    ("Outage criterion", "≥ 24 h offline storage, sync later",
     "Recover to scan-ready in < 60 s after\npower + network loss (Test 5)",
     "Replaced in Testing Plan memo (28 Oct)"),
    ("Scan workflow", "Scan → review → submit",
     "Scan records automatically; persistent\nIN/OUT; Undo; early-checkout override",
     "Criterion: one scan, zero typed entries;\nRound 3's Submit tap was removed"),
    ("Corrections", "Supervisor login + mandatory reason",
     "Anyone can edit; append-only history keeps\nevery original (DB refuses UPDATE/DELETE)",
     "Dropped after Oct 8 feedback round;\nauditability comes from history instead"),
]
fig, ax = canvas(17, 9.6)
hx = [0.2, 3.2, 7.6, 12.2]; hw = [3.0, 4.4, 4.6, 4.8]
heads = ["Decision", "Originally decided (Sept)", "Built after development (Nov)", "Why it changed"]
for x, w, h in zip(hx, hw, heads):
    ax.text(x + 0.1, 9.1, h, fontsize=15, weight="bold", color=TEAL if "Built" in h else INK)
ax.plot([0.2, 17], [8.85] * 2, color=INK2, lw=1.5)
y = 8.85
for r in rows:
    rh = 1.38
    for k, (x, w, t) in enumerate(zip(hx, hw, r)):
        ax.text(x + 0.1, y - rh / 2, t, va="center", fontsize=12.5 if k else 13.5,
                weight="bold" if k == 0 else "normal", color=INK if k != 3 else INK2, linespacing=1.3)
    arrow(ax, 7.25, y - rh / 2, 7.55, y - rh / 2, c=MUTED, lw=1.4)
    y -= rh
    ax.plot([0.2, 17], [y] * 2, color=GRID, lw=1)
ax.text(0.2, 0.25, "Sources: Design Evaluation Memo (28 Sept), Project Outline clarifications, Testing Plan memo (28 Oct), repo README / PROJECT_STATUS.md",
        fontsize=11, color=MUTED)
save(fig, "fig-03-decision-evolution.png")

# ---------------- F4 UI evolution timeline ----------------
Image.open(SCR / "prototypes/r1-scanner.png").crop((0, 60, 1024, 680)).save(SCR / "prototypes/r1-scanner-crop.png")
Image.open(SCR / "prototypes/r1-correction.png").crop((0, 50, 1024, 730)).save(SCR / "prototypes/r1-correction-crop.png")
pr, fin = SCR / "prototypes", SCR / "final"
fig, ax = canvas(20, 9.6)
cols = [
    ("Round 1 · low fidelity", "Oct 7", "40 clickable concepts:\n16 scanner, 16 correction,\n8 tool arrangements",
     "Kept: persistent IN/OUT, whole-\nstack default. Dropped: supervisor\nsign-off + reason for edits",
     pr / "r1-scanner-crop.png", pr / "r1-correction-crop.png", BLUE),
    ("Round 2 · medium fidelity", "Oct 8", "8 scanner layouts at 800×480,\n6 transaction-log editors;\nshared stack QR code",
     "Kept: fixed toolbars, partial OUT\nmoves the rest. Changed: shared\nstack QR → one ID per specimen",
     pr / "r2-scanner.png", pr / "r2-log-editor.png", BLUE),
    ("Round 3 · medium fidelity", "mid-Oct", "One ID per specimen (Data Matrix),\none ENTRY/EXIT each; review\n+ Submit; popup editor",
     "Kept: one ENTRY/EXIT, whole-\ntransaction Undo. Changed: Submit\nremoved; mock API → real DB",
     pr / "r3-scanner.png", pr / "r3-log-editor.png", BLUE),
    ("Final · working prototype", "Oct 22 → Nov 2", "Real PostgreSQL + FastAPI +\nReact; Pi 5 kiosk at 1280×720;\nauto-record on scan",
     "Tested on real Pi + scanner:\n1.30 s/scan, 119/119 records\ncorrect (Test 1)",
     fin / "03-scanner-in-confirmed.png", fin / "11-staff-correction.png", TEAL),
]
cw, gap = 4.6, 0.33
for i, (title, date, what, learned, a, b, c) in enumerate(cols):
    x = 0.2 + i * (cw + gap)
    ax.add_patch(Rectangle((x, 9.0), cw, 0.08, fc=c, ec="none"))
    ax.text(x, 9.25, title, fontsize=15.5, weight="bold", color=c)
    ax.text(x + cw, 9.25, date, fontsize=13, color=INK2, ha="right")
    h1 = img(ax, a, x, 0, cw)            # measure then move
    ax.images[-1].remove(); ax.patches[-1].remove()
    top = 8.75
    h1 = img(ax, a, x, top - h1, cw)
    ax.text(x, top - h1 - 0.12, "Room scanner", fontsize=11, color=MUTED, va="top")
    h2w = cw * 0.80
    im2 = Image.open(b); h2 = h2w * im2.height / im2.width
    y2 = top - h1 - 0.45 - h2
    img(ax, b, x, y2, h2w)
    ax.text(x + h2w + 0.08, y2 + h2, "Staff\ncorrection\nscreen", fontsize=11, color=MUTED, va="top")
    ax.text(x, y2 - 0.15, what, fontsize=12.5, color=INK, va="top", linespacing=1.3)
    ax.text(x, y2 - 1.35, learned, fontsize=12.5, color=INK2, va="top", linespacing=1.3, style="italic")
    if i < 3: arrow(ax, x + cw + 0.03, 9.04, x + cw + gap - 0.03, 9.04, c=MUTED)
save(fig, "fig-04-ui-evolution.png")

# ---------------- F5 database evolution ----------------
fig, ax = canvas(20, 8.2)
stagesdb = [
    ("v0 · Memo design (Sept)", BLUE,
     ["Identity: 1 label per stack\n(project · status · test)",
      "Storage: local SQLite cache\n+ central cloud SQL",
      "Records: raw scan log +\nseparate audit record",
      "Offline: queue 24 h, sync later"]),
    ("v1 · Round 2 (Oct 8)", BLUE,
     ["Identity: shared stack QR;\nspecimens numbered 01–20",
      "Records: transaction log in\nJSON, one row per movement",
      "Edits append a 'modify_movement'\nevent pointing at the original",
      "Next: one ENTRY/EXIT per specimen;\none conditioning period each"]),
    ("v2 · Round 3 (mid-Oct)", BLUE,
     ["Identity: 40-bit integer ID\nper specimen (Data Matrix)",
      "Each specimen: one ENTRY +\none EXIT + raw_json_time history",
      "Undo reverses every specimen\nin one transaction",
      "Next: replace the mock API with a\nreal DB that enforces these rules"]),
    ("v3 · Final (PostgreSQL)", TEAL,
     ["Identity: sequential number +\nDamm check digit, 16×16 DM",
      "conditioning_sessions: DB-enforced\none period per specimen",
      "audit_events: trigger refuses\nUPDATE / DELETE (append-only)",
      "movement_requests: request UUID\nmakes retries safe (idempotent)"]),
]
cw = 4.6
for i, (t, c, items) in enumerate(stagesdb):
    x = 0.2 + i * (cw + 0.33)
    ax.text(x, 7.7, t, fontsize=15.5, weight="bold", color=c)
    ax.add_patch(Rectangle((x, 7.45), cw, 0.07, fc=c, ec="none"))
    for k, it in enumerate(items):
        y = 6.2 - k * 1.55
        prob = it.startswith("Next")
        box(ax, x, y, cw, 1.3, it, fc="#fff6f0" if prob else ("#e6f2ee" if c == TEAL else "#f4f7fb"),
            ec=ORANGE if prob else c, fs=12.5)
    if i < 3: arrow(ax, x + cw + 0.03, 7.48, x + cw + 0.3, 7.48, c=MUTED)
ax.text(0.2, 0.05, "Sources: Design Evaluation Memo; output/prototypes/round-two & round-three READMEs; backend/app/models.py and alembic migrations (origin/main)",
        fontsize=11, color=MUTED)
save(fig, "fig-05-database-evolution.png")

# ---------------- F6 final schema ----------------
fig, ax = canvas(18, 9.4)
def table(x, y, name, fields, c=TEAL, w=3.9):
    h = 0.48 * (len(fields) + 1) + 0.1
    ax.add_patch(FancyBboxPatch((x, y - h), w, h, boxstyle="round,pad=0,rounding_size=0.06", fc="white", ec=c, lw=1.8))
    ax.add_patch(FancyBboxPatch((x, y - 0.55), w, 0.55, boxstyle="round,pad=0,rounding_size=0.06", fc=c, ec=c, lw=1.8))
    ax.text(x + 0.15, y - 0.28, name, color="white", fontsize=14, weight="bold", va="center")
    for k, f in enumerate(fields):
        ax.text(x + 0.15, y - 0.55 - 0.45 * (k + 0.6), f, fontsize=12, color=INK, va="center")
    return (x, y - h, w, h)
tp = table(0.3, 8.9, "projects", ["project_number (text, unique)"], c=BLUE)
tm = table(0.3, 7.4, "materials", ["material_ref (text, unique)"], c=BLUE)
tt = table(0.3, 5.9, "treatments", ["code  (AR, 1L…100L, …)"], c=BLUE)
ts = table(5.0, 8.9, "stacks", ["id (sequential)", "project_id → projects", "material_id → materials",
                                "test_code", "preconditioning → treatments", "version (stale-edit guard)"])
tsp = table(5.0, 4.6, "specimens", ["id (sequential) = label number", "stack_id → stacks", "number (01…N), unique per stack"])
tcs = table(10.0, 8.9, "conditioning_sessions", ["specimen_id → specimens", "room_id → rooms", "entered_at · exited_at",
                                                 "incomplete · early_exit_acknowledged", "UNIQUE: one period per specimen",
                                                 "CHECK: exit ≥ entry"], w=4.6)
tae = table(10.0, 5.0, "audit_events  (append-only)", ["kind · direction · actor", "specimen_ids [ ]  (GIN index)", "payload (original + changes)",
                                                       "recorded_at", "trigger blocks UPDATE/DELETE"], w=4.6)
tr = table(15.0, 8.9, "rooms", ["temp ± tol, RH ± tol", "required_hours = 24"], c=BLUE, w=2.8)
tst = table(15.0, 7.2, "stations", ["room_id → rooms", "(3 Pi door stations)"], c=BLUE, w=2.8)
tmr = table(15.0, 5.0, "movement_requests", ["request UUID", "saved result", "(safe retry)"], c=BLUE, w=2.8)
tpj = table(0.3, 4.3, "print_jobs · prep_drafts", ["label printing + Excel import", "review state"], c=MUTED)
def link(a, b, ya=None, yb=None):
    ax.add_patch(FancyArrowPatch(a, b, arrowstyle="-|>", mutation_scale=13, color=INK2, lw=1.4, connectionstyle="arc3,rad=0"))
link((4.2, 8.55), (5.0, 8.0)); link((4.2, 7.05), (5.0, 7.55)); link((4.2, 5.55), (5.0, 6.75))
link((6.95, 5.40), (6.95, 4.65)); link((8.9, 4.2), (10.0, 7.2)); link((14.6, 8.3), (15.0, 8.4));
link((8.9, 3.8), (10.0, 3.8))
ax.text(9.0, 3.95, "history of", fontsize=10.5, color=MUTED)
ax.text(0.3, 0.6, "Every scan is one database transaction: lock the stack, write sessions + one audit event, commit all-or-nothing.\n"
        "ENTRY/EXIT shown to staff are derived from sessions; the original observation is never overwritten.",
        fontsize=12.5, color=INK2, linespacing=1.4)
ax.text(0.3, 0.1, "Source: backend/app/models.py, alembic 0001/0002 (origin/main). Arrows = foreign keys.", fontsize=11, color=MUTED)
save(fig, "fig-06-final-schema.png")

# ---------------- F7 label evolution ----------------
fig, ax = canvas(17, 5.6)
labs = [("Round 2 · QR, one per stack", pr / "r2-qr-stack.png", 2.2,
         "Shared stack code; specimen\nnumbers picked on screen"),
        ("Round 3 · Data Matrix, 40-bit ID", pr / "r3-datamatrix.png", 2.2,
         "One code per specimen;\nscanner sent 40 binary digits"),
        ("Final · 16×16 Data Matrix + check digit", fin / "label-final.png", 6.4,
         "62 × 25.4 mm Brother QL label; code = number + Damm\ncheck digit (e.g. No. 210); 19.4 mm symbol")]
x = 0.3
for i, (t, pth, w, cap) in enumerate(labs):
    ax.text(x, 5.15, t, fontsize=14.5, weight="bold", color=TEAL if i == 2 else BLUE)
    im = Image.open(pth); h = w * im.height / im.width
    img(ax, pth, x, 4.75 - h, w, border=GRID)
    ax.text(x, 4.75 - h - 0.2, cap, fontsize=12.5, color=INK2, va="top", linespacing=1.3)
    if i < 2: arrow(ax, x + w + 1.6, 3.6, x + w + 2.1, 3.6, c=MUTED)
    x += w + 2.4 if i < 2 else 0
save(fig, "fig-07-label-evolution.png")

# ---------------- F8 architecture ----------------
fig, ax = canvas(17, 8.2)
ax.add_patch(FancyBboxPatch((5.6, 3.0), 5.8, 4.6, boxstyle="round,pad=0,rounding_size=0.12", fc="#e6f2ee", ec=TEAL, lw=2))
ax.text(8.5, 7.25, "Mac host (lab office)", ha="center", fontsize=16, weight="bold", color=TEAL)
box(ax, 5.9, 5.7, 5.2, 1.1, "FastAPI web server + React staff app\nport 8120 · HTTP only", fc="white", ec=TEAL, fs=12.5)
box(ax, 5.9, 4.4, 5.2, 1.0, "PostgreSQL 18 (private, localhost only)\nappend-only audit history", fc="white", ec=TEAL, fs=12.5)
box(ax, 5.9, 3.25, 5.2, 0.9, "Daily checkpoint backups (SHA-256 verified)", fc="white", ec=TEAL, fs=12.5)
for k, room in enumerate(["PPE room", "Shared room", "Footwear room"]):
    y = 6.2 - k * 2.0
    box(ax, 0.3, y, 3.9, 1.5, f"{room} door station\nRaspberry Pi 5 + Touch Display 2\nkiosk UI served locally on the Pi", fc="white", ec=BLUE, fs=12)
    box(ax, 0.3, y - 0.42, 3.9, 0.36, "USB 2D scanner (Eyoyo EYH2)  [HW]", fc="#f3f3f1", ec=HW, tc=INK2, fs=10.5, ls="--")
    arrow(ax, 4.25, y + 0.75, 5.55, 5.6 - k * 0.5, c=BLUE)
ax.text(4.3, 7.75, "LAN · HTTP API", fontsize=11.5, color=BLUE)
box(ax, 12.8, 5.8, 3.9, 1.3, "Staff browsers\nprep import · stacks ·\ntransactions · exports", fc="white", ec=VIOLET, fs=12)
box(ax, 12.8, 3.6, 3.9, 1.3, "Brother QL-810W printer  [HW]\n62 mm roll, 62 × 25.4 mm labels\nvia macOS print queue", fc="#f3f3f1", ec=HW, tc=INK2, fs=11.5, ls="--")
arrow(ax, 12.75, 6.45, 11.45, 6.25, c=VIOLET); arrow(ax, 11.45, 4.6, 12.75, 4.25, c=INK2)
ax.text(5.6, 2.3, "If the Mac cannot be reached, each Pi still shows its local screen marked Offline and\nrefuses scans, so no record is silently lost (no offline queue).",
        fontsize=12.5, color=INK2, linespacing=1.4)
ax.text(0.3, 0.4, "[HW] dashed gray boxes = hardware subsystem (placeholder details to be filled by the hardware team).", fontsize=11.5, color=MUTED)
save(fig, "fig-08-architecture.png")

# ---------------- F9 UI flowchart ----------------
fig, ax = canvas(22, 12.4)
steps = [
    ("1  Import prep sheet", fin / "01-staff-prep.png", "Staff app: Excel/CSV → reviewed rows"),
    ("2  Print labels", fin / "label-final.png", "One Data Matrix per specimen"),
    ("3  Scan IN at the door", fin / "02-scanner-ready-in.png", "Room + IN persist; no typing"),
    ("4  Green = committed", fin / "03-scanner-in-confirmed.png", "Shown only after DB commit"),
    ("5  Track the 24 h", fin / "09-staff-stacks.png", "Ready stacks sorted first"),
    ("6  Scan OUT (part of a stack)", fin / "05-scanner-partial-out.png", "Pick numbered pieces"),
    ("7  Early OUT is flagged", fin / "04-scanner-early-checkout.png", "< 24 h needs an override"),
    ("8  Fix mistakes, keep history", fin / "11-staff-correction.png", "Edit / Undo; original kept"),
]
w = 5.0; xs = [0.3, 5.75, 11.2, 16.65]
for i, (t, pth, cap) in enumerate(steps):
    row = i // 4; col = i % 4 if row == 0 else 3 - (i % 4)
    x = xs[col]; top = 11.7 if row == 0 else 5.75
    ax.text(x, top + 0.18, t, fontsize=15, weight="bold", color=TEAL)
    im = Image.open(pth); hh = w * im.height / im.width
    hh = min(hh, 3.6)
    ww = hh * im.width / im.height
    img(ax, pth, x + (w - ww) / 2, top - hh, ww)
    ax.text(x, top - hh - 0.18, cap, fontsize=12.5, color=INK2, va="top")
    if row == 0 and col < 3: arrow(ax, x + w + 0.05, top - 1.8, x + w + 0.42, top - 1.8, c=TEAL, lw=2.2)
    if row == 1 and col > 0: arrow(ax, x - 0.05, top - 1.8, x - 0.42, top - 1.8, c=TEAL, lw=2.2)
arrow(ax, xs[3] + 2.5, 11.7 - 4.25, xs[3] + 2.5, 5.75 + 0.6, c=TEAL, lw=2.2)
ax.text(xs[3] + 2.65, 6.85, "after ≥ 24 h", fontsize=12, color=TEAL)
ax.text(0.3, 0.75, "Failure path: if the database is unreachable the station turns red “Offline” and disables IN/OUT (screen 08-scanner-offline.png).",
        fontsize=12.5, color=CRIT)
ax.text(0.3, 0.25, "Screens captured 3 Nov 2026 from the final build (origin/main) on an isolated test database with synthetic project numbers.",
        fontsize=11, color=MUTED)
save(fig, "fig-09-ui-flowchart.png")

# ---------------- F10 Test 1 ----------------
summ = json.load(open(D / "test1-summary.json"))
allgaps = []
for f in sorted(glob.glob(str(D / "run-[0-9].csv"))):
    allgaps += [float(r["seconds_since_previous"]) for r in csv.DictReader(open(f)) if r["seconds_since_previous"]]
pooled = st.mean(allgaps)
assert abs(pooled - 1.30) < 0.005
fig, axx = plt.subplots(figsize=(12, 5.6))
runs = [s["run"] for s in summ]; means = [s["mean_seconds_per_scan"] for s in summ]; mx = [s["max_gap_seconds"] for s in summ]
xs_ = range(len(runs))
axx.bar(xs_, means, width=0.55, color=BLUE, zorder=3, label="Mean time per scan")
axx.scatter(xs_, mx, s=70, color=ORANGE, zorder=4, edgecolor="white", linewidth=2, label="Slowest gap in run")
axx.axhline(2.0, color=GOOD, lw=2, ls="--", zorder=2); axx.text(5.45, 2.08, "Target: mean < 2.0 s", ha="right", color=GOOD, fontsize=12.5)
axx.axhline(5.0, color=CRIT, lw=1.5, ls=":", zorder=2); axx.text(5.45, 5.08, "5 s = counted as manual entry", ha="right", color=CRIT, fontsize=12)
for i, m in enumerate(means): axx.text(i, m - 0.12, f"{m:.2f} s", ha="center", va="top", fontsize=12.5, color="white", weight="bold")
axx.set_xticks(list(xs_), [f"Run {r}\n(tester {'A' if r < 6 else 'B'})" for r in runs])
axx.set_ylim(0, 6.6); axx.set_ylabel("Seconds between committed scans")
axx.set_title(f"Test 1 · Scan reliability: mean {pooled:.2f} s per scan, 119/119 records match prep sheet", loc="left", fontsize=15, color=INK, pad=14)
axx.grid(axis="y", color=GRID, zorder=0); axx.spines[["top", "right"]].set_visible(False)
axx.legend(frameon=False, loc="upper left", ncol=2, fontsize=12)
fig.text(0.01, -0.03, "29 Oct 2026 · Shared-room Pi 5 + Eyoyo scanner · 20 stacks of 1–20 pieces per run (201 pieces). Run 6 missed 1 stack (handling slip) → 119/120 recorded.",
         fontsize=10.5, color=MUTED)
save(fig, "fig-10-test1-scan-time.png")

# ---------------- F11 Test 2 ----------------
sc = json.load(open(D / "test2-test3-scale-5yr.json"))
lab = ["Day 1\n3k", "Day 7\n21k", "Day 14\n42k", "Day 30\n90k", "5 years\n5.49M"]
fig, axx = plt.subplots(figsize=(12, 5.6))
xs_ = list(range(len(sc)))
for key, c, name in [("movement_ms_p50", AQUA, "Median (p50)"), ("movement_ms_p95", BLUE, "95th percentile"), ("movement_ms_max", ORANGE, "Maximum")]:
    v = [p[key] for p in sc]
    axx.plot(xs_, v, marker="o", ms=8, lw=2, color=c, label=name, zorder=3, markeredgecolor="white", markeredgewidth=1.5)
    axx.text(xs_[-1] + 0.12, v[-1], f"{v[-1]:.0f} ms" if v[-1] > 20 else f"{v[-1]:.1f} ms", va="center", fontsize=12, color=INK)
axx.axhline(1000, color=GOOD, lw=2, ls="--"); axx.text(2.0, 1150, "Target: ≤ 1,000 ms (1 s)", color=GOOD, fontsize=12.5, ha="center")
axx.set_yscale("log"); axx.set_ylim(3, 3000); axx.set_xlim(-0.3, 4.9)
axx.set_yticks([5, 10, 30, 100, 300, 1000], ["5", "10", "30", "100", "300", "1,000"])
axx.set_xticks(xs_, lab); axx.set_xlabel("Database size (simulated specimens)"); axx.set_ylabel("Scan → database commit (ms, log scale)")
axx.set_title("Test 2 · Confirmation speed stays far under 1 s as the database grows to 5 years", loc="left", fontsize=15, pad=14)
axx.grid(axis="y", color=GRID, which="major"); axx.spines[["top", "right"]].set_visible(False)
axx.legend(frameon=False, loc="upper left", bbox_to_anchor=(0.1, 0.72), fontsize=12)
fig.text(0.01, -0.07, "377–747 timed scans per size (one simulated day each, below the memo's 1,000). Excludes scanner, Wi-Fi and screen; Test 1's 1.30 s includes them.",
         fontsize=10.5, color=MUTED)
save(fig, "fig-11-test2-latency.png")

# ---------------- F12 indexing fix ----------------
before = json.load(open(D / "scale-90day-before-indexing.json"))["points"]
after = {p["day"]: p["latency_ms"] for p in json.load(open(D / "scale-90day-after-indexing.json"))}
fig, axs = plt.subplots(1, 2, figsize=(13, 5.2), sharey=True)
for axx, key, akey, title in [(axs[0], "kiosk_activity_s", "kiosk activity poll", "Kiosk activity list"),
                              (axs[1], "kiosk_inventory_s", "kiosk inventory", "Kiosk room inventory")]:
    days = [p["day"] for p in before]
    b = [p[key] for p in before]
    bx = [d for d, v in zip(days, b) if v is not None]; bv = [v for v in b if v is not None]
    a = [after[d][akey] / 1000 for d in days]
    axx.plot(bx, bv, marker="o", ms=8, lw=2, color=ORANGE, label="Before indexing")
    axx.plot(days, a, marker="o", ms=8, lw=2, color=BLUE, label="After indexing")
    axx.text(75, 30, "day 60 & 90:\n> 20 s, not measured", fontsize=11.5, color=ORANGE, ha="center")
    axx.text(days[-1], a[-1] * 1.6, f"{a[-1]:.2f} s", ha="center", fontsize=12, color=BLUE)
    axx.set_yscale("log"); axx.set_ylim(0.05, 200)
    axx.set_yticks([0.1, 1, 10, 100], ["0.1 s", "1 s", "10 s", "100 s"])
    axx.set_xlabel("Simulated day of lab operation"); axx.set_title(title, loc="left", fontsize=14)
    axx.grid(axis="y", color=GRID); axx.spines[["top", "right"]].set_visible(False)
axs[0].legend(frameon=False, loc="center left", bbox_to_anchor=(0.45, 0.45), fontsize=12)
fig.suptitle("Testing found a scaling bug: kiosk lists took 91 s by day 30 → fixed with database indexes", x=0.01, ha="left", fontsize=15, y=1.03)
fig.text(0.01, -0.04, "Cause: both queries scanned the entire history. Fix: room_id column, expression + GIN indexes, inventory read from open sessions only.",
         fontsize=10.5, color=MUTED)
save(fig, "fig-12-indexing-fix.png")

# ---------------- F13 scorecard ----------------
seeds = [json.load(open(D / f"seed-{i}.json")) for i in (1, 2, 3)]
assert sum(s["fields_checked"] for s in seeds) == 383476 and all(s["field_mismatches"] == 0 for s in seeds)
mb5 = sc[-1]["db_mb"]
score = [
    ("1  Scan reliability", "Mean < 2.0 s/scan;\n100% match prep sheet", f"{pooled:.2f} s mean (119 scans, 6 runs)\n119 / 119 records match", "PASS", GOOD),
    ("2  Confirmation speed", "p95 and max ≤ 1.0 s", f"p95 14–20 ms; max ≤ {max(p['movement_ms_max'] for p in sc):.0f} ms\nday 1 → 5-year database", "PASS*", GOOD),
    ("3  Auditability", "100% field accuracy;\n5-yr DB < 10 GB", f"0 mismatches in 383,476 fields (3 seeds)\n{mb5/1000:.1f} GB at 5 years; history edits refused", "PASS", GOOD),
    ("4  Ease of use", "UDS ≥ 4\n(≤ 1 manual / 100 scans)", "1 manual entry in 120 scans\n(stack skipped while handling) → UDS 4", "PASS", GOOD),
    ("5  Outage resilience", "Recover < 60 s after\npower + network reset", "Not yet run\n(procedure + run sheet ready)", "TO DO", WARN),
]
fig, ax = canvas(17, 7.4)
hx = [0.3, 4.1, 8.0, 14.2]
for x, h in zip(hx, ["Test", "Target", "Result (real data)", "Status"]):
    ax.text(x, 6.95, h, fontsize=15, weight="bold")
ax.plot([0.3, 16.7], [6.65] * 2, color=INK2, lw=1.5)
for i, (t, tg, res, stt, c) in enumerate(score):
    y = 6.65 - 1.2 * (i + 0.5)
    ax.text(hx[0], y, t, va="center", fontsize=14, weight="bold")
    ax.text(hx[1], y, tg, va="center", fontsize=12.5, color=INK2, linespacing=1.3)
    ax.text(hx[2], y, res, va="center", fontsize=12.5, linespacing=1.3)
    ax.add_patch(FancyBboxPatch((hx[3], y - 0.3), 1.9, 0.6, boxstyle="round,pad=0,rounding_size=0.12", fc=c, ec="none"))
    ax.text(hx[3] + 0.95, y, ("✓ " if "PASS" in stt else "… ") + stt, ha="center", va="center", fontsize=13.5, weight="bold",
            color="white" if c == GOOD else INK)
    ax.plot([0.3, 16.7], [y - 0.6] * 2, color=GRID, lw=1)
ax.text(0.3, 0.25, "* Test 2 timed 377–747 scans per database size vs. the planned 1,000.  Data: docs/TEST_RESULTS.md + docs/testing-data/ (origin/main), tests run late Oct 2026.",
        fontsize=11, color=MUTED)
save(fig, "fig-13-test-scorecard.png")
