"""Универсальная модель документа: отчёт = список секций.

Из неё рендерятся и HTML-письмо (render_html.py), и PDF (render_pdf.py), и текстовая версия письма —
поэтому цифры в трёх форматах гарантированно совпадают.

Секции (dict):
  kpis     {items:[{label, value, tone?, hint?}]}
  heading  {text}
  text     {text}
  chart    {cid, title?, caption?}           # PNG лежит в Report.charts[cid]
  table    {title?, columns:[str], rows:[[cell]], widths?:[int]}   # cell: str | {text, tone}
  callout  {tone, title, items:[str]}
tone: good | warn | bad | neutral
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Report:
    kind: str                       # hr | director | hr_head
    title: str
    subtitle: str
    subject: str
    preheader: str
    filename: str                   # имя PDF без расширения
    sections: list[dict] = field(default_factory=list)
    charts: dict[str, bytes] = field(default_factory=dict)
    recipient_name: str = ""

    def add_chart(self, cid: str, png, title: str = "", caption: str = "") -> None:
        if png:
            self.charts[cid] = png
            self.sections.append({"type": "chart", "cid": cid, "title": title, "caption": caption})


def cell_text(c) -> str:
    return c["text"] if isinstance(c, dict) else str(c)


def to_plain_text(rep: Report) -> str:
    lines = [rep.title, rep.subtitle, ""]
    for s in rep.sections:
        t = s["type"]
        if t == "kpis":
            lines += [f"• {i['label']}: {i['value']}" for i in s["items"]] + [""]
        elif t == "heading":
            lines += ["", s["text"].upper()]
        elif t == "text":
            lines += [s["text"], ""]
        elif t == "chart":
            if s.get("title"):
                lines += [f"[график] {s['title']}"]
        elif t == "table":
            if s.get("title"):
                lines.append(s["title"])
            for r in s["rows"]:
                lines.append(" | ".join(cell_text(c) for c in r))
            lines.append("")
        elif t == "callout":
            lines += [s["title"]] + [f"  – {i}" for i in s["items"]] + [""]
    lines.append("Полная версия с графиками — в HTML-письме и во вложенном PDF.")
    return "\n".join(lines)
