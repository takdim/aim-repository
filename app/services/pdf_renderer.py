import io
import os
import threading
from pathlib import Path
from typing import Optional

import fitz  # PyMuPDF
import requests
from PIL import Image, ImageDraw, ImageFont

from .cache import pdf_bytes_cache

WATERMARK_TEXT = "UNIVERSITAS HASANUDDIN"
RENDER_SCALE = 1.5  # ~108 DPI rendering quality
LOGO_PATH = Path(__file__).resolve().parent.parent / "static" / "img" / "logo_unhas.png"

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
    def _get_logo_rgba(self, max_width: int = 420, alpha_factor: float = 0.35) -> Optional[Image.Image]:
        if not LOGO_PATH.exists():
            return None

        try:
            logo = Image.open(LOGO_PATH).convert("RGBA")
        except (OSError, IOError):
            return None

        if logo.width <= 0 or logo.height <= 0:
            return None

        target_w = min(max_width, logo.width)
        target_h = max(1, int(target_w * (logo.height / logo.width)))
        logo = logo.resize((target_w, target_h), Image.LANCZOS)

        alpha = logo.getchannel("A")
        alpha = alpha.point(lambda p: int(p * alpha_factor))
        logo.putalpha(alpha)
        return logo

    def _apply_pdf_watermark(self, doc: fitz.Document) -> None:
        logo = self._get_logo_rgba(max_width=520, alpha_factor=0.72)
        logo_stream = None
        logo_ratio = 1.0
        if logo is not None:
            buf = io.BytesIO()
            logo.save(buf, format="PNG")
            logo_stream = buf.getvalue()
            logo_ratio = logo.height / max(1, logo.width)

        for page in doc:
            rect = page.rect
            size = max(20, int(min(rect.width, rect.height) * 0.045))
            wm_rect = fitz.Rect(
                rect.x0 + rect.width * 0.08,
                rect.y0 + rect.height * 0.50,
                rect.x1 - rect.width * 0.08,
                rect.y0 + rect.height * 0.62,
            )
            text_img = self._get_rotated_text_rgba(WATERMARK_TEXT, size)
            text_buf = io.BytesIO()
            text_img.save(text_buf, format="PNG")
            text_w = min(rect.width * 0.84, text_img.width)
            text_h = text_w * text_img.height / max(1, text_img.width)
            text_rect = fitz.Rect(
                rect.x0 + (rect.width - text_w) / 2,
                wm_rect.y0 + (wm_rect.height - text_h) / 2,
                rect.x0 + (rect.width + text_w) / 2,
                wm_rect.y0 + (wm_rect.height + text_h) / 2,
            )
            page.insert_image(text_rect, stream=text_buf.getvalue(), overlay=True)

            if logo_stream:
                logo_w = rect.width * 0.16
                logo_h = logo_w * logo_ratio
                gap = rect.height * 0.012

                x0 = rect.x0 + (rect.width - logo_w) / 2
                y1 = wm_rect.y0 - gap
                y0 = y1 - logo_h

                # Prevent logo from going off-page on small page sizes.
                if y0 < rect.y0 + 8:
                    y0 = rect.y0 + 8
                    y1 = y0 + logo_h

                logo_rect = fitz.Rect(x0, y0, x0 + logo_w, y1)
                page.insert_image(logo_rect, stream=logo_stream, overlay=True, keep_proportion=True)

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

    def get_page_count_from_bytes(self, pdf_bytes: bytes) -> int:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        try:
            return len(doc)
        finally:
            doc.close()

    def get_watermarked_pdf(self, eprint_id: str, doc_type: str, file_url: str) -> bytes:
        """
        Return PDF bytes ber-watermark untuk ditampilkan inline di browser.
        Hanya dipakai untuk dokumen open-access.
        """
        src_cache_key = f"pdf:{eprint_id}:{doc_type}"
        wm_cache_key = f"pdfwm:{eprint_id}:{doc_type}"

        cached_wm = pdf_bytes_cache.get(wm_cache_key)
        if cached_wm:
            return cached_wm

        src_pdf = self._fetch_pdf(src_cache_key, file_url)
        lock = _get_fetch_lock(wm_cache_key)
        with lock:
            cached_wm = pdf_bytes_cache.get(wm_cache_key)
            if cached_wm:
                return cached_wm

            doc = fitz.open(stream=src_pdf, filetype="pdf")
            try:
                self._apply_pdf_watermark(doc)

                out = io.BytesIO()
                doc.save(out, deflate=True, garbage=3)
                wm_bytes = out.getvalue()
            finally:
                doc.close()

            pdf_bytes_cache.set(wm_cache_key, wm_bytes, ttl=300)
            return wm_bytes

    def get_watermarked_pdf_from_bytes(self, cache_id: str, pdf_bytes: bytes) -> bytes:
        wm_cache_key = f"pdfwm:{cache_id}"

        cached_wm = pdf_bytes_cache.get(wm_cache_key)
        if cached_wm:
            return cached_wm

        lock = _get_fetch_lock(wm_cache_key)
        with lock:
            cached_wm = pdf_bytes_cache.get(wm_cache_key)
            if cached_wm:
                return cached_wm

            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            try:
                self._apply_pdf_watermark(doc)

                out = io.BytesIO()
                doc.save(out, deflate=True, garbage=3)
                wm_bytes = out.getvalue()
            finally:
                doc.close()

            pdf_bytes_cache.set(wm_cache_key, wm_bytes, ttl=300)
            return wm_bytes

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
        return self.render_page_from_bytes(cache_key, page_num, pdf_bytes)

    def render_page_from_bytes(
        self, cache_id: str, page_num: int, pdf_bytes: bytes
    ) -> bytes:
        """Render one zero-based PDF page from already-fetched bytes."""

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

    def _get_rotated_text_rgba(self, text: str, font_size: int) -> Image.Image:
        font = self._load_font(font_size)
        probe = Image.new("RGBA", (1, 1), (0, 0, 0, 0))
        draw = ImageDraw.Draw(probe)
        bbox = draw.textbbox((0, 0), text, font=font)
        padding = max(4, font_size // 5)
        text_img = Image.new(
            "RGBA",
            (bbox[2] - bbox[0] + padding * 2, bbox[3] - bbox[1] + padding * 2),
            (0, 0, 0, 0),
        )
        ImageDraw.Draw(text_img).text(
            (padding - bbox[0], padding - bbox[1]),
            text,
            font=font,
            fill=(70, 70, 70, 180),
        )
        return text_img.rotate(30, expand=True, resample=Image.BICUBIC)

    def _stamp_watermark(self, img: Image.Image) -> Image.Image:
        w, h = img.size
        font_size = max(28, int(min(w, h) * 0.055))
        logo = self._get_logo_rgba(max_width=max(140, int(min(w, h) * 0.26)), alpha_factor=0.72)

        rotated = self._get_rotated_text_rgba(WATERMARK_TEXT, font_size)
        pad = 24
        logo_h = logo.height if logo else 0
        logo_w = logo.width if logo else 0
        gap = max(8, int(font_size * 0.28)) if logo else 0
        block_w = max(rotated.width, logo_w) + pad * 2
        block_h = rotated.height + logo_h + gap + pad * 2
        watermark = Image.new("RGBA", (block_w, block_h), (0, 0, 0, 0))
        current_y = pad
        if logo:
            watermark.paste(logo, ((block_w - logo_w) // 2, current_y), logo)
            current_y += logo_h + gap
        watermark.paste(
            rotated,
            ((block_w - rotated.width) // 2, current_y),
            rotated,
        )

        # Center on page
        overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        x = (w - watermark.width) // 2
        y = (h - watermark.height) // 2
        overlay.paste(watermark, (x, y), mask=watermark)

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
