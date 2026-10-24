import { Keyboard } from '@maxhub/max-bot-api';
import { config } from './config.js';
import { findEmployeeForAlert, findUserByMaxId, listHrRecipients } from './db.js';
import { newConsumer } from './kafka.js';
import { mdb } from './mongo.js';
import { redis } from './state.js';
import { isHr } from './invite.js';
import { CATEGORY_TITLES } from './support.js';
import { findByNumber } from './tickets.js';
import { show } from './ui.js';

// Срочная оценка настроения (urgent) приходит из ML в Kafka onboarding.alerts -> уведомление закреплённому HR.
// Нет закреплённого HR — всем hr и hr_head. Повторы по одному сотруднику — не чаще раза в ALERT_THROTTLE_SECONDS.
// Сообщение: { type: 'red_flag', uid, ts, source: 'bot_question' | 'ticket', ticket_number?, mood, reason, explanation, text? }

const ALERT_THROTTLE_SECONDS = 60 * 60;
const GROUP_ID = 'onboarding-bot-alerts';
const RETRY_MS = 30000;
const MAX_TEXT = 300;

const truncate = (text, len) => (text.length > len ? `${text.slice(0, len - 1)}…` : text);
const timeFmt = new Intl.DateTimeFormat('ru-RU', {
  timeZone: 'Europe/Moscow', day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit',
});
const fmtTime = (date) => timeFmt.format(new Date(date)).replace(',', '');
const stats = mdb.collection('stats');

function alertText(alert, emp) {
  const who = [`${emp.name} ${emp.surname}`, emp.tag, emp.director ? `рук. ${emp.director}` : null].filter(Boolean).join(', ');
  const lines = [`🚩 Срочно: ${who}`];
  lines.push(`Тема: ${CATEGORY_TITLES[alert.reason] ?? alert.reason}`);
  if (alert.explanation) lines.push(`Что случилось: ${alert.explanation}`);
  if (alert.source === 'ticket' && alert.ticket_number) lines.push(`Источник: обращение №${alert.ticket_number}`);
  else if (alert.text) lines.push(`Источник: вопрос боту — «${truncate(alert.text, MAX_TEXT)}»`);
  return lines.join('\n');
}

// Обработка одного алерта; api — bot.api. Возвращает кому отправлено (для логов и тестов).
export async function handleAlert(api, alert) {
  if (alert?.type !== 'red_flag' || !alert.uid) return { skipped: 'not_alert' };
  const emp = await findEmployeeForAlert(alert.uid);
  if (!emp) return { skipped: 'no_employee' };

  // троттлинг по сотруднику: SET NX — если ключ уже есть, недавно уже уведомляли
  const fresh = await redis.set(`alert:${emp.id}`, '1', 'EX', ALERT_THROTTLE_SECONDS, 'NX');
  if (!fresh) return { skipped: 'throttled' };

  const recipients = emp.pinnedHr ? [emp.pinnedHr] : await listHrRecipients();
  if (!recipients.length) return { skipped: 'no_recipients' };

  const rows = [];
  if (alert.source === 'ticket' && alert.ticket_number) {
    const ticket = await findByNumber(alert.ticket_number, emp.id);
    if (ticket) rows.push([Keyboard.button.callback('Открыть обращение', `tk:new:${ticket._id}`)]);
  }
  rows.push([Keyboard.button.callback('Флаг обработан', `rf:reset:${emp.id}`)]);
  const text = alertText(alert, emp);

  const sent = [];
  for (const r of recipients) {
    try {
      await api.sendMessageToUser(Number(r.maxId), text, { attachments: [Keyboard.inlineKeyboard(rows)] });
      sent.push(String(r.maxId));
    } catch (err) {
      console.error('red flag notification failed', emp.id, r.maxId, err);
    }
  }
  return { sent };
}

// hr_head — по любым сотрудникам, hr — по своим и по тем, у кого HR не закреплён
const canReset = (hr, emp) => hr.role === 'hr_head' || !emp.hrId || emp.hrId === hr.id;

export function registerAlerts(bot) {
  bot.action(/^rf:reset:([0-9a-f-]{36})$/, async (ctx) => {
    const hr = await findUserByMaxId(ctx.user.user_id);
    if (!isHr(hr)) return ctx.answerOnCallback({ notification: 'Недостаточно прав.' });
    const emp = await findEmployeeForAlert(ctx.match[1]);
    if (!emp) return ctx.answerOnCallback({ notification: 'Сотрудник не найден.' });
    if (!canReset(hr, emp)) return ctx.answerOnCallback({ notification: 'Это не ваш сотрудник.' });

    const now = new Date();
    await stats.updateOne(
      { uid: emp.id },
      { $set: { red_flag: false, last_reason: '', red_flag_reset_at: now, red_flag_reset_by: hr.id } },
    );
    await redis.del(`alert:${emp.id}`);   // следующая срочная оценка снова уведомит сразу
    const original = ctx.message?.body?.text;
    const done = `✅ Флаг по ${emp.name} ${emp.surname} снят (${hr.name} ${hr.surname[0]}., ${fmtTime(now)}).`;
    return show(ctx, original ? `${original}\n\n${done}` : done, null, 'Флаг снят');
  });
}

// ---------- консьюмер Kafka ----------

let consumer = null;
let stopped = false;
let retryTimer = null;

async function run(api) {
  const c = newConsumer(GROUP_ID);
  await c.connect();
  await c.subscribe({ topic: config.kafka.alertsTopic, fromBeginning: false });
  await c.run({
    eachMessage: async ({ message }) => {
      try {
        const alert = JSON.parse(message.value.toString('utf8'));
        const res = await handleAlert(api, alert);
        console.log('red flag alert', alert.uid, JSON.stringify(res));
      } catch (err) {
        // не зацикливаемся на битом сообщении: ошибка в лог, смещение двигается дальше
        console.error('red flag alert failed', err);
      }
    },
  });
  consumer = c;
}

// Сбой Kafka (топика ещё нет, брокер перезапускается) не должен ронять бота: пробуем снова.
export async function startAlerts(api) {
  if (stopped) return;
  try {
    await run(api);
    console.log('alerts consumer started');
  } catch (err) {
    console.error('alerts consumer failed, retry in 30s', err.message);
    if (consumer) await consumer.disconnect().catch(() => {});
    consumer = null;
    retryTimer = setTimeout(() => startAlerts(api), RETRY_MS);
  }
}

export async function stopAlerts() {
  stopped = true;
  clearTimeout(retryTimer);
  if (consumer) await consumer.disconnect().catch(() => {});
}
