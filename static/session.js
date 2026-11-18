'use strict';
// « Se déconnecter », only on the shared service: /api/me does not exist on the local application.
(async () => {
  try {
    const response = await fetch('/api/me', { cache: 'no-store' });
    if (!response.ok) return;
    const form = document.createElement('form');
    form.method = 'post';
    form.action = '/deconnexion';
    form.className = 'logout';
    const button = document.createElement('button');
    button.type = 'submit';
    button.textContent = 'Se déconnecter';
    form.append(button);
    const place = document.querySelector('aside .aside-bottom');
    if (place) {
      place.before(form);
    } else {
      // pages without the side menu (review, administration) have their own style sheet
      form.style.cssText = 'text-align:right;margin:8px 0';
      button.style.cssText = 'width:auto;padding:6px 12px;border:1px solid #c9d3c2;border-radius:6px;background:#fff;color:#3f5d42;cursor:pointer';
      document.querySelector('main, body').prepend(form);
    }
  } catch {
    // service unreachable: nothing to show
  }
})();
