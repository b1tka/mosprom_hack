// Показ экрана: из кнопки заменяем сообщение, из команды/текста отправляем новое.
// fresh — из кнопки, но новым сообщением (например, из уведомления, которое нельзя затирать).
// Подсказка и замена идут одним answerOnCallback: на нажатие его можно вызвать только раз.
export async function show(ctx, text, kb, notification, { fresh = false } = {}) {
  const attachments = kb ? [kb] : [];
  if (!ctx.callback) return ctx.reply(text, attachments.length ? { attachments } : undefined);
  if (fresh) {
    await ctx.answerOnCallback({ notification });
    return ctx.reply(text, attachments.length ? { attachments } : undefined);
  }
  return ctx.answerOnCallback({ notification, message: { text, attachments } });
}
