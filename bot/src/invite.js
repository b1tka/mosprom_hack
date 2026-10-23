import crypto from 'node:crypto';
import QRCode from 'qrcode';
import { Keyboard } from '@maxhub/max-bot-api';
import {
  acceptInvite, createInvite, findActiveInviteByHash, findActiveInviteById,
  findUserByMaxId, listDirectors, listTags, phoneTaken,
} from './db.js';
import { clearState, getState, setState } from './state.js';
import { isEmail, normalizePhone } from './phone.js';

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
const isHr = (user) => Boolean(user && HR_ROLES.includes(user.role));

let botUsername;
async function getBotUsername(api) {
  botUsername ??= (await api.getMyInfo()).username;
  return botUsername;
}

// ---------- HR: добавление сотрудника ----------

async function askTag(ctx) {
  const tags = await listTags();
  const rows = tags.map((t) => [Keyboard.button.callback(t.name, `tag:${t.id}`)]);
  rows.push([Keyboard.button.callback('Без тега', 'tag:none')]);
  await ctx.reply('Выберите тег сотрудника:', { attachments: [Keyboard.inlineKeyboard(rows)] });
}

async function askDirector(ctx) {
  const directors = await listDirectors();
  const rows = directors.map((d) => [Keyboard.button.callback(`${d.name} ${d.surname}`, `dir:${d.id}`)]);
  rows.push([Keyboard.button.callback('Без руководителя', 'dir:none')]);
  await ctx.reply('Выберите руководителя сотрудника:', { attachments: [Keyboard.inlineKeyboard(rows)] });
}

async function askConfirm(ctx, d) {
  const lines = [
    'Проверьте данные:',
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
  await ctx.reply(lines.join('\n'), { attachments: [kb] });
}

async function issueInvite(ctx, hr, d) {
  const username = await getBotUsername(ctx.api);
  const token = crypto.randomBytes(24).toString('base64url');
  await createInvite({
    tokenHash: hashToken(token),
    name: d.name, surname: d.surname, tagId: d.tagId, directorId: d.directorId,
    email: d.email, phone: d.phone, createdBy: hr.id,
    ttlSeconds: INVITE_TTL_SECONDS,
  });
  const link = `https://max.ru/${username}?start=${token}`;
  const text = `Приглашение для ${d.name} ${d.surname} создано. Пусть отсканирует QR-код в MAX с аккаунта, `
    + `привязанного к номеру +${d.phone}. Действует 7 дней, срабатывает один раз.\n\nСсылка: ${link}`;

  // Приглашение уже в БД: сбой загрузки картинки не должен его терять, поэтому повторяем и в крайнем случае шлём одну ссылку
  const image = await uploadQr(ctx.api, link);
  if (image) return ctx.reply(text, { attachments: [image.toJson()] });
  await ctx.reply(`${text}\n\nКартинку с QR-кодом загрузить не удалось. Откройте ссылку или сделайте QR из неё.`);
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
  if (await findUserByMaxId(maxId)) return ctx.reply('Вы уже зарегистрированы. Напишите /start.');
  const invite = await findActiveInviteByHash(hashToken(token));
  if (!invite) return ctx.reply('Ссылка недействительна или устарела. Обратитесь к HR, чтобы получить новый QR-код.');
  await setState(maxId, { step: 'await_contact', data: { inviteId: invite.id, attempts: 0 } });
  const kb = Keyboard.inlineKeyboard([[Keyboard.button.requestContact('Поделиться контактом')]]);
  await ctx.reply(
    `Здравствуйте, ${invite.name}! Чтобы подтвердить, что это вы, нажмите кнопку и поделитесь контактом.`,
    { attachments: [kb] },
  );
}

async function handleContact(ctx, state) {
  const maxId = ctx.user.user_id;
  const attachment = ctx.message?.body?.attachments?.find((a) => a.type === 'contact');
  if (!attachment || !ctx.contactInfo) {
    return ctx.reply('Нажмите кнопку «Поделиться контактом» под предыдущим сообщением.');
  }
  const invite = await findActiveInviteById(state.data.inviteId);
  if (!invite) {
    await clearState(maxId);
    return ctx.reply('Приглашение больше недействительно. Обратитесь к HR.');
  }

  // контакт должен быть именно ваш (а не пересланный чужой) и совпадать с номером, который ввёл HR
  const ownContact = Number(attachment.payload.max_info?.user_id) === Number(maxId);
  const phone = normalizePhone(ctx.contactInfo.tel);
  if (!ownContact || phone !== invite.phone) {
    const attempts = (state.data.attempts ?? 0) + 1;
    if (attempts >= MAX_CONTACT_ATTEMPTS) {
      await clearState(maxId);
      return ctx.reply('Слишком много неудачных попыток. Обратитесь к HR.');
    }
    await setState(maxId, { ...state, data: { ...state.data, attempts } });
    return ctx.reply(
      ownContact
        ? 'Номер вашего аккаунта не совпадает с тем, что указал HR. Проверьте, что открыли ссылку с нужного аккаунта.'
        : 'Нужно поделиться своим контактом, а не чужим.',
    );
  }

  const result = await acceptInvite({ inviteId: invite.id, maxId });
  if (!result.ok) {
    await clearState(maxId);
    return ctx.reply(result.reason === 'already_registered'
      ? 'Вы уже зарегистрированы. Напишите /start.'
      : 'Приглашение больше недействительно. Обратитесь к HR.');
  }
  await setState(maxId, { step: 'idle', data: {} });
  await ctx.reply(`Готово, ${result.name}! Вы зарегистрированы. Напишите /start.`);
  try {
    await ctx.api.sendMessageToUser(Number(result.creatorMaxId), `${invite.name} ${invite.surname} активировал(а) приглашение.`);
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
      return ctx.reply('Недостаточно прав.');
    }
    switch (state.step) {
      case 'add_name': {
        const name = cleanName(text);
        if (!name) return ctx.reply(`Введите имя (до ${MAX_NAME_LEN} символов).`);
        await setState(maxId, { step: 'add_surname', data: { name } });
        return ctx.reply('Фамилия сотрудника?');
      }
      case 'add_surname': {
        const surname = cleanName(text);
        if (!surname) return ctx.reply(`Введите фамилию (до ${MAX_NAME_LEN} символов).`);
        await setState(maxId, { step: 'add_tag', data: { ...state.data, surname } });
        return askTag(ctx);
      }
      case 'add_email': {
        const email = text?.trim();
        if (!email || !isEmail(email)) return ctx.reply('Это не похоже на почту. Пример: ivan@company.ru');
        await setState(maxId, { step: 'add_phone', data: { ...state.data, email } });
        return ctx.reply('Номер телефона сотрудника, привязанный к его аккаунту MAX (например, +7 999 123-45-67):');
      }
      case 'add_phone': {
        const phone = normalizePhone(text);
        if (!phone) return ctx.reply('Не удалось распознать номер. Пример: +7 999 123-45-67');
        if (await phoneTaken(phone)) return ctx.reply('Сотрудник с таким номером уже есть в системе.');
        const data = { ...state.data, phone };
        await setState(maxId, { step: 'add_confirm', data });
        return askConfirm(ctx, data);
      }
      default:
        return ctx.reply('Пожалуйста, выберите вариант кнопкой выше или напишите /cancel.');
    }
  });

  bot.command('menu', async (ctx) => {
    const user = await findUserByMaxId(ctx.user.user_id);
    if (!user) return ctx.reply('Вас нет в системе. Обратитесь к HR, чтобы вас добавили.');
    const rows = [];
    if (isHr(user)) rows.push([Keyboard.button.callback('Добавить нового сотрудника', 'menu:add')]);
    if (!rows.length) return ctx.reply('Для вашей роли в меню пока нет действий.');
    await ctx.reply('Меню:', { attachments: [Keyboard.inlineKeyboard(rows)] });
  });

  bot.action('menu:add', async (ctx) => {
    const maxId = ctx.user.user_id;
    if (!isHr(await findUserByMaxId(maxId))) {
      return ctx.answerOnCallback({ notification: 'Недостаточно прав.' });
    }
    await setState(maxId, { step: 'add_name', data: {} });
    await ctx.answerOnCallback({ notification: 'Добавление сотрудника' });
    await ctx.reply('Добавление нового сотрудника. Отмена: /cancel.\nИмя сотрудника?');
  });

  bot.command('cancel', async (ctx) => {
    await setState(ctx.user.user_id, { step: 'idle', data: {} });
    await ctx.reply('Отменено.');
  });

  // кнопки HR; проверяем и шаг, и права
  const hrAction = (re, step, handler) => bot.action(re, async (ctx) => {
    const maxId = ctx.user.user_id;
    const hr = await findUserByMaxId(maxId);
    const state = await getState(maxId);
    if (!isHr(hr) || state.step !== step) {
      return ctx.answerOnCallback({ notification: 'Действие неактуально. Откройте /menu.' });
    }
    return handler(ctx, hr, state);
  });

  hrAction(/^tag:(.+)$/, 'add_tag', async (ctx, hr, state) => {
    const [, value] = ctx.match;
    const tag = (await listTags()).find((t) => t.id === value);
    if (value !== 'none' && !tag) return ctx.answerOnCallback({ notification: 'Неизвестный тег.' });
    await setState(ctx.user.user_id, { step: 'add_director', data: { ...state.data, tagId: tag?.id ?? null, tagName: tag?.name ?? null } });
    await ctx.answerOnCallback({ notification: tag?.name ?? 'Без тега' });
    return askDirector(ctx);
  });

  hrAction(/^dir:(.+)$/, 'add_director', async (ctx, hr, state) => {
    const [, value] = ctx.match;
    const director = (await listDirectors()).find((d) => d.id === value);
    if (value !== 'none' && !director) return ctx.answerOnCallback({ notification: 'Неизвестный руководитель.' });
    await setState(ctx.user.user_id, {
      step: 'add_email',
      data: { ...state.data, directorId: director?.id ?? null, directorName: director ? `${director.name} ${director.surname}` : null },
    });
    await ctx.answerOnCallback({ notification: 'Принято' });
    return ctx.reply('Почта сотрудника?');
  });

  hrAction(/^add:(ok|cancel)$/, 'add_confirm', async (ctx, hr, state) => {
    const maxId = ctx.user.user_id;
    if (ctx.match[1] === 'cancel') {
      await setState(maxId, { step: 'idle', data: {} });
      await ctx.answerOnCallback({ notification: 'Отменено' });
      return ctx.reply('Отменено.');
    }
    await ctx.answerOnCallback({ notification: 'Создаю…' });
    // состояние сбрасываем до выдачи, чтобы повторное нажатие кнопки не создало второе приглашение
    await setState(maxId, { step: 'idle', data: {} });
    try {
      return await issueInvite(ctx, hr, state.data);
    } catch (err) {
      console.error('invite creation failed', err);
      return ctx.reply('Не удалось создать приглашение. Попробуйте ещё раз через /menu.');
    }
  });
}
