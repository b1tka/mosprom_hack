// Уведомления HR из сценария: негативные ответы опросов, интерес к материалам, рассказ о себе.
import { findEmployeeForAlert, listHrRecipients } from '../db.js';
import { cb, kb } from './send.js';

// Закреплённый HR, иначе все hr и hr_head (как в алертах о красном флаге)
export async function hrRecipients(userId) {
  const emp = await findEmployeeForAlert(userId);
  if (!emp) return { emp: null, recipients: [] };
  return { emp, recipients: emp.pinnedHr ? [emp.pinnedHr] : await listHrRecipients() };
}

// Сообщение HR с кнопкой «Написать сотруднику» (tk:contact:<users.id> открывает тикет-чат со стороны HR)
export async function notifyHr(api, userId, text, { chatButton = true } = {}) {
  const { emp, recipients } = await hrRecipients(userId);
  if (!emp || !recipients.length) return [];
  const who = [`${emp.name} ${emp.surname}`, emp.tag, emp.director ? `рук. ${emp.director}` : null].filter(Boolean).join(', ');
  const rows = chatButton ? [[cb('Написать сотруднику', `tk:contact:${emp.id}`)]] : undefined;
  const sent = [];
  for (const r of recipients) {
    try {
      await api.sendMessageToUser(Number(r.maxId), `${text}\n\nСотрудник: ${who}`, rows ? { attachments: [kb(rows)] } : undefined);
      sent.push(String(r.maxId));
    } catch (err) {
      console.error('onboarding hr notification failed', userId, r.maxId, err.message);
    }
  }
  return sent;
}
