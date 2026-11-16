"""Build docs/index.html: injects the sample emails and CSV into the template."""
import json
from pathlib import Path

root = Path(__file__).resolve().parent.parent
samples = {
    "broken": (root / "examples/broken_email.html").read_text(),
    "clean": (root / "examples/clean_email.html").read_text(),
    "csv": (root / "examples/campaign_results.csv").read_text(),
}
data = json.dumps(samples).replace("</", "<\\/")
html = (root / "web/template.html").read_text().replace("__SAMPLES__", data)
(root / "docs").mkdir(exist_ok=True)
(root / "docs/index.html").write_text(html)
print("built docs/index.html", len(html), "bytes")
