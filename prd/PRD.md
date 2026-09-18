# PRD — Unhas Repository Search & Secure Viewer

Versi: 1.3 (As-Built)
Tanggal: 18 September 2026
Status: Implemented
Stack: Python + Flask + vanilla JS/HTML/CSS

---

## 1. Ringkasan Produk

Aplikasi web ini membungkus pencarian repository Unhas (repository.unhas.ac.id), menampilkan hasil dengan pagination, halaman detail eprint, dan viewer dokumen read-only berbasis render gambar per halaman.

Fokus produk:
1. Mempermudah pencarian dokumen akademik.
2. Menampilkan status ketersediaan PDF langsung di list hasil.
3. Menyediakan viewer internal tanpa expose URL PDF asli.
4. Mencegah download langsung dan meminimalkan print dari halaman viewer.

---

## 2. Tujuan Produk

1. Pengguna bisa mencari dokumen berdasarkan kata kunci dan/atau tahun.
2. Pengguna melihat status akses dokumen sebelum membuka detail.
3. Dokumen hanya bisa dilihat lewat viewer internal per halaman.
4. Dokumen restricted tetap terlindungi saat URL diakses langsung.

---

## 3. Lingkup

### In-Scope
1. Halaman utama dengan search bar.
2. Search result dengan pagination.
3. Filter tahun pada form pencarian.
4. Halaman detail eprint dengan kartu dokumen Bab 1-2, Dapus, Full Text bila tersedia.
5. Deteksi restricted per dokumen.
6. Viewer dokumen berbasis image per halaman dengan watermark.
7. Proteksi dasar anti-print dan anti-context-menu di viewer.

### Out-of-Scope
1. Login akun staff repository.
2. Upload/edit dokumen.
3. Pencegahan screenshot/screen recording 100%.
4. Native mobile app.

---

## 4. Kondisi Implementasi Saat Ini

1. Search umum menggunakan endpoint simple search Unhas.
2. Filter tahun menggunakan browse by year Unhas (view/year/{tahun}.html), lalu dipaginasi lokal.
3. Item per halaman: 20.
4. Status PDF ditampilkan di list hasil:
   1. PDF tersedia
   2. PDF terbatas
   3. PDF tidak tersedia
   4. Status PDF tidak diketahui
5. Viewer:
   1. Memuat gambar halaman via API internal.
   2. Tidak mengirim URL file PDF asli ke client.
   3. Context menu pada area viewer dinonaktifkan.
   4. Shortcut Ctrl/Cmd+P diblokir di viewer.
   5. Mode print CSS menyembunyikan konten viewer.

---

## 5. User Flow

1. User membuka halaman utama.
2. User mengisi kata kunci, atau memilih tahun, atau keduanya.
3. User submit pencarian.
4. Sistem menampilkan hasil + total + pagination.
5. Tiap item menampilkan metadata dan status PDF.
6. User membuka detail eprint.
7. User memilih dokumen (Bab 1-2/Dapus/Full Text) bila tersedia.
8. Jika restricted, tampil pesan akses terbatas.
9. Jika available, viewer internal menampilkan halaman dokumen sebagai gambar ber-watermark.

---

## 6. Functional Requirements

| ID | Requirement |
|----|-------------|
| FR-1 | Halaman utama menampilkan form pencarian dengan input query dan dropdown tahun. |
| FR-2 | Search menerima kombinasi parameter q, year, page. |
| FR-3 | Jika q kosong dan year kosong, sistem menampilkan pesan validasi. |
| FR-4 | Jika year valid diisi, sistem melakukan browse dokumen berdasarkan tahun, lalu filter query (jika ada). |
| FR-5 | Hasil pencarian menampilkan judul, penulis, tahun, tipe, dan badge status PDF. |
| FR-6 | Pagination ditampilkan untuk total > 1 halaman dan mempertahankan q + year. |
| FR-7 | Halaman detail menampilkan dokumen yang ditemukan saja (Bab 1-2, Dapus, Full Text). |
| FR-8 | Sistem menandai restricted berdasarkan teks pada blok dokumen sumber. |
| FR-9 | Dokumen non-restricted dibuka di endpoint viewer internal. |
| FR-10 | Endpoint image page melakukan re-validasi dokumen dan restricted di server. |
| FR-11 | Viewer menampilkan halaman sebagai gambar ber-watermark, bukan file PDF langsung. |
| FR-12 | Viewer memblokir shortcut print (Ctrl/Cmd+P) dan menyembunyikan konten saat print media. |

---

## 7. Non-Functional Requirements

1. Caching metadata (search/detail/pdf status) dengan TTL sekitar 10 menit.
2. Responsif desktop/mobile untuk halaman utama, hasil, detail, dan viewer.
3. Error handling tidak menyebabkan crash halaman.
4. URL PDF asli tidak tampil pada HTML/JS client.

---

## 8. Business Rules

### 8.1 Aturan Pencarian
1. Query-only: gunakan simple search Unhas.
2. Year-only: gunakan browse tahun Unhas.
3. Query + year: browse tahun lalu filter query lokal pada title/author.

### 8.2 Aturan Status PDF
1. available: ada minimal satu dokumen non-restricted.
2. restricted: dokumen ada, tetapi seluruhnya restricted.
3. unavailable: tidak ada dokumen PDF yang cocok.
4. unknown: gagal ambil detail/status.

### 8.3 Aturan Akses Viewer
1. Jika restricted, API halaman dokumen mengembalikan forbidden.
2. Viewer tidak menyediakan tombol download.
3. Print dibatasi di level shortcut/script/style, namun tetap ada batasan browser-level.

---

## 9. Arsitektur Teknis

### 9.1 Stack
1. Flask
2. requests
3. beautifulsoup4 + lxml
4. PyMuPDF (fitz)
5. Pillow
6. Vanilla JS + CSS

### 9.2 Struktur Proyek (Aktual)

```
aim-repository/
├── run.py
├── requirements.txt
├── unhas_scraper.py
├── app/
│   ├── __init__.py
│   ├── routes.py
│   ├── services/
│   │   ├── cache.py
│   │   ├── pdf_renderer.py
│   │   └── repository_client.py
│   ├── templates/
│   │   ├── base.html
│   │   ├── index.html
│   │   ├── detail.html
│   │   └── viewer.html
│   └── static/
│       ├── css/style.css
│       └── js/
│           ├── search.js
│           └── viewer.js
└── prd/PRD.md
```

---

## 10. API Internal

| Method | Endpoint | Keterangan |
|--------|----------|------------|
| GET | / | Halaman utama pencarian |
| GET | /search?q=&year=&page= | Halaman hasil pencarian |
| GET | /detail/<eprint_id> | Detail dokumen eprint |
| GET | /view/<eprint_id>/<doc_type> | Halaman viewer |
| GET | /api/page-image/<eprint_id>/<doc_type>/<page_num> | Gambar halaman dokumen |

---

## 11. Keamanan & Perlindungan Dokumen

1. URL file PDF asli tidak dikirim ke browser.
2. Viewer hanya menerima gambar halaman dari API internal.
3. Context menu viewer dinonaktifkan.
4. Shortcut print diblokir pada halaman viewer.
5. CSS print menyembunyikan konten viewer saat print.

Catatan: proteksi ini bersifat hardening aplikasi web, bukan DRM absolut.

---

## 12. Error Handling

| Kondisi | Perilaku |
|---------|----------|
| q kosong dan year kosong | Tampilkan pesan validasi |
| Hasil 0 | Tampilkan empty state |
| Detail eprint gagal | Tampilkan error di halaman detail |
| Dokumen restricted di API image | Return 403 |
| Halaman dokumen tidak ada | Return 404 |
| Kegagalan render halaman | Tampilkan pesan gagal muat di viewer |

---

## 13. Setup dan Run

Gunakan environment .venv pada root proyek.

```bash
cd /Users/aim/Coding/python/github-project/aim-repository
./.venv/bin/python -m pip install -r requirements.txt
./.venv/bin/python run.py
```

Aplikasi berjalan di:
1. http://127.0.0.1:5001

---

## 14. Backlog (Next)

1. Optimasi performa status PDF di list (batched atau async lazy enrichment).
2. Filter tambahan (doctype/division/subject).
3. Snapshot test parser HTML sumber untuk antisipasi perubahan struktur Unhas.
4. Audit logging akses viewer untuk analitik internal.