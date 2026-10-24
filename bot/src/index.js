import { Bot } from '@maxhub/max-bot-api';
import { config } from './config.js';
import { pool, findUserByMaxId } from './db.js';
import { redis, setState } from './state.js';
import { registerInvites } from './invite.js';
import { ask } from './ml.js';
import { connectKafka, disconnectKafka } from './kafka.js';

const bot = new Bot(config.botToken);

const ROLE_TITLES = {
  employee: 'новый сотрудник',
  director: 'руководитель подразделения',
  hr: 'HR-менеджер',
  hr_head: 'руководитель по подбору и развитию персонала',
};

bot.catch((err) => console.error('bot error', err));

registerInvites(bot);

// Нужен HR, чтобы внести пользователя в таблицу users
bot.command('id', (ctx) => ctx.reply(`Ваш max_id: ${ctx.user.user_id}`));

bot.command('start', async (ctx) => {
  const maxId = ctx.user.user_id;
  const user = await findUserByMaxId(maxId);
  if (!user) {
    return ctx.reply('Вас нет в системе. Обратитесь к HR: он выдаст вам QR-код для регистрации.');
  }
  await setState(maxId, { step: 'idle', data: {} });
  await ctx.reply(`Здравствуйте, ${user.name}! Ваша роль: ${ROLE_TITLES[user.role] ?? user.role}.`);
});

// Любое текстовое сообщение (не команда) -> ML-модуль, ответ придёт через Kafka
bot.on('message_created', async (ctx) => {
  const text = ctx.message?.body?.text;
  if (!text || text.startsWith('/')) return;

  const maxId = ctx.user.user_id;
  const user = await findUserByMaxId(maxId);
  if (!user) return ctx.reply('Вас нет в системе. Обратитесь к HR, чтобы вас добавили.');

  // Любое сообщение нового сотрудника — вопрос к RAG ML-сервиса; настроение ML считает сам, в фоне
  if (user.role !== 'employee') return ctx.reply('Откройте /menu, чтобы выбрать действие.');
  let result;
  try {
    result = await ask({ userId: user.id, question: text });
  } catch (err) {
    console.error('ml api failed', err);
    return ctx.reply('Сервис ответов временно недоступен, попробуйте позже.');
  }
  // 200 — ответ из базы (или вежливый отказ на нерабочий вопрос), 400 — ответа в базе нет; текст в обоих случаях от ML
  return ctx.reply(result.answer);
});

async function main() {
  await pool.query('SELECT 1');
  await connectKafka();
  await bot.start();
  console.log('bot started');
}

async function shutdown() {
  await bot.stop?.();
  await disconnectKafka();
  await pool.end();
  redis.disconnect();
  process.exit(0);
}
process.on('SIGINT', shutdown);
process.on('SIGTERM', shutdown);

main().catch((err) => {
  console.error('fatal', err);
  process.exit(1);
});
