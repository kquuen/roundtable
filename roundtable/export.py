"""Report export: Markdown → PDF via fpdf.

Lightweight — no pandoc/weasyprint dependencies needed.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Optional

from fpdf import FPDF

from roundtable.report import compose_report
from roundtable.models import AgentReview, SupervisorReview

logger = logging.getLogger("roundtable.export")


class _PDF(FPDF):
    """Custom PDF renderer with UTF-8 CJK support."""

    def __init__(self):
        super().__init__()
        self._header_date = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        self._cjk_font_path = self._resolve_cjk_font()
        self._cjk_loaded = False
        if self._cjk_font_path:
            try:
                self.add_font("CJK", "", self._cjk_font_path)
                self.add_font("CJK", "B", self._cjk_font_path)
                self._cjk_loaded = True
            except Exception as exc:
                logger.warning("Failed to load CJK font %s: %s", self._cjk_font_path, exc)
        self.add_page()
        self.set_auto_page_break(auto=True, margin=15)

    def _resolve_cjk_font(self) -> str:
        """Find a system font that supports CJK characters."""
        candidates = [
            "simhei.ttf",
            "SimHei.ttf",
            "msyh.ttc",
            "MSYH.ttc",
            "NotoSansSC-VF.ttf",
            "NotoSansCJKsc-Regular.otf",
            "wqy-zenhei.ttc",
        ]
        dirs = [
            Path("C:/Windows/Fonts"),
            Path("/usr/share/fonts"),
            Path("/usr/local/share/fonts"),
            Path.home() / ".fonts",
        ]
        for d in dirs:
            if not d.exists():
                continue
            for cand in candidates:
                p = d / cand
                if p.exists():
                    return str(p)
        return ""

    def header(self):
        if self._cjk_loaded:
            self.set_font("CJK", "", 8)
        else:
            self.set_font("Helvetica", "", 8)
        self.set_text_color(128, 128, 128)
        self.cell(0, 10, "Roundtable", align="L", new_x="RIGHT", new_y="TOP")
        self.cell(0, 10, self._header_date, align="R", new_x="LMARGIN", new_y="NEXT")
        self.ln(2)

    def footer(self):
        self.set_y(-15)
        if self._cjk_loaded:
            self.set_font("CJK", "", 8)
            page_text = f"第 {self.page_no()} 页"
        else:
            self.set_font("Helvetica", "", 8)
            page_text = f"Page {self.page_no()}"
        self.set_text_color(128, 128, 128)
        self.cell(0, 10, page_text, align="C")

    def set_cjk_font(self, size: int = 12, style: str = ""):
        if self._cjk_loaded:
            self.set_font("CJK", style, size)
        else:
            self.set_font("Helvetica", style, size)


def _strip_markdown(text: str) -> str:
    """Remove markdown syntax for plain-text PDF rendering."""
    # Remove bold/italic markers
    text = re.sub(r"\*\*", "", text)
    text = re.sub(r"\*", "", text)
    text = re.sub(r"__", "", text)
    text = re.sub(r"_", "", text)
    # Remove inline code backticks
    text = re.sub(r"`([^`]+)`", r"\1", text)
    # Remove links
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    # Remove horizontal rules
    text = re.sub(r"^---+$", "", text, flags=re.MULTILINE)
    # Remove heading markers
    text = re.sub(r"^#+\s*", "", text, flags=re.MULTILINE)
    # Remove blockquote markers
    text = re.sub(r"^>\s*", "", text, flags=re.MULTILINE)
    # Remove table delimiters
    text = re.sub(r"\|[-:\s|]+\|", "", text)
    text = text.replace("|", "  ")
    return text.strip()


def _parse_markdown_sections(md_text: str) -> list[tuple[str, str, list[str]]]:
    """Parse markdown into (level, title, lines) sections."""
    sections: list[tuple[str, str, list[str]]] = []
    current_lines: list[str] = []
    current_title = ""
    current_level = ""

    for line in md_text.splitlines():
        h2 = re.match(r"^##\s+(.*)", line)
        h3 = re.match(r"^###\s+(.*)", line)
        h1 = re.match(r"^#\s+(.*)", line)
        if h1:
            if current_title or current_lines:
                sections.append((current_level, current_title, current_lines))
            current_level = "h1"
            current_title = h1.group(1).strip()
            current_lines = []
        elif h2:
            if current_title or current_lines:
                sections.append((current_level, current_title, current_lines))
            current_level = "h2"
            current_title = h2.group(1).strip()
            current_lines = []
        elif h3:
            if current_title or current_lines:
                sections.append((current_level, current_title, current_lines))
            current_level = "h3"
            current_title = h3.group(1).strip()
            current_lines = []
        else:
            current_lines.append(line)

    if current_title or current_lines:
        sections.append((current_level, current_title, current_lines))
    return sections


def generate_pdf_from_markdown(md_text: str, title: str = "Roundtable Report") -> bytes:
    """Render markdown report as a PDF byte string."""
    pdf = _PDF()
    pdf._header_date = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    sections = _parse_markdown_sections(md_text)

    for level, sec_title, lines in sections:
        if level == "h1":
            pdf.set_cjk_font(18, "B")
            pdf.set_text_color(33, 37, 41)
            pdf.cell(0, 12, sec_title, new_x="LMARGIN", new_y="NEXT")
            pdf.ln(2)
        elif level == "h2":
            pdf.set_cjk_font(14, "B")
            pdf.set_text_color(44, 62, 80)
            pdf.cell(0, 10, sec_title, new_x="LMARGIN", new_y="NEXT")
            pdf.ln(1)
        elif level == "h3":
            pdf.set_cjk_font(12, "B")
            pdf.set_text_color(52, 73, 94)
            pdf.cell(0, 8, sec_title, new_x="LMARGIN", new_y="NEXT")
        else:
            pdf.set_cjk_font(11)
            pdf.set_text_color(33, 37, 41)

        for line in lines:
            stripped = line.strip()
            if not stripped:
                pdf.ln(2)
                continue
            # Bullet points
            if stripped.startswith("-") or stripped.startswith("*"):
                bullet_text = _strip_markdown(stripped[1:].strip())
                pdf.set_x(20)
                pdf.multi_cell(0, 6, f"• {bullet_text}")
            else:
                plain = _strip_markdown(stripped)
                pdf.multi_cell(0, 6, plain)
        pdf.ln(2)

    return pdf.output()


def generate_pdf_report(
    agent_reviews: list[AgentReview],
    supervisor_reviews: list[SupervisorReview],
    session_title: str = "",
    lang: str = "zh",
) -> bytes:
    """High-level helper: compose markdown report then convert to PDF."""
    md = compose_report(agent_reviews, supervisor_reviews, session_title, lang)
    return generate_pdf_from_markdown(md, title=session_title or "Roundtable Report")
