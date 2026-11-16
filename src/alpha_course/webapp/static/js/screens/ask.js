// «Спросить»: консультация по базе знаний.
import { h, icon, haptic, toast, openSheet } from '../ui.js';
import { api } from '../api.js';
import { state, FIELDS, showValue } from '../state.js';
import { materialCard, accessLabel, openMaterial } from '../components.js';
import { tg } from '../tg.js';
import { openReminderComposer } from '../reminder-flow.js';

const LABEL = Object.fromEntries(FIELDS.map((f) => [f.key, f.label]));
const START = ['Как выбрать налоговый режим?', 'Где взять первых клиентов?', 'Что нужно для открытия ИП?', 'Что такое эквайринг?'];
let seq = 0;

const replyText = (r) => [r.summary, ...(r.steps || []).map((s, i) => `${i + 1}. ${s}`), r.personal].filter(Boolean).join('\n');

export default function mount(view, { go }) {
  const msgs = h('div', { class: 'msgs', role: 'log', 'aria-live': 'polite', 'aria-label': 'Диалог с помощником' });
  const ta = h('textarea', { rows: 1, placeholder: 'Задай вопрос о бизнесе…', 'aria-label': 'Вопрос', enterkeyhint: 'send' });
  const sendBtn = h('button', { class: 'send', type: 'submit', 'aria-label': 'Отправить', disabled: true }, icon('send', 22));
  const form = h('form', { class: 'composer' }, h('div', { class: 'box' }, ta, sendBtn));
  const root = h('div', { class: 'chat' }, msgs, form);
  view.replaceChildren(root);
  let busy = false, intro = null, typing = null, typingTimer = null;

  const sync = () => { sendBtn.disabled = busy || !ta.value.trim(); ta.style.height = 'auto'; ta.style.height = Math.min(ta.scrollHeight, 120) + 'px'; };
  ta.addEventListener('input', sync);
  ta.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); form.requestSubmit(); } });
  form.addEventListener('submit', (e) => { e.preventDefault(); const t = ta.value.trim(); if (!t || busy) return; ta.value = ''; sync(); send(t); });

  const scrollEnd = () => requestAnimationFrame(() => window.scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' }));
  const askAbout = (t) => { if (!busy) send(t); };

  function drawIntro() {
    intro = h('div', { class: 'intro' },
      h('div', { class: 'orb' }, icon('spark', 26)),
      h('h1', { class: 'h1' }, 'Спроси о бизнесе'),
      h('p', { class: 'muted' }, 'Отвечаю по базе знаний и подбираю материалы. Если ответа в базе нет — скажу честно.'),
      h('div', { class: 'chips' }, START.map((q) => h('button', { class: 'chip', type: 'button', onclick: () => askAbout(q) }, q))));
    msgs.append(intro);
  }

  function history() {
    return state.chat.filter((m) => !m.error).slice(-8).map((m) => ({ role: m.role === 'user' ? 'user' : 'assistant', text: m.role === 'user' ? m.text : (m.reply.summary || (m.reply.clarification?.question) || '') })).filter((x) => x.text);
  }

  async function send(text) {
    intro?.remove(); intro = null;
    const hist = history();
    const um = { id: ++seq, role: 'user', text };
    state.chat.push(um); msgs.append(userNode(um)); haptic('light');
    busy = true; sync();
    showTyping(); scrollEnd();
    try {
      const reply = await api.ask(text, hist);
      stopTyping();
      if (reply.profile_updates && Object.keys(reply.profile_updates).length && state.boot) {
        Object.assign(state.boot.profile.fields, reply.profile_updates);
      }
      const bm = { id: ++seq, role: 'bot', question: text, reply };
      state.chat.push(bm); const node = botNode(bm); msgs.append(node);
      node.scrollIntoView({ behavior: 'smooth', block: 'start' });
    } catch (e) {
      stopTyping(); haptic('error');
      const bm = { id: ++seq, role: 'bot', question: text, error: e };
      state.chat.push(bm); msgs.append(botNode(bm)); scrollEnd();
    } finally { busy = false; sync(); }
  }

  function showTyping() {
    const label = h('span', null, 'Ищу в базе знаний…');
    typing = h('div', { class: 'typing', role: 'status' }, h('span', { class: 'dots', 'aria-hidden': 'true' }, h('i'), h('i'), h('i')), label);
    msgs.append(typing);
    typingTimer = setTimeout(() => { label.textContent = 'Формулирую ответ…'; }, 1400);
  }
  function stopTyping() { clearTimeout(typingTimer); typing?.remove(); typing = null; }

  const userNode = (m) => h('div', { class: 'bubble-u' }, m.text);

  function botNode(m) {
    const wrap = h('div', { class: 'stack', style: { scrollMarginTop: '16px' } });
    wrap.append(m.error ? errorCard(m) : replyCard(m));
    const up = m.reply?.profile_updates;
    if (up && Object.keys(up).length) {
      wrap.append(h('div', { class: 'remember' }, icon('check', 14), 'Запомнил: ' + Object.entries(up).map(([k, v]) => `${(LABEL[k] || k).toLowerCase()} — ${showValue(k, v) ?? v}`).join(', ')));
    }
    return wrap;
  }

  function errorCard(m) {
    return h('div', { class: 'bot', role: 'alert' },
      h('p', { class: 'sum' }, 'Не получилось получить ответ'),
      h('p', { class: 'muted small' }, m.error.friendly),
      h('div', null, h('button', { class: 'btn soft sm', type: 'button', onclick: (e) => {
        const node = e.currentTarget.closest('.stack'); const idx = state.chat.indexOf(m);
        state.chat.splice(Math.max(0, idx - 1), 2); node.previousElementSibling?.remove(); node.remove();
        send(m.question);
      } }, icon('refresh', 16), 'Повторить')));
  }

  function replyCard(m) {
    const r = m.reply;
    const card = h('div', { class: 'bot' });
    const parts = [];
    const note = (txt) => h('div', { class: 'note' }, icon('info', 18), h('span', null, txt));

    if (r.kind === 'offtopic') parts.push(note('Этот вопрос вне моей темы.'));
    if (r.status === 'no_evidence') parts.push(note('В базе знаний нет ответа на этот вопрос — не буду его выдумывать.'));
    if (r.status === 'unavailable') parts.push(note('Помощник сейчас недоступен, поэтому показываю только подходящие материалы.'));
    if (r.status === 'partial') parts.push(note('Точного ответа в базе нет — нашёл близкое.'));
    if (r.summary) parts.push(h('div', null, r.kind === 'answer' && r.steps?.length ? h('div', { class: 'blk-t' }, 'Коротко') : null, h('p', { class: 'sum' }, r.summary)));
    if (r.steps?.length) parts.push(h('div', null, h('div', { class: 'blk-t' }, 'Что сделать'), h('ol', null, r.steps.map((s) => h('li', null, s)))));
    if (r.personal) parts.push(h('div', null, h('div', { class: 'blk-t' }, 'Для тебя'), h('div', { class: 'personal' }, r.personal)));

    if (r.clarification) {
      const c = r.clarification;
      parts.push(h('div', { class: 'stack' }, h('p', { class: 'sum' }, c.question),
        h('div', { class: 'chips' }, (c.options || []).map((o) => h('button', { class: 'chip', type: 'button', onclick: () => askAbout(o) }, o)))));
    }
    if (r.reminder_event) {
      parts.push(h('div', { class: 'personal' }, h('div', { class: 'small', style: { fontWeight: 600, marginBottom: '8px' } }, 'Напоминание: ' + r.reminder_event),
        h('button', { class: 'btn primary sm', type: 'button', onclick: () => openReminderComposer({ event: r.reminder_event }) }, icon('bell', 16), 'Настроить напоминание')));
    }
    if (r.kind === 'tax') parts.push(h('a', { class: 'btn soft sm', href: '#/taxes' }, icon('calendar', 16), 'Открыть календарь'));
    if (r.kind === 'plan') parts.push(h('a', { class: 'btn soft sm', href: '#/route' }, icon('route', 16), 'К маршруту'));

    const recommendations = (r.recommendations || []).filter((x) => !r.offer?.pitch || x.material.id !== r.offer.material.id);
    if (recommendations.length) {
      parts.push(h('div', null, h('div', { class: 'blk-t' }, r.status === 'no_evidence' || r.status === 'unavailable' ? 'Ближайшие материалы' : 'Что почитать и посмотреть'),
        h('div', { class: 'stack' }, recommendations.map((x) => materialCard(x.material, x.why)))));
    }
    // A/B «paid_offer_v1», вариант B: персональный блок «Следующий шаг» (только публичные данные курса).
    if (r.offer?.pitch) {
      const o = r.offer.pitch;
      parts.push(h('div', { class: 'personal offer' },
        h('div', { class: 'blk-t' }, '🎯 Следующий шаг для тебя'),
        h('div', { style: { fontWeight: 600 } }, '💎 ' + o.title + ' · ' + o.price),
        o.benefit ? h('p', { class: 'small' }, o.benefit) : null,
        h('p', { class: 'small muted' }, o.fit + ' ' + o.note),
        h('button', { class: 'btn primary sm', type: 'button', onclick: () => openMaterial(r.offer.material, { paid: true }) }, icon('gem', 16), 'Посмотреть курс')));
    }
    const free = (r.sources || []).filter((s) => !s.is_paid);
    if (r.sources?.length) {
      parts.push(h('p', { class: 'src' }, 'Источники: ', r.sources.map((s, i) => [i ? ', ' : '',
        s.is_paid ? s.title : h('a', { href: s.url, target: '_blank', rel: 'noopener', onclick: (e) => { e.preventDefault(); api.click(s.id); tg.openLink(s.url); } }, s.title)])));
    }
    void free;
    if (r.status === 'no_evidence' && r.kind === 'answer') parts.push(freeBlock(m));
    if (r.followups?.length) parts.push(h('div', { class: 'chips' }, r.followups.map((f) => h('button', { class: 'chip', type: 'button', onclick: () => askAbout(f) }, f))));

    card.append(...parts);
    const simple = h('div', { class: 'simple', hidden: true, 'aria-live': 'polite' });
    card.append(simple);
    if (m.simple) fillSimple(simple, m.simple);
    if (r.kind === 'answer' && (r.summary || r.recommendations?.length)) card.append(tools(m, simple));
    return card;
  }

  // Явное согласие пользователя: ответ ИИ без базы знаний, с пометкой.
  function freeBlock(m) {
    const box = h('div', { class: 'stack', 'aria-live': 'polite' });
    const draw = () => {
      const f = m.free;
      if (!f) {
        box.replaceChildren(h('button', { class: 'btn soft sm', type: 'button', onclick: (e) => run(e.currentTarget) }, icon('spark', 16), 'Ответить без базы'),
          h('p', { class: 'muted small' }, 'Общий совет ИИ — не из базы знаний, детали стоит проверить.'));
      } else if (f.kind === 'offtopic') {
        box.replaceChildren(h('div', { class: 'note' }, icon('info', 18), h('span', null, 'Это не похоже на вопрос о бизнесе, поэтому ИИ не отвечает.')));
      } else if (!f.available) {
        box.replaceChildren(h('div', { class: 'note' }, icon('info', 18), h('span', null, f.message || 'ИИ сейчас недоступен. Попробуй позже.')));
      } else {
        box.replaceChildren(h('div', { class: 'blk-t' }, f.title || 'Ответ ИИ'),
          ...String(f.text).split(/\n{2,}/).map((p) => h('p', { class: 'sum', style: { whiteSpace: 'pre-line' } }, p)),
          f.links?.length ? h('div', { class: 'stack' }, h('div', { class: 'blk-t' }, 'Источники из интернета'),
            ...f.links.map((l) => h('a', { class: 'link', href: l.url, target: '_blank', rel: 'noopener noreferrer' }, l.title))) : null,
          f.note ? h('p', { class: 'muted small' }, f.note) : null,
          h('p', { class: 'muted small' }, f.links?.length ? 'Данные из интернета: важные цифры проверь по источнику.' : 'Не из базы знаний: это общий совет, важные детали проверь.'));
      }
    };
    const run = async (btn) => {
      btn.setAttribute('disabled', '');
      box.replaceChildren(h('div', { class: 'skel', style: { height: '14px', width: '85%' } }), h('div', { class: 'skel', style: { height: '14px', width: '60%' } }), h('div', { class: 'skel', style: { height: '14px', width: '40%' } }));
      try { m.free = await api.askFree(m.question); haptic('success'); }
      catch (e) { m.free = { available: false, message: e.friendly }; }
      draw();
    };
    draw();
    return box;
  }

  function fillSimple(box, s) {
    box.hidden = false;
    box.replaceChildren(h('div', { class: 'blk-t' }, 'Проще говоря'), h('p', null, s.text),
      s.terms?.length ? h('div', { class: 'terms' }, s.terms.map((t) => h('div', null, h('b', null, t.term), ' — ', t.meaning))) : null);
  }

  async function simplify(m, box, btn) {
    if (m.simple) { fillSimple(box, m.simple); return; }
    btn?.setAttribute('disabled', '');
    box.hidden = false; box.replaceChildren(h('div', { class: 'skel', style: { height: '14px', width: '80%' } }), h('div', { class: 'skel', style: { height: '14px', width: '55%' } }));
    try {
      const s = await api.simplify(m.question, replyText(m.reply));
      if (!s.text) { box.hidden = true; toast('Проще не получилось. Попробуй уточнить вопрос.'); return; }
      m.simple = s; fillSimple(box, s); haptic('success'); box.scrollIntoView({ block: 'center', behavior: 'smooth' });
    } catch (e) { box.hidden = true; toast(e.friendly); } finally { btn?.removeAttribute('disabled'); }
  }

  function tools(m, simpleBox) {
    const r = m.reply;
    const vote = (v, btn) => {
      if (m.vote) return; m.vote = v; api.feedback(v); haptic(v === 'up' ? 'success' : 'warning');
      row.querySelectorAll('[data-v]').forEach((b) => b.classList.toggle('on', b.dataset.v === v));
      if (v === 'up') toast('Спасибо за оценку!');
      else openSheet({ title: 'Что не так с ответом?', build: (close) => h('div', { class: 'stack' },
        h('button', { class: 'btn soft block', type: 'button', onclick: () => { close(); simplify(m, simpleBox); } }, icon('bulb', 18), 'Объясни проще'),
        h('button', { class: 'btn soft block', type: 'button', onclick: () => { close(); ta.value = 'Уточню: '; sync(); setTimeout(() => ta.focus(), 250); } }, icon('edit', 18), 'Уточню вопрос'),
        h('button', { class: 'btn text block', type: 'button', onclick: () => close() }, 'Ничего, спасибо')) });
    };
    const learnTarget = r.recommendations?.[0]?.material || r.sources?.[0];
    const tl = (cls, label, ic, fn, extra = {}) => h('button', { class: 'tl ' + cls, type: 'button', 'aria-label': label, title: label, onclick: fn, ...extra }, ic);
    const upBtn = tl('', 'Полезный ответ', icon('up', 20), (e) => vote('up'), { 'data-v': 'up' });
    const downBtn = tl('', 'Ответ не помог', icon('up', 20, 'flip'), () => vote('down'), { 'data-v': 'down' });
    downBtn.firstChild.style.transform = 'scaleY(-1)';
    const row = h('div', { class: 'tools' }, upBtn, downBtn,
      h('button', { class: 'tl', type: 'button', title: 'Объяснить проще', 'aria-label': 'Объясни проще', onclick: (e) => simplify(m, simpleBox, e.currentTarget) }, icon('bulb', 18), 'Проще'),
      learnTarget ? h('button', { class: 'tl', type: 'button', title: 'Напомнить изучить', 'aria-label': 'Напомнить изучить', onclick: () => openReminderComposer({ event: `Изучить: ${learnTarget.title}`, link: { url: learnTarget.url, title: learnTarget.title } }) }, icon('bell', 18), h('span', { class: 'lbl' }, 'Напомнить')) : null,
      h('span', { class: 'sp' }),
      tl('', 'Копировать ответ', icon('copy', 18), async () => {
        const txt = replyText(r);
        try { if (navigator.share && matchMedia('(pointer:coarse)').matches) await navigator.share({ text: txt }); else { await navigator.clipboard.writeText(txt); toast('Ответ скопирован'); } } catch (e) { /* отмена */ }
      }));
    if (m.vote) row.querySelector(`[data-v="${m.vote}"]`)?.classList.add('on');
    return row;
  }

  // восстановление диалога
  if (!state.chat.length) drawIntro();
  else for (const m of state.chat) msgs.append(m.role === 'user' ? userNode(m) : botNode(m));
  if (state.pendingQuestion) { const q = state.pendingQuestion; state.pendingQuestion = null; send(q); }
  else if (state.chat.length) setTimeout(() => window.scrollTo(0, document.body.scrollHeight), 0);

  return () => { clearTimeout(typingTimer); };
}
