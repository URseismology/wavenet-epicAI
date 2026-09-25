#!/usr/bin/env python
"""Markdown -> standalone HTML (print stylesheet) -> PDF via headless Chrome. Usage: md_to_pdf.py in.md out.pdf [title]
Images are referenced relative to the .md file's directory."""
import subprocess, sys, tempfile
from pathlib import Path
import markdown
src, out = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve(); title = sys.argv[3] if len(sys.argv) > 3 else src.stem
CSS = """
@page { size: Letter; margin: 16mm 14mm 16mm 14mm; }
body { font-family: -apple-system, 'Helvetica Neue', Arial, sans-serif; font-size: 10.2pt; line-height: 1.42; color: #1a1a1a; }
h1 { font-size: 19pt; margin: 0 0 4pt 0; border-bottom: 2px solid #333; padding-bottom: 4pt; }
h2 { font-size: 14pt; margin: 16pt 0 5pt 0; border-bottom: 1px solid #999; padding-bottom: 2pt; page-break-after: avoid; }
h3 { font-size: 11.5pt; margin: 12pt 0 4pt 0; page-break-after: avoid; }
table { border-collapse: collapse; width: 100%; margin: 6pt 0 8pt 0; font-size: 8.6pt; page-break-inside: avoid; }
th, td { border: 1px solid #aaa; padding: 2.5pt 4pt; vertical-align: top; }
th { background: #eee; }
code { font-family: Menlo, Consolas, monospace; font-size: 8.6pt; background: #f3f3f3; padding: 0 2pt; }
pre { background: #f3f3f3; padding: 6pt; font-size: 8pt; overflow-wrap: anywhere; white-space: pre-wrap; page-break-inside: avoid; }
img { max-width: 100%; display: block; margin: 6pt auto 2pt auto; page-break-inside: avoid; }
em { color: #333; }
p { margin: 4pt 0 6pt 0; orphans: 3; widows: 3; }
blockquote { border-left: 3px solid #bbb; margin: 6pt 0; padding: 2pt 8pt; color: #333; background: #fafafa; }
"""
html_body = markdown.markdown(src.read_text(), extensions=["tables", "fenced_code", "sane_lists", "toc"])
html = f"<!doctype html><html><head><meta charset='utf-8'><title>{title}</title><style>{CSS}</style></head><body>{html_body}</body></html>"
tmp = src.parent / f".{src.stem}_print.html"; tmp.write_text(html)
chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
r = subprocess.run([chrome, "--headless=new", "--disable-gpu", "--no-pdf-header-footer", "--allow-file-access-from-files",
                    f"--print-to-pdf={out}", f"file://{tmp}"], capture_output=True, text=True, timeout=180)
tmp.unlink(missing_ok=True)
print("wrote", out, out.stat().st_size // 1024, "KB" if out.exists() else "FAILED", r.stderr[-300:] if not out.exists() else "")
