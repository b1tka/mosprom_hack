import { Keyboard } from '@maxhub/max-bot-api';
import { findUserByMaxId } from './db.js';
import { isHr } from './invite.js';
import { getState, setState } from './state.js';
import { countOpenForHr } from './tickets.js';
import { hrScope } from './support.js';

// шаги, из которых /menu выводит: чат сотрудника с HR и ответ HR в тикет
const LEAVE_ON_MENU = ['ticket_chat', 'ticket_reply'];

async function showMenu(ctx, user) {
  const rows = [];
  if (isHr(user)) {
    const open = await countOpenForHr(hrScope(user));
    rows.push([Keyboard.button.callback('Добавить нового сотрудника', 'menu:add')]);
    rows.push([Keyboard.button.callback('База знаний', 'menu:kbsec')]);
    rows.push([Keyboard.button.callback(open ? `Обращения (${open})` : 'Обращения', 'tk:list:0')]);
  } else if (user.role === 'employee') {
    rows.push([Keyboard.button.callback('Мои обращения', 'my:list:open:0')]);
  }
  if (!rows.length) return ctx.reply('Для вашей роли в меню пока нет действий.');
  return ctx.reply('Меню:', { attachments: [Keyboard.inlineKeyboard(rows)] });
}

export function registerMenu(bot) {
  bot.command('menu', async (ctx) => {
    const maxId = ctx.user.user_id;
    const user = await findUserByMaxId(maxId);
    if (!user) return ctx.reply('Вас нет в системе. Обратитесь к HR, чтобы вас добавили.');
    if (LEAVE_ON_MENU.includes((await getState(maxId)).step)) await setState(maxId, { step: 'idle', data: {} });
    return showMenu(ctx, user);
  });

  bot.action('menu:main', async (ctx) => {
    const maxId = ctx.user.user_id;
    const user = await findUserByMaxId(maxId);
    if (!user) return ctx.answerOnCallback({ notification: 'Вас нет в системе.' });
    if (LEAVE_ON_MENU.includes((await getState(maxId)).step)) await setState(maxId, { step: 'idle', data: {} });
    await ctx.answerOnCallback({ notification: 'Меню' });
    return showMenu(ctx, user);
  });

  bot.action('menu:kbsec', async (ctx) => {
    if (!isHr(await findUserByMaxId(ctx.user.user_id))) {
      return ctx.answerOnCallback({ notification: 'Недостаточно прав.' });
    }
    await ctx.answerOnCallback({ notification: 'База знаний' });
    const kb = Keyboard.inlineKeyboard([
      [Keyboard.button.callback('Добавить файл', 'menu:kb')],
      [Keyboard.button.callback('Удалить файл', 'menu:kbdel')],
      [Keyboard.button.callback('← Меню', 'menu:main')],
    ]);
    return ctx.reply('База знаний:', { attachments: [kb] });
  });
}
