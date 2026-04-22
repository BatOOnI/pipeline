import base64
import math
import mimetypes
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QImage, QImageReader, QPainter


SUPPORTED_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


@dataclass
class PreviewBuildResult:
    preview_map: Dict[str, str]
    contact_sheet_path: str
    cache_dir: str


def slugify_filename(value: str) -> str:
    low = value.strip().lower()
    low = re.sub(r"[^a-z0-9]+", "-", low)
    low = re.sub(r"-{2,}", "-", low).strip("-")
    return low or "image"


def list_images_in_folder(folder: str) -> List[Path]:
    root = Path(folder)
    if not root.exists() or not root.is_dir():
        return []
    out = [p for p in root.iterdir() if p.is_file() and p.suffix.lower() in SUPPORTED_IMAGE_EXTS]
    out.sort(key=lambda p: p.name.lower())
    return out


def _read_scaled_image(src: Path, max_w: int = 1100, max_h: int = 1100) -> QImage:
    reader = QImageReader(str(src))
    reader.setAutoTransform(True)
    image = reader.read()
    if image.isNull():
        return QImage()
    return image.scaled(max_w, max_h, Qt.KeepAspectRatio, Qt.SmoothTransformation)


def build_preview_cache(images: List[Path], cache_dir: Path) -> Dict[str, str]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    out: Dict[str, str] = {}
    for idx, src in enumerate(images, start=1):
        img = _read_scaled_image(src)
        if img.isNull():
            continue
        dst = cache_dir / f"preview_{idx:03d}.jpg"
        img.save(str(dst), "JPG", quality=78)
        out[str(src)] = str(dst)
    return out


def build_contact_sheet(preview_map: Dict[str, str], out_path: Path, cols: int = 4) -> str:
    items = list(preview_map.items())
    if not items:
        return ""
    cols = max(2, cols)
    thumb_w = 240
    thumb_h = 170
    title_h = 36
    pad = 14
    rows = int(math.ceil(len(items) / float(cols)))
    canvas_w = pad + cols * (thumb_w + pad)
    canvas_h = pad + rows * (thumb_h + title_h + pad)

    canvas = QImage(canvas_w, canvas_h, QImage.Format_RGB32)
    canvas.fill(QColor("#f8fafc"))
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
    painter.setFont(QFont("Segoe UI", 9))

    for idx, (orig, preview) in enumerate(items):
        row = idx // cols
        col = idx % cols
        x = pad + col * (thumb_w + pad)
        y = pad + row * (thumb_h + title_h + pad)

        img = QImage(preview)
        if not img.isNull():
            scaled = img.scaled(thumb_w, thumb_h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            ox = x + (thumb_w - scaled.width()) // 2
            oy = y + (thumb_h - scaled.height()) // 2
            painter.fillRect(x, y, thumb_w, thumb_h, QColor("#e2e8f0"))
            painter.drawImage(ox, oy, scaled)

        label = f"{idx + 1:02d}. {Path(orig).name}"
        painter.setPen(QColor("#1f2937"))
        painter.drawText(x, y + thumb_h + 18, thumb_w, title_h, int(Qt.TextWordWrap), label)

    painter.end()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(str(out_path), "JPG", quality=82)
    return str(out_path)


def build_previews_and_contact_sheet(folder: str, cache_root: str) -> PreviewBuildResult:
    images = list_images_in_folder(folder)
    cache_dir = Path(cache_root)
    cache_dir.mkdir(parents=True, exist_ok=True)
    preview_map = build_preview_cache(images, cache_dir)
    contact_sheet_path = build_contact_sheet(preview_map, cache_dir / "contact_sheet.jpg")
    return PreviewBuildResult(
        preview_map=preview_map,
        contact_sheet_path=contact_sheet_path,
        cache_dir=str(cache_dir),
    )


def file_to_data_url(path: str) -> str:
    p = Path(path)
    if not p.exists():
        return ""
    mime, _ = mimetypes.guess_type(str(p))
    if not mime:
        mime = "image/jpeg"
    b64 = base64.b64encode(p.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{b64}"


def image_dimensions(path: str) -> Tuple[int, int]:
    img = QImage(path)
    if img.isNull():
        return (0, 0)
    return (int(img.width()), int(img.height()))
