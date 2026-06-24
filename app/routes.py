import re

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
from .services.repository_client import repository_client

main = Blueprint("main", __name__)

ITEMS_PER_PAGE = 20


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


# ─── Routes ──────────────────────────────────────────────────────────────────

@main.route("/")
def index():
    return render_template("index.html")


@main.route("/search")
def search():
    q = request.args.get("q", "").strip()
    if not q:
        return render_template("index.html", error="Masukkan kata kunci pencarian terlebih dahulu.")

    try:
        page = max(1, int(request.args.get("page", 1)))
    except (ValueError, TypeError):
        page = 1

    try:
        results, pagination = repository_client.search(q, page)
    except Exception as exc:
        return render_template(
            "index.html",
            query=q,
            error="Gagal mengambil hasil pencarian. Periksa koneksi internet dan coba lagi.",
        )

    pagination_pages = make_pagination_pages(pagination["current"], pagination["total_pages"])

    return render_template(
        "index.html",
        query=q,
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

    return render_template("detail.html", eprint=eprint, back_url=back_url)


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

    return render_template("viewer.html", eprint=eprint, doc=doc, restricted=False)


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
