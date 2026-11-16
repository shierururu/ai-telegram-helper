import { tg } from './tg.js';
import { loadBoot } from './state.js';
import { mountChrome, render, go, parse } from './router.js';
import { errorState } from './components.js';
import { $ } from './ui.js';
import { MODE } from './api.js';

function applyTheme() {
  const mq = matchMedia('(prefers-color-scheme: dark)');
  const scheme = tg.inTelegram && tg.colorScheme ? tg.colorScheme : (mq.matches ? 'dark' : 'light');
  document.documentElement.dataset.theme = scheme;
  const bg = getComputedStyle(document.documentElement).getPropertyValue('--bg').trim();
  tg.setColors(bg);
  document.querySelector('meta[name=theme-color]')?.setAttribute('content', bg);
}

function watchOffline() {
  const bar = $('#offline');
  const upd = () => { bar.hidden = navigator.onLine; };
  addEventListener('online', upd); addEventListener('offline', upd); upd();
}

function fitViewport() {
  const h = tg.viewportStableHeight() || window.innerHeight;
  document.documentElement.style.setProperty('--app-h', Math.round(Math.min(h, window.innerHeight || h)) + 'px');
}

async function boot() {
  fitViewport();
  addEventListener('resize', fitViewport); addEventListener('orientationchange', fitViewport);
  await tg.init();
  fitViewport(); document.addEventListener('tg-viewport', fitViewport);
  applyTheme();
  matchMedia('(prefers-color-scheme: dark)').addEventListener?.('change', applyTheme);
  document.addEventListener('tg-theme', applyTheme);
  watchOffline();
  mountChrome();
  if (MODE === 'mock') document.title += ' (mock)';
  const view = $('#view');
  try {
    const b = await loadBoot();
    if (!navigator.connection?.saveData) setTimeout(() => import('./screens/ask.js').catch(() => {}), 2500);
    if (!b.onboarded && parse().name !== 'onboarding') { go('onboarding', { replace: true }); return; }
    if (!location.hash) go('today', { replace: true }); else render();
  } catch (e) {
    document.body.classList.add('bare');
    view.replaceChildren(errorState(e, () => location.reload()));
  }
}
boot();
