import { Bot } from '@maxhub/max-bot-api';
import { config } from './config.js';
import { pool, findUserByMaxId } from './db.js';
import { redis, getState, setState } from './state.js';
import { registerInvites } from './invite.js';
import { askRag } from './ml.js';
import { connectKafka, disconnectKafka, sendToMl, startMlConsumer } from './kafka.js';

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

  const state = await getState(maxId);
  await sendToMl({ maxId, text, role: user.role, tag: user.tag });
  await setState(maxId, { ...state, step: 'waiting_ml' });

  // Любое сообщение нового сотрудника — вопрос к RAG ML-сервиса
  if (user.role !== 'employee') return ctx.reply('Откройте /menu, чтобы выбрать действие.');
  let result;
  try {
    result = await askRag(text);
  } catch (err) {
    console.error('ml api failed', err);
    return ctx.reply('Сервис ответов временно недоступен, попробуйте позже.');
  }
  if (result.code === 200) return ctx.reply(result.answer);
  return ctx.reply('Не удалось найти ответ на ваш вопрос. Попробуйте переформулировать или обратитесь к HR.');
});

async function main() {
  await pool.query('SELECT 1');
  await connectKafka();
  await startMlConsumer(async ({ maxId, text }) => {
    await bot.api.sendMessageToUser(Number(maxId), text);
    const state = await getState(maxId);
    await setState(maxId, { ...state, step: 'idle' });
  });
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
