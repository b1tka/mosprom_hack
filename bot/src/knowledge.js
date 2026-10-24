import crypto from 'node:crypto';
import path from 'node:path';
import { Keyboard } from '@maxhub/max-bot-api';
import { findUserByMaxId } from './db.js';
import { isHr } from './invite.js';
import {
  addDocument, deleteDocument, listDocuments, MlError,
} from './ml.js';
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

// ---------- удаление документа ----------

const PAGE_SIZE = 10;
const MAX_LABEL_LEN = 60;
const shortLabel = (name) => (name.length > MAX_LABEL_LEN ? `${name.slice(0, MAX_LABEL_LEN - 1)}…` : name);

// имена держим в состоянии, в кнопках — только индекс: длинное имя не влезет в payload кнопки
async function showDocumentsPage(ctx, docs, page) {
  const start = page * PAGE_SIZE;
  const rows = docs.slice(start, start + PAGE_SIZE)
    .map((name, i) => [Keyboard.button.callback(shortLabel(name), `kbdel:${start + i}`)]);
  const nav = [];
  if (page > 0) nav.push(Keyboard.button.callback('← Назад', `kbpage:${page - 1}`));
  if (start + PAGE_SIZE < docs.length) nav.push(Keyboard.button.callback('Вперёд →', `kbpage:${page + 1}`));
  if (nav.length) rows.push(nav);
  rows.push([Keyboard.button.callback('Отмена', 'kbdelok:no')]);
  const pages = Math.ceil(docs.length / PAGE_SIZE);
  const title = pages > 1 ? `Выберите файл для удаления (стр. ${page + 1} из ${pages}):` : 'Выберите файл для удаления:';
  await ctx.reply(title, { attachments: [Keyboard.inlineKeyboard(rows)] });
}

async function processDelete(api, maxId, name) {
  let text;
  try {
    await deleteDocument(name);
    text = `Файл «${name}» удалён из базы знаний.`;
  } catch (err) {
    if (err instanceof MlError && err.status === 404) {
      text = `Файла «${name}» уже нет в базе знаний.`;
    } else {
      console.error('knowledge delete failed', name, err);
      text = `Не удалось удалить «${name}»: сервис временно недоступен. Попробуйте позже через /menu.`;
    }
  }
  try {
    await api.sendMessageToUser(Number(maxId), text);
  } catch (err) {
    console.error('knowledge notification failed', err);
  }
}

export function registerKnowledge(bot) {
  // кнопки удаления: проверяем и права, и шаг диалога
  const kbAction = (re, steps, handler) => bot.action(re, async (ctx) => {
    const maxId = ctx.user.user_id;
    const state = await getState(maxId);
    if (!isHr(await findUserByMaxId(maxId)) || !steps.includes(state.step)) {
      return ctx.answerOnCallback({ notification: 'Действие неактуально. Откройте /menu.' });
    }
    return handler(ctx, maxId, state);
  });

  bot.action('menu:kbdel', async (ctx) => {
    const maxId = ctx.user.user_id;
    if (!isHr(await findUserByMaxId(maxId))) {
      return ctx.answerOnCallback({ notification: 'Недостаточно прав.' });
    }
    let docs;
    try {
      docs = await listDocuments();
    } catch (err) {
      console.error('knowledge list failed', err);
      await ctx.answerOnCallback({ notification: 'Ошибка' });
      return ctx.reply('Не удалось получить список файлов: сервис временно недоступен. Попробуйте позже.');
    }
    await ctx.answerOnCallback({ notification: 'Удаление из базы знаний' });
    if (!docs.length) return ctx.reply('База знаний пуста.');
    await setState(maxId, { step: 'kb_delete_pick', data: { docs } });
    return showDocumentsPage(ctx, docs, 0);
  });

  kbAction(/^kbpage:(\d+)$/, ['kb_delete_pick'], async (ctx, maxId, state) => {
    const page = Number(ctx.match[1]);
    if (page * PAGE_SIZE >= state.data.docs.length) return ctx.answerOnCallback({ notification: 'Нет такой страницы.' });
    await ctx.answerOnCallback({ notification: `Страница ${page + 1}` });
    return showDocumentsPage(ctx, state.data.docs, page);
  });

  kbAction(/^kbdel:(\d+)$/, ['kb_delete_pick'], async (ctx, maxId, state) => {
    const name = state.data.docs[Number(ctx.match[1])];
    if (!name) return ctx.answerOnCallback({ notification: 'Неизвестный файл.' });
    await setState(maxId, { step: 'kb_delete_confirm', data: { name } });
    await ctx.answerOnCallback({ notification: shortLabel(name) });
    const kb = Keyboard.inlineKeyboard([[
      Keyboard.button.callback('Удалить', 'kbdelok:yes'),
      Keyboard.button.callback('Отмена', 'kbdelok:no'),
    ]]);
    return ctx.reply(`Удалить «${name}» из базы знаний? Сотрудники перестанут получать ответы по нему.`, { attachments: [kb] });
  });

  kbAction(/^kbdelok:(yes|no)$/, ['kb_delete_pick', 'kb_delete_confirm'], async (ctx, maxId, state) => {
    // состояние сбрасываем сразу, чтобы повторное нажатие не запустило удаление ещё раз
    await setState(maxId, { step: 'idle', data: {} });
    if (ctx.match[1] === 'no' || state.step !== 'kb_delete_confirm') {
      await ctx.answerOnCallback({ notification: 'Отменено' });
      return ctx.reply('Отменено.');
    }
    const { name } = state.data;
    await ctx.answerOnCallback({ notification: 'Удаляю…' });
    await ctx.reply(`Удаляю «${name}». Пришлю уведомление, когда закончу.`);
    // в фоне: ML может ждать окончания индексации другого документа
    processDelete(ctx.api, maxId, name);
  });

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
