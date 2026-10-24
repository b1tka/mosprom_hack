import crypto from 'node:crypto';
import http from 'node:http';
import { config } from './config.js';
import {
  closeTicketAsHr, relayHrMessage, reopenTicketAsHr,
} from './support.js';
import { findUserById, setUserStartDate } from './db.js';
import { getEngine, startOnboardingFor } from './onboarding/register.js';
import { getTicket } from './tickets.js';

// Внутренний HTTP для тестовой админки (admin/): порт доступен только в сети compose.
// Заголовок x-admin-token == ADMIN_API_TOKEN. Действия над тикетами идут через бота,
// потому что только он отправляет сообщения в MAX и ведёт шаги диалога.
//   GET  /internal/health
//   POST /internal/tickets/:id/reply  { text }
//   POST /internal/tickets/:id/close
//   POST /internal/tickets/:id/reopen

const MAX_BODY = 64 * 1024;

const safeEqual = (a, b) => {
  const x = Buffer.from(a);
  const y = Buffer.from(b);
  return x.length === y.length && crypto.timingSafeEqual(x, y);
};

function readJson(req) {
  return new Promise((resolve, reject) => {
    let size = 0;
    const chunks = [];
    req.on('data', (c) => {
      size += c.length;
      if (size > MAX_BODY) { reject(new Error('body too large')); req.destroy(); return; }
      chunks.push(c);
    });
    req.on('end', () => {
      try { resolve(chunks.length ? JSON.parse(Buffer.concat(chunks).toString('utf8')) : {}); } catch (err) { reject(err); }
    });
    req.on('error', reject);
  });
}

// Действия админки над онбордингом сотрудника: запуск, следующий шаг сейчас, сдвиг времени, пауза, дата выхода
async function onboardingAction(userId, action, body) {
  const engine = getEngine();
  const user = await findUserById(userId);
  if (!engine || !user || user.role !== 'employee') return { ok: false, error: 'employee not found' };
  if (action === 'start') {
    const res = await startOnboardingFor(userId, { startDate: body.date || undefined });
    return res ? { ok: true, created: res.created, startDate: res.ob.startDate } : { ok: false, error: 'not started' };
  }
  if (action === 'next') {
    const id = await engine.runNext(userId);
    return id ? { ok: true, step: id } : { ok: false, error: 'нет ожидающих шагов' };
  }
  if (action === 'shift') {
    const days = Number(body.days);
    if (!Number.isInteger(days) || Math.abs(days) > 400) return { ok: false, error: 'days: целое от -400 до 400' };
    return { ok: await engine.shift(userId, days), days };
  }
  if (action === 'pause' || action === 'resume') {
    await engine.setStatus(userId, action === 'pause' ? 'paused' : 'active');
    return { ok: true };
  }
  const date = await engine.setStartDate(userId, String(body.date ?? ''));
  if (!date) return { ok: false, error: 'сценарий не запущен или неверная дата' };
  await setUserStartDate(userId, date);
  return { ok: true, startDate: date };
}

export function makeHandler(api) {
  return async (req, res) => {
    const send = (code, body) => {
      res.writeHead(code, { 'Content-Type': 'application/json; charset=utf-8' });
      res.end(JSON.stringify(body));
    };
    try {
      const token = String(req.headers['x-admin-token'] ?? '');
      if (!config.admin.apiToken || !safeEqual(token, config.admin.apiToken)) return send(401, { error: 'unauthorized' });
      const url = new URL(req.url, 'http://x');
      if (req.method === 'GET' && url.pathname === '/internal/health') return send(200, { ok: true });

      const ob = url.pathname.match(/^\/internal\/onboarding\/([0-9a-f-]{36})\/(start|next|shift|pause|resume|startdate)$/);
      if (req.method === 'POST' && ob) return send(200, await onboardingAction(ob[1], ob[2], await readJson(req)));

      const m = url.pathname.match(/^\/internal\/tickets\/([0-9a-f]{24})\/(reply|close|reopen)$/);
      if (req.method !== 'POST' || !m) return send(404, { error: 'not found' });
      const ticket = await getTicket(m[1]);
      if (!ticket) return send(404, { error: 'ticket not found' });

      if (m[2] === 'reply') {
        const { text } = await readJson(req);
        if (typeof text !== 'string' || !text.trim()) return send(400, { error: 'text required' });
        const r = await relayHrMessage(api, null, ticket, text.trim().slice(0, 3000));
        return send(r.status === 'sent' ? 200 : 409, r);
      }
      if (m[2] === 'close') {
        const closed = await closeTicketAsHr(api, ticket);
        return closed ? send(200, { status: 'closed' }) : send(409, { error: 'already closed' });
      }
      const reopened = await reopenTicketAsHr(api, ticket);
      return reopened ? send(200, { status: 'open' }) : send(409, { error: 'already open' });
    } catch (err) {
      console.error('internal api failed', err);
      return send(500, { error: 'internal error' });
    }
  };
}

let server = null;

export function startInternalApi(api) {
  if (!config.admin.apiToken) return;   // без токена внутренний API не поднимается
  server = http.createServer(makeHandler(api));
  server.listen(config.admin.internalPort, '0.0.0.0', () => console.log(`internal api on :${config.admin.internalPort}`));
}

export function stopInternalApi() {
  server?.close();
}
