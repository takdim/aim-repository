import os
import re
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from .cache import metadata_cache


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
        self.max_detail_scan_items = int(os.environ.get("RTA_MAX_DETAIL_SCAN_ITEMS", "60"))

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
                        "final_url": final_url,
                    }

                return {
                    "ok": True,
                    "message": "Login RTA berhasil.",
                    "final_url": final_url,
                }
        except requests.RequestException as exc:
            return {
                "ok": False,
                "message": f"Gagal koneksi ke RTA: {exc}",
            }

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

        try:
            with requests.Session() as session:
                self._login(session)
                # search_values di RTA cenderung untuk NIM/Nama, jadi query judul diproses lokal.
                if tokens:
                    scan_limit = min(self.max_scan_items, max(max_items * 3, 120))
                    detail_limit = min(self.max_detail_scan_items, max(max_items, 30))
                else:
                    scan_limit = min(self.max_scan_items, max(max_items * 2, 80))
                    detail_limit = min(self.max_detail_scan_items, max(max_items // 2, 20))

                rows = self._fetch_yajra_rows(session, "", year_value, scan_limit)
                items: list[dict] = []
                detail_candidates: list[dict] = []

                # Tahap 1: filter cepat tanpa fetch detail, dari data row yajra langsung.
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

                # Tahap 2: fetch detail terbatas untuk kandidat yang belum terdeteksi di tahap cepat.
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
                            haystack = _normalize_search_text(" ".join(
                                [
                                    row_item.get("title", ""),
                                    row_item.get("abstract", ""),
                                    row_item.get("author", ""),
                                    row_item.get("item_type", ""),
                                ]
                            ))
                            if not all(tok in haystack for tok in tokens):
                                continue

                        items.append(row_item)
                        if len(items) >= max_items:
                            break

                metadata_cache.set(cache_key, items, ttl=600)
                return items
        except requests.RequestException:
            return []

    def get_detail_by_id(self, source_id: str) -> dict | None:
        source_id = (source_id or "").strip()
        if not source_id.isdigit() or not self.is_configured():
            return None

        cache_key = f"rta:detail-by-id:{source_id}"
        cached = metadata_cache.get(cache_key)
        if cached:
            return cached

        detail_url = f"https://regtugasakhir.unhas.ac.id/admin/dashboard/tugas_akhirs/detil/{source_id}"

        try:
            with requests.Session() as session:
                self._login(session)
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

        return {
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

    def _fetch_detail_summary(self, session: requests.Session, detail_url: str) -> dict:
        cache_key = f"rta:detil:{detail_url}"
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

        data = {"title": title, "abstract": abstract}
        metadata_cache.set(cache_key, data, ttl=1800)
        return data


rta_client = RTAClient()