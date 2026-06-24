# PRD — Unhas Repository Search & Secure Viewer

**Versi:** 1.0
**Tanggal:** 24 Juni 2026
**Status:** Draft — siap implementasi
**Stack:** Python 3.x + Flask (venv), tanpa frontend framework berat (vanilla JS/HTML/CSS)

---

## 1. Ringkasan Produk

Aplikasi web sederhana yang membungkus (wrapper) pencarian repository Unhas (`repository.unhas.ac.id`), menampilkan hasil pencarian dengan pagination, dan menyediakan **viewer PDF read-only** (tidak bisa download) lengkap dengan watermark "Milik Universitas Hasanuddin" di tengah setiap halaman file.

Tujuan utamanya bukan sekadar mirror, tapi menambahkan **lapisan kontrol akses** di atas repository asli: file tidak bisa diunduh langsung, dan file yang statusnya *restricted* tidak ditampilkan sebagai bisa diakses ke publik.

---

## 2. Latar Belakang & Tujuan

- Repository asli mengizinkan **download langsung** untuk dokumen yang tidak restricted, dan akan redirect ke halaman login untuk dokumen yang restricted.
- Produk ini ingin:
  1. Memberi pengalaman pencarian yang lebih simpel (search di tengah, hasil + pagination di bawahnya).
  2. **Menghilangkan kemampuan download** — pengguna hanya bisa *melihat* isi dokumen di browser.
  3. Menambahkan **watermark** otomatis di setiap halaman PDF yang ditampilkan.
  4. Secara otomatis mendeteksi dokumen mana yang **belum terbuka untuk umum** (restricted) dan menampilkan modal informatif, bukan mengarahkan ke halaman login asli.

---

## 3. Lingkup (Scope)

### In-Scope
- Halaman pencarian (search bar di tengah, hasil di bawah, dengan pagination).
- Halaman detail hasil pencarian → cek ketersediaan 3 jenis dokumen: **Bab 1-2**, **Dapus**, **Full Text**.
- Deteksi status *restricted* khusus untuk Full Text (juga berlaku general jika ada tipe lain yang restricted).
- Modal "Maaf, file belum terbuka untuk umum" saat dokumen restricted diklik.
- PDF Viewer custom (bukan link langsung ke file asli) — view-only, tanpa tombol/akses download, dengan watermark di tengah setiap halaman.
- Dijalankan di Python venv + Flask.

### Out-of-Scope (v1)
- Login / akun staff repository (tidak ada fitur unlock manual).
- Mirror seluruh isi repository (hanya hasil pencarian by query).
- Pencegahan screenshot / screen recording (secara teknis tidak mungkin dicegah 100% di browser — lihat bagian 12 Batasan).
- Edit/upload dokumen baru.
- Mobile app native (cukup responsive web).

---

## 4. Target Pengguna

- Mahasiswa/dosen yang ingin browsing referensi tesis/skripsi Unhas tanpa risiko file disalin/didistribusikan ulang secara bebas.
- Pemilik aplikasi (kemungkinan untuk dijual/dipakai sebagai tool akademik, sesuai konteks freelance project).

---

## 5. User Flow

1. User membuka halaman utama → melihat **search bar di tengah halaman** (belum ada hasil apapun, layout kosong/minimalis).
2. User mengetik query (misal: `beton`) → submit search.
3. Sistem melakukan request ke endpoint pencarian repository asli:
   `https://repository.unhas.ac.id/cgi/search/simple?q={query}`
4. Hasil pencarian (judul, penulis, tahun) ditampilkan sebagai **list di bawah search bar**, dengan **pagination** (misal 10 item/halaman).
5. User klik salah satu judul di list.
6. Sistem mengambil halaman detail dokumen tersebut:
   `https://repository.unhas.ac.id/id/eprint/{id}/`
7. Sistem **parsing halaman detail** untuk mendeteksi dokumen yang tersedia, dengan pola label:
   - `Text (Bab 1-2)` → contoh: `.../2/{filename}%201-2.pdf`
   - `Text (Dapus)` → contoh: `.../3/{filename}%20dp.pdf`
   - `Text (Full Text)` → contoh: `.../4/{filename}.pdf`
8. Sistem menampilkan judul detail + tombol/link per jenis dokumen yang **ditemukan saja** (jika satu jenis tidak ada di halaman, tombolnya tidak muncul).
9. Untuk **setiap dokumen**, sistem mengecek apakah di sekitar blok dokumen tersebut ada teks **`Restricted to Repository staff only`**.
   - Jika **tidak ada** teks tersebut → dokumen **bisa diklik**, mengarah ke **Viewer internal** (bukan ke file asli).
   - Jika **ada** teks tersebut → tombol tetap tampil tapi saat diklik, sistem menampilkan **modal**: *"Maaf, file belum terbuka untuk umum"* — TIDAK mengarahkan ke halaman login repository asli.
10. Saat user klik dokumen yang available → Viewer terbuka, menampilkan PDF **per halaman sebagai gambar** dengan watermark **"Milik Universitas Hasanuddin"** di tengah setiap halaman, **tanpa ada tombol/opsi download** sama sekali (termasuk tidak ada akses ke URL file asli dari sisi client).

---

## 6. Functional Requirements

| ID | Requirement |
|----|-------------|
| FR-1 | Halaman utama menampilkan search bar terpusat secara vertikal & horizontal saat belum ada hasil pencarian. |
| FR-2 | Submit search memanggil backend yang mem-proxy `cgi/search/simple?q=...` milik repository Unhas, lalu parsing hasil (judul, link eprint id, penulis/tahun jika tersedia). |
| FR-3 | Hasil pencarian ditampilkan sebagai list vertikal di bawah search bar, dengan komponen pagination (Previous/Next + nomor halaman). |
| FR-4 | Klik item hasil pencarian → request ke backend untuk fetch & parsing halaman detail eprint. |
| FR-5 | Backend mendeteksi keberadaan 3 jenis dokumen berdasarkan label teks pada halaman detail: `Bab 1-2`, `Dapus`, `Full Text`. Jenis yang tidak ditemukan tidak ditampilkan ke user. |
| FR-6 | Untuk setiap jenis dokumen yang ditemukan, backend mengecek substring `Restricted to Repository staff only` pada blok HTML dokumen tersebut → set flag `is_restricted: true/false`. |
| FR-7 | Jika `is_restricted = false` → klik dokumen membuka Viewer internal (`/view/<id>/<jenis>`). |
| FR-8 | Jika `is_restricted = true` → klik dokumen menampilkan modal pesan: **"Maaf, file belum terbuka untuk umum"**, tanpa request apapun ke file/halaman login asli. |
| FR-9 | Viewer PDF menampilkan dokumen halaman-per-halaman dalam bentuk gambar ter-render (bukan embed PDF asli), dengan watermark teks **"Milik Universitas Hasanuddin"** diagonal, semi-transparan, di tengah setiap halaman. |
| FR-10 | Viewer **tidak memiliki** tombol/menu download, tidak ada link langsung ke file PDF asli yang bisa diakses dari sisi client (Network tab browser hanya melihat endpoint image per-halaman, bukan file PDF utuh). |
| FR-11 | Klik kanan pada area viewer (context menu) dinonaktifkan sebagai langkah tambahan (bukan pengaman utama — lihat batasan). |

---

## 7. Non-Functional Requirements

- **Caching**: hasil scraping (search & detail) di-cache sementara (misal 5–15 menit, in-memory atau file-based) agar tidak membombardir server repository asli setiap request.
- **Rate limiting / etika scraping**: beri delay/jeda antar request ke `repository.unhas.ac.id`, gunakan `User-Agent` yang jelas, hormati `robots.txt`.
- **Performance**: render watermark per halaman PDF dilakukan **lazy** (hanya saat halaman dibuka di viewer), bukan generate semua halaman sekaligus saat pertama kali dokumen ditemukan.
- **Reliability**: jika struktur HTML repository asli berubah (selector berubah), sistem harus fallback dengan pesan error yang jelas, bukan crash.
- **Security**: backend yang menyimpan/menyajikan PDF watermark tidak boleh expose URL asli file dari repository ke response/HTML yang dikirim ke browser.

---

## 8. Business Rules — Detail Logic

### 8.1 Deteksi Jenis Dokumen
Berdasarkan struktur halaman detail (lihat referensi screenshot eprint `11941`), setiap dokumen tersusun sebagai blok berisi:
- Label tipe, contoh: `Text (Bab 1-2)`, `Text (Dapus)`, `Text (Full Text)`, `Image (Cover)`.
- Nama file.
- (Opsional) baris `Restricted to Repository staff only`.
- Link `Download (xxKB/MB)`.

> ⚠️ **Catatan implementasi**: Selector HTML pasti (class/id elemen) perlu dikonfirmasi langsung dari source code halaman saat development (inspect element), karena PRD ini berdasarkan tampilan visual di screenshot. Disarankan mem-parsing berdasarkan **urutan teks** (`Text (Bab 1-2)`, `Text (Dapus)`, `Text (Full Text)`) menggunakan BeautifulSoup, lalu mengambil tag `<a>` dengan `href` berakhiran `.pdf` di blok yang sama.

### 8.2 Deteksi Restricted
- Cek apakah string `Restricted to Repository staff only` muncul **di dalam blok dokumen yang sama** dengan label jenis dokumen tersebut.
- Jika ya → `is_restricted = True` untuk jenis dokumen itu **saja** (bukan untuk seluruh eprint). Catatan: pada praktiknya yang paling sering restricted adalah **Full Text**, tapi logika ini dibuat general per-dokumen agar tetap benar jika suatu saat Bab 1-2/Dapus juga restricted.
- Backend **tidak pernah** mengikuti redirect ke `cgi/users/login?target=...` — deteksi cukup dari teks di halaman detail, tidak perlu request ke link login.

### 8.3 Aturan Modal
- Trigger: user klik dokumen dengan `is_restricted = True`.
- Tampilan: modal sederhana, judul opsional, isi: **"Maaf, file belum terbuka untuk umum"**, tombol Tutup.
- Tidak ada redirect, tidak ada network request ke file asli sama sekali saat modal muncul.

---

## 9. PDF Security Handling (View-Only + Watermark)

### Pendekatan yang direkomendasikan: **render-to-image**
1. Backend fetch PDF asli dari repository (server-to-server, URL asli **tidak pernah** dikirim ke browser).
2. PDF dirender per halaman menjadi gambar (PNG/JPEG) menggunakan **PyMuPDF (`fitz`)**.
3. Watermark teks `"Milik Universitas Hasanuddin"` digambar di tengah setiap halaman (diagonal, opacity rendah, ukuran proporsional terhadap halaman) — di-stamp di level gambar, bukan di level teks PDF, supaya tidak bisa dihapus dengan extract-text/edit PDF biasa.
4. Gambar hasil render dikirim ke frontend satu-per-satu (lazy load saat scroll/ganti halaman), ditampilkan dalam viewer custom (canvas/`<img>` + UI navigasi halaman).
5. Tidak ada endpoint yang mengembalikan file PDF utuh (asli maupun watermark) ke client — yang dikirim **hanya gambar per halaman**.

### Alternatif (lebih ringan, sedikit lebih rawan)
- Generate PDF watermark utuh di server, tampilkan via PDF.js custom build dengan tombol download & print dihapus dari UI viewer.
- ⚠️ Lebih rawan karena endpoint file masih bisa diakses langsung via URL jika ketahuan, dan PDF.js viewer default punya kemudahan save/print yang harus di-patch manual.

**→ PRD ini menetapkan pendekatan render-to-image sebagai default v1**, karena paling sesuai dengan requirement "betul-betul hanya view, tidak bisa download".

---

## 10. Arsitektur Teknis

### 10.1 Tech Stack
- Python 3.10+ di dalam **virtual environment (venv)**
- Flask (web framework + routing)
- `requests` (HTTP client untuk scraping repository asli)
- `beautifulsoup4` + `lxml` (parsing HTML)
- `PyMuPDF` (`fitz`) (render PDF → image + watermark)
- `Pillow` (manipulasi gambar tambahan jika perlu)
- Frontend: HTML + CSS + vanilla JS (fetch API untuk AJAX search & viewer), tidak perlu framework JS berat di v1.

### 10.2 Struktur Folder (usulan)
```
unhas-repo-viewer/
├── venv/
├── app/
│   ├── __init__.py
│   ├── routes.py
│   ├── services/
│   │   ├── repository_client.py    # scraping search & detail
│   │   ├── pdf_renderer.py         # render halaman PDF + watermark
│   │   └── cache.py                # cache sederhana (in-memory/TTL)
│   ├── templates/
│   │   ├── index.html              # search page + hasil + pagination
│   │   ├── detail.html             # daftar dokumen per eprint
│   │   └── viewer.html             # PDF viewer custom
│   └── static/
│       ├── css/
│       └── js/
│           ├── search.js
│           └── viewer.js
├── requirements.txt
├── run.py
└── .env (jika perlu konfigurasi base URL, dll)
```

### 10.3 Rancangan Class / Module (menjawab kebutuhan struktur class)

```python
# services/repository_client.py

class SearchResultItem:
    def __init__(self, eprint_id: str, title: str, detail_url: str):
        self.eprint_id = eprint_id
        self.title = title
        self.detail_url = detail_url

class DocumentInfo:
    def __init__(self, doc_type: str, file_url: str, is_restricted: bool):
        self.doc_type = doc_type        # "bab1_2" | "dapus" | "full_text"
        self.file_url = file_url        # URL asli (TIDAK dikirim ke client)
        self.is_restricted = is_restricted

class EprintDetail:
    def __init__(self, eprint_id: str, title: str, documents: list[DocumentInfo]):
        self.eprint_id = eprint_id
        self.title = title
        self.documents = documents

class RepositoryClient:
    BASE_URL = "https://repository.unhas.ac.id"

    def search(self, query: str, page: int = 1) -> tuple[list[SearchResultItem], dict]:
        """Hit /cgi/search/simple?q=..., parsing hasil + info pagination."""
        ...

    def get_detail(self, eprint_id: str) -> EprintDetail:
        """Hit /id/eprint/{id}/, parsing dokumen & status restricted."""
        ...
```

```python
# services/pdf_renderer.py

class PDFRenderer:
    WATERMARK_TEXT = "Milik Universitas Hasanuddin"

    def fetch_pdf_bytes(self, file_url: str) -> bytes:
        """Download PDF asli server-to-server."""
        ...

    def render_page_as_image(self, pdf_bytes: bytes, page_number: int) -> bytes:
        """Render 1 halaman PDF -> image dengan watermark di tengah, return bytes gambar."""
        ...

    def get_page_count(self, pdf_bytes: bytes) -> int:
        ...
```

```python
# services/cache.py

class SimpleCache:
    def get(self, key: str): ...
    def set(self, key: str, value, ttl_seconds: int = 600): ...
```

> Class-class di atas adalah usulan struktur dasar — boleh disederhanakan (misal jadi function-based) tergantung preferensi saat implementasi, tapi pembagian tanggung jawab (scraping vs rendering vs caching) disarankan tetap dipisah seperti ini agar mudah di-maintain.

---

## 11. API Endpoints (Internal)

| Method | Endpoint | Deskripsi |
|--------|----------|-----------|
| GET | `/` | Halaman utama dengan search bar di tengah. |
| GET | `/search?q=...&page=1` | Proxy + parsing hasil pencarian, return HTML partial/JSON untuk list + pagination. |
| GET | `/detail/<eprint_id>` | Ambil & tampilkan daftar dokumen (Bab 1-2 / Dapus / Full Text) beserta status restricted. |
| GET | `/view/<eprint_id>/<doc_type>` | Halaman viewer PDF custom (hanya bisa diakses jika `is_restricted = False`). |
| GET | `/api/page-image/<eprint_id>/<doc_type>/<page_number>` | Mengembalikan **gambar** (bukan PDF) halaman tersebut, sudah ber-watermark. Dipanggil oleh `viewer.js` secara lazy saat user scroll/ganti halaman. |

> Semua endpoint `/view` dan `/api/page-image` **wajib** melakukan re-validasi `is_restricted` di server (jangan percaya state dari client), supaya user tidak bisa bypass modal dengan langsung mengetik URL viewer.

---

## 12. Batasan Teknis (Disclaimer)

- Mekanisme ini **mencegah download langsung** (tidak ada tombol/link ke file asli, tidak ada endpoint yang mengembalikan PDF utuh ke client).
- Mekanisme ini **tidak bisa mencegah** screenshot, screen recording, atau print-screen oleh user — ini adalah batasan umum semua sistem "view-only" berbasis browser, bukan kekurangan implementasi.
- Jika struktur HTML repository asli berubah di kemudian hari, parsing (selector/regex) perlu disesuaikan ulang.

---

## 13. Error Handling & Edge Cases

| Kondisi | Perilaku Sistem |
|---------|------------------|
| Query pencarian kosong | Tampilkan pesan "Masukkan kata kunci pencarian", jangan hit backend. |
| Hasil pencarian 0 item | Tampilkan state kosong "Tidak ada hasil ditemukan untuk '{query}'". |
| Halaman detail eprint tidak bisa diakses (timeout/error) | Tampilkan pesan error generik + tombol "Coba lagi". |
| Tidak ada dokumen sama sekali (Bab1-2/Dapus/Full Text semua tidak ada) | Tampilkan pesan "Dokumen belum tersedia untuk judul ini". |
| User akses langsung URL `/view/<id>/<doc_type>` untuk dokumen restricted (tanpa lewat klik modal) | Server tetap re-cek status restricted → redirect balik / tampilkan modal, **bukan** menampilkan viewer. |
| File PDF asli gagal di-fetch dari repository (404/500 dari sisi mereka) | Tampilkan pesan error di viewer: "Dokumen tidak dapat dimuat saat ini". |

---

## 14. Setup & Environment (venv)

```bash
# 1. Buat virtual environment
python3 -m venv venv

# 2. Aktifkan venv
source venv/bin/activate        # Mac/Linux
venv\Scripts\activate           # Windows

# 3. Install dependencies
pip install flask requests beautifulsoup4 lxml pymupdf pillow

# 4. Simpan dependencies
pip freeze > requirements.txt

# 5. Jalankan aplikasi
python run.py
```

---

## 15. Out of Scope / Future Enhancements (v2+)

- Filter pencarian lanjutan (by tahun, fakultas, jenis dokumen).
- Highlight kata kunci pencarian di hasil.
- Riwayat pencarian / bookmark dokumen.
- Statistik dokumen paling banyak dilihat.
- Watermark dinamis (menyertakan nama user/timestamp, bukan teks statis) untuk audit trail jika diperlukan.

---

## 16. Open Questions / Asumsi yang Perlu Dikonfirmasi

1. Apakah field metadata tambahan (penulis, tahun, abstrak) perlu ditampilkan di list hasil pencarian, atau cukup judul saja?
2. Berapa jumlah item per halaman pagination yang diinginkan (default repository asli atau custom, misal 10/20)?
3. Apakah caching boleh disimpan di disk (file/SQLite) atau cukup in-memory (hilang saat server restart)?
4. Selector HTML pasti untuk blok dokumen & teks restricted perlu dikonfirmasi via inspect element langsung ke halaman live (PRD ini berdasarkan tampilan visual, bukan source HTML mentah).

---

*Dokumen ini siap dipakai sebagai acuan implementasi Flask app. Lanjutkan ke tahap coding per modul (`repository_client.py` → `pdf_renderer.py` → routes → templates) sesuai urutan di Bagian 10.*