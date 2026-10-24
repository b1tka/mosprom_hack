import assert from 'node:assert/strict';
import test from 'node:test';
import { buildAddData, matchDirector, matchTag } from '../src/pdfusers/match.js';

const tags = [{ id: 't1', name: 'GTP' }, { id: 't2', name: 'GrPP' }, { id: 't3', name: 'Luzhniki' }];
const directors = [
  { id: 'd1', name: 'Алексей', surname: 'Петров' },
  { id: 'd2', name: 'Ирина', surname: 'Кузнецова' },
  { id: 'd3', name: 'Мария', surname: 'Петрова' },
  { id: 'd4', name: 'Пётр', surname: 'Орлов' },
];

test('тег: латиница, кириллица и регистр', () => {
  assert.equal(matchTag('GTP', tags).id, 't1');
  assert.equal(matchTag('ГТП', tags).id, 't1');
  assert.equal(matchTag('грпп', tags).id, 't2');
  assert.equal(matchTag('Лужники', tags).id, 't3');
  assert.equal(matchTag('Москва', tags), null);
  assert.equal(matchTag(null, tags), null);
});

test('руководитель: склонения, отчество, инициалы', () => {
  assert.equal(matchDirector('Кузнецова Ирина Викторовна', directors).director.id, 'd2');
  assert.equal(matchDirector('руководитель — Кузнецовой И.В.', directors).director.id, 'd2');
  assert.equal(matchDirector('Орлов П.', directors).director.id, 'd4');
  assert.equal(matchDirector('Сидоров Павел', directors).director, null);
  assert.equal(matchDirector('', directors).director, null);
});

test('руководитель: однофамильцы — по имени, иначе кандидаты', () => {
  assert.equal(matchDirector('Петров Алексей', directors).director.id, 'd1');
  assert.equal(matchDirector('Петрова Мария Ивановна', directors).director.id, 'd3');
  const amb = matchDirector('Петров', directors);
  assert.equal(amb.director, null);
  assert.deepEqual(amb.candidates.map((d) => d.id).sort(), ['d1', 'd3']);
});

test('данные формы: всё из PDF', () => {
  const d = buildAddData({ name: ' Мария ', surname: 'Иванова', email: 'm@polyplastic.ru', phone: '8 (999) 123-45-67', tag: 'ГТП', director: 'Кузнецова Ирина' }, tags, directors);
  assert.deepEqual(d, {
    name: 'Мария', surname: 'Иванова', tagId: 't1', tagName: 'GTP', tagSet: true,
    directorId: 'd2', directorName: 'Ирина Кузнецова', directorSet: true, email: 'm@polyplastic.ru', phone: '79991234567',
  });
});

test('данные формы: чего нет или не прошло проверку — не заполняется (бот спросит)', () => {
  const d = buildAddData({ name: 'Иван', surname: 'Орлов', email: 'не почта', phone: '123', tag: null, director: null }, tags, directors);
  assert.deepEqual(d, { name: 'Иван', surname: 'Орлов', tagSet: false, directorSet: false });
  const amb = buildAddData({ name: 'И', surname: 'О', director: 'Петров' }, tags, directors);
  assert.equal(amb.directorSet, false);
  assert.equal(amb.directorCandidates.length, 2);
});
