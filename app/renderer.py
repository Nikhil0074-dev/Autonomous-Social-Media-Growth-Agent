"""Local graphic renderer using Pillow. No network, no browser needed."""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

from .schemas import TEMPLATE_NAMES

HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
MIN_SIDE, MAX_SIDE = 200, 4000


class RenderError(Exception):
    pass


class TextFitError(RenderError):
    def __init__(self, field_name: str, message: str):
        super().__init__(message)
        self.field_name = field_name


FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "C:\\Windows\\Fonts\\segoeuib.ttf",
    "C:\\Windows\\Fonts\\arialbd.ttf",
    "C:\\Windows\\Fonts\\calibrib.ttf",
]


@dataclass
class RenderSpec:
    template: str = "technical_explainer"
    width: int = 1200
    height: int = 630
    brand_name: str = "My Brand"
    primary_color: str = "#101827"
    accent_color: str = "#38BDF8"
    text_color: str = "#FFFFFF"
    category: str = ""
    headline: str = ""
    subtitle: str = ""
    footer: str = ""
    format: str = "png"
    font_path: str = ""
    logo_path: str = ""
    warnings: list = field(default_factory=list)


def find_font(custom: str = "") -> Optional[str]:
    for p in ([custom] if custom else []) + FONT_CANDIDATES:
        if p and os.path.isfile(p):
            return p
    return None


@lru_cache(maxsize=128)
def _load_font(path: Optional[str], size: int):
    if path:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            pass
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # very old Pillow: fixed-size font
        return ImageFont.load_default()


def _rgb(hex_color: str) -> tuple:
    h = hex_color.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _blend(a: tuple, b: tuple, t: float) -> tuple:
    return tuple(int(a[i] * (1 - t) + b[i] * t) for i in range(3))


def _lum(rgb: tuple) -> float:
    c = [x / 255 for x in rgb]
    c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def contrast_ratio(a: tuple, b: tuple) -> float:
    hi, lo = sorted((_lum(a), _lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def clean_text(text: str, warnings: list) -> str:
    """Remove characters that common fonts cannot draw (emoji, control chars)."""
    out = "".join(ch for ch in (text or "") if ch in "\n" or (ord(ch) >= 32 and ord(ch) <= 0xFFFF))
    out = re.sub(r"[\u200b-\u200f\u2028-\u202f\ufe0f]", "", out)
    out = re.sub(r"\s+", " ", out).strip()
    if out != re.sub(r"\s+", " ", (text or "")).strip():
        warnings.append("Some characters (such as emoji) were removed because the font cannot draw them.")
    return out


def _wrap(draw, text: str, font, max_w: int) -> Optional[list]:
    lines, cur = [], ""
    for word in text.split():
        if draw.textlength(word, font=font) > max_w:
            return None
        trial = word if not cur else cur + " " + word
        if draw.textlength(trial, font=font) <= max_w:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def _fit(draw, text, font_path, max_w, max_h, max_size, min_size, max_lines, field_name):
    for size in range(int(max_size), int(min_size) - 1, -2):
        font = _load_font(font_path, size)
        lines = _wrap(draw, text, font, max_w)
        if lines is None:
            continue
        if len(lines) <= max_lines and len(lines) * int(size * 1.22) <= max_h:
            return font, lines, size
    raise TextFitError(field_name, f"The {field_name} is too long for this template. Please shorten it.")


def validate_spec(spec: RenderSpec) -> None:
    if spec.template not in TEMPLATE_NAMES:
        raise RenderError(f"Unknown template '{spec.template}'")
    if not (MIN_SIDE <= spec.width <= MAX_SIDE and MIN_SIDE <= spec.height <= MAX_SIDE):
        raise RenderError(f"Image size must be between {MIN_SIDE} and {MAX_SIDE} pixels per side")
    if spec.format not in ("png", "jpeg"):
        raise RenderError("Format must be 'png' or 'jpeg'")
    for name in ("primary_color", "accent_color", "text_color"):
        if not HEX_RE.match(getattr(spec, name) or ""):
            raise RenderError(f"{name} must look like #RRGGBB")
    if not (spec.headline or "").strip():
        raise RenderError("The headline is empty")


def _theme(spec: RenderSpec) -> dict:
    primary, accent, text = _rgb(spec.primary_color), _rgb(spec.accent_color), _rgb(spec.text_color)
    if spec.template == "professional_insight":
        return {"bg": (248, 250, 252), "fg": primary, "accent": accent, "align": "left"}
    if spec.template == "quote_card":
        return {"bg": accent, "fg": primary, "accent": primary, "align": "center"}
    return {"bg": primary, "fg": text, "accent": accent, "align": "left"}


def render_og(spec: RenderSpec, out_path: Path, preview_path: Optional[Path] = None) -> dict:
    validate_spec(spec)
    warnings = list(spec.warnings)
    font_path = find_font(spec.font_path)
    if font_path is None:
        warnings.append("No TrueType font found; using a basic fallback font. Set SGA_FONT_PATH for better output.")
    w, h = spec.width, spec.height
    th = _theme(spec)
    bg, fg, accent = th["bg"], th["fg"], th["accent"]
    center = th["align"] == "center"
    img = Image.new("RGB", (w, h), bg)
    d = ImageDraw.Draw(img)
    margin = int(w * 0.07)
    inner_w = w - 2 * margin - (0 if center else int(w * 0.02))
    muted = _blend(fg, bg, 0.35)

    ratio = contrast_ratio(fg, bg)
    if ratio < 4.5:
        warnings.append(f"Low text contrast ({ratio:.1f}:1). Aim for at least 4.5:1.")

    # decoration
    if spec.template == "technical_explainer":
        for gx in range(int(w * 0.62), w - margin // 2, int(w * 0.04)):
            for gy in range(margin, h - margin, int(h * 0.08)):
                d.ellipse((gx - 2, gy - 2, gx + 2, gy + 2), fill=_blend(accent, bg, 0.7))
        cx, cy = int(w * 0.88), int(h * 0.3)
        for r in (int(h * 0.12), int(h * 0.2), int(h * 0.3)):
            d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=_blend(accent, bg, 0.55), width=3)
        d.rectangle((0, 0, int(w * 0.012), h), fill=accent)
    elif spec.template == "professional_insight":
        d.rectangle((0, 0, w, int(h * 0.025)), fill=accent)
        d.line((margin, h - int(h * 0.17), w - margin, h - int(h * 0.17)), fill=_blend(fg, bg, 0.8), width=2)
    else:
        d.ellipse((-int(h * 0.3), -int(h * 0.3), int(h * 0.3), int(h * 0.3)), outline=_blend(fg, bg, 0.7), width=4)
        d.ellipse((w - int(h * 0.3), h - int(h * 0.3), w + int(h * 0.3), h + int(h * 0.3)), outline=_blend(fg, bg, 0.7), width=4)

    def draw_lines(lines, font, size, y, color):
        step = int(size * 1.22)
        for ln in lines:
            x = (w - d.textlength(ln, font=font)) / 2 if center else margin
            d.text((x, y), ln, font=font, fill=color)
            y += step
        return y

    # category
    category = clean_text(spec.category, warnings).upper()
    y = margin
    if category:
        f, lines, size = _fit(d, category, font_path, inner_w, h * 0.08, h * 0.04, h * 0.025, 1, "category")
        y = draw_lines(lines, f, size, y, accent if spec.template != "quote_card" else fg)
    # footer (measured first so the body never overlaps it)
    footer_size_max = int(h * 0.04)
    footer_y = h - margin - footer_size_max
    brand = clean_text(spec.brand_name, warnings)
    footer_text = clean_text(spec.footer, warnings)
    if brand:
        f, lines, size = _fit(d, brand, font_path, int(inner_w * 0.55), h * 0.08, footer_size_max, int(h * 0.025), 1, "brand name")
        d.text(((w - d.textlength(lines[0], font=f)) / 2 if center and not footer_text else margin, footer_y),
               lines[0], font=f, fill=fg)
    if footer_text:
        f, lines, size = _fit(d, footer_text, font_path, int(inner_w * 0.4), h * 0.08, footer_size_max, int(h * 0.025), 1, "footer")
        d.text((w - margin - d.textlength(lines[0], font=f), footer_y), lines[0], font=f, fill=muted)

    # headline + subtitle
    headline = clean_text(spec.headline, warnings)
    if not headline:
        raise RenderError("The headline has no drawable characters")
    body_top = max(y + int(h * 0.04), int(h * 0.2))
    body_bottom = footer_y - int(h * 0.05)
    subtitle = clean_text(spec.subtitle, warnings)
    head_h = (body_bottom - body_top) * (0.62 if subtitle else 1.0)
    hf, hlines, hsize = _fit(d, headline, font_path, inner_w, head_h, h * 0.13, h * 0.055, 4, "headline")
    sub_fit = None
    if subtitle:
        sub_h_room = body_bottom - body_top - len(hlines) * int(hsize * 1.22) - int(h * 0.03)
        if sub_h_room < h * 0.05:
            raise TextFitError("subtitle", "There is no room for the subtitle. Shorten the headline or subtitle.")
        sub_fit = _fit(d, subtitle, font_path, inner_w, sub_h_room, h * 0.05, h * 0.03, 3, "subtitle")
    block_h = len(hlines) * int(hsize * 1.22)
    if sub_fit:
        block_h += int(h * 0.03) + len(sub_fit[1]) * int(sub_fit[2] * 1.22)
    ty = body_top + max(0, int((body_bottom - body_top - block_h) / 2)) if center else body_top
    ty = draw_lines(hlines, hf, hsize, ty, fg)
    if sub_fit:
        draw_lines(sub_fit[1], sub_fit[0], sub_fit[2], ty + int(h * 0.03), muted)

    # optional logo
    if spec.logo_path:
        try:
            logo = Image.open(spec.logo_path).convert("RGBA")
            lh = int(h * 0.09)
            logo = logo.resize((max(1, int(logo.width * lh / logo.height)), lh))
            img.paste(logo, (w - margin - logo.width, margin), logo)
        except Exception:  # noqa: BLE001
            warnings.append("The logo file could not be loaded and was skipped.")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    if spec.format == "jpeg":
        img.save(out_path, "JPEG", quality=92)
    else:
        img.save(out_path, "PNG")
    if preview_path:
        thumb = img.copy()
        thumb.thumbnail((480, 480))
        thumb.save(preview_path, "PNG")
    sha = hashlib.sha256(out_path.read_bytes()).hexdigest()
    return {
        "template": spec.template, "width": w, "height": h, "format": spec.format, "sha256": sha,
        "font": font_path or "PIL fallback", "headline_font_size": hsize, "contrast": round(ratio, 2),
        "warnings": warnings,
        "content": {"category": spec.category, "headline": spec.headline, "subtitle": spec.subtitle, "footer": spec.footer},
    }


def shorten(text: str, limit: int) -> str:
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    cut = text[: limit - 3].rsplit(" ", 1)[0] or text[: limit - 3]
    return cut.rstrip(" ,;:-") + "..."
