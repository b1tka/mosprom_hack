import { Keyboard } from '@maxhub/max-bot-api';
import { findUserByMaxId } from './db.js';
import { isHr } from './invite.js';
import { getState, setState } from './state.js';
import { show } from './ui.js';
import { countOpenForHr } from './tickets.js';
import { hrScope } from './support.js';

// шаги, из которых /menu выводит: чат сотрудника с HR и ответ HR в тикет
const LEAVE_ON_MENU = ['ticket_chat', 'ticket_reply'];

async function showMenu(ctx, user, notification) {
  const rows = [];
  if (isHr(user)) {
    const open = await countOpenForHr(hrScope(user));
    rows.push([Keyboard.button.callback('Добавить нового сотрудника', 'menu:add')]);
    rows.push([Keyboard.button.callback('Добавить сотрудника из PDF', 'menu:addpdf')]);
    rows.push([Keyboard.button.callback('База знаний', 'menu:kbsec')]);
    rows.push([Keyboard.button.callback('Мои сотрудники', 'ob:emps:0')]);
    rows.push([Keyboard.button.callback(open ? `Обращения (${open})` : 'Обращения', 'tk:list:0')]);
  } else if (user.role === 'employee') {
    rows.push([Keyboard.button.callback('Связаться с HR', 'oa:contact_hr')]);
    rows.push([Keyboard.button.callback('Где пообедать', 'oa:food')]);
    rows.push([Keyboard.button.callback('Мои обращения', 'my:list:open:0')]);
  }
  if (!rows.length) return ctx.reply('Для вашей роли в меню пока ничего нет, но скоро появится 🙂');
  return show(ctx, user.role === 'employee' ? 'Чем могу помочь? 👇' : 'Что будем делать? 👇', Keyboard.inlineKeyboard(rows), notification);
}

export function registerMenu(bot) {
  bot.command('menu', async (ctx) => {
    const maxId = ctx.user.user_id;
    const user = await findUserByMaxId(maxId);
    if (!user) return ctx.reply('Я пока не нашёл вас в системе 🤔 Попросите HR добавить вас: он выдаст QR-код для регистрации.');
    if (LEAVE_ON_MENU.includes((await getState(maxId)).step)) await setState(maxId, { step: 'idle', data: {} });
    return showMenu(ctx, user);
  });

  bot.action('menu:main', async (ctx) => {
    const maxId = ctx.user.user_id;
    const user = await findUserByMaxId(maxId);
    if (!user) return ctx.answerOnCallback({ notification: 'Не нашёл вас в системе 🤔' });
    if (LEAVE_ON_MENU.includes((await getState(maxId)).step)) await setState(maxId, { step: 'idle', data: {} });
    return showMenu(ctx, user, 'Меню');
  });

  bot.action('menu:kbsec', async (ctx) => {
    if (!isHr(await findUserByMaxId(ctx.user.user_id))) {
      return ctx.answerOnCallback({ notification: 'Эта функция доступна только HR 🙂' });
    }
    const kb = Keyboard.inlineKeyboard([
      [Keyboard.button.callback('Добавить файл', 'menu:kb')],
      [Keyboard.button.callback('Удалить файл', 'menu:kbdel')],
      [Keyboard.button.callback('← Меню', 'menu:main')],
    ]);
    return show(ctx, 'База знаний 📚 Что сделаем?', kb, 'База знаний');
  });
}
