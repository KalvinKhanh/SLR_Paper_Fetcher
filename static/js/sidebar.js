window.toggleMobileSidebar = function (forceOpen) {
  const sidebar = document.getElementById('sidebar');
  const overlay = document.getElementById('sidebarOverlay');
  if (!sidebar || !overlay) return;
  const currentlyOpen = !sidebar.classList.contains('-translate-x-full');
  const shouldOpen = typeof forceOpen === 'boolean' ? forceOpen : !currentlyOpen;
  sidebar.classList.toggle('-translate-x-full', !shouldOpen);
  overlay.classList.toggle('hidden', !shouldOpen);
  document.querySelectorAll('[data-sidebar-toggle]').forEach(button => {
    button.setAttribute('aria-expanded', String(shouldOpen));
  });
};

window.addEventListener('keydown', event => {
  if (event.key === 'Escape' && !document.getElementById('sidebarOverlay')?.classList.contains('hidden')) {
    window.toggleMobileSidebar(false);
  }
});

document.addEventListener('DOMContentLoaded', () => {
  const badge = document.getElementById('navDownloadedBadge');
  if (badge) fetch('/api/downloaded-papers').then(response => response.json()).then(data => {
    badge.textContent = data.total || 0;
  }).catch(() => {});
});
