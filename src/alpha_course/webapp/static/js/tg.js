// Обёртка над Telegram.WebApp с мягким фолбэком для обычного браузера.
const SDK = 'https://telegram.org/js/telegram-web-app.js';

// SDK подключён в index.html как async; здесь только ждём его (с таймаутом), не блокируя отрисовку.
function loadSdk() {
  return new Promise((resolve) => {
    if (window.Telegram?.WebApp) return resolve();
    const inTg = location.hash.includes('tgWebAppData') || window.TelegramWebviewProxy || window.parent !== window || (() => { try { return !!sessionStorage.getItem('__telegram__initParams'); } catch (e) { return false; } })();
    const t = setTimeout(done, inTg ? 2500 : 250);
    function done() { clearTimeout(t); resolve(); }
    const s = document.querySelector('script[data-tg]');
    s?.addEventListener('load', done); s?.addEventListener('error', done);
  });
}

let W = null;
const backStack = [];
let baseBack = null;
let mainHandler = null;

export const tg = {
  inTelegram: false,
  get initData() { return W?.initData || ''; },
  viewportStableHeight() { return this.inTelegram ? W?.viewportStableHeight || 0 : 0; },
  get colorScheme() { return W?.colorScheme || null; },

  async init() {
    await loadSdk();
    W = window.Telegram?.WebApp || null;
    this.inTelegram = !!(W && W.initData);
    try { W?.onEvent('viewportChanged', () => document.dispatchEvent(new Event('tg-viewport'))); } catch (e) { /* noop */ }
    if (W) {
      try { W.ready(); W.expand(); } catch (e) { /* старый клиент */ }
      try { W.onEvent('backButtonClicked', onBack); } catch (e) { /* noop */ }
      try { W.onEvent('themeChanged', () => document.dispatchEvent(new Event('tg-theme'))); } catch (e) { /* noop */ }
    }
    document.body.classList.toggle('tg', this.inTelegram);
  },

  setColors(bg, header = bg, bottom = bg) {
    if (!W || !this.inTelegram || !W.isVersionAtLeast?.('6.1')) return;
    try { W.setBackgroundColor?.(bg); W.setHeaderColor?.(header); W.setBottomBarColor?.(bottom); } catch (e) { /* noop */ }
  },

  // Back: базовый обработчик маршрута + стек поверх (листы).
  setBase(fn) { baseBack = fn; syncBack(); },
  pushBack(fn) {
    backStack.push(fn); syncBack();
    return () => { const i = backStack.indexOf(fn); if (i >= 0) backStack.splice(i, 1); syncBack(); };
  },

  haptic(kind = 'light') {
    const H = W?.HapticFeedback; if (!H) return;
    try {
      if (['success', 'error', 'warning'].includes(kind)) H.notificationOccurred(kind);
      else if (kind === 'select') H.selectionChanged();
      else H.impactOccurred(kind);
    } catch (e) { /* noop */ }
  },

  openLink(url) {
    if (!url) return;
    try {
      if (W && W.openLink && this.inTelegram) return W.openLink(url);
    } catch (e) { /* фолбэк ниже */ }
    window.open(url, '_blank', 'noopener');
  },

  // MainButton: возвращает функцию-снятие. В обычном браузере не показывается.
  showMain(text, handler, { loading = false, disabled = false } = {}) {
    const B = W?.MainButton; if (!B || !this.inTelegram) return () => {};
    try {
      if (mainHandler) B.offClick(mainHandler);
      mainHandler = handler; B.setText(text); B.onClick(handler);
      disabled ? B.disable?.() : B.enable?.();
      loading ? B.showProgress?.(false) : B.hideProgress?.();
      B.show();
    } catch (e) { /* noop */ }
    return () => this.hideMain(handler);
  },
  hideMain(handler) {
    const B = W?.MainButton; if (!B) return;
    try {
      if (handler && mainHandler !== handler) return;
      if (mainHandler) B.offClick(mainHandler);
      mainHandler = null; B.hideProgress?.(); B.hide();
    } catch (e) { /* noop */ }
  },
  close() { try { W?.close(); } catch (e) { /* noop */ } },
};

function current() { return backStack.length ? backStack[backStack.length - 1] : baseBack; }
function onBack() { const f = current(); if (f) f(); }
function syncBack() {
  const B = W?.BackButton; if (!B || !tg.inTelegram || !W.isVersionAtLeast?.('6.1')) return;
  try { current() ? B.show() : B.hide(); } catch (e) { /* noop */ }
}
