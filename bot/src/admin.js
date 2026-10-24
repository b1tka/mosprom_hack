import crypto from 'node:crypto';
import { config } from './config.js';
import { findUserByMaxId } from './db.js';
import { isHr } from './invite.js';
import { redis } from './state.js';

// /admin: выдаёт одноразовый код входа в тестовую админку (admin/). Режим — ADMIN_ACCESS:
//   off    — команда отключена; hr — только hr/hr_head; anyone — любой пользователь бота (только для тестирования).
// Код живёт 5 минут и сгорает после использования (проверяет сервис admin по ключу Redis admin:code:<код>).
// Админка доступна только через SSH-туннель на 127.0.0.1, поэтому код без доступа к VPS бесполезен.

const CODE_TTL_SECONDS = 5 * 60;
const MAX_CODES_PER_MINUTE = 5;
const ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789';   // без похожих символов

const makeCode = () => Array.from({ length: 8 }, () => ALPHABET[crypto.randomInt(ALPHABET.length)]).join('');

export function registerAdmin(bot) {
  bot.command('admin', async (ctx) => {
    const { access, url } = config.admin;
    if (access === 'off') return ctx.reply('Админка отключена.');
    const maxId = ctx.user.user_id;
    const user = await findUserByMaxId(maxId);
    if (access === 'hr' && !isHr(user)) return ctx.reply('Недостаточно прав.');
    if (access !== 'hr' && access !== 'anyone') return ctx.reply('Админка настроена неверно (ADMIN_ACCESS).');

    const n = await redis.incr(`admin:codes:${maxId}`);
    if (n === 1) await redis.expire(`admin:codes:${maxId}`, 60);
    if (n > MAX_CODES_PER_MINUTE) return ctx.reply('Слишком много запросов. Попробуйте через минуту.');

    const code = makeCode();
    await redis.set(`admin:code:${code}`, JSON.stringify({ maxId: String(maxId), name: user ? `${user.name} ${user.surname}` : null, role: user?.role ?? null }), 'EX', CODE_TTL_SECONDS);
    return ctx.reply(
      `Код входа в админку: ${code}\nДействует 5 минут, один раз.\n\nАдрес (через SSH-туннель): ${url}\nСсылка со входом: ${url}/auth?code=${code}`,
    );
  });
}
