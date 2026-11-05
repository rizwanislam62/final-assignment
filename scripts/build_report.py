"""Build report/BusBidder_Spain_Report.pdf from report/report.md.

    python scripts/build_report.py

A small converter for the Markdown subset used in the report: front matter,
headings (#, ##, ###), paragraphs, bullet and numbered lists, block quotes,
pipe tables, images with captions, and inline **bold**, *italic* and `code`.
"""

from __future__ import annotations

import re
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.platypus import (
    BaseDocTemplate, Frame, Image, KeepTogether, ListFlowable, ListItem, PageBreak, PageTemplate,
    Paragraph, Spacer, Table, TableStyle,
)
from reportlab.platypus.tableofcontents import TableOfContents

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "report" / "report.md"
TARGET = ROOT / "report" / "BusBidder_Spain_Report.pdf"

NAVY = colors.HexColor("#1B3A5C")
GRID = colors.HexColor("#B8C4D0")
HEADER_BG = colors.HexColor("#1B3A5C")
ZEBRA = colors.HexColor("#F2F5F8")

base = getSampleStyleSheet()
STYLES = {
    "body": ParagraphStyle("body", parent=base["BodyText"], fontName="Helvetica", fontSize=10, leading=13.6,
                           spaceAfter=6),
    "h1": ParagraphStyle("h1", parent=base["Heading1"], fontName="Helvetica-Bold", fontSize=16, leading=20,
                         textColor=NAVY, spaceBefore=14, spaceAfter=8),
    "h2": ParagraphStyle("h2", parent=base["Heading2"], fontName="Helvetica-Bold", fontSize=12.5, leading=16,
                         textColor=NAVY, spaceBefore=10, spaceAfter=5),
    "h3": ParagraphStyle("h3", parent=base["Heading3"], fontName="Helvetica-Bold", fontSize=11, leading=14,
                         spaceBefore=8, spaceAfter=4),
    "cell": ParagraphStyle("cell", fontName="Helvetica", fontSize=8, leading=10),
    "cellhead": ParagraphStyle("cellhead", fontName="Helvetica-Bold", fontSize=8, leading=10,
                               textColor=colors.white),
    "caption": ParagraphStyle("caption", fontName="Helvetica-Oblique", fontSize=8.5, leading=11,
                              alignment=TA_CENTER, textColor=colors.HexColor("#3A4A5A"), spaceAfter=10),
    "quote": ParagraphStyle("quote", fontName="Helvetica", fontSize=9.2, leading=12.5, leftIndent=14,
                            rightIndent=10, textColor=colors.HexColor("#2C3E50"), backColor=ZEBRA,
                            borderPadding=(6, 6, 6, 6), spaceBefore=4, spaceAfter=10),
    "title": ParagraphStyle("title", fontName="Helvetica-Bold", fontSize=30, leading=36, alignment=TA_CENTER,
                            textColor=NAVY),
    "subtitle": ParagraphStyle("subtitle", fontName="Helvetica", fontSize=13, leading=18, alignment=TA_CENTER),
    "meta": ParagraphStyle("meta", fontName="Helvetica", fontSize=11, leading=16, alignment=TA_CENTER),
    "toc1": ParagraphStyle("toc1", fontName="Helvetica-Bold", fontSize=9.5, leading=12, leftIndent=0,
                           spaceBefore=3),
    "toc2": ParagraphStyle("toc2", fontName="Helvetica", fontSize=9, leading=11, leftIndent=16),
}


# ------------------------------------------------------------------- inline
def inline(text: str) -> str:
    """Convert inline Markdown to reportlab mini-HTML."""
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    codes: list[str] = []

    def keep_code(match):
        codes.append(match.group(1))
        return f"\x00{len(codes) - 1}\x00"

    text = re.sub(r"`([^`]+)`", keep_code, text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<i>\1</i>", text)
    text = re.sub(r"(https?://[^\s)]+)", r'<link href="\1" color="blue">\1</link>', text)
    return re.sub(r"\x00(\d+)\x00",
                  lambda m: f'<font face="Courier" size="8.6">{codes[int(m.group(1))]}</font>', text)


# -------------------------------------------------------------------- blocks
PAD = 8.0  # left + right cell padding in points


def _text_width(text: str, bold: bool) -> tuple[float, float]:
    """(full width, widest single word) of a cell's text, in points at 8 pt."""
    plain = re.sub(r"\*\*|(?<!\w)\*|\*(?!\w)", "", text)
    parts = re.split(r"(`[^`]+`)", plain)
    full, widest = 0.0, 0.0
    for part in parts:
        if not part:
            continue
        if part.startswith("`"):
            font, chunk, size = "Courier", part.strip("`"), 8.6   # inline() draws code at 8.6 pt
        else:
            font, chunk, size = ("Helvetica-Bold" if bold else "Helvetica"), part, 8
        full += stringWidth(chunk, font, size)
        for word in chunk.split():
            widest = max(widest, stringWidth(word, font, size))
    return full, widest


def column_widths(header: list[str], body: list[list[str]], width: float) -> list[float]:
    ncols = len(header)
    natural, minimum = [0.0] * ncols, [0.0] * ncols
    for row_index, row in enumerate([header] + body):
        for c in range(ncols):
            full, widest = _text_width(row[c], bold=row_index == 0)
            natural[c] = max(natural[c], full + PAD)
            minimum[c] = max(minimum[c], widest + PAD)
    if sum(natural) <= width:  # everything fits on one line: share the spare width
        spare = width - sum(natural)
        return [n + spare * n / sum(natural) for n in natural]
    if sum(minimum) >= width:
        return [width * m / sum(minimum) for m in minimum]
    extra = [n - m for n, m in zip(natural, minimum)]
    room = width - sum(minimum)
    return [m + room * e / sum(extra) for m, e in zip(minimum, extra)]


def make_table(lines: list[str], width: float) -> Table:
    rows = [[c.strip() for c in line.strip().strip("|").split("|")] for line in lines]
    header, body = rows[0], [r for r in rows[2:]]
    ncols = len(header)
    body = [r + [""] * (ncols - len(r)) for r in body]
    col_widths = column_widths(header, body, width)
    data = [[Paragraph(inline(c), STYLES["cellhead"]) for c in header]]
    data += [[Paragraph(inline(c), STYLES["cell"]) for c in r] for r in body]
    table = Table(data, colWidths=col_widths, repeatRows=1, hAlign="LEFT")
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), HEADER_BG),
        ("GRID", (0, 0), (-1, -1), 0.4, GRID),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 2.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
        ("LEFTPADDING", (0, 0), (-1, -1), 3.5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3.5),
    ]
    for i in range(1, len(data)):
        if i % 2 == 0:
            style.append(("BACKGROUND", (0, i), (-1, i), ZEBRA))
    table.setStyle(TableStyle(style))
    return table


def make_image(alt: str, src: str, width: float, max_height: float) -> list:
    path = (SOURCE.parent / src).resolve()
    img = Image(str(path))
    scale = min(width / img.drawWidth, max_height / img.drawHeight)
    img.drawWidth *= scale
    img.drawHeight *= scale
    return [KeepTogether([img, Spacer(1, 3), Paragraph(inline(alt), STYLES["caption"])])]


def make_list(items: list[str], ordered: bool) -> ListFlowable:
    flow = [ListItem(Paragraph(inline(t), STYLES["body"]), leftIndent=14) for t in items]
    return ListFlowable(flow, bulletType="1" if ordered else "bullet", start="1" if ordered else None,
                        leftIndent=14, bulletFontSize=8 if not ordered else 9.5, bulletOffsetY=-1)


class ReportDoc(BaseDocTemplate):
    def __init__(self, filename, meta, **kw):
        super().__init__(filename, pagesize=A4, leftMargin=2.1 * cm, rightMargin=2.1 * cm,
                         topMargin=2.0 * cm, bottomMargin=2.0 * cm, **kw)
        self.meta = meta
        frame = Frame(self.leftMargin, self.bottomMargin, self.width, self.height, id="main")
        self.addPageTemplates([
            PageTemplate("title", frames=[frame]),
            PageTemplate("normal", frames=[frame], onPage=self._decorate),
        ])

    def _decorate(self, canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.HexColor("#5A6B7B"))
        canvas.drawString(self.leftMargin, A4[1] - 1.25 * cm, "ACIT4420 Problem-solving with scripting - BusBidder Spain")
        canvas.drawRightString(A4[0] - self.rightMargin, 1.2 * cm, f"Page {doc.page}")
        canvas.restoreState()

    def afterFlowable(self, flowable):
        if isinstance(flowable, Paragraph) and flowable.style.name in ("h1", "h2"):
            level = 0 if flowable.style.name == "h1" else 1
            text = flowable.getPlainText()
            key = f"h{id(flowable)}"
            self.canv.bookmarkPage(key)
            self.canv.addOutlineEntry(text, key, level=level, closed=level > 0)
            self.notify("TOCEntry", (level, text, self.page, key))


def parse(md: str):
    meta: dict[str, str] = {}
    lines = md.splitlines()
    if lines and lines[0].strip() == "---":
        end = lines.index("---", 1)
        for line in lines[1:end]:
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip()
        lines = lines[end + 1:]
    return meta, lines


def build() -> None:
    meta, lines = parse(SOURCE.read_text(encoding="utf-8"))
    doc = ReportDoc(str(TARGET), meta, title=meta.get("title", "Report"), author=meta.get("author", ""))
    width = doc.width
    story: list = []

    # title page
    story += [Spacer(1, 5 * cm), Paragraph(inline(meta.get("title", "")), STYLES["title"]), Spacer(1, 0.6 * cm),
              Paragraph(inline(meta.get("subtitle", "")), STYLES["subtitle"]), Spacer(1, 2.5 * cm),
              Paragraph(inline(meta.get("course", "")), STYLES["meta"]),
              Paragraph(inline(meta.get("author", "")), STYLES["meta"]),
              Paragraph(inline(meta.get("date", "")), STYLES["meta"]),
              Spacer(1, 3 * cm),
              Paragraph("Source code, data, tests and output examples: see README.md in the submitted project.",
                        STYLES["meta"])]
    from reportlab.platypus.doctemplate import NextPageTemplate
    story += [NextPageTemplate("normal"), PageBreak()]
    toc = TableOfContents()
    toc.levelStyles = [STYLES["toc1"], STYLES["toc2"]]
    toc.tableStyle = TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                                 ("RIGHTPADDING", (0, 0), (-1, -1), 0), ("TOPPADDING", (0, 0), (-1, -1), 1),
                                 ("BOTTOMPADDING", (0, 0), (-1, -1), 1)])
    story += [Paragraph("Contents", STYLES["h1"].clone("tochead")), toc, PageBreak()]

    i = 0
    para: list[str] = []

    def flush():
        if para:
            story.append(Paragraph(inline(" ".join(para)), STYLES["body"]))
            para.clear()

    first_h1 = True
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            flush()
            i += 1
            continue
        if stripped.startswith("#"):
            flush()
            level = len(stripped) - len(stripped.lstrip("#"))
            text = stripped[level:].strip()
            if level == 1 and not first_h1 and text in ("References",):
                pass
            first_h1 = False
            story.append(Paragraph(inline(text), STYLES[f"h{min(level, 3)}"]))
            i += 1
            continue
        if stripped.startswith("|"):
            flush()
            block = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                block.append(lines[i])
                i += 1
            story += [make_table(block, width), Spacer(1, 7)]
            continue
        image = re.match(r"^!\[(.*)\]\((.+)\)$", stripped)
        if image:
            flush()
            story += make_image(image.group(1), image.group(2), width, 15 * cm)
            i += 1
            continue
        if stripped.startswith(">"):
            flush()
            quote = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                quote.append(lines[i].strip().lstrip(">").strip())
                i += 1
            story.append(Paragraph(inline(" ".join(quote)), STYLES["quote"]))
            continue
        bullet = re.match(r"^([*-]|\d+\.)\s+(.*)$", stripped)
        if bullet and not line.startswith(" "):
            flush()
            ordered = bullet.group(1)[0].isdigit()
            items: list[str] = []
            while i < len(lines):
                cur = lines[i]
                m = re.match(r"^([*-]|\d+\.)\s+(.*)$", cur.strip())
                if m and not cur.startswith(" "):
                    items.append(m.group(2))
                elif cur.startswith(" ") and cur.strip() and items:
                    items[-1] += " " + cur.strip()
                else:
                    break
                i += 1
            story += [make_list(items, ordered), Spacer(1, 4)]
            continue
        para.append(stripped)
        i += 1
    flush()
    doc.multiBuild(story)
    print(f"Report written to {TARGET}")


if __name__ == "__main__":
    build()
