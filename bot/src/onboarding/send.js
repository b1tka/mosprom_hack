// Отправка сообщений сценария: картинки (загружаются в MAX один раз, токен кэшируется), кнопки, файлы-вложения.
import { readFileSync } from 'node:fs';
import { Keyboard } from '@maxhub/max-bot-api';
import { redis } from '../state.js';
import { imagePath, slotFile } from '../scenario/load.js';

const IMG_TTL_SECONDS = 7 * 24 * 60 * 60;

export const kb = (rows) => Keyboard.inlineKeyboard(rows);
export const cb = (text, payload) => Keyboard.button.callback(text, payload);

export const render = (text, ob) => String(text ?? '').replaceAll('{name}', ob.name ?? '');

async function imageAttachment(api, scenario, n, { fresh = false } = {}) {
  const key = `ob:img:${n}`;
  if (!fresh) {
    const cached = await redis.get(key);
    if (cached) return JSON.parse(cached);
  }
  const att = (await api.uploadImage({ source: readFileSync(imagePath(scenario, n)) })).toJson();
  await redis.set(key, JSON.stringify(att), 'EX', IMG_TTL_SECONDS);
  return att;
}

// Сообщение: text, img (номер картинки), rows (кнопки), format ('markdown')
export async function sendMessage(api, scenario, maxId, { text, img, rows, format }) {
  const attempt = async (fresh) => {
    const attachments = [];
    if (img) attachments.push(await imageAttachment(api, scenario, img, { fresh }));
    if (rows?.length) attachments.push(kb(rows));
    const extra = { attachments, ...(format ? { format } : {}) };
    return api.sendMessageToUser(Number(maxId), text ?? '', extra);
  };
  try {
    return await attempt(false);
  } catch (err) {
    if (!img) throw err;
    // токен картинки мог устареть — загружаем заново и пробуем ещё раз
    console.error('onboarding image send failed, re-uploading', img, err.message);
    return attempt(true);
  }
}

// Необязательное вложение-файл из assets/content (нет файла — ничего не отправляем)
export async function sendSlotFile(api, scenario, maxId, slot, tag) {
  const file = slotFile(scenario, slot, tag);
  if (!file) return false;
  try {
    const att = (await api.uploadFile({ source: readFileSync(file) })).toJson();
    await api.sendMessageToUser(Number(maxId), '', { attachments: [att] });
    return true;
  } catch (err) {
    console.error('onboarding slot file failed', slot, err.message);
    return false;
  }
}

// Картинка вопроса при редактировании сообщения после ответа (текст меняется, картинка остаётся)
export async function questionAttachments(api, scenario, img) {
  return img ? [await imageAttachment(api, scenario, img)] : [];
}
