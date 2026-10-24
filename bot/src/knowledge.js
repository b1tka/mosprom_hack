import crypto from 'node:crypto';
import path from 'node:path';
import { findUserByMaxId } from './db.js';
import { isHr } from './invite.js';
import { addDocument, MlError } from './ml.js';
import { deleteFile, putFile } from './s3.js';
import { getState, setState } from './state.js';

// те же форматы, что понимает ML (ml/app/ingest.py SUPPORTED)
const CONTENT_TYPES = { '.pdf': 'application/pdf', '.txt': 'text/plain', '.md': 'text/markdown' };
const MAX_FILE_BYTES = 50 * 1024 * 1024;
const INCOMING_PREFIX = 'incoming/';

// имя файла становится именем документа в базе знаний: одноимённый документ заменяется
const cleanFilename = (name) => path.basename(String(name ?? '').replace(/\\/g, '/'))
  .replace(/[\u0000-\u001f]/g, '')
  .trim()
  .slice(-200);

async function downloadAttachment(url) {
  const res = await fetch(url, { signal: AbortSignal.timeout(120000) });
  if (!res.ok) throw new Error(`file download HTTP ${res.status}`);
  return Buffer.from(await res.arrayBuffer());
}

function failureText(name, err) {
  if (err instanceof MlError && err.status === 400) {
    return `Не удалось добавить «${name}»: из файла не извлёкся текст. Проверьте файл и отправьте снова через /menu.`;
  }
  return `Не удалось добавить «${name}» в базу знаний: сервис временно недоступен. Попробуйте позже через /menu.`;
}

// Долгая часть (скачивание, S3, индексация в ML — до десятков минут) идёт в фоне:
// polling библиотеки ждёт обработчики, и иначе бот замолчал бы для всех на время индексации.
async function processUpload(api, maxId, attachment, name) {
  const key = `${INCOMING_PREFIX}${crypto.randomUUID()}/${name}`;
  let text;
  try {
    const body = await downloadAttachment(attachment.payload.url);
    if (body.length > MAX_FILE_BYTES) throw new Error(`file too large: ${body.length}`);
    await putFile(key, body, CONTENT_TYPES[path.extname(name).toLowerCase()]);
    const result = await addDocument({ key });
    text = `Файл «${result.source}» ${result.replaced ? 'обновлён' : 'добавлен'} в базу знаний `
      + `(фрагментов: ${result.chunks_added}). Сотрудники уже могут задавать по нему вопросы.`;
  } catch (err) {
    console.error('knowledge upload failed', key, err);
    // при успехе ML сам переносит файл в knowledge/; при ошибке входящий файл не нужен
    await deleteFile(key).catch((e) => console.error('incoming file cleanup failed', key, e));
    text = failureText(name, err);
  }
  try {
    await api.sendMessageToUser(Number(maxId), text);
  } catch (err) {
    console.error('knowledge notification failed', err);
  }
}

export function registerKnowledge(bot) {
  bot.action('menu:kb', async (ctx) => {
    const maxId = ctx.user.user_id;
    if (!isHr(await findUserByMaxId(maxId))) {
      return ctx.answerOnCallback({ notification: 'Недостаточно прав.' });
    }
    await setState(maxId, { step: 'kb_await_file', data: {} });
    await ctx.answerOnCallback({ notification: 'Загрузка в базу знаний' });
    await ctx.reply('Пришлите документ файлом (PDF, TXT или MD, до 50 МБ). '
      + 'Документ с тем же именем будет заменён. Отмена: /cancel.');
  });

  bot.use(async (ctx, next) => {
    if (ctx.updateType !== 'message_created') return next();
    if (ctx.message?.body?.text?.startsWith('/')) return next();
    const maxId = ctx.user.user_id;
    const state = await getState(maxId);
    if (state.step !== 'kb_await_file') return next();

    if (!isHr(await findUserByMaxId(maxId))) {
      await setState(maxId, { step: 'idle', data: {} });
      return ctx.reply('Недостаточно прав.');
    }
    const attachment = ctx.message?.body?.attachments?.find((a) => a.type === 'file');
    if (!attachment) return ctx.reply('Пришлите документ файлом (PDF, TXT или MD) или напишите /cancel.');

    const name = cleanFilename(attachment.filename);
    if (!CONTENT_TYPES[path.extname(name).toLowerCase()]) {
      return ctx.reply('Поддерживаются только PDF, TXT и MD. Пришлите другой файл или напишите /cancel.');
    }
    if (attachment.size > MAX_FILE_BYTES) return ctx.reply('Файл больше 50 МБ. Пришлите файл поменьше или напишите /cancel.');

    await setState(maxId, { step: 'idle', data: {} });
    await ctx.reply(`Файл «${name}» принят. Добавляю в базу знаний — это может занять несколько минут, пришлю уведомление.`);
    processUpload(ctx.api, maxId, attachment, name);
  });
}
