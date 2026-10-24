"""Build the offline documentation page from the project's Markdown guides."""
import html
import os
from pathlib import Path
import re

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtGui import QTextDocument
from PySide6.QtWidgets import QApplication


ROOT = Path(__file__).resolve().parents[1]
GUIDES = (
    ("project", "Start here", "README.md", "Set up the app and choose the guide for your next step."),
    ("desktop", "Desktop app", "DESKTOP_GUIDE.md", "Connect a model, chat, download local models, and invite your team."),
    ("providers", "Model providers", "PROVIDER_ADAPTERS.md", "Configure a model provider, API keys, model IDs, and command-line agents."),
    ("network", "Hosting and networks", "NETWORK_SETUP.md", "Run a relay, connect devices on a LAN or the internet, and troubleshoot connectivity."),
    ("reference", "Development", "DOCUMENTATION.md", "Understand the architecture, run tests, build the app, and use the Connection Server."),
)


def render_guide(key, filename):
    """Convert a Markdown guide to HTML with unique anchors and bundled guide links."""
    document = QTextDocument()
    document.setMarkdown(ROOT.joinpath(filename).read_text(encoding="utf-8"))
    body = re.search(r"<body[^>]*>(.*)</body>", document.toHtml(), re.S).group(1)

    def heading(match):
        """Add a guide-prefixed slug to an HTML heading for stable internal navigation."""
        level, attributes, content = match.groups()
        text = html.unescape(re.sub(r"<[^>]+>", "", content)).lower()
        slug = re.sub(r"[^\w\s-]", "", text).strip().replace(" ", "-")
        return f'<h{level} id="{key}-{slug}"{attributes}>{content}</h{level}>'

    body = re.sub(r"<h([1-6])([^>]*)>(.*?)</h\1>", heading, body, flags=re.S)

    def link(match):
        """Rewrite Markdown guide and local fragment links to their bundled HTML anchors."""
        target = html.unescape(match.group(1))
        if target.startswith("#"):
            return f'href="#{key}-{target[1:]}"'
        for guide_key, _, source, _ in GUIDES:
            if target == source or target.startswith(source + "#"):
                suffix = "-" + target.split("#", 1)[1] if "#" in target else ""
                return f'href="#{guide_key}{suffix}"'
        return match.group(0)

    return re.sub(r'href="([^"]+)"', link, body)


def main():
    """Build the offline documentation hub from the project Markdown guides."""
    QApplication.instance() or QApplication([])
    cards = "".join(f'<a class="card" href="#{key}"><h2>{title}</h2><p>{description}</p></a>' for key, title, _, description in GUIDES)
    guides = "".join(f'<section class="guide" id="{key}"><a class="back" href="#start">Back to documentation</a>{render_guide(key, filename)}</section>'
                     for key, _, filename, _ in GUIDES)
    page = '''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Multiplayer AI documentation</title><style>
:root{color-scheme:light dark;font-family:system-ui,-apple-system,"Segoe UI",sans-serif;line-height:1.65;scroll-behavior:smooth}
body{margin:0;background:#f6f7f9;color:#20252b}main{max-width:1060px;margin:0 auto;padding:64px 28px 96px}
h1{font-size:2.5rem;line-height:1.2;letter-spacing:-.035em}h2{line-height:1.3}p{max-width:80ch}.intro{font-size:1.15rem;color:#525d68}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:16px;margin:32px 0 64px}
.card{display:block;border:1px solid #d5dce3;border-radius:12px;padding:22px;color:inherit;background:#fff;text-decoration:none}
.card:hover{border-color:#2563eb}.card h2{margin-top:0;font-size:1.2rem}.card p{color:#525d68;margin-bottom:0}
.guide{margin-top:60px;padding-top:24px;border-top:1px solid #d5dce3;scroll-margin-top:24px}
.back{display:inline-block;margin-bottom:12px}a{color:#165ac6}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#e9edf2;padding:18px;border-radius:8px}
code,pre{font-family:ui-monospace,Consolas,monospace}table{border-collapse:collapse;display:block;overflow-x:auto;max-width:100%}
td,th{border:1px solid #d5dce3;padding:10px;text-align:left}img{max-width:100%}.guide p{white-space:normal!important}
.guide span{font-family:inherit!important}.guide pre span{font-family:ui-monospace,Consolas,monospace!important}
@media(prefers-color-scheme:dark){body{background:#14171b;color:#edf0f4}.intro,.card p{color:#b6c0cc}.card{background:#1e232a;border-color:#3a434e}.guide,td,th{border-color:#3a434e}a{color:#91b7ff}pre{background:#242b34}}
@media(prefers-reduced-motion:reduce){:root{scroll-behavior:auto}}
</style></head><body><main id="start"><p>Multiplayer AI / Help</p><h1>Documentation for your next step</h1>
<p class="intro">New here? Start with the setup guide, then choose what you want to do next. All guides are included on this page and work offline. Links to external services need an internet connection.</p>
<div class="cards">''' + cards + "</div>" + guides + "</main></body></html>"
    ROOT.joinpath("docs.html").write_text(page, encoding="utf-8")
    print(f"Built docs.html from {len(GUIDES)} project guides.")


if __name__ == "__main__":
    main()
