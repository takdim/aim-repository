"""
Unhas Repository PDF Scraper + Watermark Tool
=============================================
- Scrape semua link PDF dari halaman repository Unhas
- Download PDF dan tambahkan watermark
- Usage: python unhas_scraper.py [URL]
"""

import os
import sys
import io
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.colors import Color
from pypdf import PdfReader, PdfWriter


# ─── Konfigurasi ─────────────────────────────────────────────────────────────

WATERMARK_TEXT = "© Repository Unhas - Hanya untuk keperluan akademik"
OUTPUT_DIR = "downloaded_pdfs"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}


# ─── Fungsi Scraping ──────────────────────────────────────────────────────────

def scrape_pdf_links(page_url: str) -> list[dict]:
    """
    Scrape semua link PDF yang tersedia (tidak restricted) dari halaman eprint.
    Mengembalikan list dict: [{"filename": ..., "url": ..., "label": ...}]
    """
    print(f"[*] Mengambil halaman: {page_url}")
    response = requests.get(page_url, headers=HEADERS, timeout=30)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    pdf_links = []

    # Cari semua tag <a> yang mengarah ke file .pdf
    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"].strip()

        # Normalkan URL relatif → absolut
        full_url = urljoin(page_url, href)

        if not full_url.lower().endswith(".pdf"):
            continue

        # Cek apakah file restricted (ada teks "Restricted")
        parent_text = a_tag.find_parent().get_text(separator=" ") if a_tag.find_parent() else ""
        is_restricted = "restricted" in parent_text.lower()

        filename = os.path.basename(urlparse(full_url).path)
        label = a_tag.get_text(strip=True) or filename

        pdf_links.append({
            "filename": filename,
            "url": full_url,
            "label": label,
            "restricted": is_restricted,
        })

    # Hapus duplikat berdasarkan nama file (ambil URL /id/eprint/ sebagai prioritas)
    seen_filenames = {}
    for link in pdf_links:
        fname = link["filename"]
        if fname not in seen_filenames:
            seen_filenames[fname] = link
        else:
            # Prioritaskan URL yang mengandung /id/eprint/
            if "/id/eprint/" in link["url"]:
                seen_filenames[fname] = link

    return list(seen_filenames.values())


# ─── Fungsi Watermark ─────────────────────────────────────────────────────────

def create_watermark_pdf(text: str) -> bytes:
    """
    Buat halaman PDF berisi teks watermark diagonal (transparan).
    Mengembalikan bytes PDF watermark.
    """
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    # Warna abu-abu transparan
    c.setFillColor(Color(0.5, 0.5, 0.5, alpha=0.25))
    c.setFont("Helvetica-Bold", 28)

    # Rotasi 45 derajat di tengah halaman
    c.saveState()
    c.translate(width / 2, height / 2)
    c.rotate(45)
    c.drawCentredString(0, 0, text)
    c.restoreState()

    # Watermark kedua (pojok bawah, lebih kecil)
    c.setFillColor(Color(0.5, 0.5, 0.5, alpha=0.35))
    c.setFont("Helvetica", 10)
    c.drawString(30, 20, text)

    c.save()
    buffer.seek(0)
    return buffer.read()


def apply_watermark(pdf_bytes: bytes, watermark_text: str) -> bytes:
    """
    Overlay watermark ke setiap halaman PDF.
    Mengembalikan bytes PDF hasil watermark.
    """
    watermark_bytes = create_watermark_pdf(watermark_text)
    watermark_pdf = PdfReader(io.BytesIO(watermark_bytes))
    watermark_page = watermark_pdf.pages[0]

    reader = PdfReader(io.BytesIO(pdf_bytes))
    writer = PdfWriter()

    for page in reader.pages:
        page.merge_page(watermark_page)
        writer.add_page(page)

    output_buffer = io.BytesIO()
    writer.write(output_buffer)
    output_buffer.seek(0)
    return output_buffer.read()


# ─── Fungsi Download + Simpan ─────────────────────────────────────────────────

def download_and_watermark(pdf_info: dict, output_dir: str, watermark_text: str):
    """Download PDF, tambahkan watermark, simpan ke disk."""
    url = pdf_info["url"]
    filename = pdf_info["filename"]
    out_path = os.path.join(output_dir, f"watermarked_{filename}")

    print(f"  [↓] Mendownload: {filename}")
    print(f"      URL: {url}")

    response = requests.get(url, headers=HEADERS, timeout=120, stream=True)

    if response.status_code in (401, 403):
        print(f"  [!] Akses ditolak ({response.status_code}) - file restricted/perlu login: {filename}")
        return
    elif response.status_code == 404:
        print(f"  [!] File tidak ditemukan (404): {filename}")
        return

    response.raise_for_status()

    pdf_bytes = b"".join(response.iter_content(chunk_size=8192))
    print(f"  [✓] Download selesai ({len(pdf_bytes) / 1024 / 1024:.1f} MB)")

    print(f"  [~] Menambahkan watermark...")
    try:
        watermarked = apply_watermark(pdf_bytes, watermark_text)
        with open(out_path, "wb") as f:
            f.write(watermarked)
        print(f"  [✓] Disimpan → {out_path}")
    except Exception as e:
        print(f"  [!] Gagal menambahkan watermark: {e}")
        # Simpan PDF asli tanpa watermark sebagai fallback
        fallback_path = os.path.join(output_dir, filename)
        with open(fallback_path, "wb") as f:
            f.write(pdf_bytes)
        print(f"  [~] Disimpan tanpa watermark → {fallback_path}")


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    url = sys.argv[1] if len(sys.argv) > 1 else "https://repository.unhas.ac.id/id/eprint/11039/"

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # 1. Scrape PDF links
    pdf_links = scrape_pdf_links(url)

    if not pdf_links:
        print("[!] Tidak ditemukan link PDF di halaman tersebut.")
        return

    print(f"\n[*] Ditemukan {len(pdf_links)} link PDF:\n")
    for i, link in enumerate(pdf_links, 1):
        status = "🔒 RESTRICTED" if link["restricted"] else "✅ TERSEDIA"
        print(f"  {i}. [{status}] {link['label']}")
        print(f"     {link['url']}\n")

    # 2. Download & watermark hanya yang tersedia
    available = [l for l in pdf_links if not l["restricted"]]
    print(f"[*] Akan mendownload {len(available)} file tersedia...\n")

    for pdf_info in available:
        download_and_watermark(pdf_info, OUTPUT_DIR, WATERMARK_TEXT)

    print(f"\n[✓] Selesai! File tersimpan di folder: {OUTPUT_DIR}/")


if __name__ == "__main__":
    main()
