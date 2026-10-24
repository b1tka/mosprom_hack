import crypto from 'node:crypto';
import { Keyboard } from '@maxhub/max-bot-api';
import {
  findUserByMaxId, listDirectors, listTags, phoneTaken,
} from './db.js';
import { beginAddFromData, isHr } from './invite.js';
import { cleanFilename, downloadAttachment, processUpload } from './knowledge.js';
import { extractUser, MlError } from './ml.js';
import { buildAddData } from './pdfusers/match.js';
import { redis, getState, setState } from './state.js';
import { show } from './ui.js';

// HR присылает боту PDF с данными нового сотрудника (анкета, карточка, приказ — формат произвольный), ML-сервис через LLM достаёт
// имя, фамилию, почту, телефон, тег и руководителя, дальше работает обычная форма «Добавить нового сотрудника»:
// недостающие поля бот спросит, HR проверит карточку и нажмёт «Создать» — появится приглашение с QR.
// Один PDF — один человек. Тот же файл можно отправить в базу знаний: если PDF пришёл вне шага диалога, бот спрашивает, что с ним делать.

const MAX_PDF_BYTES = 10 * 1024 * 1024;
const PICK_TTL_SECONDS = 60 * 60;

const isPdf = (a) => a?.type === 'file' && /\.pdf$/i.test(a.filename ?? '');
const adapter = (api, maxId) => ({
  api,
  user: { user_id: maxId },
  reply: (text, extra) => api.sendMessageToUser(Number(maxId), text, extra),
});

function failureText(err) {
  if (err instanceof MlError) {
    if (err.status === 400) return 'Не смог прочитать текст в этом PDF 😕 Возможно, это скан плохого качества. Добавьте сотрудника вручную через /menu.';
    if (err.status === 413) return 'Файл слишком большой 😕 Нужен PDF до 10 МБ.';
  }
  return 'Сервис распознавания сейчас недоступен 😕 Попробуйте чуть позже или добавьте сотрудника вручную через /menu.';
}

// Фон: скачать PDF -> ML -> форма добавления. Идёт мимо обработчика сообщения: polling библиотеки не должен ждать LLM.
export async function processEmployeePdf(api, maxId, file) {
  const ctx = adapter(api, maxId);
  try {
    await ctx.reply(`Читаю файл «${file.name}» 📖 Это может занять до минуты, напишу, как будет готово.`);
    const pdf = await downloadAttachment(file.url);
    if (pdf.length > MAX_PDF_BYTES) throw new MlError(413, 'too large');
    const res = await extractUser(pdf);
    if (!res.users?.length) {
      await ctx.reply('Не нашёл в файле данных сотрудника 🤔 Проверьте, что в PDF есть имя и фамилия, или добавьте сотрудника вручную через /menu.');
      return;
    }
    const found = res.users[0];
    const [tags, directors] = await Promise.all([listTags(), listDirectors()]);
    const data = buildAddData(found, tags, directors);
    if (data.phone && await phoneTaken(data.phone)) {
      await ctx.reply(`Нашёл в файле ${data.name} ${data.surname}, но сотрудник с номером +${data.phone} уже есть в системе 🙂`);
      return;
    }
    const extra = res.users.length > 1 ? `\nВ файле несколько человек, беру первого: «${found.name} ${found.surname}». Остальных добавьте отдельными файлами 🙂` : '';
    const missing = [!data.tagSet && 'тег', !data.directorSet && 'руководитель', !data.email && 'почта', !data.phone && 'телефон'].filter(Boolean);
    await ctx.reply(`Нашёл в файле: ${data.name} ${data.surname} ✅${extra}${missing.length ? `\nНе нашёл: ${missing.join(', ')}. Сейчас уточню.` : ''}`);
    await beginAddFromData(ctx, maxId, data);
  } catch (err) {
    console.error('pdf employee extraction failed', file.name, err);
    await ctx.reply(failureText(err)).catch((e) => console.error('pdf notification failed', e));
  }
}

const fileInfo = (attachment) => ({ url: attachment.payload?.url, name: cleanFilename(attachment.filename) || 'file.pdf', size: attachment.size });

export function registerPdfUsers(bot) {
  // HR нажал «Добавить сотрудника из PDF» в меню
  bot.action('menu:addpdf', async (ctx) => {
    const maxId = ctx.user.user_id;
    if (!isHr(await findUserByMaxId(maxId))) return ctx.answerOnCallback({ notification: 'Эта функция доступна только HR 🙂' });
    await setState(maxId, { step: 'pdf_await', data: {} });
    return show(ctx, 'Пришлите PDF с данными сотрудника (анкета, карточка, приказ, формат любой) 📎 Один файл — один человек. Отмена: /cancel.', null, 'Добавить из PDF');
  });

  bot.use(async (ctx, next) => {
    if (ctx.updateType !== 'message_created') return next();
    const attachment = ctx.message?.body?.attachments?.find(isPdf);
    const maxId = ctx.user.user_id;
    const state = await getState(maxId);
    if (state.step === 'pdf_await' && !ctx.message?.body?.text?.startsWith('/')) {
      if (!isHr(await findUserByMaxId(maxId))) {
        await setState(maxId, { step: 'idle', data: {} });
        return ctx.reply('Эта функция доступна только HR 🙂');
      }
      if (!attachment) return ctx.reply('Пришлите, пожалуйста, PDF-файл или напишите /cancel 🙂');
      if (attachment.size > MAX_PDF_BYTES) return ctx.reply('Файл больше 10 МБ 😕 Пришлите файл поменьше или напишите /cancel.');
      await setState(maxId, { step: 'idle', data: {} });
      processEmployeePdf(ctx.api, maxId, fileInfo(attachment));
      return null;
    }
    // PDF вне шага диалога: спрашиваем, что с ним делать (только HR; остальным файл — как раньше)
    if (!attachment || state.step !== 'idle' && state.step !== 'start') return next();
    if (!isHr(await findUserByMaxId(maxId))) return next();
    if (attachment.size > 50 * 1024 * 1024) return ctx.reply('Файл больше 50 МБ 😕');
    const id = crypto.randomBytes(5).toString('hex');
    const info = fileInfo(attachment);
    await redis.set(`pdfpick:${id}`, JSON.stringify({ maxId: String(maxId), attachment: { type: 'file', filename: attachment.filename, size: attachment.size, payload: { url: attachment.payload?.url } }, ...info }), 'EX', PICK_TTL_SECONDS);
    return ctx.reply(`Получил «${info.name}» 📎 Что с ним сделать?`, {
      attachments: [Keyboard.inlineKeyboard([
        [Keyboard.button.callback('Добавить сотрудника', `pdf:emp:${id}`)],
        [Keyboard.button.callback('Добавить в базу знаний', `pdf:kb:${id}`)],
        [Keyboard.button.callback('Ничего', `pdf:no:${id}`)],
      ])],
    });
  });

  bot.action(/^pdf:(emp|kb|no):([0-9a-f]{10})$/, async (ctx) => {
    const maxId = ctx.user.user_id;
    if (!isHr(await findUserByMaxId(maxId))) return ctx.answerOnCallback({ notification: 'Эта функция доступна только HR 🙂' });
    const raw = await redis.getdel(`pdfpick:${ctx.match[2]}`);   // GETDEL: повторное нажатие не запустит обработку дважды
    if (!raw) return ctx.answerOnCallback({ notification: 'Эта кнопка уже неактуальна 🙂 Пришлите файл ещё раз.' });
    const pick = JSON.parse(raw);
    if (pick.maxId !== String(maxId)) return ctx.answerOnCallback({ notification: 'Это не ваш файл 🙂' });
    const action = ctx.match[1];
    if (action === 'no') return show(ctx, 'Хорошо, ничего не делаю 🙂', null, 'Отменено');
    if (action === 'kb') {
      processUpload(ctx.api, maxId, pick.attachment, pick.name);
      return show(ctx, `Файл «${pick.name}» получил 👍 Добавляю в базу знаний, это может занять несколько минут. Напишу, как только будет готово!`, null, 'В базу знаний');
    }
    processEmployeePdf(ctx.api, maxId, { url: pick.url, name: pick.name });
    return show(ctx, `Файл «${pick.name}» получил 👍 Достаю данные сотрудника…`, null, 'Добавляю сотрудника');
  });
}
