"""Inline plotly.js into dashboard.html so it works fully offline."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DASH = ROOT / "outputs" / "demo" / "dashboard.html"
PLOTLY = ROOT / "plotly_inline.js"


def main() -> None:
    html = DASH.read_text(encoding="utf-8")
    js = PLOTLY.read_text(encoding="utf-8")
    pattern = re.compile(
        r"<script[^>]*src=\"https://cdn\.plot\.ly/plotly-[0-9.]+\.min\.js\"[^>]*></script>"
    )
    match = pattern.search(html)
    if not match:
        print("CDN script tag not found")
        return
    print("found CDN tag:", match.group(0)[:120])
    html = pattern.sub(lambda m: "<script>\n" + js + "\n</script>", html, count=1)
    (ROOT / "outputs" / "demo" / "dashboard_backup_no_plotly.html").write_bytes(
        (ROOT / "outputs" / "demo" / "dashboard_backup.html").read_bytes()
        if (ROOT / "outputs" / "demo" / "dashboard_backup.html").exists()
        else b""
    )
    DASH.write_text(html, encoding="utf-8")
    print("inlined. new size:", DASH.stat().st_size)


if __name__ == "__main__":
    main()
