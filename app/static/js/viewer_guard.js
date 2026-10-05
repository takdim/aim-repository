'use strict';

(function () {
  function isPrintShortcut(event) {
    const key = String(event.key || '').toLowerCase();
    return (event.ctrlKey || event.metaKey) && key === 'p';
  }

  document.addEventListener('keydown', function (event) {
    const key = String(event.key || '').toLowerCase();
    const blockedShortcut = isPrintShortcut(event)
      || ((event.ctrlKey || event.metaKey) && ['s', 'u'].includes(key))
      || (event.key === 'PrintScreen');
    if (blockedShortcut) {
      event.preventDefault();
      event.stopPropagation();
    }
  }, true);

  document.addEventListener('contextmenu', function (event) {
    event.preventDefault();
  }, true);

  document.addEventListener('copy', function (event) {
    event.preventDefault();
  }, true);

  const originalPrint = window.print;
  window.print = function () {
    return undefined;
  };

  // Simpan referensi asli untuk keperluan debugging internal.
  window.__originalPrint = originalPrint;
})();
