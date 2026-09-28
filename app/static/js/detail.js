'use strict';

function openRestrictedModal() {
  const modal = document.getElementById('restrictedModal');
  if (!modal) return;
  modal.classList.add('active');
  modal.setAttribute('aria-hidden', 'false');

  const closeBtn = document.getElementById('modalCloseBtn');
  if (closeBtn) {
    closeBtn.focus();
  }
}

function closeRestrictedModal() {
  const modal = document.getElementById('restrictedModal');
  if (!modal) return;
  modal.classList.remove('active');
  modal.setAttribute('aria-hidden', 'true');
}

document.addEventListener('DOMContentLoaded', function () {
  const docCards = document.querySelectorAll('.doc-card');
  const modal = document.getElementById('restrictedModal');
  const closeBtn = document.getElementById('modalCloseBtn');

  docCards.forEach(function (card) {
    function activateCard() {
      const restricted = card.getAttribute('data-restricted') === '1';
      const targetUrl = card.getAttribute('data-target-url') || '';

      if (restricted) {
        openRestrictedModal();
        return;
      }

      if (targetUrl) {
        window.location.href = targetUrl;
      }
    }

    card.addEventListener('click', activateCard);
    card.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        activateCard();
      }
    });
  });

  if (closeBtn) {
    closeBtn.addEventListener('click', closeRestrictedModal);
  }

  if (modal) {
    modal.addEventListener('click', function (e) {
      if (e.target === modal) {
        closeRestrictedModal();
      }
    });
  }

  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') {
      closeRestrictedModal();
    }
  });
});
