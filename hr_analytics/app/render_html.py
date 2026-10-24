from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.document import Report

TONES = {
    "good": {"bg": "#e6f6ef", "fg": "#0e7a52", "bar": "#1baf7a"},
    "warn": {"bg": "#fff1e0", "fg": "#a8590a", "bar": "#eb8a34"},
    "bad": {"bg": "#fdeaea", "fg": "#b02a2a", "bar": "#d23c3c"},
    "neutral": {"bg": "#f1f4f7", "fg": "#1b2733", "bar": "#aab3bd"},
}

_env = Environment(loader=FileSystemLoader(str(Path(__file__).parent / "templates")),
                   autoescape=select_autoescape(["html", "j2"]))


def render_html(rep: Report, generated: str) -> str:
    return _env.get_template("email.html.j2").render(rep=rep, tones=TONES, generated=generated)
