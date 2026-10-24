import crypto from 'node:crypto';
import QRCode from 'qrcode';
import { Keyboard } from '@maxhub/max-bot-api';
import {
  acceptInvite, createInvite, findActiveInviteByHash, findActiveInviteById,
  findUserByMaxId, listDirectors, listTags, phoneTaken,
} from './db.js';
import { clearState, getState, setState } from './state.js';
import { isEmail, normalizePhone } from './phone.js';
import { hooks } from './onboarding/hooks.js';
import { getScenario } from './scenario/load.js';
import { defaultStartDate } from './scenario/schedule.js';
import { show } from './ui.js';

const MAX_NAME_LEN = 100;
const INVITE_TTL_SECONDS = 7 * 24 * 60 * 60;
const MAX_CONTACT_ATTEMPTS = 5;
const UPLOAD_ATTEMPTS = 3;
const HR_ROLES = ['hr', 'hr_head'];

const hashToken = (token) => crypto.createHash('sha256').update(token).digest('hex');
const cleanName = (text) => {
  const value = text?.trim();
  return value && value.length <= MAX_NAME_LEN ? value : null;
};
export const isHr = (user) => Boolean(user && HR_ROLES.includes(user.role));

let botUsername;
async function getBotUsername(api) {
  botUsername ??= (await api.getMyInfo()).username;
  return botUsername;
}

// ---------- HR: добавление сотрудника ----------

async function askTag(ctx, notification) {
  const tags = await listTags();
  const rows = tags.map((t) => [Keyboard.button.callback(t.name, `tag:${t.id}`)]);
  rows.push([Keyboard.button.callback('Без тега', 'tag:none')]);
  await show(ctx, 'Отлично! Выберите тег сотрудника 👇', Keyboard.inlineKeyboard(rows), notification);
}

async function askDirector(ctx, notification, only = null) {
  const directors = only ?? await listDirectors();
  const rows = directors.map((d) => [Keyboard.button.callback(`${d.name} ${d.surname}`, `dir:${d.id}`)]);
  rows.push([Keyboard.button.callback('Без руководителя', 'dir:none')]);
  await show(ctx, 'Почти готово! Кто будет руководителем сотрудника? 👇', Keyboard.inlineKeyboard(rows), notification);
}

// Следующий шаг добавления сотрудника: известные поля (из PDF или уже введённые) пропускаются
async function continueAdd(ctx, maxId, data, notification) {
  if (!data.tagSet) {
    await setState(maxId, { step: 'add_tag', data });
    return askTag(ctx, notification);
  }
  if (!data.directorSet) {
    await setState(maxId, { step: 'add_director', data });
    return askDirector(ctx, notification, data.directorCandidates ?? null);
  }
  if (!data.email) {
    await setState(maxId, { step: 'add_email', data });
    return show(ctx, 'Какая у сотрудника рабочая почта? ✉️', null, notification);
  }
  if (!data.phone) {
    await setState(maxId, { step: 'add_phone', data });
    return show(ctx, 'Теперь номер телефона, привязанный к его аккаунту MAX (например, +7 999 123-45-67) 📱', null, notification);
  }
  await setState(maxId, { step: 'add_confirm', data });
  return askConfirm(ctx, data);
}

// Для распознавания PDF: продолжить форму с данными из файла; ctx — адаптер поверх api (работает и из фона)
export const beginAddFromData = (ctx, maxId, data) => continueAdd(ctx, maxId, data);

async function askConfirm(ctx, d) {
  const lines = [
    'Давайте проверим всё перед созданием 👇',
    `${d.name} ${d.surname}`,
    `Тег: ${d.tagName ?? 'нет'}`,
    `Руководитель: ${d.directorName ?? 'нет'}`,
    `Почта: ${d.email}`,
    `Телефон: +${d.phone}`,
  ];
  const kb = Keyboard.inlineKeyboard([[
    Keyboard.button.callback('Создать', 'add:ok'),
    Keyboard.button.callback('Отмена', 'add:cancel'),
  ]]);
  await show(ctx, lines.join('\n'), kb);
}

async function issueInvite(ctx, hr, d) {
  const username = await getBotUsername(ctx.api);
  const token = crypto.randomBytes(24).toString('base64url');
  // дата выхода считается от генерации QR: +3 дня, выходной -> понедельник (HR может поправить в «Мои сотрудники»)
  const startDate = defaultStartDate(new Date(), getScenario().cfg);
  await createInvite({
    tokenHash: hashToken(token),
    name: d.name, surname: d.surname, tagId: d.tagId, directorId: d.directorId,
    email: d.email, phone: d.phone, createdBy: hr.id,
    ttlSeconds: INVITE_TTL_SECONDS,
    startDate,
  });
  const link = `https://max.ru/${username}?start=${token}`;
  const text = `Готово! 🎉 Приглашение для ${d.name} ${d.surname} создано. Пусть отсканирует QR-код в MAX с аккаунта, `
    + `привязанного к номеру +${d.phone}. Оно действует 7 дней и срабатывает один раз.\n`
    + `Дата выхода на работу: ${startDate.split('-').reverse().join('.')} (сценарий онбординга считается от неё; изменить можно в меню «Мои сотрудники»).\n\nСсылка: ${link}`;

  // Приглашение уже в БД: сбой загрузки картинки не должен его терять, поэтому повторяем и в крайнем случае шлём одну ссылку
  const image = await uploadQr(ctx.api, link);
  if (image) return ctx.reply(text, { attachments: [image.toJson()] });
  await ctx.reply(`${text}\n\nQR-картинку загрузить не получилось 😕 Откройте ссылку или сделайте QR из неё.`);
}

async function uploadQr(api, link) {
  const png = await QRCode.toBuffer(link, { type: 'png', width: 512, margin: 2 });
  for (let attempt = 1; attempt <= UPLOAD_ATTEMPTS; attempt += 1) {
    try {
      return await api.uploadImage({ source: png });
    } catch (err) {
      console.error(`qr upload failed (attempt ${attempt}/${UPLOAD_ATTEMPTS})`, err);
      if (attempt < UPLOAD_ATTEMPTS) await new Promise((r) => setTimeout(r, 500 * attempt));
    }
  }
  return null;
}

// ---------- сотрудник: активация по QR ----------

async function startActivation(ctx, token) {
  const maxId = ctx.user.user_id;
  if (await findUserByMaxId(maxId)) return ctx.reply('Ты уже с нами! 😊 Напиши /start.');
  const invite = await findActiveInviteByHash(hashToken(token));
  if (!invite) return ctx.reply('Эта ссылка не работает или уже устарела 😕 Попроси HR выдать новый QR-код.');
  await setState(maxId, { step: 'await_contact', data: { inviteId: invite.id, attempts: 0 } });
  const kb = Keyboard.inlineKeyboard([[Keyboard.button.requestContact('Поделиться контактом')]]);
  await ctx.reply(
    `Привет, ${invite.name}! 👋 Чтобы убедиться, что это ты, нажми кнопку и поделись контактом.`,
    { attachments: [kb] },
  );
}

async function handleContact(ctx, state) {
  const maxId = ctx.user.user_id;
  const attachment = ctx.message?.body?.attachments?.find((a) => a.type === 'contact');
  if (!attachment || !ctx.contactInfo) {
    return ctx.reply('Нажми, пожалуйста, кнопку «Поделиться контактом» под предыдущим сообщением 👇');
  }
  const invite = await findActiveInviteById(state.data.inviteId);
  if (!invite) {
    await clearState(maxId);
    return ctx.reply('Приглашение больше не действует 😕 Обратись к HR, он поможет.');
  }

  // контакт должен быть именно ваш (а не пересланный чужой) и совпадать с номером, который ввёл HR
  const ownContact = Number(attachment.payload.max_info?.user_id) === Number(maxId);
  const phone = normalizePhone(ctx.contactInfo.tel);
  if (!ownContact || phone !== invite.phone) {
    const attempts = (state.data.attempts ?? 0) + 1;
    if (attempts >= MAX_CONTACT_ATTEMPTS) {
      await clearState(maxId);
      return ctx.reply('Слишком много неудачных попыток 😕 Обратись к HR, он поможет.');
    }
    await setState(maxId, { ...state, data: { ...state.data, attempts } });
    return ctx.reply(
      ownContact
        ? 'Номер этого аккаунта не совпадает с тем, что указал HR 🤔 Проверь, что открыл ссылку с нужного аккаунта.'
        : 'Нужно поделиться своим контактом, а не чужим 🙂',
    );
  }

  const result = await acceptInvite({ inviteId: invite.id, maxId });
  if (!result.ok) {
    await clearState(maxId);
    return ctx.reply(result.reason === 'already_registered'
      ? 'Ты уже с нами! 😊 Напиши /start.'
      : 'Приглашение больше не действует 😕 Обратись к HR, он поможет.');
  }
  await setState(maxId, { step: 'idle', data: {} });
  await ctx.reply(`Добро пожаловать, ${result.name}! 🎉 Регистрация завершена.`);
  try {
    await hooks.onActivated({ ctx, userId: result.userId, maxId, startDate: result.startDate });
  } catch (err) {
    console.error('onboarding start failed', err);
  }
  try {
    await ctx.api.sendMessageToUser(Number(result.creatorMaxId), `${invite.name} ${invite.surname} активировал(а) приглашение 🎉 Дата выхода: ${(result.startDate ?? '').split('-').reverse().join('.') || 'не задана'}.`);
  } catch (err) {
    console.error('creator notification failed', err);
  }
}

// ---------- подключение к боту ----------

export function registerInvites(bot) {
  // Payload после перехода по QR/ссылке: либо событие bot_started, либо "/start <token>" в уже открытом чате
  bot.on('bot_started', async (ctx, next) => {
    if (!ctx.startPayload) return next();
    return startActivation(ctx, ctx.startPayload);
  });

  bot.use(async (ctx, next) => {
    if (ctx.updateType !== 'message_created') return next();
    const text = ctx.message?.body?.text;
    const maxId = ctx.user.user_id;

    if (text?.startsWith('/start ')) {
      const token = text.split(/\s+/)[1];
      if (token) return startActivation(ctx, token);
    }
    if (text?.startsWith('/')) return next();

    const state = await getState(maxId);
    if (state.step === 'await_contact') return handleContact(ctx, state);
    if (!state.step.startsWith('add_')) return next();

    // шаги HR; права проверяем на каждом шаге
    if (!isHr(await findUserByMaxId(maxId))) {
      await clearState(maxId);
      return ctx.reply('Эта функция доступна только HR 🙂');
    }
    switch (state.step) {
      case 'add_name': {
        const name = cleanName(text);
        if (!name) return ctx.reply(`Хм, не получилось 🤔 Введите имя (до ${MAX_NAME_LEN} символов).`);
        await setState(maxId, { step: 'add_surname', data: { name } });
        return ctx.reply('Отлично! А фамилия? 🙂');
      }
      case 'add_surname': {
        const surname = cleanName(text);
        if (!surname) return ctx.reply(`Хм, не получилось 🤔 Введите фамилию (до ${MAX_NAME_LEN} символов).`);
        return continueAdd(ctx, maxId, { ...state.data, surname });
      }
      case 'add_email': {
        const email = text?.trim();
        if (!email || !isEmail(email)) return ctx.reply('Хм, это не похоже на почту 🤔 Пример: ivan@company.ru');
        return continueAdd(ctx, maxId, { ...state.data, email });
      }
      case 'add_phone': {
        const phone = normalizePhone(text);
        if (!phone) return ctx.reply('Не получилось распознать номер 🤔 Пример: +7 999 123-45-67');
        if (await phoneTaken(phone)) return ctx.reply('Сотрудник с таким номером уже есть в системе 🙂');
        return continueAdd(ctx, maxId, { ...state.data, phone });
      }
      default:
        return ctx.reply('Выберите, пожалуйста, вариант кнопкой выше или напишите /cancel 🙂');
    }
  });

  bot.action('menu:add', async (ctx) => {
    const maxId = ctx.user.user_id;
    if (!isHr(await findUserByMaxId(maxId))) {
      return ctx.answerOnCallback({ notification: 'Эта функция доступна только HR 🙂' });
    }
    await setState(maxId, { step: 'add_name', data: {} });
    await show(ctx, 'Давайте добавим нового сотрудника! 🎉 Отменить можно в любой момент: /cancel.\nКак его зовут?', null, 'Добавление сотрудника');
  });

  bot.command('cancel', async (ctx) => {
    await setState(ctx.user.user_id, { step: 'idle', data: {} });
    await ctx.reply('Хорошо, отменил 🙂 Если что, я рядом.');
  });

  // кнопки HR; проверяем и шаг, и права
  const hrAction = (re, step, handler) => bot.action(re, async (ctx) => {
    const maxId = ctx.user.user_id;
    const hr = await findUserByMaxId(maxId);
    const state = await getState(maxId);
    if (!isHr(hr) || state.step !== step) {
      return ctx.answerOnCallback({ notification: 'Эта кнопка уже неактуальна 🙂 Откройте /menu.' });
    }
    return handler(ctx, hr, state);
  });

  hrAction(/^tag:(.+)$/, 'add_tag', async (ctx, hr, state) => {
    const [, value] = ctx.match;
    const tag = (await listTags()).find((t) => t.id === value);
    if (value !== 'none' && !tag) return ctx.answerOnCallback({ notification: 'Не нашёл такой тег 🤔' });
    return continueAdd(ctx, ctx.user.user_id, { ...state.data, tagId: tag?.id ?? null, tagName: tag?.name ?? null, tagSet: true }, tag?.name ?? 'Без тега');
  });

  hrAction(/^dir:(.+)$/, 'add_director', async (ctx, hr, state) => {
    const [, value] = ctx.match;
    const director = (await listDirectors()).find((d) => d.id === value);
    if (value !== 'none' && !director) return ctx.answerOnCallback({ notification: 'Не нашёл такого руководителя 🤔' });
    return continueAdd(ctx, ctx.user.user_id, {
      ...state.data,
      directorId: director?.id ?? null,
      directorName: director ? `${director.name} ${director.surname}` : null,
      directorSet: true,
      directorCandidates: undefined,
    }, 'Принято');
  });

  hrAction(/^add:(ok|cancel)$/, 'add_confirm', async (ctx, hr, state) => {
    const maxId = ctx.user.user_id;
    if (ctx.match[1] === 'cancel') {
      await setState(maxId, { step: 'idle', data: {} });
      return show(ctx, 'Хорошо, отменил 🙂 Если что, я рядом.', null, 'Отменено');
    }
    await show(ctx, 'Создаю приглашение…', null, 'Создаю…');
    // состояние сбрасываем до выдачи, чтобы повторное нажатие кнопки не создало второе приглашение
    await setState(maxId, { step: 'idle', data: {} });
    try {
      return await issueInvite(ctx, hr, state.data);
    } catch (err) {
      console.error('invite creation failed', err);
      return ctx.reply('Ой, не получилось создать приглашение 😕 Попробуйте ещё раз через /menu.');
    }
  });
}
