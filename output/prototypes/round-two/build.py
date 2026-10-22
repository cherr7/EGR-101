"""Build isolated round-two previews and physical-scanner demo assets."""
from pathlib import Path
import importlib.util
import json
import sys

ROOT = Path(__file__).resolve().parent
RENDERER = Path.home() / ".codex/plugins/cache/openai-bundled/visualize/1.0.46/skills/visualize/scripts/render.py"

spec = importlib.util.spec_from_file_location("round_two_renderer", RENDERER)
renderer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(renderer)
for source, target, title in (
    ("scanner-layouts.html", "scanners.html", "Fabric · fixed scanner layouts"),
    ("transaction-log-layouts.html", "logs.html", "Fabric · transaction log layouts"),
):
    renderer.export_html(ROOT / source, ROOT / "previews" / target, title=title, force=True)

station = '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>DEMO · Fabric USB scanner station</title><style>:root{color-scheme:light dark}html,body{margin:0;background:light-dark(#f5f7f9,#151e28)}#fabric-scanner-round-two>.viz-controls{display:none}#fabric-scanner-round-two .viz-carousel .r2-device{border-radius:0;border:0}#fabric-scanner-round-two{max-width:800px;margin:0 auto}.viz-carousel>section[hidden]{display:none}.cursor-interaction{cursor:pointer}</style></head><body>'
station += (ROOT / "scanner-layouts.html").read_text()
station += '<script>document.querySelector(".r2-input input").focus({preventScroll:true});</script></body></html>'
(ROOT / "station.html").write_text(station)

try:
    import qrcode
except ImportError:
    print("Run with the project's .venv/bin/python to also generate demo QR images.")
    sys.exit(0)
(ROOT / "qr").mkdir(exist_ok=True)
for index, (stack, count) in enumerate((("A", 10), ("B", 20), ("C", 6)), start=1):
    codes = [(f"{stack}-stack", f"UL1:S:00000000-0000-4000-8000-00000000000{index}")]
    codes += [(f"{stack}-{number:02}", f"UL1:P:00000000-0000-4000-8000-{stack.lower()}00000000{number:03}") for number in range(1, count + 1)]
    for name, code in codes:
        qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=8, border=4)
        qr.add_data(code)
        qr.make(fit=True)
        qr.make_image(fill_color="black", back_color="white").save(ROOT / "qr" / f"{name}.png")
print("Built two independent previews, the 800 × 480 station, and 39 demo QR images.")
