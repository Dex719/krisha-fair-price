/* Inline SVG-иконки (стиль lucide, stroke = currentColor).
   Заменяют Material Symbols font: без загрузки шрифта иконки не ломаются
   и всегда наследуют цвет текста в обеих темах.
   Использование: <span data-icon="chart" data-icon-size="20"></span>
   или из JS: kfpIcon("chart", 20). */
(() => {
  const ICONS = {
    chart: '<path d="M3 3v18h18"/><path d="m19 9-5 5-4-4-3 3"/>',
    moon: '<path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>',
    sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2"/><path d="M12 20v2"/><path d="m4.93 4.93 1.41 1.41"/><path d="m17.66 17.66 1.41 1.41"/><path d="M2 12h2"/><path d="M20 12h2"/><path d="m6.34 17.66-1.41 1.41"/><path d="m19.07 4.93-1.41 1.41"/>',
    link: '<path d="M9 17H7A5 5 0 0 1 7 7h2"/><path d="M15 7h2a5 5 0 1 1 0 10h-2"/><path d="M8 12h8"/>',
    sparkles: '<path fill="currentColor" stroke="none" d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9z"/><path fill="currentColor" stroke="none" d="M19 14.5l.9 2.1 2.1.9-2.1.9-.9 2.1-.9-2.1-2.1-.9 2.1-.9z"/>',
    "trending-down": '<path d="M22 17l-8.5-8.5-5 5L2 7"/><path d="M16 17h6v-6"/>',
    "check-circle": '<circle cx="12" cy="12" r="10"/><path d="m9 12 2 2 4-4"/>',
    flame: '<path d="M8.5 14.5A2.5 2.5 0 0 0 11 12c0-1.38-.5-2-1-3-1.072-2.143-.224-4.054 2-6 .5 2.5 2 4.9 4 6.5 2 1.6 3 3.5 3 5.5a7 7 0 1 1-14 0c0-1.153.433-2.294 1-3a2.5 2.5 0 0 0 2.5 2.5z"/>',
    help: '<circle cx="12" cy="12" r="10"/><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"/><path d="M12 17h.01"/>',
    search: '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/>',
    city: '<path d="M6 22V4a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v18"/><path d="M6 12H4a2 2 0 0 0-2 2v6a2 2 0 0 0 2 2h2"/><path d="M18 9h2a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2h-2"/><path d="M10 6h4"/><path d="M10 10h4"/><path d="M10 14h4"/><path d="M10 18h4"/>',
    bars: '<path d="M12 20V10"/><path d="M18 20V4"/><path d="M6 20v-4"/>',
    door: '<path d="M18 21V5a2 2 0 0 0-2-2H8a2 2 0 0 0-2 2v16"/><path d="M3 21h18"/><circle cx="14.4" cy="12" r=".8" fill="currentColor" stroke="none"/>',
    building: '<rect x="4" y="2" width="16" height="20" rx="2"/><path d="M9 6h2"/><path d="M13 6h2"/><path d="M9 10h2"/><path d="M13 10h2"/><path d="M9 14h2"/><path d="M13 14h2"/><path d="M10 22v-3h4v3"/>',
    "map-pin": '<path d="M20 10c0 6-8 12-8 12s-8-6-8-12a8 8 0 0 1 16 0z"/><circle cx="12" cy="10" r="3"/>',
    "chevron-left": '<path d="m15 18-6-6 6-6"/>',
    "chevron-right": '<path d="m9 18 6-6-6-6"/>',
    photo: '<rect x="3" y="3" width="18" height="18" rx="3"/><circle cx="9" cy="9" r="2"/><path d="m21 15-3.1-3.1a2 2 0 0 0-2.8 0L6 21"/>',
    list: '<path d="M8 6h13"/><path d="M8 12h13"/><path d="M8 18h13"/><path d="M3 6h.01"/><path d="M3 12h.01"/><path d="M3 18h.01"/>',
    telegram: '<path d="m22 2-7 20-4-9-9-4z"/><path d="M22 2 11 13"/>',
    threads: '<circle cx="12" cy="12" r="4"/><path d="M16 8v5a3 3 0 0 0 6 0v-1a10 10 0 1 0-4 8"/>',
  };

  window.kfpIcon = (name, size = 24) =>
    `<svg class="svg-icon" style="width:${size}px;height:${size}px" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[name] || ICONS.help}</svg>`;

  window.kfpApplyIcons = (root = document) =>
    root.querySelectorAll("[data-icon]").forEach((el) => {
      el.innerHTML = window.kfpIcon(el.dataset.icon, el.dataset.iconSize || 24);
    });

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => window.kfpApplyIcons());
  } else {
    window.kfpApplyIcons();
  }
})();

/* Ripple при нажатии на кнопки (M3 state layer) */
(function () {
  document.addEventListener("pointerdown", function (e) {
    var btn = e.target.closest ? e.target.closest(".btn, .icon-btn, .paste-btn") : null;
    if (!btn) return;
    var rect = btn.getBoundingClientRect();
    var size = Math.max(rect.width, rect.height);
    var ink = document.createElement("span");
    ink.className = "ripple-ink";
    ink.style.width = ink.style.height = size + "px";
    ink.style.left = (e.clientX - rect.left - size / 2) + "px";
    ink.style.top = (e.clientY - rect.top - size / 2) + "px";
    btn.appendChild(ink);
    ink.addEventListener("animationend", function () { ink.remove(); });
  });
})();
