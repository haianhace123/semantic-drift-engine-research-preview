// Preserve Back navigation without permitting inline JavaScript in CSP.
document.addEventListener('DOMContentLoaded', () => {
  const back = document.getElementById('backLink');
  if (!back) return;
  back.addEventListener('click', (event) => {
    // Navigate only to an existing, same-origin referring page. Otherwise
    // use the real /login.html href and let the browser handle it.
    if (!document.referrer) return;
    try {
      const referrer = new URL(document.referrer);
      if (referrer.origin !== window.location.origin || window.history.length <= 1) return;
      event.preventDefault();
      window.history.back();
    } catch {
      // Invalid referrer: retain the anchor's normal navigation.
    }
  });
});
