import io
import os
import threading
from typing import Optional

import fitz  # PyMuPDF
import requests
from PIL import Image, ImageDraw, ImageFont

from .cache import pdf_bytes_cache

WATERMARK_TEXT = "Milik Universitas Hasanuddin"
RENDER_SCALE = 1.5  # ~108 DPI rendering quality

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

# Per-URL fetch locks — prevents duplicate downloads on concurrent requests
_fetch_locks: dict[str, threading.Lock] = {}
_locks_meta = threading.Lock()


def _get_fetch_lock(key: str) -> threading.Lock:
    with _locks_meta:
        if key not in _fetch_locks:
            _fetch_locks[key] = threading.Lock()
        return _fetch_locks[key]


class PDFRenderer:
    # ─── PDF Fetching ─────────────────────────────────────────────────────────

    def _fetch_pdf(self, cache_key: str, file_url: str) -> bytes:
        """
        Download PDF server-to-server dan cache hasilnya 5 menit.
        URL asli TIDAK pernah dikirim ke client — hanya dipakai di sini.
        Dengan double-checked locking untuk hindari download ganda.
        """
        cached = pdf_bytes_cache.get(cache_key)
        if cached:
            return cached

        lock = _get_fetch_lock(cache_key)
        with lock:
            # Double-check setelah lock diperoleh
            cached = pdf_bytes_cache.get(cache_key)
            if cached:
                return cached

            resp = requests.get(file_url, headers=HEADERS, timeout=90, stream=True)
            resp.raise_for_status()
            pdf_bytes = b"".join(resp.iter_content(chunk_size=65536))
            pdf_bytes_cache.set(cache_key, pdf_bytes, ttl=300)  # 5 menit
            return pdf_bytes

    # ─── Page Count ──────────────────────────────────────────────────────────

    def get_page_count(self, eprint_id: str, doc_type: str, file_url: str) -> int:
        cache_key = f"pdf:{eprint_id}:{doc_type}"
        pdf_bytes = self._fetch_pdf(cache_key, file_url)
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        count = len(doc)
        doc.close()
        return count

    # ─── Page Rendering ───────────────────────────────────────────────────────

    def render_page(
        self, eprint_id: str, doc_type: str, page_num: int, file_url: str
    ) -> bytes:
        """
        Render satu halaman PDF sebagai JPEG dengan watermark.
        page_num: 0-based index.
        Return: JPEG bytes gambar halaman + watermark.
        """
        cache_key = f"pdf:{eprint_id}:{doc_type}"
        pdf_bytes = self._fetch_pdf(cache_key, file_url)

        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        try:
            if page_num < 0 or page_num >= len(doc):
                raise ValueError(f"Page {page_num} out of range (total {len(doc)})")

            page = doc[page_num]
            mat = fitz.Matrix(RENDER_SCALE, RENDER_SCALE)
            pix = page.get_pixmap(matrix=mat, alpha=False)
        finally:
            doc.close()

        # Convert pixmap → PIL Image
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)

        # Stamp watermark
        img = self._stamp_watermark(img)

        # Encode to JPEG
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=82, optimize=True)
        return buf.getvalue()

    # ─── Watermark ────────────────────────────────────────────────────────────

    def _stamp_watermark(self, img: Image.Image) -> Image.Image:
        w, h = img.size
        font_size = max(28, int(min(w, h) * 0.055))
        font = self._load_font(font_size)

        # Measure text
        tmp = Image.new("RGBA", (1, 1))
        tmp_draw = ImageDraw.Draw(tmp)
        bbox = tmp_draw.textbbox((0, 0), WATERMARK_TEXT, font=font)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]

        # Draw text on transparent canvas
        pad = 24
        txt_img = Image.new("RGBA", (tw + pad * 2, th + pad * 2), (0, 0, 0, 0))
        txt_draw = ImageDraw.Draw(txt_img)
        txt_draw.text((pad, pad), WATERMARK_TEXT, font=font, fill=(80, 80, 80, 72))

        # Rotate 45° diagonal
        rotated = txt_img.rotate(45, expand=True, resample=Image.BICUBIC)

        # Center on page
        overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        x = (w - rotated.width) // 2
        y = (h - rotated.height) // 2
        overlay.paste(rotated, (x, y), mask=rotated)

        result = Image.alpha_composite(img.convert("RGBA"), overlay)
        return result.convert("RGB")

    def _load_font(self, size: int) -> ImageFont.FreeTypeFont:
        font_paths = [
            "/System/Library/Fonts/Helvetica.ttc",
            "/System/Library/Fonts/Arial.ttf",
            "/Library/Fonts/Arial.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
            "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
        ]
        for path in font_paths:
            if os.path.exists(path):
                try:
                    return ImageFont.truetype(path, size)
                except (OSError, IOError):
                    continue
        return ImageFont.load_default()


# Singleton
pdf_renderer = PDFRenderer()
