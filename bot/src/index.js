import { Bot } from '@maxhub/max-bot-api';
import { config } from './config.js';
import { pool, findUserByMaxId } from './db.js';
import { redis, setState } from './state.js';
import { registerAlerts, startAlerts, stopAlerts } from './alerts.js';
import { registerInvites } from './invite.js';
import { registerKnowledge } from './knowledge.js';
import { registerMenu } from './menu.js';
import { registerSupport, replyToQuestion } from './support.js';
import { ask } from './ml.js';
import { connectKafka, disconnectKafka } from './kafka.js';
import { connectMongo, disconnectMongo } from './mongo.js';

const bot = new Bot(config.botToken);

const ROLE_TITLES = {
  employee: 'новый сотрудник',
  director: 'руководитель подразделения',
  hr: 'HR-менеджер',
  hr_head: 'руководитель по подбору и развитию персонала',
};

bot.catch((err) => console.error('bot error', err));

// порядок важен: middleware шагов диалога (приглашения, база знаний, чат с HR) — до общего обработчика сообщений
registerInvites(bot);
registerKnowledge(bot);
registerSupport(bot);
registerMenu(bot);
registerAlerts(bot);

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

// Любое текстовое сообщение (не команда) нового сотрудника — вопрос к RAG ML-сервиса
bot.on('message_created', async (ctx) => {
  const text = ctx.message?.body?.text;
  if (!text || text.startsWith('/')) return;

  const maxId = ctx.user.user_id;
  const user = await findUserByMaxId(maxId);
  if (!user) return ctx.reply('Вас нет в системе. Обратитесь к HR, чтобы вас добавили.');

  // Любое сообщение нового сотрудника — вопрос к RAG ML-сервиса; настроение ML считает сам, в фоне
  if (user.role !== 'employee') return ctx.reply('Откройте /menu, чтобы выбрать действие.');
  // LLM отвечает до минуты, а polling библиотеки ждёт все обработчики пачки — поэтому ответ готовим в фоне
  answerInBackground(ctx, user, text);
});

async function answerInBackground(ctx, user, question) {
  try {
    ctx.sendAction('typing_on').catch(() => {});
    let result = null;
    try {
      result = await ask({ userId: user.id, question });
    } catch (err) {
      console.error('ml api failed', err);
    }
    // 200 — ответ + кнопка «Связаться с HR», 400 — ответа в базе нет, тикет HR создаётся сам; null — ML недоступен
    await replyToQuestion(ctx, user, question, result);
  } catch (err) {
    console.error('answer failed', err);
  }
}

async function main() {
  await pool.query('SELECT 1');
  await connectMongo();
  await connectKafka();
  startAlerts(bot.api);   // не ждём: при сбое Kafka консьюмер сам повторяет подключение
  await bot.start();
  console.log('bot started');
}

async function shutdown() {
  await bot.stop?.();
  await stopAlerts();
  await disconnectKafka();
  await disconnectMongo();
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
