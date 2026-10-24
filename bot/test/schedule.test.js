import assert from 'node:assert/strict';
import test from 'node:test';
import {
  defaultStartDate, dueAt, normalizeStartDate, planSteps, stageFor,
} from '../src/scenario/schedule.js';

const cfg = { utc_offset_min: 180, start_offset_days: 3 };
const iso = (d) => d.toISOString();
// локальное время Москвы -> UTC: 10:00 МСК = 07:00Z
const msk = (s) => new Date(`${s}+03:00`);

test('дата выхода: QR + 3 дня, выходной -> понедельник', () => {
  assert.equal(defaultStartDate(msk('2026-10-26T12:00:00'), cfg), '2026-10-29');   // пн + 3 = чт
  assert.equal(defaultStartDate(msk('2026-10-28T12:00:00'), cfg), '2026-11-02');   // ср + 3 = сб -> пн
  assert.equal(defaultStartDate(msk('2026-10-29T12:00:00'), cfg), '2026-11-02');   // чт + 3 = вс -> пн
  assert.equal(defaultStartDate(msk('2026-10-30T12:00:00'), cfg), '2026-11-02');   // пт + 3 = пн
});

test('локальная дата определяется по Москве, а не по UTC', () => {
  // 23:30Z 28 окт = 02:30 МСК 29 окт -> дата 29, +3 = 1 ноября (вс) -> пн 2 ноября
  assert.equal(defaultStartDate(new Date('2026-10-28T23:30:00Z'), cfg), '2026-11-02');
});

test('введённый HR выходной сдвигается на понедельник', () => {
  assert.equal(normalizeStartDate('2026-11-01'), '2026-11-02');
  assert.equal(normalizeStartDate('2026-11-03'), '2026-11-03');
});

const ctx = { start: '2026-11-02', activatedAt: msk('2026-10-30T12:00:00') };   // пн 2 ноября

test('рабочие дни: первый, второй, третий', () => {
  assert.equal(iso(dueAt({ rel: 'start', workday: 0, time: '10:00' }, ctx, cfg)), '2026-11-02T07:00:00.000Z');
  assert.equal(iso(dueAt({ rel: 'start', workday: 1, time: '10:00' }, ctx, cfg)), '2026-11-03T07:00:00.000Z');
  assert.equal(iso(dueAt({ rel: 'start', workday: 2, time: '17:30' }, ctx, cfg)), '2026-11-04T14:30:00.000Z');
});

test('третий рабочий день пропускает выходные', () => {
  const c = { start: '2026-10-29', activatedAt: msk('2026-10-26T12:00:00') };   // чт 29 окт
  assert.equal(iso(dueAt({ rel: 'start', workday: 2, time: '09:00' }, c, cfg)), '2026-11-02T06:00:00.000Z');   // чт, пт, пн
});

test('за день до выхода: weekend keep оставляет воскресенье', () => {
  assert.equal(iso(dueAt({ rel: 'start', days: -1, time: '12:00', weekend: 'keep' }, ctx, cfg)), '2026-11-01T09:00:00.000Z');
  assert.equal(iso(dueAt({ rel: 'start', days: -1, time: '12:00', weekend: 'prev' }, ctx, cfg)), '2026-10-30T09:00:00.000Z');
});

test('календарные смещения и перенос с выходных', () => {
  // выход пн 2 ноября: +10 дней = чт 12 ноября; +14 = пн 16 ноября
  assert.equal(iso(dueAt({ rel: 'start', days: 10, time: '10:00' }, ctx, cfg)), '2026-11-12T07:00:00.000Z');
  assert.equal(iso(dueAt({ rel: 'start', days: 14, time: '10:00' }, ctx, cfg)), '2026-11-16T07:00:00.000Z');
  // +1 месяц = среда 2 декабря; +1 мес. 3 недели = 23 декабря (ср)
  assert.equal(iso(dueAt({ rel: 'start', months: 1, time: '09:00' }, ctx, cfg)), '2026-12-02T06:00:00.000Z');
  assert.equal(iso(dueAt({ rel: 'start', months: 1, days: 21, time: '09:00' }, ctx, cfg)), '2026-12-23T06:00:00.000Z');
  // выходной -> следующий понедельник: 2 янв 2027 (сб) = 2 нояб + 2 мес. -> 4 янв (пн)
  assert.equal(iso(dueAt({ rel: 'start', months: 2, time: '09:00' }, ctx, cfg)), '2027-01-04T06:00:00.000Z');
});

test('конец испытательного срока: +3 месяца -1 день, выходной -> предыдущий рабочий', () => {
  // 2 нояб + 3 мес = 2 фев 2027 (вт), -1 день = пн 1 фев
  assert.equal(iso(dueAt({ rel: 'start', months: 3, days: -1, time: '09:00', weekend: 'prev' }, ctx, cfg)), '2027-02-01T06:00:00.000Z');
  // выход чт 29 окт: +3 мес = 29 янв, -1 = ср 28 янв; выход пн 2 мар: +3 мес = 2 июн, -1 = 1 июн (пн)
  const c = { start: '2026-12-01', activatedAt: msk('2026-11-27T12:00:00') };   // вт: 1 дек + 3 мес = 1 март (пн), -1 = вс 28 фев -> пт 26 фев
  assert.equal(iso(dueAt({ rel: 'start', months: 3, days: -1, time: '09:00', weekend: 'prev' }, c, cfg)), '2027-02-26T06:00:00.000Z');
});

test('месяц с 31 числа прижимается к концу месяца', () => {
  const c = { start: '2026-12-31', activatedAt: msk('2026-12-28T12:00:00') };
  // 31 дек + 2 мес = 28 фев 2027 (вс) -> понедельник 1 марта
  assert.equal(iso(dueAt({ rel: 'start', months: 2, time: '09:00' }, c, cfg)), '2027-03-01T06:00:00.000Z');
});

test('активация: сразу плюс задержка', () => {
  assert.equal(iso(dueAt({ rel: 'activation', delay_min: 5 }, ctx, cfg)), '2026-10-30T09:05:00.000Z');
});

test('план: поздняя активация — недавние шаги уходят сразу, старые пропускаются', () => {
  const steps = [
    { id: 'pre', at: { rel: 'activation' } },
    { id: 'before', at: { rel: 'start', days: -1, time: '12:00', weekend: 'keep' } },
    { id: 'd1', at: { rel: 'start', workday: 0, time: '10:00' } },
    { id: 'w2', at: { rel: 'start', days: 14, time: '10:00' } },
  ];
  const late = { start: '2026-11-02', activatedAt: msk('2026-11-02T13:00:00') };   // сотрудник активировал QR уже в день выхода
  const plan = Object.fromEntries(planSteps(steps, late, cfg).map((p) => [p.id, p]));
  assert.equal(plan.pre.state, 'pending');
  assert.equal(plan.before.state, 'skipped');           // вчера 12:00 — прошло больше суток
  assert.equal(plan.d1.state, 'pending');               // 10:00 сегодня — прошло 3 часа, уйдёт сразу
  assert.equal(iso(plan.d1.dueAt), iso(late.activatedAt));
  assert.equal(plan.w2.state, 'pending');
  assert.equal(iso(plan.w2.dueAt), '2026-11-16T07:00:00.000Z');
});

test('этап для оценки настроения', () => {
  const at = (s) => stageFor('2026-11-02', msk(`${s}T12:00:00`), cfg);
  assert.equal(at('2026-10-31'), 'preparing_for_start_job');
  assert.equal(at('2026-11-02'), 'first_adaptation_days');
  assert.equal(at('2026-11-06'), 'first_adaptation_days');
  assert.equal(at('2026-11-10'), 'beggining_of_adaptation');
  assert.equal(at('2026-12-15'), 'main_adaptation_part');
  assert.equal(at('2027-02-10'), 'completion_of_adaptation');
});
