import crypto from 'node:crypto';
import { Keyboard } from '@maxhub/max-bot-api';
import { findEmployeeContacts, findUserByMaxId } from './db.js';
import { isHr } from './invite.js';
import { ticketTitle } from './ml.js';
import { sendTicketDialog } from './kafka.js';
import { getState, redis, setState } from './state.js';
import { show } from './ui.js';
import {
  addMessage, closeTicket, createTicket, getTicket, listClosedForHr, listForEmployee, listOpenForHr,
  markRead, nextUnreadForHr, reopenTicket, setTitle,
} from './tickets.js';

// Обращения (тикеты) сотрудника к закреплённому HR. Данные — в Mongo (tickets.js), шаги диалога — в Redis:
// сотрудник в чате с HR — 'ticket_chat' { ticketId }, HR отвечает в тикет — 'ticket_reply' { ticketId }.

const PAGE_SIZE = 10;
const CARD_MESSAGES = 10;
const MAX_MESSAGE_PREVIEW = 300;
const MAX_CARD_LEN = 3900;          // лимит текста сообщения в MAX — 4000
const MAX_LABEL_LEN = 60;
const FALLBACK_TITLE_LEN = 60;
const LASTQA_TTL_SECONDS = 7 * 24 * 60 * 60;
const RELAY_TYPES = ['image', 'video', 'audio', 'file'];

export const CATEGORY_TITLES = {
  technical_issues: 'технические проблемы',
  conflict: 'конфликт',
  workload: 'нагрузка',
  work_question: 'рабочий вопрос',
  gratitude: 'благодарность',
  absence: 'отпуск, больничный',
  finance: 'финансы',
  documents: 'документы',
};

const truncate = (text, len) => (text.length > len ? `${text.slice(0, len - 1)}…` : text);
const shortName = (p) => `${p.name} ${p.surname ? `${p.surname[0]}.` : ''}`.trim();
const fullName = (p) => `${p.name} ${p.surname}`;
const timeFmt = new Intl.DateTimeFormat('ru-RU', {
  timeZone: 'Europe/Moscow', day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit',
});
const fmtTime = (date) => timeFmt.format(new Date(date)).replace(',', '');
const kb = (rows) => Keyboard.inlineKeyboard(rows);
const btn = (text, payload) => Keyboard.button.callback(text, payload);
const idOf = (t) => String(t._id);
const idle = { step: 'idle', data: {} };

// Диалог закрытого тикета уходит на оценку настроения; сбой Kafka закрытию не мешает и ответа не ждёт
const publishDialog = (ticket) => sendTicketDialog(ticket)
  .catch((err) => console.error('ticket dialog to kafka failed', ticket.number, err));

// hr видит тикеты своих сотрудников, hr_head — все
export const hrScope = (hr) => (hr.role === 'hr_head' ? {} : { hrId: hr.id });
const canSee = (hr, t) => hr.role === 'hr_head' || t.hrId === hr.id;

// ---------- вложения ----------

function extractAttachments(ctx) {
  return (ctx.message?.body?.attachments ?? [])
    .filter((a) => RELAY_TYPES.includes(a.type) && a.payload?.token)
    .map((a) => ({ type: a.type, filename: a.filename ?? null, token: a.payload.token, url: a.payload.url ?? null }));
}

const describeAttachment = (a) => (a.type === 'image' ? '🖼 фото' : `📎 ${a.filename ?? a.type}`);

async function reupload(api, a) {
  const res = await fetch(a.url, { signal: AbortSignal.timeout(120000) });
  if (!res.ok) throw new Error(`attachment download HTTP ${res.status}`);
  const source = Buffer.from(await res.arrayBuffer());
  const upload = { image: api.uploadImage, video: api.uploadVideo, audio: api.uploadAudio, file: api.uploadFile }[a.type];
  return (await upload.call(api, { source })).toJson();
}

// Пересылка: сначала по token исходного вложения; если MAX его не принял — скачиваем по url и загружаем заново;
// в крайнем случае — только текст с пометкой, что вложения не дошли.
async function sendRelay(api, maxId, text, attachments, keyboard) {
  const extra = (media) => ({ attachments: [...media, ...(keyboard ? [keyboard] : [])] });
  if (!attachments.length) return api.sendMessageToUser(Number(maxId), text, keyboard ? extra([]) : undefined);
  try {
    return await api.sendMessageToUser(Number(maxId), text, extra(attachments.map((a) => ({ type: a.type, payload: { token: a.token } }))));
  } catch (err) {
    console.error('relay by token failed, re-uploading', err);
  }
  try {
    const media = [];
    for (const a of attachments) media.push(await reupload(api, a));
    return await api.sendMessageToUser(Number(maxId), text, extra(media));
  } catch (err) {
    console.error('relay re-upload failed', err);
  }
  const names = attachments.map(describeAttachment).join(', ');
  return api.sendMessageToUser(Number(maxId), `${text}\n\n(вложения не удалось переслать: ${names})`, keyboard ? extra([]) : undefined);
}

// ---------- текст карточки ----------

function messageLine(m, viewer) {
  let who;
  if (m.from === 'bot') who = 'Бот';
  else if (m.from === 'hr') who = viewer.side === 'hr' && m.authorId === viewer.userId ? 'Вы' : 'HR';
  else who = viewer.side === 'employee' ? 'Вы' : viewer.employeeName;
  const parts = [m.text ? truncate(m.text, MAX_MESSAGE_PREVIEW) : '', ...(m.attachments ?? []).map(describeAttachment)].filter(Boolean);
  return `${fmtTime(m.at)} ${who}: ${parts.join(' ')}`;
}

function messagesBlock(t, viewer, budget) {
  const lines = t.messages.slice(-CARD_MESSAGES).map((m) => messageLine(m, viewer));
  // самые старые выкидываем, пока карточка не влезет в лимит
  while (lines.length > 1 && lines.join('\n').length > budget) lines.shift();
  const hidden = t.messages.length - lines.length;
  return [...(hidden > 0 ? [`(ещё ранних сообщений: ${hidden})`] : []), ...lines].join('\n');
}

function statusLine(t) {
  const created = `Создано ${fmtTime(t.createdAt)} · ${t.source === 'auto' ? 'авто (бот не знал ответа)' : 'по кнопке'}`;
  if (t.status !== 'closed') return created;
  return `${created}\nЗакрыто ${fmtTime(t.closedAt)} · ${t.closedBy === 'employee' ? 'сотрудником' : 'HR'}`;
}

function hrCardText(t, hr) {
  const e = t.employee;
  const head = [
    `№${t.number} · ${t.title}`,
    ...(t.category ? [`Категория: ${CATEGORY_TITLES[t.category] ?? t.category}`] : []),
    [fullName(e), e.tag, e.director ? `рук. ${e.director}` : null].filter(Boolean).join(' · '),
    statusLine(t),
    '─────',
  ].join('\n');
  const foot = t.status === 'open' ? `─────\n✏ Пишите — уйдёт: ${e.name}` : '─────';
  const viewer = { side: 'hr', userId: hr.id, employeeName: e.name };
  return [head, messagesBlock(t, viewer, MAX_CARD_LEN - head.length - foot.length - 2), foot].join('\n');
}

function employeeCardText(t) {
  const head = [`№${t.number} · ${t.title}`, statusLine(t), '─────'].join('\n');
  return [head, messagesBlock(t, { side: 'employee' }, MAX_CARD_LEN - head.length - 1)].join('\n');
}

// ---------- создание тикета ----------

async function rememberQa(maxId, question, answer) {
  const qid = crypto.randomBytes(6).toString('hex');
  await redis.set(`lastqa:${maxId}:${qid}`, JSON.stringify({ question, answer }), 'EX', LASTQA_TTL_SECONDS);
  return qid;
}

async function refineTitle(ticket, question) {
  try {
    await setTitle(ticket._id, await ticketTitle(question));
  } catch (err) {
    console.error('ticket title failed, keeping fallback', ticket.number, err.message);
  }
}

// Тикет из вопроса сотрудника (+ ответ бота как контекст), сотрудник входит в чат с HR, HR получает уведомление.
async function openTicket(ctx, user, { question, answer, source }) {
  const maxId = ctx.user.user_id;
  const contacts = await findEmployeeContacts(user.id);
  if (!contacts?.hr) {
    return ctx.reply('За вами не закреплён HR-специалист, поэтому создать обращение не получится. Обратитесь в отдел кадров.');
  }
  const now = new Date();
  const messages = [{ at: now, from: 'employee', authorId: user.id, text: question, attachments: [] }];
  if (answer) messages.push({ at: now, from: 'bot', authorId: null, text: answer, attachments: [] });
  const ticket = await createTicket({
    employee: {
      id: user.id, maxId: String(maxId), name: user.name, surname: user.surname, tag: user.tag ?? null, director: contacts.director,
    },
    hr: contacts.hr,
    source,
    title: truncate(question.replace(/\s+/g, ' ').trim(), FALLBACK_TITLE_LEN),
    messages,
  });
  await setState(maxId, { step: 'ticket_chat', data: { ticketId: idOf(ticket) } });
  await ctx.reply(
    `Создано обращение №${ticket.number}. Вы в чате с HR (${shortName(contacts.hr)}): всё, что вы напишете, получит HR. `
      + 'Чтобы снова задавать вопросы боту, нажмите кнопку ниже или напишите /bot.',
    { attachments: [kb([[btn('Вернуться к боту', 'tk:exit')]])] },
  );
  try {
    await ctx.api.sendMessageToUser(
      Number(contacts.hr.maxId),
      `🆕 Новое обращение №${ticket.number} от ${shortName(user)}:\n«${truncate(question, MAX_MESSAGE_PREVIEW)}»`,
      { attachments: [kb([[btn('Открыть', `tk:new:${idOf(ticket)}`)]])] },
    );
  } catch (err) {
    console.error('hr notification failed', ticket.number, err);
  }
  // тема от LLM — в фоне, до минуты; пока в списке временный заголовок
  refineTitle(ticket, question);
  return ticket;
}

// Ответ RAG сотруднику (index.js). 200 — ответ + кнопка «Связаться с HR»; 400 — ответ + тикет автоматически;
// result === null — ML недоступен, тоже предлагаем HR.
export async function replyToQuestion(ctx, user, question, result) {
  if (result?.code === 400) {
    await ctx.reply(result.answer);
    return openTicket(ctx, user, { question, answer: result.answer, source: 'auto' });
  }
  const text = result ? result.answer : 'Сервис ответов временно недоступен, попробуйте позже.';
  const qid = await rememberQa(ctx.user.user_id, question, result?.answer ?? null);
  return ctx.reply(text, { attachments: [kb([[btn('Связаться с HR', `tk:ask:${qid}`)]])] });
}

// ---------- переписка ----------

async function employeeMessage(ctx, user, state) {
  const maxId = ctx.user.user_id;
  const ticket = await getTicket(state.data.ticketId);
  if (!ticket || ticket.employee.id !== user.id) {
    await setState(maxId, idle);
    return ctx.reply('Обращение не найдено. Вы снова общаетесь с ботом.');
  }
  const text = ctx.message?.body?.text ?? '';
  const attachments = extractAttachments(ctx);
  if (!text && !attachments.length) return ctx.reply('Можно отправить текст, фото или файл.');
  const updated = await addMessage(ticket._id, { at: new Date(), from: 'employee', authorId: user.id, text, attachments });
  if (!updated) {
    await setState(maxId, idle);
    return ctx.reply(`Обращение №${ticket.number} уже закрыто, сообщение не отправлено. Вы снова общаетесь с ботом.`);
  }

  // HR сейчас в этом тикете — просто реплика; иначе уведомление с кнопкой
  const hrState = await getState(ticket.hrMaxId);
  const hrInTicket = hrState.step === 'ticket_reply' && hrState.data.ticketId === idOf(ticket);
  try {
    if (hrInTicket) {
      await sendRelay(ctx.api, ticket.hrMaxId, `💬 ${shortName(ticket.employee)} (№${ticket.number}):\n${text}`, attachments);
      await markRead(ticket._id, 'hr');
    } else {
      await sendRelay(
        ctx.api,
        ticket.hrMaxId,
        `💬 Новое сообщение в №${ticket.number} «${updated.title}» от ${shortName(ticket.employee)}:\n${truncate(text, MAX_MESSAGE_PREVIEW)}`,
        attachments,
        kb([[btn('Открыть', `tk:new:${idOf(ticket)}`)]]),
      );
    }
  } catch (err) {
    console.error('relay to hr failed', ticket.number, err);
  }
}

async function hrMessage(ctx, hr, state) {
  const maxId = ctx.user.user_id;
  const ticket = await getTicket(state.data.ticketId);
  if (!ticket || !canSee(hr, ticket)) {
    await setState(maxId, idle);
    return ctx.reply('Обращение не найдено. Откройте /menu.');
  }
  const text = ctx.message?.body?.text ?? '';
  const attachments = extractAttachments(ctx);
  if (!text && !attachments.length) return ctx.reply('Можно отправить текст, фото или файл.');
  const res = await relayHrMessage(ctx.api, hr.id, ticket, text, attachments);
  if (res.status === 'closed') {
    await setState(maxId, idle);
    return ctx.reply(`Обращение №${ticket.number} закрыто, сообщение не отправлено. Переоткрыть можно из архива.`);
  }
  if (res.status === 'delivery_failed') return ctx.reply(`Не удалось доставить сообщение сотруднику (№${ticket.number}). Попробуйте позже.`);
  return ctx.reply(`→ №${ticket.number}, ${shortName(ticket.employee)}`);
}

// Ответ HR в тикет: общая часть для бота и админки. hrId может быть null (ответ из админки).
// -> { status: 'sent' | 'closed' | 'delivery_failed' }
export async function relayHrMessage(api, hrId, ticket, text, attachments = []) {
  const updated = await addMessage(ticket._id, { at: new Date(), from: 'hr', authorId: hrId, text, attachments });
  if (!updated) return { status: 'closed' };
  const empState = await getState(ticket.employee.maxId);
  const empInTicket = empState.step === 'ticket_chat' && empState.data.ticketId === idOf(ticket);
  const rows = [[btn('Вопрос решён', `tk:solve:${idOf(ticket)}`)]];
  if (!empInTicket) rows.unshift([btn('Ответить', `tk:chat:${idOf(ticket)}`)]);
  try {
    await sendRelay(api, ticket.employee.maxId, `👤 HR по №${ticket.number} «${updated.title}»:\n${text}`, attachments, kb(rows));
    if (empInTicket) await markRead(ticket._id, 'employee');
  } catch (err) {
    console.error('relay to employee failed', ticket.number, err);
    return { status: 'delivery_failed' };
  }
  return { status: 'sent' };
}

// Закрытие тикета со стороны HR (кнопка в боте и админка): диалог на оценку настроения, сброс шагов, уведомление сотруднику.
// null — тикет уже закрыт или не найден.
export async function closeTicketAsHr(api, ticket) {
  const closed = await closeTicket(ticket._id, 'hr');
  if (!closed) return null;
  publishDialog(closed);
  const id = idOf(ticket);
  for (const maxId of [ticket.hrMaxId, ticket.employee.maxId]) {
    const st = await getState(maxId);
    if (['ticket_reply', 'ticket_chat'].includes(st.step) && st.data.ticketId === id) await setState(maxId, idle);
  }
  try {
    await api.sendMessageToUser(
      Number(ticket.employee.maxId),
      `✅ HR закрыл обращение №${ticket.number} «${closed.title}». Если вопрос остался — задайте его боту, `
        + 'под ответом будет кнопка «Связаться с HR».',
    );
  } catch (err) {
    console.error('employee close notification failed', ticket.number, err);
  }
  return closed;
}

// Переоткрытие тикета HR-ом (кнопка в боте и админка). null — тикет уже открыт или не найден.
export async function reopenTicketAsHr(api, ticket) {
  const reopened = await reopenTicket(ticket._id);
  if (!reopened) return null;
  try {
    await api.sendMessageToUser(
      Number(ticket.employee.maxId),
      `HR снова открыл обращение №${ticket.number} «${reopened.title}».`,
      { attachments: [kb([[btn('Ответить', `tk:chat:${idOf(ticket)}`)]])] },
    );
  } catch (err) {
    console.error('employee reopen notification failed', ticket.number, err);
  }
  return reopened;
}

// ---------- экраны HR ----------

async function showHrCard(ctx, hr, ticket, notification, opts) {
  const maxId = ctx.user.user_id;
  await markRead(ticket._id, 'hr');
  const id = idOf(ticket);
  let rows;
  if (ticket.status === 'open') {
    await setState(maxId, { step: 'ticket_reply', data: { ticketId: id } });
    rows = [
      [btn('✅ Закрыть', `tk:close:${id}`), btn('След. непроч. →', `tk:next:${id}`)],
      [btn('← К обращениям', 'tk:list:0')],
    ];
  } else {
    await setState(maxId, idle);
    rows = [[btn('Переоткрыть', `tk:reopen:${id}`)], [btn('← Архив', 'tk:arch:0')]];
  }
  return show(ctx, hrCardText(ticket, hr), kb(rows), notification, opts);
}

function pageNav(prefix, page, total) {
  const nav = [];
  if (page > 0) nav.push(btn('← Назад', `${prefix}:${page - 1}`));
  if ((page + 1) * PAGE_SIZE < total) nav.push(btn('Вперёд →', `${prefix}:${page + 1}`));
  return nav.length ? [nav] : [];
}

const pagesSuffix = (page, total) => (total > PAGE_SIZE ? `, стр. ${page + 1} из ${Math.ceil(total / PAGE_SIZE)}` : '');

async function showHrList(ctx, hr, page, notification) {
  const { items, total } = await listOpenForHr(hrScope(hr), { skip: page * PAGE_SIZE, limit: PAGE_SIZE });
  const rows = items.map((t) => {
    const unread = t.unreadHr ? `●${t.unreadHr} ` : '';
    return [btn(truncate(`${unread}№${t.number} · ${shortName(t.employee)} · ${t.title}`, MAX_LABEL_LEN), `tk:open:${idOf(t)}`)];
  });
  rows.push(...pageNav('tk:list', page, total));
  rows.push([btn('Архив', 'tk:arch:0'), btn('← Меню', 'menu:main')]);
  const title = total ? `Обращения (открытых: ${total}${pagesSuffix(page, total)}):` : 'Открытых обращений нет.';
  return show(ctx, title, kb(rows), notification);
}

async function showHrArchive(ctx, hr, page, notification) {
  const { items, total } = await listClosedForHr(hrScope(hr), { skip: page * PAGE_SIZE, limit: PAGE_SIZE });
  const rows = items.map((t) => [btn(truncate(`№${t.number} · ${shortName(t.employee)} · ${t.title}`, MAX_LABEL_LEN), `tk:open:${idOf(t)}`)]);
  rows.push(...pageNav('tk:arch', page, total));
  rows.push([btn('← К обращениям', 'tk:list:0')]);
  const title = total ? `Архив обращений (${total}${pagesSuffix(page, total)}):` : 'Архив пуст.';
  return show(ctx, title, kb(rows), notification);
}

// ---------- экраны сотрудника ----------

async function showEmployeeList(ctx, user, status, page, notification) {
  const { items, total } = await listForEmployee(user.id, status, { skip: page * PAGE_SIZE, limit: PAGE_SIZE });
  const rows = items.map((t) => {
    const unread = t.unreadEmployee ? '● ' : '';
    return [btn(truncate(`${unread}№${t.number} · ${t.title}`, MAX_LABEL_LEN), `my:open:${idOf(t)}`)];
  });
  rows.push(...pageNav(`my:list:${status}`, page, total));
  rows.push(status === 'open'
    ? [btn('Архив', 'my:list:closed:0'), btn('← Меню', 'menu:main')]
    : [btn('← Открытые', 'my:list:open:0')]);
  let title;
  if (status === 'open') title = total ? `Ваши открытые обращения (${total}${pagesSuffix(page, total)}):` : 'Открытых обращений нет.';
  else title = total ? `Архив ваших обращений (${total}${pagesSuffix(page, total)}):` : 'Архив пуст.';
  return show(ctx, title, kb(rows), notification);
}

async function showEmployeeCard(ctx, ticket, notification) {
  await markRead(ticket._id, 'employee');
  const id = idOf(ticket);
  const rows = ticket.status === 'open'
    ? [[btn('Написать', `tk:chat:${id}`), btn('Вопрос решён', `tk:solve:${id}`)], [btn('← Мои обращения', 'my:list:open:0')]]
    : [[btn('← Архив', 'my:list:closed:0')]];
  return show(ctx, employeeCardText(ticket), kb(rows), notification);
}

async function enterChat(ctx, ticket) {
  await setState(ctx.user.user_id, { step: 'ticket_chat', data: { ticketId: idOf(ticket) } });
  await markRead(ticket._id, 'employee');
  return ctx.reply(
    `Вы в чате по обращению №${ticket.number} «${ticket.title}». Пишите — получит HR. Вернуться к боту: кнопка ниже или /bot.`,
    { attachments: [kb([[btn('Вернуться к боту', 'tk:exit')]])] },
  );
}

const BACK_TO_BOT = 'Вы снова общаетесь с ботом: задавайте вопросы. Обращения к HR — в /menu.';

// ---------- подключение к боту ----------

export function registerSupport(bot) {
  bot.command('bot', async (ctx) => {
    const maxId = ctx.user.user_id;
    const { step } = await getState(maxId);
    if (step === 'ticket_reply') {
      await setState(maxId, idle);
      return ctx.reply('Вы вышли из обращения. Обращения — в /menu.');
    }
    await setState(maxId, idle);
    return ctx.reply(BACK_TO_BOT);
  });

  // сообщения в режиме чата (сотрудник) и ответа в тикет (HR); команды проходят дальше
  bot.use(async (ctx, next) => {
    if (ctx.updateType !== 'message_created') return next();
    if (ctx.message?.body?.text?.startsWith('/')) return next();
    const maxId = ctx.user.user_id;
    const state = await getState(maxId);
    if (state.step !== 'ticket_chat' && state.step !== 'ticket_reply') return next();

    const user = await findUserByMaxId(maxId);
    if (state.step === 'ticket_chat' && user?.role === 'employee') return employeeMessage(ctx, user, state);
    if (state.step === 'ticket_reply' && isHr(user)) return hrMessage(ctx, user, state);
    await setState(maxId, idle);
    return next();
  });

  // кнопки сотрудника: проверяем роль и что тикет его
  const employeeAction = (re, handler) => bot.action(re, async (ctx) => {
    const user = await findUserByMaxId(ctx.user.user_id);
    if (user?.role !== 'employee') return ctx.answerOnCallback({ notification: 'Действие недоступно.' });
    return handler(ctx, user);
  });
  const ownTicket = async (ctx, user, id) => {
    const ticket = await getTicket(id);
    if (!ticket || ticket.employee.id !== user.id) {
      await ctx.answerOnCallback({ notification: 'Обращение не найдено.' });
      return null;
    }
    return ticket;
  };

  employeeAction(/^tk:ask:([0-9a-f]+)$/, async (ctx, user) => {
    // GETDEL: повторное нажатие той же кнопки не создаст второй тикет
    const raw = await redis.getdel(`lastqa:${ctx.user.user_id}:${ctx.match[1]}`);
    if (!raw) return ctx.answerOnCallback({ notification: 'Кнопка устарела. Задайте вопрос ещё раз.' });
    await ctx.answerOnCallback({ notification: 'Создаю обращение…' });
    const { question, answer } = JSON.parse(raw);
    return openTicket(ctx, user, { question, answer, source: 'button' });
  });

  employeeAction('tk:exit', async (ctx) => {
    await setState(ctx.user.user_id, idle);
    return show(ctx, BACK_TO_BOT, null, 'Вы снова с ботом');
  });

  employeeAction(/^tk:chat:([0-9a-f]{24})$/, async (ctx, user) => {
    const ticket = await ownTicket(ctx, user, ctx.match[1]);
    if (!ticket) return null;
    if (ticket.status !== 'open') return ctx.answerOnCallback({ notification: 'Обращение уже закрыто.' });
    await ctx.answerOnCallback({ notification: `Чат по №${ticket.number}` });
    return enterChat(ctx, ticket);
  });

  employeeAction(/^tk:solve:([0-9a-f]{24})$/, async (ctx, user) => {
    const ticket = await ownTicket(ctx, user, ctx.match[1]);
    if (!ticket) return null;
    const closed = await closeTicket(ticket._id, 'employee');
    if (!closed) return ctx.answerOnCallback({ notification: 'Обращение уже закрыто.' });
    publishDialog(closed);
    const maxId = ctx.user.user_id;
    const state = await getState(maxId);
    if (state.step === 'ticket_chat' && state.data.ticketId === idOf(ticket)) await setState(maxId, idle);
    await ctx.answerOnCallback({ notification: 'Закрыто' });
    await ctx.reply(`Обращение №${ticket.number} закрыто. Спасибо! Вы снова общаетесь с ботом.`);
    try {
      await ctx.api.sendMessageToUser(
        Number(ticket.hrMaxId),
        `✅ ${shortName(ticket.employee)} закрыл(а) обращение №${ticket.number} «${closed.title}»: вопрос решён.`,
      );
    } catch (err) {
      console.error('hr close notification failed', ticket.number, err);
    }
    return null;
  });

  employeeAction(/^my:list:(open|closed):(\d+)$/, async (ctx, user) => {
    return showEmployeeList(ctx, user, ctx.match[1], Number(ctx.match[2]), 'Обращения');
  });

  employeeAction(/^my:open:([0-9a-f]{24})$/, async (ctx, user) => {
    const ticket = await ownTicket(ctx, user, ctx.match[1]);
    if (!ticket) return null;
    return showEmployeeCard(ctx, ticket, `№${ticket.number}`);
  });

  // кнопки HR: права и видимость тикета проверяются на каждом нажатии
  const hrAction = (re, handler) => bot.action(re, async (ctx) => {
    const hr = await findUserByMaxId(ctx.user.user_id);
    if (!isHr(hr)) return ctx.answerOnCallback({ notification: 'Недостаточно прав.' });
    return handler(ctx, hr);
  });
  const visibleTicket = async (ctx, hr, id) => {
    const ticket = await getTicket(id);
    if (!ticket || !canSee(hr, ticket)) {
      await ctx.answerOnCallback({ notification: 'Обращение не найдено.' });
      return null;
    }
    return ticket;
  };

  hrAction(/^tk:list:(\d+)$/, async (ctx, hr) => {
    await setState(ctx.user.user_id, idle);
    return showHrList(ctx, hr, Number(ctx.match[1]), 'Обращения');
  });

  hrAction(/^tk:arch:(\d+)$/, async (ctx, hr) => {
    await setState(ctx.user.user_id, idle);
    return showHrArchive(ctx, hr, Number(ctx.match[1]), 'Архив');
  });

  // tk:open — из списка (заменяем список карточкой), tk:new — из уведомления (уведомление остаётся, карточка новым сообщением)
  hrAction(/^tk:(open|new):([0-9a-f]{24})$/, async (ctx, hr) => {
    const ticket = await visibleTicket(ctx, hr, ctx.match[2]);
    if (!ticket) return null;
    return showHrCard(ctx, hr, ticket, `№${ticket.number}`, { fresh: ctx.match[1] === 'new' });
  });

  hrAction(/^tk:next:([0-9a-f]{24})$/, async (ctx, hr) => {
    const next = await nextUnreadForHr(hrScope(hr), ctx.match[1]);
    if (!next) return ctx.answerOnCallback({ notification: 'Больше непрочитанных нет.' });
    return showHrCard(ctx, hr, await getTicket(next._id), `№${next.number}`);
  });

  hrAction(/^tk:close:([0-9a-f]{24})$/, async (ctx, hr) => {
    const ticket = await visibleTicket(ctx, hr, ctx.match[1]);
    if (!ticket) return null;
    const closed = await closeTicketAsHr(ctx.api, ticket);
    if (!closed) return ctx.answerOnCallback({ notification: 'Обращение уже закрыто.' });
    await setState(ctx.user.user_id, idle);
    return show(
      ctx,
      `Обращение №${ticket.number} закрыто.`,
      kb([[btn('След. непроч. →', `tk:next:${idOf(ticket)}`)], [btn('← К обращениям', 'tk:list:0')]]),
      'Закрыто',
    );
  });

  hrAction(/^tk:reopen:([0-9a-f]{24})$/, async (ctx, hr) => {
    const ticket = await visibleTicket(ctx, hr, ctx.match[1]);
    if (!ticket) return null;
    const reopened = await reopenTicketAsHr(ctx.api, ticket);
    if (!reopened) return ctx.answerOnCallback({ notification: 'Обращение уже открыто.' });
    return showHrCard(ctx, hr, reopened, 'Переоткрыто');
  });
}
