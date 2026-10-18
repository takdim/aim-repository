import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from .cache import metadata_cache, pdf_bytes_cache

RTA_ALLOWED_HOST = "regtugasakhir.unhas.ac.id"
MAX_PDF_BYTES = int(os.environ.get("MAX_PDF_BYTES", str(50 * 1024 * 1024)))
APPROVED_IDS_KEY = "rta:approved_ids"
APPROVED_SCAN_MARKER = "rta:approved_scan_done"


def _is_allowed_rta_url(url: str) -> bool:
    """Hanya izinkan HTTPS ke host RTA resmi (cegah SSRF & kebocoran sesi login)."""
    try:
        parsed = urlparse(url or "")
    except ValueError:
        return False
    return parsed.scheme == "https" and (parsed.hostname or "").lower() == RTA_ALLOWED_HOST


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "")).strip()


def _normalize_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").lower())


def _normalize_search_text(value: str) -> str:
    text = (value or "").lower()
    text = re.sub(r"[#*_`]+", " ", text)
    text = re.sub(r"[^a-z0-9\-\s]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _query_tokens(value: str) -> list[str]:
    text = _normalize_search_text(value)
    if not text:
        return []
    return [tok for tok in text.split(" ") if tok]


def _looks_like_placeholder_title(value: str) -> bool:
    text = _clean_text(value).lower()
    if not text:
        return True
    if text in {"-", "--", "n/a", "na"}:
        return True
    if text.startswith("##"):
        return True
    if text in {"(indonesia)", "(inggris)", "judul", "judul (indonesia)", "judul (inggris)"}:
        return True
    return False


def _attachment_label_from_text(text: str) -> str:
    t = _clean_text(text).lower()
    if not t:
        return "Dokumen"

    if re.search(r"bab\s*(i|1)\s*[-–/]?\s*(ii|2)|bab\s*1\s*(dan|&)\s*2", t):
        return "Bab 1-2"
    if "daftar pustaka" in t or "dapus" in t:
        return "Daftar Pustaka"
    if "full text" in t or "fulltext" in t:
        return "Full Text"
    return "Dokumen"


def _doc_kind_from_label(label: str) -> str:
    text = _clean_text(label).lower()
    if text == "bab 1-2":
        return "bab1_2"
    if text == "daftar pustaka":
        return "dapus"
    if text == "full text":
        return "full_text"
    return "dokumen"


def _normalize_author(value: str) -> str:
    text = _clean_text(value)
    if not text:
        return ""

    text = re.sub(r"(?i)^nama\s+lengkap\s*:\s*", "", text).strip()
    text = re.sub(r"(?i)^lengkap\s*:\s*", "", text).strip()
    text = re.sub(r"(?i)^nama\s*(mahasiswa)?\s*:\s*", "", text).strip()
    text = re.split(r"(?i)\b(nomor induk|nim|angkatan|handphone|no\.?\s*hp|email)\b", text, maxsplit=1)[0].strip()
    return text


class RTAClient:
    def __init__(self) -> None:
        self.login_url = (
            os.environ.get("RTA_LOGIN_URL", "").strip()
            or os.environ.get("RTA_BASE_URL", "").strip()
        )
        self.email = (
            os.environ.get("RTA_EMAIL", "").strip()
            or os.environ.get("RTA_USERNAME", "").strip()
        )
        self.password = os.environ.get("RTA_PASSWORD", "").strip()
        self.email_field_name = os.environ.get("RTA_EMAIL_FIELD_NAME", "email").strip() or "email"
        self.password_field_name = os.environ.get("RTA_PASSWORD_FIELD_NAME", "password").strip() or "password"
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_\-]*", self.email_field_name):
            self.email_field_name = "email"
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_\-]*", self.password_field_name):
            self.password_field_name = "password"
        self.verify_ssl = os.environ.get("RTA_VERIFY_SSL", "1") == "1"
        self.timeout = int(os.environ.get("RTA_REQUEST_TIMEOUT_SECONDS", "20"))
        self.max_items = int(os.environ.get("RTA_MAX_ITEMS", "120"))
        self.max_scan_items = int(os.environ.get("RTA_MAX_SCAN_ITEMS", "280"))
        # Pencarian judul tanpa filter tahun harus memindai lebih dalam agar data lama ikut terjangkau.
        self.max_scan_items_no_year = int(os.environ.get("RTA_MAX_SCAN_ITEMS_NO_YEAR", "5000"))
        self.max_detail_scan_items = int(os.environ.get("RTA_MAX_DETAIL_SCAN_ITEMS", "60"))

        # Indeks judul lokal: endpoint daftar RTA tidak menyertakan judul, jadi judul
        # harus diambil dari halaman detail. Dilakukan sekali di background lalu disimpan ke disk.
        self.index_max_rows = int(os.environ.get("RTA_INDEX_MAX_ROWS", "100000"))
        self.index_workers = int(os.environ.get("RTA_INDEX_WORKERS", "6"))
        self.index_refresh_seconds = int(os.environ.get("RTA_INDEX_REFRESH_SECONDS", str(24 * 3600)))
        self.index_path = Path(__file__).resolve().parents[2] / "instance" / "rta_title_index.json"
        self._index: dict[str, dict] = {}
        self._index_complete_at = 0.0
        self._index_lock = threading.Lock()
        self._indexer_thread: threading.Thread | None = None
        self._load_index()

        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8",
            "Referer": self.login_url,
        }

    def is_configured(self) -> bool:
        return bool(self.login_url and self.email and self.password)

    # ─── Indeks judul lokal ──────────────────────────────────────────────────

    def _load_index(self) -> None:
        try:
            data = json.loads(self.index_path.read_text(encoding="utf-8"))
            self._index = dict(data.get("items", {}))
            self._index_complete_at = float(data.get("complete_at", 0) or 0)
        except (OSError, ValueError):
            self._index = {}
            self._index_complete_at = 0.0

    def _save_index(self) -> None:
        try:
            self.index_path.parent.mkdir(parents=True, exist_ok=True)
            with self._index_lock:
                payload = {"complete_at": self._index_complete_at, "items": dict(self._index)}
            tmp = self.index_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.index_path)
        except OSError:
            pass

    def index_status(self) -> dict:
        return {
            "entries": len(self._index),
            "complete_at": self._index_complete_at,
            "running": bool(self._indexer_thread and self._indexer_thread.is_alive()),
        }

    def ensure_indexer(self) -> None:
        """Jalankan crawler indeks di background jika belum ada/sudah kedaluwarsa."""
        if not self.is_configured():
            return
        fresh = self._index_complete_at and (time.time() - self._index_complete_at) < self.index_refresh_seconds
        if fresh:
            return
        with self._index_lock:
            if self._indexer_thread and self._indexer_thread.is_alive():
                return
            self._indexer_thread = threading.Thread(target=self._run_indexer, name="rta-indexer", daemon=True)
            self._indexer_thread.start()

    def _run_indexer(self) -> None:
        try:
            with requests.Session() as session:
                self._login(session)
                rows = self._fetch_yajra_rows(session, "", "", self.index_max_rows)
        except requests.RequestException:
            return

        pending = [r for r in rows if str(r.get("id", "")).strip() not in self._index]
        local = threading.local()
        failures = 0
        done = 0

        def work(row: dict) -> bool:
            sess = getattr(local, "session", None)
            if sess is None:
                sess = requests.Session()
                self._login(sess)
                local.session = sess
            row_id = str(row.get("id", "")).strip()
            try:
                item = self._row_to_item(sess, row, fetch_detail=True)
            except requests.RequestException:
                return False
            with self._index_lock:
                # Judul placeholder disimpan sebagai penanda kosong agar tidak diambil ulang.
                self._index[row_id] = item or {"source_id": row_id, "title": ""}
            return True

        with ThreadPoolExecutor(max_workers=max(1, self.index_workers)) as pool:
            for ok in pool.map(work, pending):
                done += 1
                if not ok:
                    failures += 1
                if done % 100 == 0:
                    self._save_index()

        if failures == 0:
            self._index_complete_at = time.time()
        self._save_index()

    def _search_index(self, tokens: list[str], year_value: str, limit: int) -> list[dict]:
        with self._index_lock:
            entries = list(self._index.values())
        matches: list[dict] = []
        for item in entries:
            title = item.get("title", "")
            if not title:
                continue
            if year_value and str(item.get("year", "")) != year_value:
                continue
            haystack = _normalize_search_text(" ".join([
                title,
                item.get("abstract", ""),
                item.get("author", ""),
                item.get("item_type", ""),
            ]))
            if all(tok in haystack for tok in tokens):
                matches.append(dict(item))
        matches.sort(key=lambda i: str(i.get("year", "")), reverse=True)
        return matches[:limit]

    def login_check(self) -> dict:
        if not self.is_configured():
            return {
                "ok": False,
                "message": "Konfigurasi RTA belum lengkap (URL/email/password).",
            }

        try:
            with requests.Session() as session:
                response = self._login(session)
                response.raise_for_status()

                final_url = response.url
                final_text = response.text.lower()
                login_path = urlparse(self.login_url).path.lower()
                final_path = urlparse(final_url).path.lower()

                still_login_form = (
                    'name="email"' in final_text
                    and 'name="password"' in final_text
                    and "masuk" in final_text
                )
                still_on_login_path = bool(login_path) and final_path == login_path

                if still_login_form or still_on_login_path:
                    return {
                        "ok": False,
                        "message": "Login RTA gagal. Cek email/password atau kebutuhan token tambahan.",
                    }

                return {
                    "ok": True,
                    "message": "Login RTA berhasil.",
                }
        except requests.RequestException:
            return {
                "ok": False,
                "message": "Gagal koneksi ke RTA.",
            }

    KNOWN_RTA_YEARS = [2026, 2025, 2024, 2023, 2022, 2021, 2020]

    def search_approved(self, query: str = "", year: int | None = None, limit: int | None = None) -> list[dict]:
        if not self.is_configured():
            return []

        max_items = min(limit or self.max_items, self.max_items)
        query_text = query.strip()
        tokens = _query_tokens(query_text)
        year_value = str(year) if year else ""
        cache_key = f"rta:approved:{_normalize_search_text(query_text)}:{year_value}:{max_items}"
        cached = metadata_cache.get(cache_key)
        if cached:
            return cached

        # 1. Cek indeks judul lokal (instan < 1ms jika judul sudah terindeks)
        if tokens:
            index_matches = self._search_index(tokens, year_value, max_items)
            if index_matches and (self._index_complete_at or len(index_matches) >= max_items or not year_value):
                metadata_cache.set(cache_key, index_matches, ttl=300)
                return index_matches

        # 2. Jika tahun tidak dispesifikasi (year=None), RTA server secara default
        # HANYA mengembalikan data tahun aktif (2026) dan mengabaikan 2020-2025.
        # Oleh karena itu, lakukan pencarian lintas tahun RTA secara paralel.
        if year is None and tokens:
            items: list[dict] = list(self._search_index(tokens, "", max_items))
            known_ids = {str(it.get("source_id", "")) for it in items}

            def _search_year(y: int) -> list[dict]:
                try:
                    return self._search_approved_for_year(query_text, tokens, str(y), max_items)
                except Exception:
                    return []

            with ThreadPoolExecutor(max_workers=min(len(self.KNOWN_RTA_YEARS), 6)) as pool:
                for year_results in pool.map(_search_year, self.KNOWN_RTA_YEARS):
                    for it in year_results:
                        sid = str(it.get("source_id", ""))
                        if sid not in known_ids:
                            known_ids.add(sid)
                            items.append(it)
                            if len(items) >= max_items:
                                break
                    if len(items) >= max_items:
                        break

            self._save_index()
            metadata_cache.set(cache_key, items, ttl=600)
            return items

        # 3. Pencarian untuk satu tahun tertentu (atau query tanpa token)
        items = self._search_approved_for_year(query_text, tokens, year_value, max_items)
        self._save_index()
        metadata_cache.set(cache_key, items, ttl=600)
        return items

    def _search_approved_for_year(
        self, query_text: str, tokens: list[str], year_value: str, max_items: int
    ) -> list[dict]:
        try:
            with requests.Session() as session:
                self._login(session)
                if tokens:
                    scan_limit = min(self.max_scan_items, max(max_items * 3, 120))
                    detail_limit = min(self.max_detail_scan_items, max(max_items, 30))
                else:
                    scan_limit = min(self.max_scan_items, max(max_items * 2, 80))
                    detail_limit = min(self.max_detail_scan_items, max(max_items // 2, 20))

                rows_cache_key = f"rta:rows:{year_value}:{scan_limit}"
                rows = metadata_cache.get(rows_cache_key)
                if rows is None:
                    rows = self._fetch_yajra_rows(session, "", year_value, scan_limit)
                    if tokens:
                        metadata_cache.set(rows_cache_key, rows, ttl=600)

                items: list[dict] = []
                detail_candidates: list[dict] = []

                for row in rows:
                    title_raw = _clean_text(str(row.get("judul_id") or row.get("judul_en") or ""))
                    quick_text = _normalize_search_text(" ".join([
                        title_raw,
                        _clean_text(str(row.get("show_name") or "")),
                        _clean_text(str(row.get("show_prodi") or "")),
                        _clean_text(str(row.get("year_wisuda") or "")),
                    ]))

                    if not tokens:
                        row_item = self._row_to_item(session, row, fetch_detail=False)
                        if row_item:
                            items.append(row_item)
                    else:
                        if title_raw and not _looks_like_placeholder_title(title_raw) and all(tok in quick_text for tok in tokens):
                            row_item = self._row_to_item(session, row, fetch_detail=False)
                            if row_item:
                                items.append(row_item)
                        else:
                            detail_candidates.append(row)

                    if len(items) >= max_items:
                        break

                if len(items) < max_items:
                    detail_count = 0
                    for row in detail_candidates:
                        if detail_count >= detail_limit:
                            break

                        row_item = self._row_to_item(session, row, fetch_detail=True)
                        detail_count += 1
                        if not row_item:
                            continue

                        if tokens:
                            haystack = _normalize_search_text(" ".join([
                                row_item.get("title", ""),
                                row_item.get("abstract", ""),
                                row_item.get("author", ""),
                                row_item.get("item_type", ""),
                            ]))
                            if not all(tok in haystack for tok in tokens):
                                continue

                        items.append(row_item)
                        if len(items) >= max_items:
                            break

                return items
        except requests.RequestException:
            return []

    def get_detail_by_id(self, source_id: str) -> dict | None:
        source_id = (source_id or "").strip()
        if not source_id.isdigit() or not self.is_configured():
            return None

        cache_key = f"rta:detail-by-id:v3:{source_id}"
        cached = metadata_cache.get(cache_key)
        if cached:
            return cached

        detail_url = f"https://regtugasakhir.unhas.ac.id/admin/dashboard/tugas_akhirs/detil/{source_id}"

        try:
            with requests.Session() as session:
                self._login(session)
                # Hanya dokumen yang berstatus approved yang boleh ditampilkan (cegah enumerasi ID).
                if not self._is_approved(session, source_id):
                    return None
                summary = self._fetch_detail_summary(session, detail_url)
                title = _clean_text(summary.get("title", ""))
                abstract = _clean_text(summary.get("abstract", ""))

                if _looks_like_placeholder_title(title):
                    return None

                result = {
                    "source": "rta",
                    "source_id": source_id,
                    "source_url": detail_url,
                    "title": title,
                    "abstract": abstract,
                    "author": _clean_text(summary.get("author", "")),
                    "year": _clean_text(summary.get("year", "")),
                    "item_type": _clean_text(summary.get("item_type", "")),
                    "documents": summary.get("documents", []),
                }
                metadata_cache.set(cache_key, result, ttl=900)
                return result
        except requests.RequestException:
            return None

    def _login(self, session: requests.Session) -> requests.Response:
        login_page = session.get(
            self.login_url,
            headers=self.headers,
            timeout=self.timeout,
            verify=self.verify_ssl,
        )
        login_page.raise_for_status()

        soup = BeautifulSoup(login_page.text, "lxml")
        form = soup.find("form")
        action_url = self.login_url
        payload: dict[str, str] = {}

        if form is not None:
            action = form.get("action")
            if action:
                action_url = urljoin(self.login_url, action)

            for field in form.find_all("input"):
                name = str(field.get("name", "")).strip()
                if not name:
                    continue
                field_type = str(field.get("type", "text")).lower()
                if field_type in {"hidden", "submit"}:
                    payload[name] = str(field.get("value", ""))

        payload[self.email_field_name] = self.email
        payload[self.password_field_name] = self.password
        payload.setdefault("email", self.email)
        payload.setdefault("password", self.password)

        return session.post(
            action_url,
            data=payload,
            headers=self.headers,
            timeout=self.timeout,
            verify=self.verify_ssl,
            allow_redirects=True,
        )

    def _remember_approved(self, rows: list[dict]) -> None:
        ids = set(metadata_cache.get(APPROVED_IDS_KEY) or set())
        for row in rows:
            row_id = str(row.get("id", "")).strip()
            if row_id.isdigit():
                ids.add(row_id)
        metadata_cache.set(APPROVED_IDS_KEY, ids, ttl=1800)

    def _is_approved(self, session: requests.Session, source_id: str) -> bool:
        ids = metadata_cache.get(APPROVED_IDS_KEY) or set()
        if source_id in ids:
            return True
        # Hindari scan ulang ke RTA setiap kali ID asing diminta.
        if metadata_cache.get(APPROVED_SCAN_MARKER):
            return False
        self._fetch_yajra_rows(session, "", "", self.max_scan_items)
        metadata_cache.set(APPROVED_SCAN_MARKER, True, ttl=120)
        ids = metadata_cache.get(APPROVED_IDS_KEY) or set()
        return source_id in ids

    def fetch_attachment_pdf(self, file_url: str) -> bytes:
        if not self.is_configured():
            raise requests.RequestException("RTA client belum terkonfigurasi")
        if not _is_allowed_rta_url(file_url):
            raise requests.RequestException("URL lampiran tidak diizinkan")

        cache_key = f"rta:pdf-src:{file_url}"
        cached = pdf_bytes_cache.get(cache_key)
        if cached:
            return cached

        with requests.Session() as session:
            self._login(session)
            with session.get(
                file_url,
                headers=self.headers,
                timeout=self.timeout,
                verify=self.verify_ssl,
                allow_redirects=True,
                stream=True,
            ) as resp:
                resp.raise_for_status()
                if not _is_allowed_rta_url(resp.url):
                    raise requests.RequestException("Redirect ke host tidak diizinkan")

                content_type = str(resp.headers.get("Content-Type", "")).lower()
                if "application/pdf" not in content_type and not file_url.lower().endswith(".pdf"):
                    raise requests.RequestException("Lampiran RTA bukan PDF")

                declared = int(resp.headers.get("Content-Length") or 0)
                if declared > MAX_PDF_BYTES:
                    raise requests.RequestException("Lampiran terlalu besar")

                chunks: list[bytes] = []
                total = 0
                for chunk in resp.iter_content(chunk_size=65536):
                    total += len(chunk)
                    if total > MAX_PDF_BYTES:
                        raise requests.RequestException("Lampiran terlalu besar")
                    chunks.append(chunk)

            data = b"".join(chunks)
            pdf_bytes_cache.set(cache_key, data, ttl=300)
            return data

    def _fetch_yajra_rows(
        self,
        session: requests.Session,
        query: str,
        year_value: str,
        limit: int,
    ) -> list[dict]:
        rows: list[dict] = []
        start = 0
        draw = 1
        length = min(50, max(10, limit))

        while len(rows) < limit:
            params = {
                "draw": str(draw),
                "start": str(start),
                "length": str(length),
                "search[value]": "",
                "search[regex]": "false",
                "current_approval_status_id": "2",
                "year_wisuda": year_value,
                "search_values": query,
                "jenis_berkas_id": "",
                "current_approver_id": "",
                "periode_wisuda_id": "",
                "is_publicable": "",
                "jenjang_pendidikan_id": "",
                "fakultas_id": "",
                "is_resubmit": "",
                "is_old_data": "",
            }

            resp = session.get(
                "https://regtugasakhir.unhas.ac.id/admin/dashboard/tugas_akhirs/yajra",
                params=params,
                headers=self.headers,
                timeout=self.timeout,
                verify=self.verify_ssl,
            )
            resp.raise_for_status()

            payload = resp.json()
            data = payload.get("data", []) or []
            if not data:
                break

            rows.extend(data)
            self._remember_approved(data)
            start += len(data)
            draw += 1

            total = int(payload.get("recordsFiltered") or len(rows))
            if start >= total:
                break

        return rows[:limit]

    def _row_to_item(self, session: requests.Session, row: dict, fetch_detail: bool = True) -> dict | None:
        row_id = str(row.get("id", "")).strip()
        action_html = str(row.get("action", ""))

        detail_url = ""
        m = re.search(r"href='([^']*/tugas_akhirs/detil/\d+)'", action_html)
        if m:
            detail_url = m.group(1)
        elif row_id.isdigit():
            detail_url = f"https://regtugasakhir.unhas.ac.id/admin/dashboard/tugas_akhirs/detil/{row_id}"

        if detail_url and not _is_allowed_rta_url(detail_url):
            detail_url = ""

        title = _clean_text(str(row.get("judul_id") or row.get("judul_en") or ""))
        abstract = _clean_text(str(row.get("abstrak") or ""))

        if fetch_detail and detail_url and (_looks_like_placeholder_title(title) or not abstract):
            detail_data = self._fetch_detail_summary(session, detail_url)
            if detail_data:
                if _looks_like_placeholder_title(title):
                    title = detail_data.get("title", "")
                abstract = abstract or detail_data.get("abstract", "")

        if _looks_like_placeholder_title(title):
            return None

        item = {
            "source": "rta",
            "source_id": row_id,
            "source_url": detail_url,
            "title": title,
            "abstract": abstract,
            "author": _clean_text(str(row.get("show_name") or "")),
            "year": _clean_text(str(row.get("year_wisuda") or "")),
            "item_type": _clean_text(str(row.get("show_prodi") or "")),
            "dedupe_key": _normalize_key(title),
        }
        if title and not _looks_like_placeholder_title(title):
            with self._index_lock:
                self._index[row_id] = item
        return item

    def _fetch_detail_summary(self, session: requests.Session, detail_url: str) -> dict:
        cache_key = f"rta:detil:v3:{detail_url}"
        cached = metadata_cache.get(cache_key)
        if cached:
            return cached

        resp = session.get(
            detail_url,
            headers=self.headers,
            timeout=self.timeout,
            verify=self.verify_ssl,
        )
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "lxml")
        title = ""
        abstract = ""
        author = ""
        year = ""
        item_type = ""

        # Pola paling stabil: span id="item-*" dipasangkan dengan strong.text-primary terdekat sebelumnya.
        for span in soup.select("span[id^='item-']"):
            value = _clean_text(span.get_text(" ", strip=True))
            if not value:
                continue

            label_node = span.find_previous("strong", class_="text-primary")
            label = _clean_text(label_node.get_text(" ", strip=True)).lower() if label_node else ""

            if "judul" in label and not title and len(value) > 10:
                title = value

            if ("abstrak" in label or "abstract" in label) and not abstract and len(value) > 20:
                abstract = value

        # Fallback jika struktur span tidak ditemukan.
        if not title or not abstract:
            for strong in soup.select("strong.text-primary"):
                label = _clean_text(strong.get_text(" ", strip=True)).lower()
                container = strong.find_parent("div") or strong.find_parent("td") or strong.parent
                if container is None:
                    continue
                content = _clean_text(container.get_text(" ", strip=True))

                if "judul" in label and not title:
                    content = re.sub(r"(?i)^judul\s*(indonesia|inggris)?\s*", "", content).strip()
                    if len(content) > 10:
                        title = content

                if ("abstrak" in label or "abstract" in label) and not abstract:
                    content = re.sub(r"(?i)^abstrak\s*", "", content).strip()
                    if len(content) > 20:
                        abstract = content

        if not title:
            heading = soup.find(["h1", "h2", "h3"])
            if heading:
                title = _clean_text(heading.get_text(" ", strip=True))

        # Ekstraksi metadata tambahan (penulis/tahun/prodi) dari label terdekat.
        for span in soup.select("span[id^='item-']"):
            value = _clean_text(span.get_text(" ", strip=True))
            if not value:
                continue

            label_node = span.find_previous("strong", class_="text-primary")
            label = _clean_text(label_node.get_text(" ", strip=True)).lower() if label_node else ""

            if not author and ("nama" in label or "mahasiswa" in label):
                author = _normalize_author(value)

            if not item_type and ("prodi" in label or "program studi" in label):
                item_type = value

            if not year and ("tahun" in label or "wisuda" in label):
                m_year = re.search(r"\b(19|20)\d{2}\b", value)
                if m_year:
                    year = m_year.group(0)

        # Fallback metadata jika pola utama tidak tersedia.
        if not author or not item_type or not year:
            for strong in soup.select("strong.text-primary"):
                label = _clean_text(strong.get_text(" ", strip=True)).lower()
                container = strong.find_parent("div") or strong.find_parent("td") or strong.parent
                if container is None:
                    continue
                content = _clean_text(container.get_text(" ", strip=True))

                if not author and ("nama" in label or "mahasiswa" in label):
                    author = _normalize_author(content)

                if not item_type and ("prodi" in label or "program studi" in label):
                    item_type = re.sub(r"(?i)^(prodi|program studi)\s*", "", content).strip()

                if not year and ("tahun" in label or "wisuda" in label):
                    m_year = re.search(r"\b(19|20)\d{2}\b", content)
                    if m_year:
                        year = m_year.group(0)

        documents: list[dict] = []
        seen_urls: set[str] = set()

        for a in soup.find_all("a", href=True):
            href = str(a.get("href", "")).strip()
            if not href:
                continue
            if not re.search(r"/attachments/\d+/show/.*\.pdf", href, re.I):
                continue

            url = href
            if not url.startswith("http"):
                url = urljoin("https://regtugasakhir.unhas.ac.id", url)
            if not _is_allowed_rta_url(url):
                continue

            if url in seen_urls:
                continue

            context_parts: list[str] = [_clean_text(a.get_text(" ", strip=True))]
            node = a
            for _ in range(6):
                node = node.parent
                if node is None:
                    break
                context_parts.append(_clean_text(node.get_text(" ", strip=True)))

            label = "Dokumen"
            for part in context_parts:
                candidate = _attachment_label_from_text(part)
                if candidate != "Dokumen":
                    label = candidate
                    break

            documents.append(
                {
                    "label": label,
                    "kind": _doc_kind_from_label(label),
                    "url": url,
                }
            )
            seen_urls.add(url)

        # Prioritaskan urutan label agar Bab 1-2 muncul lebih dulu jika ada.
        order = {"Bab 1-2": 0, "Daftar Pustaka": 1, "Full Text": 2, "Dokumen": 3}
        documents.sort(key=lambda d: (order.get(d.get("label", "Dokumen"), 99), d.get("label", ""), d.get("url", "")))

        # Tampilkan satu item per label agar UI tidak menampilkan tipe dokumen yang sama berulang.
        unique_by_label: list[dict] = []
        seen_labels: set[str] = set()
        for doc in documents:
            label = str(doc.get("label", "Dokumen")).strip() or "Dokumen"
            if label in seen_labels:
                continue
            seen_labels.add(label)
            unique_by_label.append(doc)
        documents = unique_by_label

        data = {
            "title": title,
            "abstract": abstract,
            "author": author,
            "year": year,
            "item_type": item_type,
            "documents": documents,
        }
        metadata_cache.set(cache_key, data, ttl=1800)
        return data


rta_client = RTAClient()