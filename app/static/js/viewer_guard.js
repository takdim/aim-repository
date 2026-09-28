'use strict';

(function () {
  function isPrintShortcut(event) {
    const key = String(event.key || '').toLowerCase();
    return (event.ctrlKey || event.metaKey) && key === 'p';
  }

  document.addEventListener('keydown', function (event) {
    if (isPrintShortcut(event)) {
      event.preventDefault();
      event.stopPropagation();
    }
  }, true);

  const originalPrint = window.print;
  window.print = function () {
    return undefined;
  };

  // Simpan referensi asli untuk keperluan debugging internal.
  window.__originalPrint = originalPrint;
})();
