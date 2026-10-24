// Интеграционный тест движка: живые Mongo и Redis, поддельные часы и мок MAX API. Запуск: INTEGRATION=1 node --test test/engine.int.test.js
// (нужны MONGO_URL и REDIS_URL, как у бота). Без INTEGRATION=1 тест пропускается.
import assert from 'node:assert/strict';
import test, { after, before } from 'node:test';

const RUN = process.env.INTEGRATION === '1';
const t = RUN ? test : test.skip;

const { loadScenario } = RUN ? await import('../src/scenario/load.js') : {};
const { createEngine } = RUN ? await import('../src/onboarding/engine.js') : {};
const store = RUN ? await import('../src/onboarding/store.js') : {};
const { connectMongo, disconnectMongo } = RUN ? await import('../src/mongo.js') : {};
const { redis } = RUN ? await import('../src/state.js') : {};

const PREFIX = 'itest-';
const scenario = RUN ? loadScenario() : null;

function harness({ tag = 'GrPP', n = 1 } = {}) {
  const clock = { t: new Date('2026-10-28T09:00:00Z') };      // среда 12:00 МСК: QR сгенерирован -> выход пн 2 ноября
  const sent = [];
  const calls = { notify: [], kafka: [], mail: [], actions: [], rag: [] };
  const api = {
    uploadImage: async () => ({ toJson: () => ({ type: 'image', payload: { token: 'tok' } }) }),
    uploadFile: async () => ({ toJson: () => ({ type: 'file', payload: { token: 'f' } }) }),
    sendMessageToUser: async (to, text, extra) => {
      const keyboard = extra?.attachments?.find((a) => a.type === 'inline_keyboard');
      const image = extra?.attachments?.some((a) => a.type === 'image');
      sent.push({ to: String(to), text, image, format: extra?.format,
        buttons: keyboard ? keyboard.payload.buttons.flat().map((b) => ({ text: b.text, payload: b.payload })) : [], at: new Date(clock.t) });
    },
  };
  const engine = createEngine({
    scenario, api, now: () => new Date(clock.t), sleep: async () => {},
    rag: async (q) => { calls.rag.push(q); return `RAG:${q.slice(0, 20)}`; },
    mail: async (m) => { calls.mail.push(m); return { sent: true }; },
    notifyHr: async (uid, text) => { calls.notify.push(text); return ['1']; },
    recipients: async () => ({ recipients: [{ maxId: '1' }] }),
    kafkaAnswer: async (a) => { calls.kafka.push(a); },
    actions: { contact_hr: async () => calls.actions.push('contact_hr'), report_problem: async () => calls.actions.push('report_problem') },
  });
  const user = { id: `${PREFIX}${tag}-${n}-${Date.now()}`, max_id: 880000000 + n, name: 'Вася', tag, email: 'v@example.com' };
  return { clock, sent, calls, engine, user };
}

// Пройти ожидания: ответы по политике. policy(wait, ins) -> токен кнопки | { text } | { photo: true }
async function answerAll(h, policy, limit = 80) {
  for (let i = 0; i < limit; i += 1) {
    const ob = await store.findByUser(h.user.id);
    const w = ob.waits[0];
    if (!w) return ob;
    const ins = h.engine.internals.stepDef(w.stepId).program[w.pc];
    const ans = policy(w, ins) ?? defaultAnswer(w, ins);
    let res;
    if (ans.text !== undefined) res = await h.engine.onText({ maxId: ob.maxId, text: ans.text, attachments: ans.attachments ?? [] });
    else res = await h.engine.onCallback({ maxId: ob.maxId, waitId: w.id, token: ans.token });
    assert.ok(res && res.ok !== false, `ответ не принят: ${w.stepId}/${w.qid} ${JSON.stringify(ans)} -> ${JSON.stringify(res)}`);
    if (res.resume) await res.resume();
  }
  const last = await store.findByUser(h.user.id);
  throw new Error(`ожиданий слишком много (цикл?): ${JSON.stringify(last.waits.map((w) => [w.stepId, w.qid, w.kind, w.purpose]))} ${JSON.stringify(last.steps.filter((s) => s.state !== 'sent' && s.state !== 'pending').map((s) => [s.id, s.state, s.pc]))}`);
}

function defaultAnswer(w, ins) {
  if (w.kind === 'text') return { text: 'Текст ответа' };
  if (ins.op === 'ask') return { token: (ins.options.find((o) => !o.negative) ?? ins.options[0]).key };
  if (ins.op === 'multi') return { token: 'd' };
  if (ins.op === 'scale') return { token: 'v9' };
  throw new Error(`нет ответа по умолчанию: ${ins.op}`);
}

const advance = async (h, iso) => { h.clock.t = new Date(iso); await h.engine.tick(); };

async function cleanup() {
  await store.onb().deleteMany({ userId: { $regex: `^${PREFIX}` } });
  await store.surveyAnswers().deleteMany({ userId: { $regex: `^${PREFIX}` } });
}

// моменты первого дня и 2–3 рабочих дней (МСК -> UTC), чтобы не перепрыгивать шаги
const DAY1 = ['2026-10-28T09:00:00Z', '2026-11-01T09:30:00Z', '2026-11-02T07:30:00Z', '2026-11-02T08:30:00Z', '2026-11-02T09:30:00Z', '2026-11-02T10:00:00Z',
  '2026-11-02T10:30:00Z', '2026-11-02T12:30:00Z', '2026-11-02T15:30:00Z'];
const play = async (h, times, policy = () => null) => { for (const iso of times) { await advance(h, iso); await answerAll(h, policy); } };

const stepStates = (ob) => Object.fromEntries(ob.steps.map((s) => [s.id, s.state]));

if (RUN) {
  before(async () => { await connectMongo(); await cleanup(); });
  after(async () => { await cleanup(); await disconnectMongo(); redis.disconnect(); });
}

t('дата выхода: QR + 3 дня, и план шагов', async () => {
  const h = harness();
  const { ob } = await h.engine.start(h.user);
  assert.equal(ob.startDate, '2026-11-02');                           // ср 28 окт + 3 = сб 31 -> пн 2 ноября
  const due = Object.fromEntries(ob.steps.map((s) => [s.id, s.dueAt.toISOString()]));
  assert.equal(due.pre_start, '2026-10-28T09:00:00.000Z');
  assert.equal(due.day_before, '2026-11-01T09:00:00.000Z');          // воскресенье 12:00 МСК
  assert.equal(due.day1_10, '2026-11-02T07:00:00.000Z');
  assert.equal(due.day3_1730, '2026-11-04T14:30:00.000Z');
  assert.ok(!ob.steps.some((s) => s.id === 'day1_11_map_gtp'));      // шаг только для GTP
  await cleanup();
});

t('до выхода: знакомство, вопрос ты/вы, документы по тегу, транспорт через RAG', async () => {
  const h = harness({ tag: 'GTP', n: 2 });
  await h.engine.start(h.user);
  await h.engine.tick();
  let ob = await store.findByUser(h.user.id);
  assert.deepEqual(h.sent.slice(0, 2).map((m) => m.image), [true, true]);   // картинки 14 и 2
  assert.match(h.sent[0].text, /Что умеет этот бот/);
  assert.match(h.sent[1].text, /Добро пожаловать в нашу команду/);
  assert.equal(ob.waits[0].qid, 'address');
  assert.equal(h.sent.at(-1).buttons.length, 2);                     // «На ТЫ», «На ВЫ»
  assert.equal(ob.steps[0].state, 'waiting');

  const w = ob.waits[0];
  const res = await h.engine.onCallback({ maxId: ob.maxId, waitId: w.id, token: 'vy' });
  assert.ok(res.ok && /✓ На ВЫ/.test(res.edit.text));
  const again = await h.engine.onCallback({ maxId: ob.maxId, waitId: w.id, token: 'ty' });   // повторное нажатие
  assert.equal(again.ok, false);
  await res.resume();
  ob = await store.findByUser(h.user.id);
  assert.equal(ob.address, 'vy');
  const texts = h.sent.map((m) => m.text);
  assert.ok(texts.some((x) => x === 'Очень приятно с тобой познакомиться, Вася!'));
  assert.ok(texts.some((x) => /9\. Фотографии 3\*4/.test(x)));       // у GTP фото в списке документов
  assert.equal(ob.waits[0].qid, 'transport');
  assert.match(h.sent.at(-1).text, /дорога до предприятия/);          // вариант текста для GTP
  await answerAll(h, () => ({ token: 'yes' }));
  assert.ok(h.calls.rag.some((q) => /корпоративном транспорте/.test(q)));
  ob = await store.findByUser(h.user.id);
  assert.equal(ob.steps[0].state, 'sent');
  assert.match(h.sent.at(-1).text, /До встречи, Вася!/);
  await cleanup();
});

t('рестарт и повторный проход не дублируют сообщения', async () => {
  const h = harness({ n: 3 });
  await h.engine.start(h.user);
  await h.engine.tick();
  await answerAll(h, () => null);
  const count = h.sent.length;
  await h.engine.tick(); await h.engine.tick();
  assert.equal(h.sent.length, count);
  await cleanup();
});

t('полный путь за 3 месяца: GrPP (офис)', async () => {
  const h = harness({ tag: 'GrPP', n: 4 });
  await h.engine.start(h.user);
  const timeline = ['2026-10-28T09:00:00Z', '2026-11-01T09:30:00Z', '2026-11-02T07:30:00Z', '2026-11-02T08:30:00Z', '2026-11-02T09:30:00Z', '2026-11-02T10:00:00Z',
    '2026-11-02T10:30:00Z', '2026-11-02T12:30:00Z', '2026-11-02T15:30:00Z', '2026-11-03T07:30:00Z', '2026-11-04T06:30:00Z', '2026-11-04T14:45:00Z',
    '2026-11-12T08:00:00Z', '2026-11-16T08:00:00Z', '2026-12-02T07:00:00Z', '2026-12-30T08:00:00Z', '2026-12-23T07:00:00Z', '2027-01-04T07:00:00Z',
    '2027-01-18T08:00:00Z', '2027-02-01T07:00:00Z'];
  for (const iso of timeline) { await advance(h, iso); await answerAll(h, () => null); }
  const ob = await store.findByUser(h.user.id);
  const bad = ob.steps.filter((s) => !['sent', 'skipped'].includes(s.state));
  assert.deepEqual(bad.map((s) => `${s.id}:${s.state}`), []);
  assert.equal(ob.status, 'done');
  assert.ok(h.calls.mail.length === 1 && /Добро пожаловать/.test(h.calls.mail[0].subject) && /Здравствуйте, Вася!/.test(h.calls.mail[0].text));
  assert.ok(h.sent.some((m) => /Первый рабочий день позади/.test(m.text)));                   // офис
  assert.ok(h.sent.some((m) => /подборку фильмов/.test(m.text)));
  assert.ok(h.sent.some((m) => /книги для саморазвития/.test(m.text)));                        // офис
  assert.ok(!h.sent.some((m) => /спортивный комплекс/.test(m.text)));                         // зал только GTP
  assert.ok(h.sent.some((m) => m.format === 'markdown' && /\[плейлист\]/.test(m.text)));
  assert.equal(h.calls.notify.filter((x) => /Негативный/.test(x)).length, 0);                 // везде позитивные ответы
  const answers = await store.surveyAnswers().find({ userId: h.user.id }).toArray();
  assert.ok(answers.length >= 30, `ответов: ${answers.length}`);
  assert.ok(answers.some((a) => a.survey === 'week2' && a.qid === 'q3' && a.value === 9));
  // порядок отправки: все сообщения не раньше предыдущих
  for (let i = 1; i < h.sent.length; i += 1) assert.ok(h.sent[i].at >= h.sent[i - 1].at);
  await cleanup();
});

t('полный путь: GTP (предприятие) — свои шаги', async () => {
  const h = harness({ tag: 'GTP', n: 5 });
  await h.engine.start(h.user);
  for (const iso of ['2026-10-28T09:00:00Z', '2026-11-01T09:30:00Z', '2026-11-02T07:30:00Z', '2026-11-02T08:30:00Z', '2026-11-02T09:30:00Z', '2026-11-02T10:00:00Z',
    '2026-11-02T10:30:00Z', '2026-11-02T12:30:00Z', '2026-11-02T15:30:00Z', '2026-11-04T06:30:00Z', '2026-11-04T08:30:00Z', '2026-11-04T14:45:00Z', '2026-11-16T08:00:00Z',
    '2026-12-02T07:00:00Z', '2026-12-23T07:00:00Z', '2027-01-04T07:00:00Z', '2027-02-01T07:00:00Z']) { await advance(h, iso); await answerAll(h, () => null); }
  const ob = await store.findByUser(h.user.id);
  assert.equal(ob.status, 'done');
  assert.ok(h.sent.some((m) => /Наша территория довольно большая/.test(m.text)));
  assert.ok(h.sent.some((m) => /спортивный комплекс/.test(m.text)));
  assert.ok(!h.sent.some((m) => /Первый рабочий день позади/.test(m.text)));                  // не офис
  assert.ok(!h.sent.some((m) => /книги для саморазвития/.test(m.text)));
  assert.ok(!h.sent.some((m) => /корпоративных мероприятия/.test(m.text)));                   // только GrPP/Luzhniki
  assert.ok(h.sent.some((m) => /http:\/\/ats\//.test(m.text)));
  await cleanup();
});

t('негативные ответы: уведомление HR, причина, оценка настроения, ветки с кнопками', async () => {
  const h = harness({ tag: 'GrPP', n: 6 });
  await h.engine.start(h.user);
  await play(h, DAY1.slice(0, 5), (w) => (w.qid === 'access' ? { token: 'no' } : null));
  // доступы: «Нет»
  assert.ok(h.sent.some((m) => /уточнить у твоего руководителя/.test(m.text)));
  assert.ok(h.calls.notify.some((x) => /Негативный ответ/.test(x) && /Ответ: Нет/.test(x)));
  h.calls.notify.length = 0;
  h.sent.length = 0;
  await advance(h, '2026-11-02T10:00:00Z');   // 13:00 МСК справочник, остальное
  await advance(h, '2026-11-04T14:45:00Z');   // опрос 3 дня
  const negative = { q1: 'c', q2: 'c', q3: 'c' };
  await answerAll(h, (w) => (negative[w.qid] ? { token: negative[w.qid] } : null));
  const withButtons = h.sent.filter((m) => m.buttons.some((b) => b.payload.startsWith('oa:')));
  assert.ok(withButtons.length >= 3);
  assert.ok(withButtons.some((m) => m.buttons.some((b) => b.payload === 'oa:report_problem') && m.buttons.some((b) => b.payload === 'oa:contact_hr')));
  assert.equal(h.calls.notify.filter((x) => /Негативный ответ/.test(x)).length, 3);
  // опрос 2 недели: шкала ниже 8 просит причину, да/нет — тоже
  h.calls.notify.length = 0; h.calls.kafka.length = 0;
  await advance(h, '2026-11-16T08:00:00Z');
  await answerAll(h, (w, ins) => {
    if (w.purpose === 'reason') return { text: `причина ${w.qid}` };
    if (w.qid === 'q1') return { token: 'no' };
    if (ins.op === 'scale' && w.qid === 'q3') return { token: 'v4' };
    return null;
  });
  const ans = await store.surveyAnswers().find({ userId: h.user.id, survey: 'week2' }).toArray();
  const q1 = ans.filter((a) => a.qid === 'q1');
  assert.ok(q1.some((a) => a.key === 'no' && a.negative) && q1.some((a) => a.text === 'причина q1'));
  assert.ok(ans.some((a) => a.qid === 'q3' && a.value === 4 && a.negative) && ans.some((a) => a.qid === 'q3' && a.text === 'причина q3'));
  assert.ok(h.calls.kafka.some((k) => /Нет: причина q1/.test(k.answer) && k.stage === 'beggining_of_adaptation' && k.uid === h.user.id));
  assert.ok(h.calls.notify.some((x) => /Причина негативного ответа/.test(x) && /причина q3/.test(x)));
  const ob = await store.findByUser(h.user.id);
  assert.equal(ob.steps.find((s) => s.id === 'week2_check').state, 'sent');
  await cleanup();
});

t('множественный выбор, «свой вариант» и реакции по ответам', async () => {
  const h = harness({ tag: 'GTP', n: 7 });
  await h.engine.start(h.user);
  await play(h, [...DAY1, '2026-11-03T07:30:00Z', '2026-11-04T08:30:00Z', '2026-11-04T14:45:00Z', '2026-11-12T08:00:00Z', '2026-11-16T08:00:00Z', '2026-12-02T07:00:00Z']);
  h.sent.length = 0;
  await advance(h, '2026-12-23T07:00:00Z');
  // q1: «Уже устал»; q2: никто (+ руководитель); q3: свой вариант
  let ob = await store.findByUser(h.user.id);
  let w = ob.waits[0];
  let r = await h.engine.onCallback({ maxId: ob.maxId, waitId: w.id, token: 'c' }); await r.resume();
  ob = await store.findByUser(h.user.id); w = ob.waits[0];
  assert.equal(w.qid, 'q2');
  r = await h.engine.onCallback({ maxId: ob.maxId, waitId: w.id, token: 'tboss' });
  assert.ok(r.ok && r.edit.rows.flat().find((b) => /Руководитель/.test(b.text)).text.startsWith('✅'));
  r = await h.engine.onCallback({ maxId: ob.maxId, waitId: w.id, token: 'tnobody' });
  r = await h.engine.onCallback({ maxId: ob.maxId, waitId: w.id, token: 'tboss' });                // снять
  r = await h.engine.onCallback({ maxId: ob.maxId, waitId: w.id, token: 'd' });
  assert.ok(/Никто!/.test(r.edit.text) && !/Руководитель/.test(r.edit.text));
  await r.resume();
  ob = await store.findByUser(h.user.id); w = ob.waits[0];
  assert.equal(w.qid, 'q3');
  assert.ok(!w.text.includes('Удаленный'));                                                       // у GTP нет варианта про удалёнку
  r = await h.engine.onCallback({ maxId: ob.maxId, waitId: w.id, token: 'o' }); await r.resume();
  r = await h.engine.onText({ maxId: ob.maxId, text: 'Хочу ментора' }); await r.resume();
  const texts = h.sent.map((m) => m.text);
  assert.ok(texts.some((x) => /Состояние эмоционального напряжения/.test(x)));                    // реакция на q1=c
  assert.ok(texts.some((x) => /Поддержка - важная составляющая/.test(x)));                        // реакция на q2 содержит «Никто!»
  assert.ok(!texts.some((x) => /Очень жаль, что работа не приносит/.test(x)));                    // q1=b не выбирали
  const answers = await store.surveyAnswers().find({ userId: h.user.id, survey: 'two_months' }).toArray();
  assert.ok(answers.some((a) => a.qid === 'q3' && a.key === 'other' && a.text === 'Хочу ментора'));
  assert.deepEqual((await store.findByUser(h.user.id)).steps.find((s) => s.id === 'm1w3_survey').answers.q2, ['nobody']);
  await cleanup();
});

t('рассказ о себе: текст и фото уходят HR, шаг идёт дальше', async () => {
  const h = harness({ tag: 'Luzhniki', n: 8 });
  await h.engine.start(h.user);
  await play(h, DAY1);
  await advance(h, '2026-11-03T07:30:00Z');
  const ob = await store.findByUser(h.user.id);
  assert.equal(ob.waits[0].qid, 'about_me');
  assert.equal((await h.engine.onText({ maxId: ob.maxId, text: '   ' })), null);                  // пустое не принимается
  const r = await h.engine.onText({ maxId: ob.maxId, text: 'Я инженер, 5 лет в отрасли', attachments: [{ type: 'image', payload: { token: 'ph' } }] });
  await r.resume();
  assert.ok(h.calls.notify.some((x) => /рассказал о себе/.test(x) && /инженер/.test(x)));
  assert.ok(h.sent.some((m) => /Передал твой рассказ/.test(m.text)));
  assert.equal((await store.findByUser(h.user.id)).steps.find((s) => s.id === 'day2_10').state, 'sent');
  await cleanup();
});

t('напоминания об опросе: через сутки и ещё через сутки, потом опрос просрочен', async () => {
  const h = harness({ tag: 'GrPP', n: 9 });
  await h.engine.start(h.user);
  for (const iso of ['2026-10-28T09:00:00Z', '2026-11-01T09:30:00Z', '2026-11-02T07:30:00Z', '2026-11-02T09:30:00Z', '2026-11-02T10:00:00Z', '2026-11-03T07:30:00Z']) { await advance(h, iso); await answerAll(h, () => null); }
  await advance(h, '2026-11-04T14:45:00Z');                       // опрос запущен, не отвечаем
  let ob = await store.findByUser(h.user.id);
  assert.equal(ob.steps.find((s) => s.id === 'day3_1730').state, 'waiting');
  h.sent.length = 0;
  await advance(h, '2026-11-05T14:00:00Z');                       // <24 ч: тихо
  assert.equal(h.sent.length, 0);
  await advance(h, '2026-11-05T15:00:00Z');                       // >24 ч: напоминание 1
  assert.equal(h.sent.length, 1);
  assert.ok(/не прошел опрос/.test(h.sent[0].text) && /Привет, Вася!/.test(h.sent[0].text) && h.sent[0].image);
  await advance(h, '2026-11-06T15:30:00Z');                       // напоминание 2
  assert.equal(h.sent.length, 2);
  await advance(h, '2026-11-07T16:00:00Z');                       // больше не напоминаем, опрос просрочен
  assert.equal(h.sent.length, 2);
  ob = await store.findByUser(h.user.id);
  assert.equal(ob.steps.find((s) => s.id === 'day3_1730').state, 'expired');
  assert.equal(ob.waits.length, 0);
  await cleanup();
});

t('вопрос без ответа: ты/вы по таймауту берётся значение по умолчанию', async () => {
  const h = harness({ tag: 'GrPP', n: 10 });
  await h.engine.start(h.user);
  await h.engine.tick();
  assert.equal((await store.findByUser(h.user.id)).steps[0].state, 'waiting');
  await advance(h, '2026-10-28T22:00:00Z');                       // прошло больше 12 часов
  const ob = await store.findByUser(h.user.id);
  assert.equal(ob.address, 'ty');
  assert.ok(h.sent.some((m) => /Очень приятно с тобой познакомиться/.test(m.text)));
  assert.equal(ob.waits[0].qid, 'transport');
  await cleanup();
});

t('поздняя активация, смена даты выхода, пауза и «выполнить следующий шаг»', async () => {
  const h = harness({ tag: 'Luzhniki', n: 11 });
  h.clock.t = new Date('2026-11-02T10:00:00Z');                   // вторник-понедельник: сотрудник активировал QR в день выхода (13:00 МСК)
  const { ob } = await h.engine.start(h.user, { startDate: '2026-11-02' });
  const st = stepStates(ob);
  assert.equal(st.day_before, 'skipped');                          // вчера 12:00 — больше суток назад
  assert.equal(st.day1_10, 'pending');                             // 10:00 МСК — 3 часа назад, уйдёт сразу
  await h.engine.tick();
  await answerAll(h, () => null);
  assert.ok(h.sent.some((m) => /Поздравляю с первым рабочим днем/.test(m.text)));
  // смена даты выхода: будущие шаги пересчитываются
  const before = (await store.findByUser(h.user.id)).steps.find((s) => s.id === 'week2_check').dueAt.toISOString();
  assert.equal(await h.engine.setStartDate(h.user.id, '2026-11-09'), '2026-11-09');
  const after = (await store.findByUser(h.user.id)).steps.find((s) => s.id === 'week2_check').dueAt.toISOString();
  assert.notEqual(before, after);
  assert.equal(after, '2026-11-23T07:00:00.000Z');
  // пауза: шаги не уходят
  await h.engine.setStatus(h.user.id, 'paused');
  h.sent.length = 0;
  await advance(h, '2026-12-30T08:00:00Z');
  assert.equal(h.sent.length, 0);
  // «выполнить следующий» снимает паузу и отправляет ближайший шаг
  await h.engine.setStatus(h.user.id, 'active');
  const nextId = await h.engine.runNext(h.user.id);
  assert.ok(nextId);
  await answerAll(h, () => null);
  assert.ok(h.sent.length > 0);
  await cleanup();
});
