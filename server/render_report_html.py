"""Render a standalone report HTML file from server/ui/report.html.

Usage: python server/render_report_html.py report.json out.html [--template PATH]
"""
import argparse
import json
import os

DEFAULT_TEMPLATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui", "report.html")


def safe_json(data):
    """JSON safe to embed inside an HTML <script> element."""
    text = json.dumps(data, ensure_ascii=False)
    return (
        text.replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def render(report, template_html):
    marker = "<script"
    idx = template_html.find(marker)
    if idx < 0:
        raise ValueError("template has no <script> tag")
    inject = "<script>window.openai={toolOutput:" + safe_json(report) + "};</script>"
    return template_html[:idx] + inject + template_html[idx:]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("report_json")
    p.add_argument("out_html")
    p.add_argument("--template", default=os.environ.get("WORKOUT_REPORT_TEMPLATE", DEFAULT_TEMPLATE))
    a = p.parse_args()
    with open(a.report_json, encoding="utf-8") as f:
        report = json.load(f)
    with open(a.template, encoding="utf-8") as f:
        template = f.read()
    with open(a.out_html, "w", encoding="utf-8") as f:
        f.write(render(report, template))


if __name__ == "__main__":
    main()
