/**
 * viewer.js — PDF viewer read-only dengan navigasi halaman
 * Tidak ada download: hanya menerima gambar per-halaman dari server.
 * URL file PDF asli TIDAK pernah diketahui oleh script ini.
 */

'use strict';

// ── State ─────────────────────────────────────────────────────────────────
let currentPage = 1;
let totalPages  = 0;
let isLoading   = false;

// ── DOM refs ──────────────────────────────────────────────────────────────
const imgEl        = document.getElementById('pdfPageImg');
const spinner      = document.getElementById('loadingSpinner');
const errorMsg     = document.getElementById('viewerErrorMsg');
const pageInput    = document.getElementById('pageInput');
const totalDisplay = document.getElementById('totalPageDisplay');
const pageInfoText = document.getElementById('pageInfoText');
const btnFirst     = document.getElementById('btnFirst');
const btnPrev      = document.getElementById('btnPrev');
const btnNext      = document.getElementById('btnNext');
const btnLast      = document.getElementById('btnLast');

// ── Init ──────────────────────────────────────────────────────────────────
window.addEventListener('DOMContentLoaded', function () {
  if (!imgEl) return;  // viewer tidak dirender (restricted/error)
  loadPage(1);
  bindEvents();
});

// ── Load page ─────────────────────────────────────────────────────────────
function loadPage(page) {
  if (isLoading) return;
  if (page < 1 || (totalPages > 0 && page > totalPages)) return;

  isLoading = true;
  currentPage = page;

  showSpinner(true);
  hideError();
  imgEl.style.display = 'none';

  const url = `/api/page-image/${EPRINT_ID}/${DOC_TYPE}/${page}`;

  fetch(url, { credentials: 'same-origin' })
    .then(function (resp) {
      if (!resp.ok) {
        throw new Error('HTTP ' + resp.status);
      }

      // Baca total halaman dari header (tersedia sejak request pertama)
      const count = parseInt(resp.headers.get('X-Page-Count') || '0', 10);
      if (count > 0 && totalPages !== count) {
        totalPages = count;
        totalDisplay.textContent = totalPages;
      }

      return resp.blob();
    })
    .then(function (blob) {
      const objectUrl = URL.createObjectURL(blob);

      // Preload gambar agar tidak ada flicker
      const tmp = new Image();
      tmp.onload = function () {
        // Revoke URL lama sebelum assign baru
        if (imgEl.src && imgEl.src.startsWith('blob:')) {
          URL.revokeObjectURL(imgEl.src);
        }
        imgEl.src = objectUrl;
        imgEl.style.display = 'block';
        showSpinner(false);
        updateUI();
        isLoading = false;

        // Scroll ke atas canvas
        const canvas = document.getElementById('viewerCanvas');
        if (canvas) canvas.scrollTop = 0;
      };
      tmp.onerror = function () {
        URL.revokeObjectURL(objectUrl);
        showSpinner(false);
        showError();
        isLoading = false;
      };
      tmp.src = objectUrl;
    })
    .catch(function () {
      showSpinner(false);
      showError();
      isLoading = false;
    });
}

// ── Navigation ────────────────────────────────────────────────────────────
window.goToPage = function (page) {
  if (isLoading) return;
  const p = parseInt(page, 10);
  if (isNaN(p)) return;
  if (p < 1 || (totalPages > 0 && p > totalPages)) return;
  loadPage(p);
};

window.retryCurrentPage = function () {
  hideError();
  loadPage(currentPage);
};

// ── UI Update ─────────────────────────────────────────────────────────────
function updateUI() {
  pageInput.value = currentPage;

  if (totalPages > 0) {
    pageInfoText.textContent = currentPage + ' / ' + totalPages;
    totalDisplay.textContent = totalPages;
  } else {
    pageInfoText.textContent = 'Halaman ' + currentPage;
  }

  btnFirst.disabled = currentPage <= 1;
  btnPrev.disabled  = currentPage <= 1;
  btnNext.disabled  = totalPages > 0 && currentPage >= totalPages;
  btnLast.disabled  = totalPages > 0 && currentPage >= totalPages;
}

function showSpinner(show) {
  spinner.style.display = show ? 'block' : 'none';
}

function showError() {
  errorMsg.style.display = 'flex';
}

function hideError() {
  errorMsg.style.display = 'none';
}

// ── Event Bindings ────────────────────────────────────────────────────────
function bindEvents() {
  // Page input — tekan Enter atau blur untuk pindah
  if (pageInput) {
    pageInput.addEventListener('keydown', function (e) {
      if (e.key === 'Enter') {
        goToPage(pageInput.value);
        pageInput.blur();
      }
    });
    pageInput.addEventListener('blur', function () {
      const p = parseInt(pageInput.value, 10);
      if (!isNaN(p) && p !== currentPage) {
        goToPage(p);
      }
    });
  }

  // Keyboard arrow navigation
  document.addEventListener('keydown', function (e) {
    // Jangan intercept saat user mengetik di input
    if (document.activeElement === pageInput) return;

    if (e.key === 'ArrowRight' || e.key === 'ArrowDown') {
      e.preventDefault();
      goToPage(currentPage + 1);
    } else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') {
      e.preventDefault();
      goToPage(currentPage - 1);
    } else if (e.key === 'Home') {
      e.preventDefault();
      goToPage(1);
    } else if (e.key === 'End' && totalPages > 0) {
      e.preventDefault();
      goToPage(totalPages);
    }
  });

  // Nonaktifkan context menu di area viewer (mencegah "Save Image As")
  const canvas = document.getElementById('viewerCanvas');
  if (canvas) {
    canvas.addEventListener('contextmenu', function (e) {
      e.preventDefault();
      return false;
    });
  }

  // Nonaktifkan drag image
  if (imgEl) {
    imgEl.addEventListener('dragstart', function (e) {
      e.preventDefault();
    });
  }
}
