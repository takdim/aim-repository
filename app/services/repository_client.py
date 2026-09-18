import re
import requests
from bs4 import BeautifulSoup
from dataclasses import dataclass, field
from typing import Optional

from .cache import metadata_cache

BASE_URL = "https://repository.unhas.ac.id"
RESULTS_PER_PAGE = 20

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8",
}

# Document type detection patterns — dipakai pada LABEL teks saja, bukan full block text
DOC_PATTERNS = [
    (re.compile(r"bab\s*1[-–\s]?2|bab\s+1\b|bab1", re.I), "bab1_2", "Bab 1-2"),
    (re.compile(r"daftar\s+pustaka|dapus|\bdp\b", re.I), "dapus", "Daftar Pustaka"),
    (re.compile(r"full[\s_]?text|fulltext", re.I), "full_text", "Full Text"),
]


@dataclass
class SearchResultItem:
    eprint_id: str
    title: str
    author: str = ""
    year: str = ""
    item_type: str = ""
    pdf_status: str = "unknown"  # available | restricted | unavailable | unknown


@dataclass
class DocumentInfo:
    doc_type: str       # "bab1_2" | "dapus" | "full_text"
    label: str          # Display label for UI
    file_url: str       # URL asli — TIDAK dikirim ke browser/client
    is_restricted: bool = False


@dataclass
class EprintDetail:
    eprint_id: str
    title: str
    author: str = ""
    year: str = ""
    abstract: str = ""
    documents: list = field(default_factory=list)


class RepositoryClient:
    # ─── Search ──────────────────────────────────────────────────────────────

    def search(self, query: str, page: int = 1) -> tuple[list, dict]:
        """
        Cari di repository Unhas. Return (results, pagination_info).
        Hasil di-cache 10 menit.
        """
        cache_key = f"search:{query.lower()}:{page}"
        cached = metadata_cache.get(cache_key)
        if cached:
            return cached

        offset = (page - 1) * RESULTS_PER_PAGE
        params: dict = {"q": query, "_order": "bytitle", "_action_search": "Search"}
        if offset > 0:
            params["_offset"] = str(offset)

        resp = requests.get(
            f"{BASE_URL}/cgi/search/simple",
            params=params,
            headers=HEADERS,
            timeout=20,
        )
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "lxml")
        results = self._parse_search_results(soup)
        pagination = self._parse_pagination(soup, page)

        result = (results, pagination)
        metadata_cache.set(cache_key, result, ttl=600)
        return result

    def browse_by_year(self, year: int, page: int = 1, query: str = "") -> tuple[list, dict]:
        """
        Browse dokumen berdasarkan tahun dari endpoint /view/year/{year}.html.
        Jika query diisi, hasil difilter pada judul/penulis.
        """
        normalized_query = query.strip().lower()
        cache_key = f"browse_year:{year}:{normalized_query}:{page}"
        cached = metadata_cache.get(cache_key)
        if cached:
            return cached

        all_items = self._get_year_items(year)

        if normalized_query:
            filtered = [
                item for item in all_items
                if normalized_query in item.title.lower() or normalized_query in item.author.lower()
            ]
        else:
            filtered = all_items

        total = len(filtered)
        total_pages = max(1, (total + RESULTS_PER_PAGE - 1) // RESULTS_PER_PAGE)
        safe_page = min(max(1, page), total_pages)
        start = (safe_page - 1) * RESULTS_PER_PAGE
        end = start + RESULTS_PER_PAGE
        paged_items = filtered[start:end]

        pagination = {
            "current": safe_page,
            "total_pages": total_pages,
            "total_results": total,
            "per_page": RESULTS_PER_PAGE,
        }

        result = (paged_items, pagination)
        metadata_cache.set(cache_key, result, ttl=600)
        return result

    def _get_year_items(self, year: int) -> list[SearchResultItem]:
        cache_key = f"year_items:{year}"
        cached = metadata_cache.get(cache_key)
        if cached:
            return cached

        resp = requests.get(
            f"{BASE_URL}/view/year/{year}.html",
            headers=HEADERS,
            timeout=25,
        )
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "lxml")
        items = self._parse_year_browse_results(soup, year)
        metadata_cache.set(cache_key, items, ttl=600)
        return items

    def _parse_year_browse_results(self, soup: BeautifulSoup, year: int) -> list[SearchResultItem]:
        items: list[SearchResultItem] = []
        seen_ids: set[str] = set()

        for a in soup.find_all("a", href=re.compile(r"/id/eprint/\d+/?")):
            href = str(a.get("href", ""))
            m = re.search(r"/id/eprint/(\d+)", href)
            if not m:
                continue
            eprint_id = m.group(1)
            if eprint_id in seen_ids:
                continue

            title = a.get_text(" ", strip=True)
            if not title or len(title) < 5:
                continue

            row = a.find_parent("li") or a.find_parent("p") or a.find_parent("div")
            if row is None:
                continue
            text = row.get_text(" ", strip=True)

            if not re.search(rf"\({year}\)", text):
                continue

            author = ""
            item_type = ""

            author_m = re.match(rf"^(.*?)\s*\({year}\)", text)
            if author_m:
                author = author_m.group(1).strip()[:150]

            type_m = re.search(r"(Skripsi|Thesis|Tesis|Disertasi|D4)\s+thesis", text, re.I)
            if type_m:
                item_type = type_m.group(1).title()

            seen_ids.add(eprint_id)
            items.append(
                SearchResultItem(
                    eprint_id=eprint_id,
                    title=title,
                    author=author,
                    year=str(year),
                    item_type=item_type,
                )
            )

        return items

    def _parse_search_results(self, soup: BeautifulSoup) -> list:
        items: list[SearchResultItem] = []
        seen_ids: set[str] = set()

        for a in soup.find_all("a", href=re.compile(r"/id/eprint/\d+/?")):
            href = str(a["href"])
            m = re.search(r"/id/eprint/(\d+)", href)
            if not m:
                continue
            eprint_id = m.group(1)
            if eprint_id in seen_ids:
                continue

            title = a.get_text(strip=True)
            if not title or len(title) < 5:
                continue

            seen_ids.add(eprint_id)

            author, year, item_type = "", "", ""
            parent = a.find_parent("p") or a.find_parent("li") or a.find_parent("div")
            if parent:
                text = parent.get_text(separator=" ", strip=True)
                year_m = re.search(r"\((\d{4})\)", text)
                if year_m:
                    year = year_m.group(1)

                idx = text.find(title)
                if idx > 0:
                    raw_author = text[:idx].strip()
                    raw_author = re.sub(r"\(\d{4}\)\s*$", "", raw_author).strip()
                    author = raw_author[:100]

                # Item type (Skripsi / Thesis / etc.)
                type_m = re.search(r"(Skripsi|Thesis|Tesis|Disertasi)\s+thesis", text, re.I)
                if type_m:
                    item_type = type_m.group(1).title()

            items.append(
                SearchResultItem(
                    eprint_id=eprint_id,
                    title=title,
                    author=author,
                    year=year,
                    item_type=item_type,
                )
            )

        return items

    def _parse_pagination(self, soup: BeautifulSoup, current_page: int) -> dict:
        total = 0

        # Beberapa halaman EPrints memecah kalimat kontrol hasil ke banyak <span>,
        # jadi parsing harus dilakukan pada teks gabungan, bukan string per node.
        text_blob = " ".join(soup.stripped_strings)

        patterns = [
            r"Displaying\s+results\s+\d+\s+to\s+\d+\s+of\s+([\d,\.]+)",
            r"Results\s+\d+\s+to\s+\d+\s+of\s+([\d,\.]+)",
            r"Menampilkan\s+\d+\s*[\-–]\s*\d+\s+dari\s+([\d,\.]+)",
        ]

        for pattern in patterns:
            m = re.search(pattern, text_blob, re.I)
            if m:
                total = int(re.sub(r"[^\d]", "", m.group(1)))
                break

        total_pages = max(1, (total + RESULTS_PER_PAGE - 1) // RESULTS_PER_PAGE)
        return {
            "current": current_page,
            "total_pages": total_pages,
            "total_results": total,
            "per_page": RESULTS_PER_PAGE,
        }

    # ─── Detail ──────────────────────────────────────────────────────────────

    def get_detail(self, eprint_id: str) -> EprintDetail:
        """
        Ambil detail satu eprint. Return EprintDetail dengan list DocumentInfo.
        Di-cache 10 menit. File URL TIDAK dikirim ke client — hanya ada di cache server.
        """
        cache_key = f"detail:{eprint_id}"
        cached = metadata_cache.get(cache_key)
        if cached:
            return cached

        resp = requests.get(
            f"{BASE_URL}/id/eprint/{eprint_id}/",
            headers=HEADERS,
            timeout=20,
        )
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "lxml")
        detail = self._parse_detail(soup, eprint_id)

        metadata_cache.set(cache_key, detail, ttl=600)
        return detail

    def get_pdf_status(self, eprint_id: str) -> str:
        """
        Ringkas status ketersediaan PDF dari detail dokumen.
        Return: available | restricted | unavailable | unknown
        """
        cache_key = f"pdf_status:{eprint_id}"
        cached = metadata_cache.get(cache_key)
        if cached:
            return cached

        try:
            detail = self.get_detail(eprint_id)
        except Exception:
            return "unknown"

        if not detail.documents:
            status = "unavailable"
        elif any(not doc.is_restricted for doc in detail.documents):
            status = "available"
        else:
            status = "restricted"

        metadata_cache.set(cache_key, status, ttl=600)
        return status

    def _parse_detail(self, soup: BeautifulSoup, eprint_id: str) -> EprintDetail:
        # Title
        title = ""
        h1 = soup.find("h1")
        if h1:
            title = h1.get_text(strip=True)

        # Author & year from citation table
        author, year = self._parse_metadata_table(soup)

        # Abstract
        abstract = ""
        for heading in soup.find_all(["h2", "h3", "h4"]):
            if "abstrak" in heading.get_text(strip=True).lower() or "abstract" in heading.get_text(strip=True).lower():
                sib = heading.find_next_sibling()
                if sib:
                    abstract = sib.get_text(strip=True)[:600]
                break

        documents = self._parse_documents(soup, eprint_id)

        return EprintDetail(
            eprint_id=eprint_id,
            title=title,
            author=author,
            year=year,
            abstract=abstract,
            documents=documents,
        )

    def _parse_metadata_table(self, soup: BeautifulSoup) -> tuple[str, str]:
        author, year = "", ""
        for row in soup.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) < 2:
                continue
            key = cells[0].get_text(strip=True).lower()
            val = cells[1].get_text(strip=True)
            if "creator" in key or "author" in key or "penulis" in key:
                author = val[:150]
            elif "date" in key or "tahun" in key:
                year_m = re.search(r"\d{4}", val)
                if year_m:
                    year = year_m.group()
        return author, year

    def _parse_documents(self, soup: BeautifulSoup, eprint_id: str) -> list:
        """
        Temukan dokumen Bab 1-2, Dapus, Full Text dari halaman detail.
        Deteksi restricted dari teks 'Restricted to Repository staff only'.

        EPrints menyusun setiap file dalam blok div.ep_summary_page_document.
        Kita iterasi tiap blok, ekstrak tipe dokumen + URL + status restricted.
        """
        documents: list[DocumentInfo] = []
        seen_types: set[str] = set()

        # Strategy A: pakai class EPrints standar (paling andal)
        blocks = soup.find_all("div", class_="ep_summary_page_document")

        # Strategy B: fallback jika class berbeda — gunakan anchor thumbnail
        if not blocks:
            blocks = []
            for img in soup.find_all("img", alt=True):
                if "thumbnail" not in str(img.get("alt", "")).lower():
                    continue
                # Naik DOM sampai menemukan blok yang berisi tipe label + download link
                node = img.parent
                for _ in range(10):
                    if node is None:
                        break
                    # Cek ada span.document_format atau teks "Text ("
                    has_label = bool(
                        node.find("span", class_="document_format")
                        or re.search(r"Text\s*\(", node.get_text(), re.I)
                    )
                    has_pdf = bool(node.find("a", href=re.compile(r"\.pdf", re.I)))
                    if has_label and has_pdf:
                        blocks.append(node)
                        break
                    node = node.parent

        for block in blocks:
            # Baca label dari span.document_format jika ada (paling akurat)
            label_el = block.find("span", class_="document_format")
            label_text = label_el.get_text(strip=True) if label_el else block.get_text(separator=" ", strip=True)[:60]

            doc_type, label = self._match_doc_type(label_text)
            if doc_type is None or doc_type in seen_types:
                continue

            pdf_url = self._extract_pdf_url(block, eprint_id)
            if not pdf_url:
                continue

            is_restricted = self._is_restricted(block)
            seen_types.add(doc_type)
            documents.append(DocumentInfo(doc_type=doc_type, label=label,
                                          file_url=pdf_url, is_restricted=is_restricted))

        order_map = {"bab1_2": 0, "dapus": 1, "full_text": 2}
        documents.sort(key=lambda d: order_map.get(d.doc_type, 99))
        return documents

    def _match_doc_type(self, text: str) -> tuple[Optional[str], str]:
        for pattern, doc_type, label in DOC_PATTERNS:
            if pattern.search(text):
                return doc_type, label
        return None, ""

    def _extract_pdf_url(self, block, eprint_id: str) -> Optional[str]:
        """Ambil URL PDF dari blok, prioritaskan /id/eprint/ URL."""
        candidates: list[str] = []
        for a in block.find_all("a", href=re.compile(r"\.pdf", re.I)):
            href = str(a.get("href", ""))
            if f"/{eprint_id}/" not in href and f"/eprint/{eprint_id}" not in href:
                continue
            full = href if href.startswith("http") else BASE_URL + href
            full = full.replace("http://", "https://")
            if "/id/eprint/" in full:
                return full       # prefer canonical URL
            candidates.append(full)
        return candidates[0] if candidates else None

    def _is_restricted(self, block) -> bool:
        text = block.get_text(separator="\n")
        return bool(re.search(r"restricted to repository staff", text, re.I))


# Singleton
repository_client = RepositoryClient()
