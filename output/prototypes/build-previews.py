"""Rebuild local review pages from the three editable conversation fragments."""
from __future__ import annotations

import importlib.util
import html
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parent
RENDERER = Path.home() / ".codex/plugins/cache/openai-bundled/visualize/1.0.46/skills/visualize/scripts/render.py"
PREVIEWS = (
    ("scanner-designs.html", "scanners.html", "Room scanner designs", "scanner", "scanner-catalogue.json", 16),
    ("correction-designs.html", "corrections.html", "Record correction designs", "correction", "correction-catalogue.json", 16),
    ("interface-landscape.html", "landscape.html", "Lab interface arrangements", "architecture", "architecture-catalogue.json", 8),
)


def main() -> None:
    if not RENDERER.is_file():
        raise SystemExit(f"Visualization preview renderer unavailable: {RENDERER}. Existing preview pages remain usable.")
    spec = importlib.util.spec_from_file_location("prototype_renderer", RENDERER)
    assert spec and spec.loader
    renderer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(renderer)
    concepts = []
    scanner_source = (ROOT / "scanner-designs.html").read_text()
    button_labels = {}
    for test_id, content in re.findall(r'<button\b[^>]*data-testid="([^"]+)"[^>]*>(.*?)</button>', scanner_source, re.S):
        button_labels[test_id] = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", content)).split())

    def scanner_step(action: str) -> str:
        if action in button_labels:
            return button_labels[action]
        if "-specimen-" in action:
            return f'Select specimen {action.rsplit("-", 1)[-1]}'
        if action.endswith("-ack"):
            return "Acknowledge the early-exit warning and record OUT"
        return action.replace("-", " ").capitalize()
    for source, target, title, family, catalogue, count in PREVIEWS:
        items = json.loads((ROOT / catalogue).read_text())
        if len(items) != count or len({item["id"] for item in items}) != count:
            raise ValueError(f"Expected {count} unique concepts in {catalogue}")
        for item in items:
            entry = {**item, "family": family, "key": f'{family}:{item["id"]}'}
            if family == "scanner":
                entry["trial_actions"] = [scanner_step(action) for action in item.get("trial_actions", [])]
            concepts.append(entry)
        renderer.export_html(ROOT / source, ROOT / "previews" / target, title=title, force=True)

    combined = (ROOT / "workflow-explorer-template.html").read_text()
    for token, source in (("SCANNER_FRAGMENT", "scanner-designs.html"), ("CORRECTION_FRAGMENT", "correction-designs.html"), ("LANDSCAPE_FRAGMENT", "interface-landscape.html")):
        combined = combined.replace(f"<!-- {token} -->", (ROOT / source).read_text())
    (ROOT / "workflow-explorer.html").write_text(combined)
    renderer.export_html(ROOT / "workflow-explorer.html", ROOT / "previews" / "explorer.html", title="Fabric workflow explorer", force=True)

    page = ROOT / "index.html"
    markup = page.read_text()
    payload = json.dumps(concepts, ensure_ascii=False).replace("</", "<\\/")
    markup, replacements = re.subn(
        r'(<script id="concept-data" type="application/json">).*?(</script>)',
        lambda match: match[1] + payload + match[2],
        markup,
        count=1,
        flags=re.S,
    )
    if replacements != 1:
        raise ValueError("Gallery catalogue placeholder missing")
    page.write_text(markup)

    guide = ROOT / "DESIGN-GUIDE.md"
    text = guide.read_text().split("<!-- CATALOGUE -->")[0].rstrip()
    text += "\n\n<!-- CATALOGUE -->\n\n## Complete concept catalogue\n"
    for family, heading in (("scanner", "Scanner designs"), ("correction", "Correction designs"), ("architecture", "Tool arrangements")):
        text += f"\n### {heading}\n"
        for item in concepts:
            if item["family"] != family:
                continue
            device = item.get("device") or item.get("devices") or "Multiple devices"
            if isinstance(device, list):
                device = ", ".join(device)
            text += f'\n#### {item["name"]}\n\n'
            text += f'- **Device:** {device}\n'
            for field, label in (("functional_difference", "Functionality"), ("benefit", "Benefit"), ("risk", "Risk"), ("backend_change", "Implementation")):
                text += f'- **{label}:** {item.get(field, "See interactive example.")}\n'
            actions = item.get("trial_actions", [])
            if isinstance(actions, str):
                actions = [actions]
            text += f'- **Try:** {" → ".join(actions)}\n'
    guide.write_text(text)
    print(f"Built {len(concepts)} concepts, three review pages, and the combined explorer.")


if __name__ == "__main__":
    main()
