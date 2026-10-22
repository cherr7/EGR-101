"""Build the focused frontend demos; create integer-ID Data Matrix label assets."""
from pathlib import Path
import importlib.util
import json
import sys
from PIL import Image
import zxingcpp

ROOT=Path(__file__).resolve().parent
RENDERER=Path.home()/".codex/plugins/cache/openai-bundled/visualize/1.0.55/skills/visualize/scripts/render.py"
spec=importlib.util.spec_from_file_location("focused_renderer",RENDERER)
renderer=importlib.util.module_from_spec(spec)
sys.modules[spec.name]=renderer
spec.loader.exec_module(renderer)
model=(ROOT/"demo-api.js").read_text()
(ROOT/"scanner-implementation-guide.md").write_text((ROOT.parents[2]/"docs/SCANNER_IMPLEMENTATION_GUIDE.md").read_text())
capture=(ROOT/"scanner-capture.js").read_text()
for name,title in (("scanner","Fabric · specimen scanner"),("log","Fabric · popup transaction editor"),("stacks","Fabric · stack readiness")):
    source=(ROOT/f"{name}-template.html").read_text().replace("/* DEMO_API */",model).replace("/* SCANNER_CAPTURE */",capture)
    (ROOT/f"{name}.html").write_text(source)
    renderer.export_html(ROOT/f"{name}.html",ROOT/"previews"/f"{name}.html",title=title,force=True)

station_head='<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>DEMO · integer specimen scanner</title><style>:root{color-scheme:light dark}html,body{margin:0;background:light-dark(#f3f6fa,#16212e)}#fabric-focused-scanner>.viz-controls{display:none}#fabric-focused-scanner .viz-dotted-background .focused-station{border:0;border-radius:0}#fabric-focused-scanner{max-width:800px;margin:0 auto}.cursor-interaction{cursor:pointer}</style></head><body>'
(ROOT/"station.html").write_text(station_head+'<script src="station-state.js"></script>'+(ROOT/"scanner.html").read_text()+'</body></html>')

fixtures=[]
for stack,base,count,project,material,test,treatment in (
    ("A",100000000100,10,"00073-LAB","Cotton twill","D5034","AR"),
    ("B",1099511627755,20,"AB-004","Nylon weave","D2261","3L"),
    ("C",271000000000,6,"00702","Polyester mesh","D5034","1L"),
):
    for number in range(1,count+1):
        specimen_id=base+number
        payload=format(specimen_id,"040b")
        matrix=memoryview(zxingcpp.write_barcode(zxingcpp.BarcodeFormat.DataMatrix,payload,quiet_zone=1))
        image=Image.frombytes("L",(matrix.shape[1],matrix.shape[0]),matrix.tobytes()).resize((192,192),Image.Resampling.NEAREST)
        (ROOT/"datamatrix").mkdir(exist_ok=True)
        image.save(ROOT/"datamatrix"/f"{specimen_id}.png")
        decoded=zxingcpp.read_barcode(image)
        assert decoded and decoded.text==payload and int(decoded.text,2)==specimen_id
        fixtures.append(dict(ID=specimen_id,encoded_payload=payload,encoding="binary-text",stack=stack,number=number,project=project,material=material,test=test,treatment=treatment,symbol_modules=int(matrix.shape[0]-2)))
(ROOT/"label-fixtures.json").write_text(json.dumps(fixtures,indent=2))
page=(ROOT/"labels-template.html").read_text().replace("/* LABEL_FIXTURES */",json.dumps(fixtures))
(ROOT/"labels.html").write_text(page)
print("Built three frontend previews, the 800 × 480 station, and 36 decoded integer-ID Data Matrix labels.")
