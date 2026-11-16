"""Optional offline PDF -> PNG page import for a manuscript workspace."""
import argparse
import json
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("pdf", type=Path)
parser.add_argument("workspace", type=Path)
parser.add_argument("directory", help="new relative page directory inside the workspace")
args = parser.parse_args()
try:
    import fitz
except ImportError:
    parser.exit(2, "Rendering requires PyMuPDF: python -m pip install pymupdf\n")
root = args.workspace.resolve()
out = (root / args.directory).resolve()
if not out.is_relative_to(root) or out == root:
    parser.error("Choose a new directory under the workspace")
pages = []
with fitz.open(args.pdf) as pdf:
    out.mkdir(parents=True, exist_ok=False)
    for number, page in enumerate(pdf, 1):
        dest = out / f"page-{number}.png"
        page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False).save(str(dest))
        pages.append({"file": dest.relative_to(root).as_posix()})
print(json.dumps(pages, indent=2))
