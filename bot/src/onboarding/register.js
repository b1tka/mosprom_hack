// Подключение онбординга к боту: движок, планировщик, обработчики кнопок и текста, команды /HR и /Food, меню HR «Мои сотрудники».
import {
  findUserById, findUserByMaxId, listEmployeesOf, setUserStartDate,
} from '../db.js';
import { isHr } from '../invite.js';
import { sendHrAnswer } from '../kafka.js';
import { ask } from '../ml.js';
import { getScenario } from '../scenario/load.js';
import { getState, setState } from '../state.js';
import { openContactTicket, openTicket } from '../support.js';
import { show } from '../ui.js';
import { clock } from './clock.js';
import { createEngine } from './engine.js';
import { hooks } from './hooks.js';
import { sendMail } from './mailer.js';
import { notifyHr } from './notify.js';
import {
  cb, kb, questionAttachments, render,
} from './send.js';
import { findByUser, onb } from './store.js';

const TICK_MS = 30_000;
const PAGE = 10;
const RAG_USER = 'scenario';   // канонические вопросы сценария идут в RAG от служебного uid, а не от сотрудника

let engine = null;
let timer = null;
let running = false;
let botApi = null;

export const getEngine = () => engine;

const adapter = (maxId) => ({
  api: botApi,
  user: { user_id: maxId },
  reply: (text, extra) => botApi.sendMessageToUser(Number(maxId), text, extra),
});

function buildEngine(api) {
  const scenario = getScenario();
  return createEngine({
    scenario,
    api,
    now: () => clock.now(),
    sleep: (ms) => new Promise((r) => { setTimeout(r, ms); }),
    // ответ из базы знаний по фиксированному вопросу: только если ML нашёл ответ (code 200)
    rag: async (query) => {
      const res = await ask({ userId: RAG_USER, question: query });
      return res.code === 200 ? res.answer : null;
    },
    mail: sendMail,
    notifyHr: (userId, text, opts) => notifyHr(api, userId, text, opts),
    kafkaAnswer: sendHrAnswer,
    actions: {
      contact_hr: async (ob) => {
        const user = await findUserById(ob.userId);
        if (user) await openContactTicket(adapter(ob.maxId), user, { note: 'Сотрудник нажал «Связаться с рекрутером» в сценарии' });
      },
      report_problem: async (ob) => startReport(ob.maxId),
    },
  });
}

async function startReport(maxId) {
  await setState(maxId, { step: 'report_problem', data: {} });
  await botApi.sendMessageToUser(Number(maxId), getScenario().texts.report_problem_prompt);
}

// Запуск онбординга для сотрудника (активация QR, кнопка у HR, админка)
export async function startOnboardingFor(userId, { startDate } = {}) {
  const user = await findUserById(userId);
  if (!user || user.role !== 'employee') return null;
  const res = await engine.start(user, { startDate: startDate ?? user.start_date ?? undefined });
  if (res.created) {
    await setUserStartDate(user.id, res.ob.startDate);
    engine.tick().catch((err) => console.error('onboarding first tick failed', err));
  }
  return res;
}

const statusIcon = { active: '▶', paused: '⏸', done: '✅' };

function progress(ob) {
  const done = ob.steps.filter((s) => ['sent', 'skipped', 'expired'].includes(s.state)).length;
  return `${done}/${ob.steps.length}`;
}

const fmtDate = (iso) => (iso ? iso.split('-').reverse().join('.') : '—');

async function allowedEmployee(hr, userId) {
  const emp = await findUserById(userId);
  if (!emp || emp.role !== 'employee') return null;
  if (hr.role === 'hr_head' || emp.hr_id === hr.id || !emp.hr_id) return emp;
  return null;
}

async function showEmployees(ctx, hr, page, notification) {
  const all = await listEmployeesOf(hr.role === 'hr_head' ? null : hr.id);
  const slice = all.slice(page * PAGE, page * PAGE + PAGE);
  const rows = [];
  for (const e of slice) {
    const ob = await findByUser(e.id);
    const mark = ob ? `${statusIcon[ob.status] ?? '•'} ${progress(ob)}` : '— не запущен';
    rows.push([cb(`${e.surname} ${e.name} · ${e.tag ?? 'без тега'} · ${fmtDate(e.start_date)} · ${mark}`.slice(0, 60), `ob:emp:${e.id}`)]);
  }
  const nav = [];
  if (page > 0) nav.push(cb('← Назад', `ob:emps:${page - 1}`));
  if ((page + 1) * PAGE < all.length) nav.push(cb('Вперёд →', `ob:emps:${page + 1}`));
  if (nav.length) rows.push(nav);
  rows.push([cb('← Меню', 'menu:main')]);
  return show(ctx, all.length ? `Мои сотрудники (${all.length}):` : 'Сотрудников пока нет.', kb(rows), notification);
}

async function showEmployeeCard(ctx, emp, notification) {
  const ob = await findByUser(emp.id);
  const lines = [`${emp.surname} ${emp.name}`, `Тег: ${emp.tag ?? 'нет'}`, `Дата выхода: ${fmtDate(ob?.startDate ?? emp.start_date)}`];
  const rows = [];
  if (ob) {
    lines.push(`Онбординг: ${statusIcon[ob.status] ?? ''} ${ob.status === 'paused' ? 'на паузе' : ob.status === 'done' ? 'завершён' : 'идёт'}, шагов ${progress(ob)}`);
    lines.push(`Обращение: ${ob.address === 'vy' ? 'на «вы»' : ob.address === 'ty' ? 'на «ты»' : 'не выбрано'}`);
    const next = ob.steps.filter((s) => s.state === 'pending').sort((a, b) => a.dueAt - b.dueAt)[0];
    if (next) lines.push(`Ближайший шаг: ${new Date(next.dueAt).toLocaleString('ru-RU', { timeZone: 'Europe/Moscow', day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })} — ${getScenario().steps.find((s) => s.id === next.id)?.title ?? next.id}`);
    rows.push([cb('Изменить дату выхода', `ob:date:${emp.id}`)]);
    if (ob.status === 'active') rows.push([cb('⏸ Пауза', `ob:pause:${emp.id}`)]);
    if (ob.status === 'paused') rows.push([cb('▶ Продолжить', `ob:resume:${emp.id}`)]);
  } else {
    lines.push('Сценарий онбординга ещё не запущен.');
    rows.push([cb('Запустить сценарий', `ob:launch:${emp.id}`)]);
  }
  rows.push([cb('← Сотрудники', 'ob:emps:0')]);
  return show(ctx, lines.join('\n'), kb(rows), notification);
}

const parseDate = (text) => {
  const m = /^\s*(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{4})\s*$/.exec(text ?? '');
  if (!m) return null;
  const iso = `${m[3]}-${m[2].padStart(2, '0')}-${m[1].padStart(2, '0')}`;
  const d = new Date(`${iso}T00:00:00Z`);
  return Number.isNaN(d.getTime()) || d.toISOString().slice(0, 10) !== iso ? null : iso;
};

export function registerOnboarding(bot) {
  botApi = bot.api;
  engine = buildEngine(bot.api);
  hooks.onActivated = async ({ userId, startDate }) => {
    await startOnboardingFor(userId, { startDate: startDate ?? undefined });
  };
  const scenario = getScenario();

  // ----- кнопки опросов и вопросов сценария
  bot.action(/^ob:([0-9a-f]{8}):(.+)$/, async (ctx) => {
    const res = await engine.onCallback({ maxId: ctx.user.user_id, waitId: ctx.match[1], token: ctx.match[2] });
    if (!res.ok) return ctx.answerOnCallback({ notification: res.toast });
    const attachments = res.edit?.rows ? [kb(res.edit.rows)] : [];
    const media = await questionAttachments(ctx.api, scenario, res.edit?.img).catch(() => []);
    await ctx.answerOnCallback({
      notification: res.toast,
      message: res.edit ? { text: res.edit.text, attachments: [...media, ...attachments] } : undefined,
    });
    // продолжение сценария (паузы между сообщениями) — в фоне: polling не должен ждать
    res.resume?.().catch((err) => console.error('onboarding resume failed', err));
    return null;
  });

  // ----- быстрые действия из кнопок под сообщениями сценария
  bot.action('oa:contact_hr', async (ctx) => {
    const user = await findUserByMaxId(ctx.user.user_id);
    if (user?.role !== 'employee') return ctx.answerOnCallback({ notification: 'Доступно сотрудникам.' });
    await ctx.answerOnCallback({ notification: 'Связываю с HR' });
    return openContactTicket(ctx, user);
  });
  bot.action('oa:report_problem', async (ctx) => {
    const user = await findUserByMaxId(ctx.user.user_id);
    if (user?.role !== 'employee') return ctx.answerOnCallback({ notification: 'Доступно сотрудникам.' });
    await ctx.answerOnCallback({ notification: 'Опишите проблему' });
    return startReport(ctx.user.user_id);
  });
  bot.action('oa:food', async (ctx) => {
    const ob = await findByUser((await findUserByMaxId(ctx.user.user_id))?.id);
    if (!ob) return ctx.answerOnCallback({ notification: 'Недоступно.' });
    await ctx.answerOnCallback({ notification: 'Где пообедать' });
    return engine.sendBlock(ob, 'food');
  });

  // ----- команды
  bot.command(/^hr$/i, async (ctx) => {
    const user = await findUserByMaxId(ctx.user.user_id);
    if (user?.role !== 'employee') return ctx.reply('Команда для сотрудников. Откройте /menu.');
    return openContactTicket(ctx, user);
  });
  bot.command(/^food$/i, async (ctx) => {
    const user = await findUserByMaxId(ctx.user.user_id);
    if (user?.role !== 'employee') return ctx.reply('Команда для сотрудников.');
    const ob = await findByUser(user.id);
    return engine.sendBlock(ob ?? { userId: user.id, maxId: String(ctx.user.user_id), name: user.name, tag: user.tag }, 'food');
  });

  // ----- текст: «передать проблему», дата выхода от HR, ответы на вопросы сценария
  bot.use(async (ctx, next) => {
    if (ctx.updateType !== 'message_created') return next();
    const text = ctx.message?.body?.text;
    if (text?.startsWith('/')) return next();
    const maxId = ctx.user.user_id;
    const state = await getState(maxId);

    if (state.step === 'report_problem') {
      const user = await findUserByMaxId(maxId);
      if (!text?.trim()) return ctx.reply('Опишите проблему текстом.');
      await setState(maxId, { step: 'idle', data: {} });
      return openTicket(ctx, user, { question: text.trim(), answer: null, source: 'report' });
    }
    if (state.step === 'ob_date') {
      const hr = await findUserByMaxId(maxId);
      const emp = isHr(hr) ? await allowedEmployee(hr, state.data.userId) : null;
      if (!emp) { await setState(maxId, { step: 'idle', data: {} }); return ctx.reply('Недостаточно прав.'); }
      const iso = parseDate(text);
      if (!iso) return ctx.reply('Не понял дату. Формат: ДД.ММ.ГГГГ, например 02.11.2026. Отмена: /cancel.');
      await setState(maxId, { step: 'idle', data: {} });
      const applied = await engine.setStartDate(emp.id, iso);
      if (applied) await setUserStartDate(emp.id, applied);
      return ctx.reply(applied
        ? `Дата выхода: ${fmtDate(applied)}${applied !== iso ? ' (выходной сдвинут на понедельник)' : ''}. Расписание пересчитано.`
        : 'У сотрудника не запущен сценарий.');
    }
    if (state.step !== 'idle' && state.step !== 'start') return next();   // чужой шаг диалога: решают другие обработчики

    const res = await engine.onText({ maxId, text, attachments: ctx.message?.body?.attachments ?? [] });
    if (!res) return next();
    res.resume?.().catch((err) => console.error('onboarding text resume failed', err));
    return null;
  });

  // ----- меню HR: «Мои сотрудники»
  const hrAction = (re, handler) => bot.action(re, async (ctx) => {
    const hr = await findUserByMaxId(ctx.user.user_id);
    if (!isHr(hr)) return ctx.answerOnCallback({ notification: 'Недостаточно прав.' });
    return handler(ctx, hr);
  });
  hrAction(/^ob:emps:(\d+)$/, (ctx, hr) => showEmployees(ctx, hr, Number(ctx.match[1]), 'Сотрудники'));
  hrAction(/^ob:emp:([0-9a-f-]{36})$/, async (ctx, hr) => {
    const emp = await allowedEmployee(hr, ctx.match[1]);
    if (!emp) return ctx.answerOnCallback({ notification: 'Сотрудник не найден.' });
    return showEmployeeCard(ctx, emp, `${emp.surname}`);
  });
  hrAction(/^ob:(pause|resume):([0-9a-f-]{36})$/, async (ctx, hr) => {
    const emp = await allowedEmployee(hr, ctx.match[2]);
    if (!emp) return ctx.answerOnCallback({ notification: 'Сотрудник не найден.' });
    await engine.setStatus(emp.id, ctx.match[1] === 'pause' ? 'paused' : 'active');
    return showEmployeeCard(ctx, emp, ctx.match[1] === 'pause' ? 'На паузе' : 'Продолжено');
  });
  hrAction(/^ob:date:([0-9a-f-]{36})$/, async (ctx, hr) => {
    const emp = await allowedEmployee(hr, ctx.match[1]);
    if (!emp) return ctx.answerOnCallback({ notification: 'Сотрудник не найден.' });
    await setState(ctx.user.user_id, { step: 'ob_date', data: { userId: emp.id } });
    return show(ctx, `Введите новую дату выхода сотрудника ${emp.surname} ${emp.name} в формате ДД.ММ.ГГГГ (например, 02.11.2026). Отмена: /cancel.`, null, 'Дата выхода');
  });
  hrAction(/^ob:launch:([0-9a-f-]{36})$/, async (ctx, hr) => {
    const emp = await allowedEmployee(hr, ctx.match[1]);
    if (!emp) return ctx.answerOnCallback({ notification: 'Сотрудник не найден.' });
    await startOnboardingFor(emp.id);
    return showEmployeeCard(ctx, emp, 'Сценарий запущен');
  });
}

// Планировщик: проход раз в TICK_MS; пересекающиеся проходы пропускаются
export function startScheduler() {
  if (timer) return;
  const pass = async () => {
    if (running) return;
    running = true;
    try {
      await engine.tick();
    } catch (err) {
      console.error('onboarding tick failed', err);
    } finally {
      running = false;
    }
  };
  timer = setInterval(pass, TICK_MS);
  pass();
  console.log(`onboarding scheduler started (${TICK_MS / 1000}s)`);
}

export function stopScheduler() {
  clearInterval(timer);
  timer = null;
}

export { render as renderScenarioText };
