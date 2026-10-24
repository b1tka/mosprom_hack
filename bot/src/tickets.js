import { ObjectId } from 'mongodb';
import { mdb } from './mongo.js';

// Тикет — обращение сотрудника к закреплённому HR; переписка лежит внутри документа (messages).
// {
//   number, status: 'open' | 'closed', source: 'button' | 'auto',
//   employee: { id, maxId, name, surname, tag, director },   // снимок на момент создания
//   hrId, hrMaxId,
//   title, category, titleSource: 'fallback' | 'llm',
//   messages: [{ at, from: 'employee' | 'bot' | 'hr', authorId, text, attachments: [{ type, filename, token, url }] }],
//   unreadHr, unreadEmployee, createdAt, lastMessageAt, closedAt, closedBy: 'hr' | 'employee' | null
// }
const tickets = mdb.collection('tickets');
const counters = mdb.collection('counters');

// в списках переписка не нужна
const HEADER = { messages: 0 };

export const toId = (id) => (ObjectId.isValid(id) ? new ObjectId(String(id)) : null);

async function nextNumber() {
  const doc = await counters.findOneAndUpdate(
    { _id: 'tickets' },
    { $inc: { seq: 1 } },
    { upsert: true, returnDocument: 'after' },
  );
  return doc.seq;
}

export async function createTicket({ employee, hr, source, title, messages }) {
  const now = new Date();
  const doc = {
    number: await nextNumber(),
    status: 'open',
    source,
    employee,
    hrId: hr.id,
    hrMaxId: String(hr.maxId),
    title,
    category: null,
    titleSource: 'fallback',
    messages,
    unreadHr: messages.filter((m) => m.from === 'employee').length,
    unreadEmployee: 0,
    createdAt: now,
    lastMessageAt: now,
    closedAt: null,
    closedBy: null,
  };
  const { insertedId } = await tickets.insertOne(doc);
  return { ...doc, _id: insertedId };
}

export async function getTicket(id) {
  const _id = toId(id);
  return _id ? tickets.findOne({ _id }) : null;
}

// Сообщение в открытый тикет; непрочитанное растёт у второй стороны. null — тикет закрыт или не найден.
export async function addMessage(id, message) {
  const unread = message.from === 'hr' ? 'unreadEmployee' : 'unreadHr';
  return tickets.findOneAndUpdate(
    { _id: toId(id), status: 'open' },
    { $push: { messages: message }, $inc: { [unread]: 1 }, $set: { lastMessageAt: message.at } },
    { returnDocument: 'after' },
  );
}

export async function markRead(id, side) {
  await tickets.updateOne({ _id: toId(id) }, { $set: { [side === 'hr' ? 'unreadHr' : 'unreadEmployee']: 0 } });
}

// Тикет по номеру (для уведомления о red_flag); employeeId — защита от чужого номера
export async function findByNumber(number, employeeId) {
  return tickets.findOne({ number: Number(number), 'employee.id': employeeId }, { projection: { _id: 1, number: 1, hrId: 1 } });
}

// заголовок от LLM приходит позже временного; повторно не перезаписываем
export async function setTitle(id, { title, category }) {
  await tickets.updateOne({ _id: toId(id), titleSource: 'fallback' }, { $set: { title, category, titleSource: 'llm' } });
}

// null — уже закрыт (повторное нажатие) или не найден
export async function closeTicket(id, by) {
  return tickets.findOneAndUpdate(
    { _id: toId(id), status: 'open' },
    { $set: { status: 'closed', closedAt: new Date(), closedBy: by } },
    { returnDocument: 'after' },
  );
}

export async function reopenTicket(id) {
  return tickets.findOneAndUpdate(
    { _id: toId(id), status: 'closed' },
    { $set: { status: 'open', closedAt: null, closedBy: null, lastMessageAt: new Date() } },
    { returnDocument: 'after' },
  );
}

// ---------- списки ----------

// scope: { hrId } для hr, {} для hr_head (видит все)
export async function listOpenForHr(scope, { skip, limit }) {
  const match = { ...scope, status: 'open' };
  const [items, total] = await Promise.all([
    tickets.aggregate([
      { $match: match },
      { $project: HEADER },
      { $addFields: { hasUnread: { $gt: ['$unreadHr', 0] } } },
      { $sort: { hasUnread: -1, lastMessageAt: -1, _id: -1 } },
      { $skip: skip },
      { $limit: limit },
    ]).toArray(),
    tickets.countDocuments(match),
  ]);
  return { items, total };
}

export async function listClosedForHr(scope, { skip, limit }) {
  const match = { ...scope, status: 'closed' };
  const [items, total] = await Promise.all([
    tickets.find(match, { projection: HEADER }).sort({ closedAt: -1, _id: -1 }).skip(skip).limit(limit).toArray(),
    tickets.countDocuments(match),
  ]);
  return { items, total };
}

export async function countOpenForHr(scope) {
  return tickets.countDocuments({ ...scope, status: 'open' });
}

// следующий открытый тикет с непрочитанным, кроме текущего
export async function nextUnreadForHr(scope, exceptId) {
  return tickets.findOne(
    { ...scope, status: 'open', unreadHr: { $gt: 0 }, _id: { $ne: toId(exceptId) } },
    { projection: HEADER, sort: { lastMessageAt: -1 } },
  );
}

export async function listForEmployee(employeeId, status, { skip, limit }) {
  const match = { 'employee.id': employeeId, status };
  const sort = status === 'open' ? { lastMessageAt: -1, _id: -1 } : { closedAt: -1, _id: -1 };
  const [items, total] = await Promise.all([
    tickets.find(match, { projection: HEADER }).sort(sort).skip(skip).limit(limit).toArray(),
    tickets.countDocuments(match),
  ]);
  return { items, total };
}
