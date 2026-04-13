#!/usr/bin/env python3
"""CI step: 把 DASHBOARD_TOKEN 注入到 docs/index.html"""
import os, pathlib

token = os.environ.get("DASHBOARD_TOKEN", "")
html_path = pathlib.Path("docs/index.html")
html = html_path.read_text()

placeholder = "<!-- GITHUB_TOKEN_PLACEHOLDER -->"
replacement = f'const GITHUB_TOKEN = "{token}";'

if placeholder in html:
    html = html.replace(placeholder, replacement, 1)
    html_path.write_text(html)
    print("Token injected via placeholder OK")
else:
    print("WARNING: placeholder not found")
