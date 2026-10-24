// Сопоставление данных из PDF (ответ ML /v1/users/extract) со справочниками БД. Чистые функции — их проверяют юнит-тесты.
import { isEmail, normalizePhone } from '../phone.js';

const norm = (s) => String(s ?? '').toLowerCase().replaceAll('ё', 'е').replace(/[^a-zа-я0-9\s-]/g, ' ').replace(/\s+/g, ' ').trim();

// как тег может быть написан (ML уже приводит к GTP/GrPP/Luzhniki, но запасной разбор дешёвый)
const TAG_ALIASES = { gtp: 'gtp', гтп: 'gtp', grpp: 'grpp', грпп: 'grpp', luzhniki: 'luzhniki', лужники: 'luzhniki' };

export function matchTag(value, tags) {
  const key = TAG_ALIASES[norm(value)] ?? norm(value);
  return tags.find((t) => norm(t.name) === key) ?? null;
}

// склонения («Петрову», «Петровым»): общее начало не короче max(4, длина − 2)
const similar = (a, b) => {
  if (!a || !b) return false;
  if (a === b) return true;
  let i = 0;
  while (i < a.length && i < b.length && a[i] === b[i]) i += 1;
  return i >= Math.max(4, Math.min(a.length, b.length) - 2);
};

/**
 * Руководитель по тексту («Петров Алексей Николаевич», «Петрову А.Н.») среди руководителей из БД.
 * -> { director, candidates }: director — единственное совпадение; candidates — несколько кандидатов с одной фамилией.
 */
export function matchDirector(text, directors) {
  const tokens = norm(text).split(' ').filter(Boolean);
  if (!tokens.length) return { director: null, candidates: [] };
  const bySurname = directors.filter((d) => tokens.some((t) => similar(t, norm(d.surname))));
  if (bySurname.length <= 1) return { director: bySurname[0] ?? null, candidates: [] };
  const byName = bySurname.filter((d) => tokens.some((t) => t.replace(/\.$/, '').length >= 1 && norm(d.name).startsWith(t.slice(0, Math.min(3, t.length)))));
  if (byName.length === 1) return { director: byName[0], candidates: [] };
  return { director: null, candidates: bySurname };
}

/**
 * Данные для формы добавления сотрудника. Поля, которых нет или которые не прошли проверку, не заполняются:
 * бот спросит их у HR обычными шагами (тег, руководитель, почта, телефон).
 */
export function buildAddData(user, tags, directors) {
  const data = { name: user.name.trim(), surname: user.surname.trim(), tagSet: false, directorSet: false };
  const tag = matchTag(user.tag, tags);
  if (tag) Object.assign(data, { tagId: tag.id, tagName: tag.name, tagSet: true });
  if (user.director) {
    const { director, candidates } = matchDirector(user.director, directors);
    if (director) Object.assign(data, { directorId: director.id, directorName: `${director.name} ${director.surname}`, directorSet: true });
    else if (candidates.length) data.directorCandidates = candidates;
  }
  if (user.email && isEmail(user.email.trim())) data.email = user.email.trim();
  const phone = normalizePhone(user.phone ?? '');
  if (phone) data.phone = phone;
  return data;
}
