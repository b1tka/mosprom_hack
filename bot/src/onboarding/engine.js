// Движок онбординга: план шагов по расписанию, диалоговые узлы (выбор, шкала, текст), реакции на ответы.
//
// Шаг (step) = программа инструкций из scenario.yaml. Исполнение идёт по номеру инструкции (pc); вопрос останавливает
// программу и создаёт «ожидание» (wait) в документе сотрудника; ответ (кнопка или текст) снимает ожидание и
// запускает программу дальше с нужной инструкции. Всё состояние — в Mongo, поэтому рестарт бота ничего не теряет.
import crypto from 'node:crypto';
import { applies, isOffice } from '../scenario/load.js';
import { defaultStartDate, normalizeStartDate, planSteps, stageFor } from '../scenario/schedule.js';
import {
  findByMaxId, findByUser, onb, saveAnswer, setStep,
} from './store.js';
import { hrRecipients } from './notify.js';
import {
  cb, render, sendMessage, sendSlotFile,
} from './send.js';

const STALE_RUNNING_MS = 10 * 60 * 1000;
const MAX_CLAIMS_PER_TICK = 20;
const SCALE_ROW = 5;

const newId = () => crypto.randomBytes(4).toString('hex');
const truncate = (s, n) => (s.length > n ? `${s.slice(0, n - 1)}…` : s);

/**
 * deps: { scenario, api, now(): Date, sleep(ms), rag(query, ob) -> string|null, mail({to,subject,text}) -> {sent,reason},
 *         notifyHr(userId, text, opts), kafkaAnswer({uid, hrQuestion, answer, stage}), actions: { contact_hr(ob), report_problem(ob, ctx) } }
 */
export function createEngine(deps) {
  const {
    scenario, api, now, sleep, rag, mail, notifyHr, kafkaAnswer, actions, recipients = hrRecipients,
  } = deps;
  const stepDef = (id) => scenario.steps.find((s) => s.id === id);
  const cfg = scenario.cfg;

  const whenOk = (when, answers) => {
    if (!when) return true;
    return Object.entries(when).every(([qid, expected]) => {
      const got = answers?.[qid];
      const want = Array.isArray(expected) ? expected : [expected];
      return Array.isArray(got) ? got.some((g) => want.includes(g)) : want.includes(got);
    });
  };

  const empOf = (ob) => ({ tag: ob.tag });
  const stage = (ob) => stageFor(ob.startDate, now(), cfg);

  // ---------------------------------------------------------------- запуск
  /** Создать онбординг сотрудника и сразу отправить шаги, чьё время пришло. user: { id, max_id, name, tag, email } */
  async function start(user, { startDate } = {}) {
    const existing = await findByUser(user.id);
    if (existing) return { ob: existing, created: false };
    const activatedAt = now();
    const start0 = startDate ? normalizeStartDate(startDate) : defaultStartDate(activatedAt, cfg);
    const mine = scenario.steps.filter((s) => applies(s, { tag: user.tag }, scenario));
    const plan = planSteps(mine, { start: start0, activatedAt }, cfg).map((p) => ({ ...p, title: stepDef(p.id).title, answers: {}, pc: 0, reminders: 0 }));
    const ob = {
      userId: user.id, maxId: String(user.max_id), name: user.name, tag: user.tag, email: user.email ?? null,
      startDate: start0, address: null, status: 'active', activatedAt, steps: plan, waits: [], createdAt: activatedAt, updatedAt: activatedAt,
    };
    const { insertedId } = await onb().insertOne(ob);
    return { ob: { ...ob, _id: insertedId }, created: true };
  }

  // ---------------------------------------------------------------- исполнение программы шага
  async function say(ob, ins) {
    const text = render(ins.text, ob);
    const rows = ins.buttons?.length ? ins.buttons.map((b) => [cb(b.label, `oa:${b.action}`)]) : undefined;
    if (text || ins.img || rows) await sendMessage(api, scenario, ob.maxId, { text, img: ins.img, rows, format: ins.format });
    if (ins.slot) await sendSlotFile(api, scenario, ob.maxId, ins.slot, ob.tag);
  }

  async function doRag(ob, ins) {
    let answer = null;
    try {
      answer = await rag(ins.query, ob);
    } catch (err) {
      console.error('onboarding rag failed', err.message);
    }
    if (answer) {
      await sendMessage(api, scenario, ob.maxId, { text: answer });
    } else {
      await sendMessage(api, scenario, ob.maxId, { text: render(ins.fallback, ob) });
      await notifyHr(ob.userId, `ℹ️ Сотруднику нужна информация, ответа в базе знаний нет: «${ins.query}»`, { chatButton: true });
    }
  }

  async function doEmail(ob, ins) {
    const res = await mail({ to: ob.email, subject: ins.subject, text: render(ins.text, ob) });
    if (!res.sent) console.warn(`onboarding: письмо «${ins.subject}» не отправлено (${res.reason})`);
    return res;
  }

  function keyboardFor(wait, ins) {
    const rows = [];
    const w = wait.id;
    if (ins.op === 'ask') {
      const labels = ins.options.map((o) => cb(o.label, `ob:${w}:${o.key}`));
      if (ins.kind === 'yesno' || labels.length <= 2) rows.push(labels);
      else for (const l of labels) rows.push([l]);
      if (ins.other) rows.push([cb(ins.other, `ob:${w}:o`)]);
    } else if (ins.op === 'multi') {
      for (const o of ins.options) rows.push([cb(`${wait.selected?.includes(o.key) ? '✅ ' : '⬜ '}${o.label}`, `ob:${w}:t${o.key}`)]);
      rows.push([cb(ins.done_label, `ob:${w}:d`)]);
    } else if (ins.op === 'scale') {
      const nums = [];
      for (let n = ins.min; n <= ins.max; n += 1) nums.push(cb(String(n), `ob:${w}:v${n}`));
      for (let i = 0; i < nums.length; i += SCALE_ROW) rows.push(nums.slice(i, i + SCALE_ROW));
    } else if (ins.op === 'text') {
      if (ins.optional_label) rows.push([cb(ins.optional_label, `ob:${w}:s`)]);
      for (const b of ins.buttons ?? []) rows.push([cb(b.label, `ob:${w}:@${b.action}`)]);
    }
    return rows;
  }

  async function ask(ob, step, ins, pc, extra = {}) {
    const wait = {
      id: newId(), stepId: step.id, pc, kind: ins.op, qid: ins.id, text: render(ins.text, ob), img: ins.img ?? null,
      selected: [], createdAt: now(), photo: !!ins.photo,
      timeoutAt: ins.op === 'ask' && ins.timeout_h ? new Date(now().getTime() + ins.timeout_h * 3600 * 1000) : null,
      ...extra,
    };
    await onb().updateOne({ _id: ob._id }, { $push: { waits: wait } });
    const text = ins.op === 'scale' ? `${wait.text}\n\n${scenario.texts.scale_hint ?? ''}`.trim() : wait.text;
    await sendMessage(api, scenario, ob.maxId, { text, img: wait.img, rows: keyboardFor(wait, ins) });
    return wait;
  }

  // Основной цикл: исполняет инструкции шага с pc до вопроса или конца
  async function run(obId, stepId, startPc) {
    const step = stepDef(stepId);
    let pc = startPc;
    await onb().updateOne({ _id: obId, 'steps.id': stepId }, { $set: { 'steps.$.state': 'running', 'steps.$.startedAt': now() } });
    for (;;) {
      const ob = await onb().findOne({ _id: obId });
      if (!ob || ob.status === 'paused') return;
      const st = ob.steps.find((s) => s.id === stepId);
      if (pc >= step.program.length) {
        await setStep(obId, stepId, { state: 'sent', sentAt: now(), pc });
        await finishIfDone(obId);
        return;
      }
      const ins = step.program[pc];
      if (!applies(ins, empOf(ob), scenario) || !whenOk(ins.when, st.answers)) { pc += 1; continue; }
      try {
        if (ins.op === 'jmp') { pc = ins.to; continue; }
        if (ins.op === 'say') { await say(ob, ins); await sleep(cfg.pause_ms); }
        else if (ins.op === 'rag') { await doRag(ob, ins); await sleep(cfg.pause_ms); }
        else if (ins.op === 'email') await doEmail(ob, ins);
        else {   // ask | multi | scale | text — вопрос: ждём ответ
          await ask(ob, step, ins, pc);
          const remindAt = step.survey && !st.remindAt ? new Date(now().getTime() + scenario.survey_reminder.after_h * 3600 * 1000) : st.remindAt ?? null;
          await setStep(obId, stepId, { state: 'waiting', pc, ...(remindAt ? { remindAt } : {}) });
          return;
        }
      } catch (err) {
        // не доставили (бот заблокирован и т. п.): шаг не должен зависнуть, пишем причину и идём дальше
        console.error(`onboarding: шаг ${stepId} инструкция ${pc} (${ins.op}) не выполнена`, err.message);
        await setStep(obId, stepId, { lastError: `${ins.op}@${pc}: ${err.message}`.slice(0, 300) });
      }
      pc += 1;
      await setStep(obId, stepId, { pc });
    }
  }

  async function finishIfDone(obId) {
    const ob = await onb().findOne({ _id: obId });
    if (ob && ob.steps.every((s) => ['sent', 'skipped', 'expired'].includes(s.state))) {
      await onb().updateOne({ _id: obId }, { $set: { status: 'done', finishedAt: now() } });
    }
  }

  // ---------------------------------------------------------------- планировщик
  // Захватывает ближайший шаг, время которого пришло. У сотрудника одновременно идёт только один шаг (сообщения не перемешиваются);
  // захват атомарный: updateOne срабатывает, только если шаг всё ещё pending.
  async function claimDue(obId = null) {
    for (let attempt = 0; attempt < 5; attempt += 1) {
      const t = now();
      const filter = {
        status: 'active',
        ...(obId ? { _id: obId } : {}),
        $and: [
          { steps: { $elemMatch: { state: 'pending', dueAt: { $lte: t } } } },
          { steps: { $not: { $elemMatch: { state: 'running' } } } },
        ],
      };
      const ob = await onb().findOne(filter);
      if (!ob) return null;
      const st = ob.steps.filter((s) => s.state === 'pending' && s.dueAt <= t).sort((a, b) => a.dueAt - b.dueAt)[0];
      const res = await onb().updateOne(
        { _id: ob._id, steps: { $elemMatch: { id: st.id, state: 'pending' } } },
        { $set: { 'steps.$.state': 'running', 'steps.$.startedAt': t } },
      );
      if (res.modifiedCount === 1) return { ob, step: st };
    }
    return null;
  }

  async function recoverStale() {
    const before = new Date(now().getTime() - STALE_RUNNING_MS);
    const stale = await onb().find({ status: 'active', steps: { $elemMatch: { state: 'running', startedAt: { $lt: before } } } }).toArray();
    for (const ob of stale) {
      for (const st of ob.steps.filter((s) => s.state === 'running' && s.startedAt < before)) {
        console.warn(`onboarding: шаг ${st.id} сотрудника ${ob.userId} завис в running, продолжаю с pc=${st.pc ?? 0}`);
        await run(ob._id, st.id, st.pc ?? 0).catch((err) => console.error('onboarding recover failed', err));
      }
    }
  }

  async function sendReminders() {
    const t = now();
    const { text, img, max } = scenario.survey_reminder;
    const obs = await onb().find({ status: 'active', steps: { $elemMatch: { state: 'waiting', remindAt: { $lte: t } } } }).toArray();
    for (const ob of obs) {
      for (const st of ob.steps.filter((s) => s.state === 'waiting' && s.remindAt && s.remindAt <= t)) {
        if ((st.reminders ?? 0) >= max) {   // напоминаний достаточно: опрос считаем просроченным
          await onb().updateOne({ _id: ob._id }, { $pull: { waits: { stepId: st.id } } });
          await setStep(ob._id, st.id, { state: 'expired', remindAt: null });
          await finishIfDone(ob._id);
          continue;
        }
        try {
          await sendMessage(api, scenario, ob.maxId, { text: render(text, ob), img });
        } catch (err) {
          console.error('onboarding reminder failed', ob.userId, err.message);
        }
        await setStep(ob._id, st.id, { reminders: (st.reminders ?? 0) + 1, remindAt: new Date(t.getTime() + scenario.survey_reminder.after_h * 3600 * 1000) });
      }
    }
  }

  // ответы по умолчанию для неотвеченных вопросов с таймаутом (например, «ты/вы» до выхода)
  async function applyTimeouts() {
    const t = now();
    const obs = await onb().find({ status: 'active', waits: { $elemMatch: { timeoutAt: { $lte: t } } } }).toArray();
    for (const ob of obs) {
      for (const w of ob.waits.filter((x) => x.timeoutAt && x.timeoutAt <= t)) {
        const ins = stepDef(w.stepId)?.program[w.pc];
        if (!ins || ins.default === undefined) { await onb().updateOne({ _id: ob._id }, { $pull: { waits: { id: w.id } } }); continue; }
        const taken = await onb().findOneAndUpdate({ _id: ob._id, 'waits.id': w.id }, { $pull: { waits: { id: w.id } } }, { returnDocument: 'before' });
        if (!taken) continue;   // успели ответить сами
        const res = await answerChoice(ob, w, ins, ins.default, { auto: true });
        if (res?.resume) await res.resume().catch((err) => console.error('onboarding timeout resume failed', err));
      }
    }
  }

  /** Один проход планировщика. Возвращает число запущенных шагов */
  async function tick() {
    await recoverStale();
    let launched = 0;
    const jobs = [];
    const busy = new Set();
    for (let i = 0; i < MAX_CLAIMS_PER_TICK; i += 1) {
      const claimed = await claimDue();
      if (!claimed) break;
      const id = String(claimed.ob._id);
      if (busy.has(id)) break;   // защита от зацикливания, если захват не меняет состояние
      busy.add(id);
      launched += 1;
      // разные сотрудники — параллельно; шаги одного сотрудника — по очереди, один за другим без ожидания следующего прохода
      jobs.push((async () => {
        let c = claimed;
        while (c) {
          await run(c.ob._id, c.step.id, 0).catch((err) => console.error(`onboarding: шаг ${c.step.id} упал`, err));
          c = await claimDue(claimed.ob._id);
          if (c) launched += 1;
        }
      })());
    }
    await Promise.all(jobs);
    await applyTimeouts();
    await sendReminders();
    return launched;
  }

  // ---------------------------------------------------------------- ответы
  async function record(ob, w, ins, data) {
    const step = stepDef(w.stepId);
    await saveAnswer({ ob, stepId: w.stepId, survey: step.survey, qid: w.qid, now: now(), ...data });
  }

  async function mood(ob, ins, answerText) {
    try {
      await kafkaAnswer({ uid: ob.userId, hrQuestion: ins.text, answer: answerText, stage: stage(ob) });
    } catch (err) {
      console.error('onboarding kafka answer failed', err.message);
    }
  }

  const negativeNote = (ob, ins, answer) => notifyHr(ob.userId, `⚠️ Негативный ответ в опросе.\nВопрос: ${truncate(render(ins.text, ob), 300)}\nОтвет: ${truncate(String(answer), 300)}`);

  const resumeAt = (ob, w, pc) => async () => {
    await run(ob._id, w.stepId, pc);
  };

  // текстовое ожидание (причина, «свой вариант», свободный ответ)
  async function askText(ob, w, ins, prompt, extra) {
    const wait = {
      id: newId(), stepId: w.stepId, pc: w.pc, kind: 'text', qid: w.qid, text: prompt, purpose: extra.purpose, after: extra.after,
      selected: [], createdAt: now(), photo: false, label: extra.label ?? null, value: extra.value ?? null, key: extra.key ?? null,
    };
    await onb().updateOne({ _id: ob._id }, { $push: { waits: wait } });
    await sendMessage(api, scenario, ob.maxId, { text: prompt });
    await setStep(ob._id, w.stepId, { state: 'waiting' });
  }

  // выбор варианта (ask). token = key варианта
  async function answerChoice(ob, w, ins, key, { auto = false } = {}) {
    const opt = ins.options.find((o) => o.key === key);
    if (!opt) return null;
    const step = stepDef(w.stepId);
    await record(ob, w, ins, { key: opt.key, label: opt.label, negative: opt.negative });
    if (w.qid === 'address' && (opt.key === 'ty' || opt.key === 'vy')) await onb().updateOne({ _id: ob._id }, { $set: { address: opt.key } });
    if (ins.mood || opt.negative) await mood(ob, ins, opt.label);
    if (opt.negative) await negativeNote(ob, ins, opt.label);
    const edit = { text: `${w.text}\n\n✓ ${opt.label}`, img: w.img };
    if (ins.reason && opt.negative && !auto) {
      return {
        edit,
        resume: async () => {
          await askText(ob, w, ins, scenario.texts.reason_prompt, { purpose: 'reason', after: opt.goto, label: opt.label, key: opt.key });
        },
      };
    }
    return { edit, resume: resumeAt(ob, w, opt.goto), step };
  }

  /**
   * Нажатие кнопки опроса. -> { ok, toast?, edit?: {text, img, keyboard?}, resume?: fn }
   * token: ключ варианта | o (свой вариант) | t<key> (переключатель) | d (готово) | v<n> (шкала) | s (пропустить) | @<действие>
   */
  async function onCallback({ maxId, waitId, token }) {
    const ob = await onb().findOne({ maxId: String(maxId), 'waits.id': waitId });
    if (!ob) return { ok: false, toast: 'Этот вопрос уже закрыт.' };
    const w = ob.waits.find((x) => x.id === waitId);
    const ins = stepDef(w.stepId)?.program[w.pc];
    if (!ins) return { ok: false, toast: 'Вопрос устарел.' };
    // ожидание текста (причина, «свой вариант», рассказ) принимает только текст; у узла text допустимы «пропустить» и действия
    if (w.kind === 'text' && !(w.purpose === undefined && (token === 's' || token.startsWith('@')))) return { ok: false, toast: 'Ответь, пожалуйста, текстом.' };

    // переключатель в множественном выборе: ожидание не снимаем
    if (ins.op === 'multi' && token.startsWith('t')) {
      const key = token.slice(1);
      if (!ins.options.some((o) => o.key === key)) return { ok: false, toast: 'Неизвестный вариант.' };
      const selected = w.selected.includes(key) ? w.selected.filter((k) => k !== key) : [...w.selected, key];
      await onb().updateOne({ _id: ob._id, 'waits.id': waitId }, { $set: { 'waits.$.selected': selected } });
      return { ok: true, toast: 'Выбрано', edit: { text: w.text, img: w.img, rows: keyboardFor({ ...w, selected }, ins) } };
    }

    // снимаем ожидание атомарно: повторное нажатие получит «уже закрыт»
    const taken = await onb().findOneAndUpdate({ _id: ob._id, 'waits.id': waitId }, { $pull: { waits: { id: waitId } } }, { returnDocument: 'before' });
    if (!taken) return { ok: false, toast: 'Этот вопрос уже закрыт.' };

    if (token.startsWith('@') && ins.op === 'text') {
      const action = token.slice(1);
      return {
        ok: true, toast: 'Готово', edit: { text: `${w.text}\n\n✓ ${scenario.actions[action] ?? action}`, img: w.img },
        resume: async () => { await actions[action]?.(ob); await run(ob._id, w.stepId, w.pc + 1); },
      };
    }
    if (token === 's' && ins.op === 'text') {
      await record(ob, w, ins, { text: null, label: 'пропущено' });
      return { ok: true, toast: 'Пропущено', edit: { text: `${w.text}\n\n✓ Пропущено`, img: w.img }, resume: resumeAt(ob, w, w.pc + 1) };
    }
    if (ins.op === 'ask' && token === 'o' && ins.other) {
      return {
        ok: true, toast: ins.other, edit: { text: `${w.text}\n\n✓ ${ins.other}`, img: w.img },
        resume: async () => askText(ob, w, ins, 'Напиши свой вариант одним сообщением.', { purpose: 'other', after: ins.next }),
      };
    }
    if (ins.op === 'ask') {
      const res = await answerChoice(ob, w, ins, token);
      return res ? { ok: true, toast: 'Принято', edit: res.edit, resume: res.resume } : { ok: false, toast: 'Неизвестный вариант.' };
    }
    if (ins.op === 'multi' && token === 'd') {
      const picked = ins.options.filter((o) => w.selected.includes(o.key));
      const label = picked.map((o) => o.label).join(', ') || 'ничего не выбрано';
      await record(ob, w, ins, { key: null, value: null, label, text: null, negative: picked.some((o) => o.negative) });
      await onb().updateOne({ _id: ob._id, 'steps.id': w.stepId }, { $set: { [`steps.$.answers.${w.qid}`]: picked.map((o) => o.key) } });
      const neg = picked.filter((o) => o.negative);
      if (neg.length) await negativeNote(ob, ins, neg.map((o) => o.label).join(', '));
      if (ins.mood || neg.length) await mood(ob, ins, label);
      return { ok: true, toast: 'Принято', edit: { text: `${w.text}\n\n✓ ${label}`, img: w.img }, resume: resumeAt(ob, w, w.pc + 1) };
    }
    if (ins.op === 'scale' && /^v\d+$/.test(token)) {
      const n = Number(token.slice(1));
      if (n < ins.min || n > ins.max) return { ok: false, toast: 'Неверная оценка.' };
      const low = n < ins.low;
      await record(ob, w, ins, { value: n, label: String(n), negative: low });
      await onb().updateOne({ _id: ob._id, 'steps.id': w.stepId }, { $set: { [`steps.$.answers.${w.qid}`]: n } });
      const edit = { text: `${w.text}\n\n✓ ${n} из ${ins.max}`, img: w.img };
      if (low) {
        await negativeNote(ob, ins, `${n} из ${ins.max}`);
        return { ok: true, toast: String(n), edit, resume: async () => askText(ob, w, ins, scenario.texts.reason_prompt, { purpose: 'reason', after: w.pc + 1, value: n, label: `${n} из ${ins.max}` }) };
      }
      return { ok: true, toast: String(n), edit, resume: resumeAt(ob, w, w.pc + 1) };
    }
    // неподходящий токен: возвращаем ожидание
    await onb().updateOne({ _id: ob._id }, { $push: { waits: w } });
    return { ok: false, toast: 'Не понял ответ.' };
  }

  /**
   * Текст или фото от сотрудника. -> null (не наш ответ) | { resume, reply? }
   * message: { text, attachments: [{type, ...}] } — как в событии MAX
   */
  async function onText({ maxId, text, attachments = [] }) {
    const ob = await findByMaxId(maxId);
    if (!ob) return null;
    const cands = ob.waits.filter((x) => x.kind === 'text').sort((a, b) => b.createdAt - a.createdAt);
    const w = cands[0];
    if (!w) return null;
    const hasPhoto = attachments.some((a) => a.type === 'image');
    const clean = (text ?? '').trim();
    if (!clean && !(w.photo && hasPhoto)) return null;
    const taken = await onb().findOneAndUpdate({ _id: ob._id, 'waits.id': w.id }, { $pull: { waits: { id: w.id } } }, { returnDocument: 'before' });
    if (!taken) return null;
    const step = stepDef(w.stepId);
    const ins = step.program[w.pc];
    const data = { text: clean || '(фото)', label: clean || '(фото)' };

    if (w.purpose === 'reason') {
      await record(ob, w, ins, { value: w.value, key: w.key, label: w.label, text: clean, negative: true });
      await mood(ob, ins, `${w.label}: ${clean}`);
      await notifyHr(ob.userId, `⚠️ Причина негативного ответа.\nВопрос: ${truncate(render(ins.text, ob), 300)}\nОтвет: ${w.label}\nПричина: ${truncate(clean, 500)}`);
      return { resume: resumeAt(ob, w, w.after) };
    }
    if (w.purpose === 'other') {
      await record(ob, w, ins, { key: 'other', label: clean, text: clean });
      await mood(ob, ins, clean);
      return { resume: resumeAt(ob, w, w.after) };
    }
    // свободный текст из узла text
    await record(ob, w, ins, { ...data, negative: false });
    if (ins.mood) await mood(ob, ins, clean);
    const resume = async () => {
      if (ins.forward === 'hr') {
        await notifyHr(ob.userId, `🙋 Сотрудник рассказал о себе:\n${clean || '(без текста)'}${hasPhoto ? '\n(приложено фото)' : ''}`, { chatButton: true });
        await forwardPhotos(ob, attachments);
      }
      if (ins.done && scenario.texts[ins.done]) await sendMessage(api, scenario, ob.maxId, { text: render(scenario.texts[ins.done], ob) });
      await run(ob._id, w.stepId, w.pc + 1);
    };
    return { resume };
  }

  // Фото сотрудника — закреплённому HR (токены картинок MAX пересылаются как есть)
  async function forwardPhotos(ob, attachments) {
    const photos = attachments.filter((a) => a.type === 'image' && a.payload?.token);
    if (!photos.length) return;
    const { recipients: list } = await recipients(ob.userId);
    for (const r of list) {
      try {
        await api.sendMessageToUser(Number(r.maxId), `Фото: ${ob.name}`, { attachments: photos.map((a) => ({ type: 'image', payload: { token: a.payload.token } })) });
      } catch (err) {
        console.error('onboarding forward photo failed', err.message);
      }
    }
  }

  // ---------------------------------------------------------------- управление (HR, админка)
  async function setStartDate(userId, startDate) {
    const ob = await findByUser(userId);
    if (!ob) return null;
    const start0 = normalizeStartDate(startDate);
    const mine = scenario.steps.filter((s) => applies(s, { tag: ob.tag }, scenario));
    const fresh = planSteps(mine, { start: start0, activatedAt: ob.activatedAt }, cfg);
    // уже отправленные/пропущенные шаги не трогаем, будущие пересчитываем (если время ушло в прошлое — шаг уйдёт при ближайшем проходе)
    const steps = ob.steps.map((s) => {
      if (s.state !== 'pending') return s;
      const f = fresh.find((x) => x.id === s.id);
      return f ? { ...s, dueAt: f.dueAt } : s;
    });
    await onb().updateOne({ _id: ob._id }, { $set: { startDate: start0, steps, updatedAt: now() } });
    return start0;
  }

  async function setStatus(userId, status) {
    await onb().updateOne({ userId }, { $set: { status, updatedAt: now() } });
  }

  // следующий ожидающий шаг — сделать «просроченным» и сразу выполнить (для тестирования в админке)
  async function runNext(userId) {
    const ob = await findByUser(userId);
    const next = ob?.steps.filter((s) => s.state === 'pending').sort((a, b) => a.dueAt - b.dueAt)[0];
    if (!next) return null;
    await setStep(ob._id, next.id, { dueAt: new Date(now().getTime() - 1000) });
    if (ob.status === 'paused') await setStatus(userId, 'active');
    await tick();
    return next.id;
  }

  // сдвиг времени всех будущих шагов (проверка 3 месяцев за минуты): отрицательное значение — ближе
  async function shift(userId, days) {
    const ob = await findByUser(userId);
    if (!ob) return false;
    const ms = days * 86400000;
    const steps = ob.steps.map((s) => (s.state === 'pending' ? { ...s, dueAt: new Date(s.dueAt.getTime() + ms) } : s));
    await onb().updateOne({ _id: ob._id }, { $set: { steps, updatedAt: now() } });
    return true;
  }

  return {
    start, tick, run, onCallback, onText, setStartDate, setStatus, runNext, shift,
    // для команд (/Food) и действий: выполнить произвольный блок инструкций без шага
    async sendBlock(ob, name) {
      for (const ins of scenario.blocks[name] ?? []) {
        if (!applies(ins, empOf(ob), scenario)) continue;
        if (ins.op === 'say') await say(ob, ins);
        if (ins.op === 'rag') await doRag(ob, ins);
      }
    },
    internals: { keyboardFor, whenOk, stepDef, isOffice },
  };
}
