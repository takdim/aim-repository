// search.js — animasi/UX tambahan untuk halaman pencarian
document.addEventListener('DOMContentLoaded', function () {
  const form = document.getElementById('searchForm');
  const input = document.getElementById('searchInput');
  const yearSelect = form ? form.querySelector('select[name="year"]') : null;

  if (form && input) {
    form.addEventListener('submit', function (e) {
      const q = input.value.trim();
      const selectedYear = yearSelect ? yearSelect.value.trim() : '';
      if (!q && !selectedYear) {
        e.preventDefault();
        input.focus();
      }
    });

    // Tekan Enter pada input → submit
    input.addEventListener('keydown', function (e) {
      if (e.key === 'Enter') {
        form.requestSubmit ? form.requestSubmit() : form.submit();
      }
    });
  }
});
