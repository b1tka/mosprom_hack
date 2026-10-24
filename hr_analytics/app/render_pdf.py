"""PDF-версия отчёта (reportlab) из той же модели секций, что и HTML-письмо."""
from __future__ import annotations

import io
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (Image, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle)

from app.document import Report

NAVY, LIME, INK, INK2 = colors.HexColor("#0f2a43"), colors.HexColor("#a4c617"), colors.HexColor("#1b2733"), colors.HexColor("#5b6775")
TONES = {
    "good": (colors.HexColor("#e6f6ef"), colors.HexColor("#0e7a52")),
    "warn": (colors.HexColor("#fff1e0"), colors.HexColor("#a8590a")),
    "bad": (colors.HexColor("#fdeaea"), colors.HexColor("#b02a2a")),
    "neutral": (colors.HexColor("#f1f4f7"), INK),
}

_FONTS = [
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ("/usr/share/fonts/dejavu/DejaVuSans.ttf", "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf"),
    ("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf", "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"),
    ("/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf", "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf"),
    ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/arialbd.ttf"),
    ("/Library/Fonts/Arial.ttf", "/Library/Fonts/Arial Bold.ttf"),
]
_registered: tuple[str, str] | None = None


def _fonts() -> tuple[str, str]:
    global _registered
    if _registered:
        return _registered
    for reg, bold in _FONTS:
        if Path(reg).exists() and Path(bold).exists():
            pdfmetrics.registerFont(TTFont("RptFont", reg))
            pdfmetrics.registerFont(TTFont("RptFont-Bold", bold))
            pdfmetrics.registerFontFamily("RptFont", normal="RptFont", bold="RptFont-Bold")
            _registered = ("RptFont", "RptFont-Bold")
            return _registered
    raise FileNotFoundError("Нужен шрифт с кириллицей: установите fonts-dejavu-core (Linux) или Arial (Windows).")


def _p(text, size=9.5, color=INK, bold=False, leading=None, align=0) -> Paragraph:
    reg, b = _fonts()
    return Paragraph(escape(str(text)), ParagraphStyle("x", fontName=b if bold else reg, fontSize=size,
                                                       textColor=color, leading=leading or size * 1.35, alignment=align))


def render_pdf(rep: Report, generated: str) -> bytes:
    reg, bold = _fonts()
    buf = io.BytesIO()
    pw, ph = A4
    margin = 16 * mm
    width = pw - 2 * margin

    def page(canvas, doc):
        canvas.saveState()
        canvas.setFillColor(NAVY)
        canvas.rect(0, ph - 9 * mm, pw, 9 * mm, stroke=0, fill=1)
        canvas.setFillColor(LIME)
        canvas.rect(0, ph - 9 * mm, 48 * mm, 9 * mm, stroke=0, fill=1)
        canvas.setFont(bold, 8)
        canvas.setFillColor(NAVY)
        canvas.drawString(5 * mm, ph - 6 * mm, "КОРСИК · ОНБОРДИНГ")
        canvas.setFont(reg, 8)
        canvas.setFillColor(INK2)
        canvas.drawString(margin, 8 * mm, f"{rep.title} · сформировано {generated}")
        canvas.drawRightString(pw - margin, 8 * mm, f"стр. {doc.page}")
        canvas.restoreState()

    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=margin, rightMargin=margin, topMargin=17 * mm,
                            bottomMargin=16 * mm, title=rep.title, author="Корсик · Онбординг")
    story = [_p(rep.title, 20, NAVY, True), Spacer(1, 2 * mm), _p(rep.subtitle, 10, INK2), Spacer(1, 5 * mm)]

    for s in rep.sections:
        t = s["type"]
        if t == "kpis":
            cells = []
            for i in s["items"]:
                bg, fg = TONES[i.get("tone") or "neutral"]
                cells.append([_p(i["label"].upper(), 7, INK2), _p(i["value"], 20, fg, True, 24), _p(i.get("hint") or "", 7, INK2)])
            n = len(cells)
            tbl = Table([cells], colWidths=[width / n] * n)
            st = [("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 6), ("TOPPADDING", (0, 0), (-1, -1), 6),
                  ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]
            for k, i in enumerate(s["items"]):
                st += [("BACKGROUND", (k, 0), (k, 0), TONES[i.get("tone") or "neutral"][0]), ("LINEAFTER", (k, 0), (k, 0), 4, colors.white)]
            tbl.setStyle(TableStyle(st))
            story += [tbl, Spacer(1, 4 * mm)]
        elif t == "heading":
            story += [Spacer(1, 3 * mm), _p(s["text"], 13, INK, True), Spacer(1, 1 * mm),
                      Table([[""]], colWidths=[width], rowHeights=[1.2], style=[("LINEBELOW", (0, 0), (-1, -1), 1.2, LIME)]),
                      Spacer(1, 3 * mm)]
        elif t == "text":
            story += [_p(s["text"], 9.5, INK2), Spacer(1, 3 * mm)]
        elif t == "callout":
            bg, fg = TONES[s["tone"]]
            inner = [[_p(s["title"], 10.5, fg, True)]] + [[_p("• " + i, 9)] for i in s["items"]]
            tbl = Table(inner, colWidths=[width])
            tbl.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), bg), ("LINEBEFORE", (0, 0), (0, -1), 3, fg),
                                     ("LEFTPADDING", (0, 0), (-1, -1), 10), ("TOPPADDING", (0, 0), (-1, -1), 2),
                                     ("BOTTOMPADDING", (0, 0), (-1, -1), 2), ("TOPPADDING", (0, 0), (-1, 0), 7),
                                     ("BOTTOMPADDING", (0, -1), (-1, -1), 7)]))
            story += [tbl, Spacer(1, 4 * mm)]
        elif t == "chart":
            png = rep.charts[s["cid"]]
            iw, ih = ImageReader(io.BytesIO(png)).getSize()
            w = min(width, 150 * mm if s["cid"] == "mood" else width)
            parts = []
            if s.get("title"):
                parts.append(_p(s["title"], 10.5, INK, True))
            if s.get("caption"):
                parts.append(_p(s["caption"], 8, INK2))
            parts += [Spacer(1, 1.5 * mm), Image(io.BytesIO(png), width=w, height=w * ih / iw, hAlign="LEFT"), Spacer(1, 4 * mm)]
            story.append(KeepTogether(parts))
        elif t == "table":
            if s.get("title"):
                story.append(_p(s["title"], 10.5, INK, True))
            n = len(s["columns"])
            data = [[_p(c.upper(), 6.5, INK2, True) for c in s["columns"]]]
            style = [("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f1f4f7")), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                     ("LINEBELOW", (0, 0), (-1, -1), .4, colors.HexColor("#e3e7eb")), ("LEFTPADDING", (0, 0), (-1, -1), 3),
                     ("RIGHTPADDING", (0, 0), (-1, -1), 3), ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]
            for ri, row in enumerate(s["rows"], 1):
                cells = []
                for ci, c in enumerate(row):
                    if isinstance(c, dict):
                        bg, fg = TONES[c["tone"]]
                        cells.append(_p(c["text"], 7.5, fg, True))
                        style.append(("BACKGROUND", (ci, ri), (ci, ri), bg))
                    else:
                        cells.append(_p(c, 7.5, INK, ci == 0))
                data.append(cells)
            first = 0.2 * width
            rest = (width - first) / max(1, n - 1)
            tbl = Table(data, colWidths=[first] + [rest] * (n - 1), repeatRows=1)
            tbl.setStyle(TableStyle(style))
            story += [Spacer(1, 1.5 * mm), tbl, Spacer(1, 4 * mm)]
    story.append(_p("Индекс настроения: позитивное = 100, нейтральное = 60, негативное = 25, критическое = 0. "
                    "Оценки сформированы ИИ-модулем автоматически.", 7.5, INK2))
    doc.build(story, onFirstPage=page, onLaterPages=page)
    return buf.getvalue()
