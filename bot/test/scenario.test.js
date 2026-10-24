import assert from 'node:assert/strict';
import { mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { applies, loadScenario, TAGS } from '../src/scenario/load.js';

const sc = loadScenario();

test('сценарий из репозитория загружается и содержит ожидаемые шаги', () => {
  const ids = sc.steps.map((s) => s.id);
  for (const id of ['pre_start', 'day_before', 'day1_10', 'day3_1730', 'week2_check', 'm1w3_survey', 'probation_end']) assert.ok(ids.includes(id), id);
  assert.equal(sc.steps.length, 22);
});

test('переходы веток указывают внутрь программы шага', () => {
  for (const s of sc.steps) {
    s.program.forEach((ins, i) => {
      if (ins.op === 'jmp') assert.ok(ins.to > i && ins.to <= s.program.length, `${s.id}[${i}] jmp ${ins.to}`);
      if (ins.op === 'ask') {
        assert.ok(ins.next > i && ins.next <= s.program.length, `${s.id}[${i}] next`);
        for (const o of ins.options) assert.ok(o.goto > i && o.goto <= s.program.length, `${s.id}[${i}] ${o.key}.goto`);
      }
    });
  }
});

test('у каждого тега есть шаги, а теговые шаги не пересекаются ошибочно', () => {
  for (const tag of TAGS) {
    const mine = sc.steps.filter((s) => applies(s, { tag }, sc));
    assert.ok(mine.length >= 17, `${tag}: ${mine.length} шагов`);
  }
  const gtp = (id) => applies(sc.steps.find((s) => s.id === id), { tag: 'GTP' }, sc);
  assert.ok(gtp('day1_11_map_gtp') && gtp('day3_11_gym'));
  assert.ok(!gtp('day1_18') && !gtp('day3_09') && !gtp('m2w2_books'));            // «только офис»
  const grpp = (id) => applies(sc.steps.find((s) => s.id === id), { tag: 'GrPP' }, sc);
  assert.ok(grpp('day1_18') && grpp('day3_09') && grpp('day10_events') && !grpp('day1_11_map_gtp'));
});

test('варианты по тегу: документы и вопросы про транспорт не дублируются у одного сотрудника', () => {
  const pre = sc.steps.find((s) => s.id === 'pre_start');
  for (const tag of TAGS) {
    const mine = pre.program.filter((i) => applies(i, { tag }, sc));
    assert.equal(mine.filter((i) => i.op === 'ask' && i.id === 'transport').length, 1, tag);
    assert.equal(mine.filter((i) => i.op === 'say' && /Необходимые документы/.test(i.text ?? '')).length, 1, tag);
  }
  const docs = (tag) => pre.program.find((i) => applies(i, { tag }, sc) && /Необходимые документы/.test(i.text ?? '')).text;
  assert.ok(!/Фотографии 3\*4/.test(docs('GrPP')) && /Фотографии 3\*4/.test(docs('GTP')));    // как в ТЗ: фото только в списке GTP
});

test('тексты ТЗ на месте: «{name}» вместо «Х», ключевые формулировки', () => {
  const all = JSON.stringify(sc.steps);
  assert.ok(all.includes('{name}'));
  assert.ok(!/Привет, Х!/.test(all));
  for (const phrase of ['Как мы будем общаться?', 'Хочешь, подскажу, где пообедать?', 'Первый рабочий день позади!',
    'Вопрос 3: Понятны ли тебе задачи на испытательный срок?', 'Ты уже на финишной прямой']) {
    assert.ok(all.includes(phrase), phrase);
  }
});

function bad(yaml) {
  const dir = mkdtempSync(path.join(tmpdir(), 'scn-'));
  const file = path.join(dir, 'scenario.yaml');
  writeFileSync(file, yaml);
  return () => loadScenario(file);
}
const base = `
unknown_question: {text: x}
survey_reminder: {text: x}
`;

test('валидатор: перечисляет все ошибки сразу', () => {
  const run = bad(`${base}
steps:
  - id: a
    at: {rel: start, time: "25h"}
    tags: [Nope]
    nodes:
      - say: {text: x, img: 999}
      - ask: {id: q, text: t, options: [{key: a, label: A}]}
      - use: missing
      - {say: x, ask: {id: z}}
  - id: a
    at: {rel: tomorrow}
    nodes: []
`);
  assert.throws(run, (e) => {
    for (const part of ['at.time', 'неизвестный тег Nope', 'нет картинки 999', 'минимум 2 варианта', 'нет блока missing', 'ровно один', 'id повторяется', 'at.rel']) {
      assert.ok(e.message.includes(part), `нет «${part}» в:\n${e.message}`);
    }
    return true;
  });
});

test('валидатор: повторяющиеся id вопросов у одного тега и when на неизвестный вопрос', () => {
  const run = bad(`${base}
steps:
  - id: a
    at: {rel: activation}
    nodes:
      - ask: {id: q, text: t, options: [{key: a, label: A}, {key: b, label: B}]}
      - ask: {id: q, text: t, options: [{key: a, label: A}, {key: b, label: B}]}
      - say: {text: x, when: {nope: a}}
`);
  assert.throws(run, (e) => e.message.includes('повторяется для тега') && e.message.includes('when ссылается на неизвестный вопрос nope'));
});

test('валидатор: битый YAML и пустой сценарий', () => {
  assert.throws(bad('steps: [\n'), /не читается как YAML/);
  assert.throws(bad('foo: 1\n'), /нет списка steps/);
});
