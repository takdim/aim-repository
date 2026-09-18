import re
import os
from html import escape
from datetime import date

from flask import (
    Blueprint,
    Response,
    abort,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)

from .services.pdf_renderer import pdf_renderer
from .services.cache import metadata_cache
from .services.repository_client import repository_client
from .services.rta_client import rta_client

main = Blueprint("main", __name__)

ITEMS_PER_PAGE = 20
MIN_YEAR = 1976
COMBINED_FETCH_LIMIT = int(os.environ.get("COMBINED_FETCH_LIMIT", "120"))
SITE_BASE_URL = os.environ.get("SITE_BASE_URL", "http://127.0.0.1:5001").rstrip("/")


# ─── Helpers ─────────────────────────────────────────────────────────────────

def make_pagination_pages(current: int, total: int) -> list:
    """Hasilkan daftar nomor halaman + '...' untuk UI pagination."""
    if total <= 1:
        return []
    if total <= 9:
        return list(range(1, total + 1))
    if current <= 5:
        return list(range(1, 7)) + ["...", total]
    if current >= total - 4:
        return [1, "..."] + list(range(total - 5, total + 1))
    return [1, "...", current - 1, current, current + 1, "...", total]


def _normalize_title_key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


def _sanitize_query_input(text: str) -> str:
    clean = (text or "").strip()
    clean = re.sub(r"^#+\s*", "", clean)
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean


def _short_abstract(text: str, max_len: int = 380) -> str:
    clean = re.sub(r"\s+", " ", (text or "")).strip()
    if len(clean) <= max_len:
        return clean
    return clean[: max_len - 1].rstrip() + "…"


def _absolute_url(path_or_url: str) -> str:
    if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
        return path_or_url
    if not path_or_url.startswith("/"):
        path_or_url = "/" + path_or_url
    return SITE_BASE_URL + path_or_url


def _publication_date(year_text: str) -> str:
    year = (year_text or "").strip()
    return f"{year}/01/01" if re.fullmatch(r"\d{4}", year) else ""


def _find_open_doc_for_pdf(eprint) -> object | None:
    preferred = next((d for d in eprint.documents if d.doc_type == "full_text" and not d.is_restricted), None)
    if preferred is not None:
        return preferred
    return next((d for d in eprint.documents if not d.is_restricted), None)


def _bool_robots_value() -> str:
    return "index,follow" if os.environ.get("ALLOW_ROBOTS_INDEX", "0") == "1" else "noindex,nofollow"


def _collect_repository_items(q: str, year_filter: int | None, limit: int) -> list[dict]:
    items: list[dict] = []
    page = 1
    total_pages = 1

    while len(items) < limit and page <= total_pages:
        if year_filter is not None:
            batch, pagination = repository_client.browse_by_year(year_filter, page, q)
        else:
            batch, pagination = repository_client.search(q, page)

        total_pages = pagination.get("total_pages", 1)
        if not batch:
            break

        for row in batch:
            pdf_status = "unknown"
            try:
                pdf_status = repository_client.get_pdf_status(row.eprint_id)
            except Exception:
                pdf_status = "unknown"

            items.append(
                {
                    "source": "repository",
                    "source_id": row.eprint_id,
                    "eprint_id": row.eprint_id,
                    "title": row.title,
                    "abstract": "",
                    "author": row.author,
                    "year": row.year,
                    "item_type": row.item_type,
                    "pdf_status": pdf_status,
                    "dedupe_key": _normalize_title_key(row.title),
                }
            )

            if len(items) >= limit:
                break

        page += 1

    return items


def _merge_deduplicate(primary: list[dict], secondary: list[dict]) -> list[dict]:
    merged: list[dict] = []
    idx_by_key: dict[str, int] = {}

    for item in primary + secondary:
        key = item.get("dedupe_key") or _normalize_title_key(item.get("title", ""))
        if not key:
            key = f"{item.get('source', 'x')}:{item.get('source_id', '')}:{len(merged)}"

        if key not in idx_by_key:
            idx_by_key[key] = len(merged)
            merged.append(item)
            continue

        existing = merged[idx_by_key[key]]
        # Pertahankan item pertama, tapi lengkapi abstrak jika item duplikat punya abstrak lebih baik.
        if len(item.get("abstract", "")) > len(existing.get("abstract", "")):
            existing["abstract"] = item.get("abstract", "")

    return merged


# ─── Routes ──────────────────────────────────────────────────────────────────

@main.route("/")
def index():
    return render_template(
        "index.html",
        selected_year="",
        year_options=list(range(date.today().year, MIN_YEAR - 1, -1)),
    )


@main.route("/search")
def search():
    q = _sanitize_query_input(request.args.get("q", ""))
    year_raw = request.args.get("year", "").strip()

    selected_year = ""
    year_filter = None
    if year_raw and re.fullmatch(r"\d{4}", year_raw):
        year_candidate = int(year_raw)
        if MIN_YEAR <= year_candidate <= date.today().year:
            year_filter = year_candidate
            selected_year = year_raw

    if not q and year_filter is None:
        return render_template(
            "index.html",
            error="Masukkan kata kunci atau pilih tahun terlebih dahulu.",
            selected_year=selected_year,
            year_options=list(range(date.today().year, MIN_YEAR - 1, -1)),
        )

    try:
        page = max(1, int(request.args.get("page", 1)))
    except (ValueError, TypeError):
        page = 1

    # Cache final combined search result per query+year+page untuk percepat query berulang.
    result_cache_key = f"search:combined:{q.lower()}:{year_filter or ''}:{page}"
    cached_payload = metadata_cache.get(result_cache_key)
    if cached_payload:
        return render_template(
            "index.html",
            query=q,
            selected_year=selected_year,
            year_options=list(range(date.today().year, MIN_YEAR - 1, -1)),
            results=cached_payload["results"],
            pagination=cached_payload["pagination"],
            pagination_pages=cached_payload["pagination_pages"],
        )

    # Fetch bertahap: cukup ambil kelipatan kecil dari page size agar respons tetap cepat.
    fetch_limit = min(COMBINED_FETCH_LIMIT, max(ITEMS_PER_PAGE * 2, page * ITEMS_PER_PAGE + ITEMS_PER_PAGE))

    repo_failed = False
    rta_failed = False

    try:
        repo_items = _collect_repository_items(q, year_filter, fetch_limit)
    except Exception:
        repo_failed = True
        repo_items = []

    try:
        rta_items = rta_client.search_approved(q, year_filter, fetch_limit)
        for item in rta_items:
            item["abstract"] = _short_abstract(item.get("abstract", ""))
    except Exception:
        rta_failed = True
        rta_items = []

    if repo_failed and rta_failed:
        return render_template(
            "index.html",
            query=q,
            error="Gagal mengambil hasil pencarian. Periksa koneksi internet dan coba lagi.",
            selected_year=selected_year,
            year_options=list(range(date.today().year, MIN_YEAR - 1, -1)),
        )

    combined = _merge_deduplicate(rta_items, repo_items)
    total_results = len(combined)
    total_pages = max(1, (total_results + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE)
    safe_page = min(max(1, page), total_pages)
    start = (safe_page - 1) * ITEMS_PER_PAGE
    end = start + ITEMS_PER_PAGE
    results = combined[start:end]

    pagination = {
        "current": safe_page,
        "total_pages": total_pages,
        "total_results": total_results,
        "per_page": ITEMS_PER_PAGE,
    }

    pagination_pages = make_pagination_pages(pagination["current"], pagination["total_pages"])

    metadata_cache.set(
        result_cache_key,
        {
            "results": results,
            "pagination": pagination,
            "pagination_pages": pagination_pages,
        },
        ttl=180,
    )

    return render_template(
        "index.html",
        query=q,
        selected_year=selected_year,
        year_options=list(range(date.today().year, MIN_YEAR - 1, -1)),
        results=results,
        pagination=pagination,
        pagination_pages=pagination_pages,
    )


@main.route("/detail/<eprint_id>")
def detail(eprint_id: str):
    if not eprint_id.isdigit():
        abort(400)

    # Ambil back_url dari query param — validasi hanya boleh path internal
    raw_back = request.args.get("back", "")
    if raw_back.startswith("/search") or raw_back == "/":
        back_url = raw_back
    else:
        back_url = url_for("main.index")

    try:
        eprint = repository_client.get_detail(eprint_id)
    except Exception:
        return render_template(
            "detail.html",
            error="Gagal memuat detail dokumen. Coba lagi.",
            eprint_id=eprint_id,
            back_url=back_url,
        ), 500

    open_doc = _find_open_doc_for_pdf(eprint)
    citation_pdf_url = ""
    if open_doc is not None:
        citation_pdf_url = _absolute_url(
            url_for("main.api_pdf_inline", eprint_id=eprint.eprint_id, doc_type=open_doc.doc_type)
        )

    citation_page_url = _absolute_url(url_for("main.detail", eprint_id=eprint.eprint_id))
    source_uri = f"https://repository.unhas.ac.id/id/eprint/{eprint.eprint_id}"

    return render_template(
        "detail.html",
        eprint=eprint,
        back_url=back_url,
        canonical_url=citation_page_url,
        meta_robots=_bool_robots_value(),
        citation_title=eprint.title,
        citation_author=eprint.author,
        citation_publication_date=_publication_date(eprint.year),
        citation_pdf_url=citation_pdf_url,
        citation_abstract_url=citation_page_url,
        detail_uri=citation_page_url,
        source_uri=source_uri,
    )


@main.route("/detail-rta/<source_id>")
def detail_rta(source_id: str):
    if not source_id.isdigit():
        abort(400)

    raw_back = request.args.get("back", "")
    if raw_back.startswith("/search") or raw_back == "/":
        back_url = raw_back
    else:
        back_url = url_for("main.index")

    detail_data = rta_client.get_detail_by_id(source_id)
    if not detail_data:
        return render_template(
            "rta_detail.html",
            error="Gagal memuat detail RTA. Coba lagi.",
            back_url=back_url,
        ), 500

    citation_page_url = _absolute_url(url_for("main.detail_rta", source_id=source_id))
    return render_template(
        "rta_detail.html",
        item=detail_data,
        back_url=back_url,
        canonical_url=citation_page_url,
        meta_robots=_bool_robots_value(),
        citation_title=detail_data.get("title", ""),
        citation_author=detail_data.get("author", ""),
        citation_publication_date=_publication_date(str(detail_data.get("year", ""))),
        citation_abstract_url=citation_page_url,
        detail_uri=citation_page_url,
        source_uri=detail_data.get("source_url", ""),
    )


@main.route("/view/<eprint_id>/<doc_type>")
def view_pdf(eprint_id: str, doc_type: str):
    if not eprint_id.isdigit():
        abort(400)
    if not re.match(r"^[a-z0-9_]+$", doc_type):
        abort(400)

    # Server-side re-validasi (jangan percaya state dari client)
    try:
        eprint = repository_client.get_detail(eprint_id)
    except Exception:
        return render_template("viewer.html", load_error=True, eprint_id=eprint_id), 500

    doc = next((d for d in eprint.documents if d.doc_type == doc_type), None)
    if doc is None:
        abort(404)

    # Restricted → tampilkan halaman viewer dengan flag restricted (bukan redirect login asli)
    if doc.is_restricted:
        return render_template("viewer.html", eprint=eprint, doc=doc, restricted=True)

    # Open-access → gunakan viewer PDF default browser (inline) dengan watermark.
    return redirect(url_for("main.api_pdf_inline", eprint_id=eprint_id, doc_type=doc_type))


@main.route("/api/pdf-inline/<eprint_id>/<doc_type>")
def api_pdf_inline(eprint_id: str, doc_type: str):
    if not eprint_id.isdigit():
        abort(400)
    if not re.match(r"^[a-z0-9_]+$", doc_type):
        abort(400)

    try:
        eprint = repository_client.get_detail(eprint_id)
    except Exception:
        abort(503)

    doc = next((d for d in eprint.documents if d.doc_type == doc_type), None)
    if doc is None:
        abort(404)
    if doc.is_restricted:
        abort(403)

    try:
        pdf_bytes = pdf_renderer.get_watermarked_pdf(eprint_id, doc_type, doc.file_url)
    except Exception:
        abort(500)

    filename = f"eprint-{eprint_id}-{doc_type}.pdf"
    return Response(
        pdf_bytes,
        mimetype="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="{filename}"',
            "Cache-Control": "private, max-age=300",
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "SAMEORIGIN",
        },
    )


@main.route("/api/page-image/<eprint_id>/<doc_type>/<int:page_num>")
def api_page_image(eprint_id: str, doc_type: str, page_num: int):
    """
    Return JPEG gambar satu halaman PDF dengan watermark.
    URL file PDF asli TIDAK pernah dikirim ke client.
    Header X-Page-Count menyertakan total halaman.
    """
    if not eprint_id.isdigit():
        abort(400)
    if not re.match(r"^[a-z0-9_]+$", doc_type):
        abort(400)
    if page_num < 1:
        abort(400)

    # Re-validasi server-side setiap request
    try:
        eprint = repository_client.get_detail(eprint_id)
    except Exception:
        abort(503)

    doc = next((d for d in eprint.documents if d.doc_type == doc_type), None)
    if doc is None:
        abort(404)
    if doc.is_restricted:
        abort(403)

    try:
        # Render halaman (0-based index)
        img_bytes = pdf_renderer.render_page(eprint_id, doc_type, page_num - 1, doc.file_url)
        page_count = pdf_renderer.get_page_count(eprint_id, doc_type, doc.file_url)
    except ValueError:
        abort(404)
    except Exception:
        abort(500)

    return Response(
        img_bytes,
        mimetype="image/jpeg",
        headers={
            "X-Page-Count": str(page_count),
            "Cache-Control": "no-store, no-cache, must-revalidate",
            "Pragma": "no-cache",
            "X-Content-Type-Options": "nosniff",
            # Cegah embedding di frame lain
            "X-Frame-Options": "SAMEORIGIN",
        },
    )


@main.route("/api/rta/login-check", methods=["GET", "POST"])
def rta_login_check():
    """Validasi kredensial login RTA dari .env (tanpa membocorkan nilai rahasia)."""
    result = rta_client.login_check()
    status_code = 200 if result.get("ok") else 400
    return jsonify(result), status_code


@main.route("/robots.txt")
def robots_txt():
    if os.environ.get("ALLOW_ROBOTS_INDEX", "0") == "1":
        content = (
            "User-agent: *\n"
            "Allow: /\n"
            "Disallow: /api/rta/\n"
            "Disallow: /search\n"
            f"Sitemap: {_absolute_url('/sitemap.xml')}\n"
        )
    else:
        content = "User-agent: *\nDisallow: /\n"

    return Response(content, mimetype="text/plain; charset=utf-8")


@main.route("/sitemap.xml")
def sitemap_xml():
    cache_key = "seo:sitemap:xml"
    cached = metadata_cache.get(cache_key)
    if cached:
        return Response(cached, mimetype="application/xml; charset=utf-8")

    urls: list[str] = [_absolute_url(url_for("main.index"))]

    enable_sitemap = os.environ.get("ENABLE_SITEMAP", "0") == "1"
    sitemap_limit = int(os.environ.get("SITEMAP_LIMIT", "200"))

    if enable_sitemap:
        current_year = date.today().year
        collected = 0
        page = 1
        total_pages = 1
        while collected < sitemap_limit and page <= total_pages:
            results, pagination = repository_client.browse_by_year(current_year, page, "")
            total_pages = pagination.get("total_pages", 1)
            for item in results:
                urls.append(_absolute_url(url_for("main.detail", eprint_id=item.eprint_id)))
                collected += 1
                if collected >= sitemap_limit:
                    break
            if not results:
                break
            page += 1

        rta_items = rta_client.search_approved("", current_year, min(120, sitemap_limit))
        for item in rta_items:
            source_id = str(item.get("source_id", "")).strip()
            if source_id.isdigit():
                urls.append(_absolute_url(url_for("main.detail_rta", source_id=source_id)))

    seen = set()
    unique_urls = []
    for url in urls:
        if url in seen:
            continue
        seen.add(url)
        unique_urls.append(url)

    xml_parts = ['<?xml version="1.0" encoding="UTF-8"?>']
    xml_parts.append('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">')
    for url in unique_urls:
        xml_parts.append("  <url>")
        xml_parts.append(f"    <loc>{escape(url)}</loc>")
        xml_parts.append("  </url>")
    xml_parts.append("</urlset>")

    xml = "\n".join(xml_parts)
    metadata_cache.set(cache_key, xml, ttl=900)
    return Response(xml, mimetype="application/xml; charset=utf-8")
