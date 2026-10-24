// Загрузка, проверка и компиляция сценария (scenario.yaml). Ошибка в файле -> бот не стартует и перечисляет все проблемы.
// Шаг компилируется в плоский список инструкций (program): ветки ответов становятся переходами (goto/jmp),
// поэтому состояние диалога — это номер шага и номер инструкции (pc), его легко хранить в Mongo.
import { existsSync, readdirSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { parse } from 'yaml';

const HERE = path.dirname(fileURLToPath(import.meta.url));
export const DEFAULT_SCENARIO = path.join(HERE, 'scenario.yaml');
export const ASSETS_DIR = path.join(HERE, '..', '..', 'assets');

export const TAGS = ['GTP', 'GrPP', 'Luzhniki'];
const OPS = ['say', 'ask', 'multi', 'scale', 'text', 'rag', 'use', 'email'];
const ACTIONS = ['contact_hr', 'report_problem'];
const WEEKEND = ['next', 'prev', 'keep'];

const isObj = (v) => v && typeof v === 'object' && !Array.isArray(v);

// кнопка быстрого действия: строка (имя действия) или {action, label}
function normButton(b, actions, where, errors) {
  const spec = typeof b === 'string' ? { action: b } : b;
  if (!isObj(spec) || !ACTIONS.includes(spec.action)) {
    errors.push(`${where}: неизвестное действие кнопки ${JSON.stringify(b)} (допустимо: ${ACTIONS.join(', ')})`);
    return null;
  }
  return { action: spec.action, label: spec.label ?? actions[spec.action] };
}

function checkAt(at, where, errors) {
  if (!isObj(at)) { errors.push(`${where}: нет at`); return; }
  if (at.rel === 'activation') return;
  if (at.rel !== 'start') { errors.push(`${where}: at.rel должен быть activation или start`); return; }
  if (at.time !== undefined && !/^\d{1,2}:\d{2}$/.test(at.time)) errors.push(`${where}: at.time неверного формата (ЧЧ:ММ)`);
  if (at.weekend !== undefined && !WEEKEND.includes(at.weekend)) errors.push(`${where}: at.weekend: ${WEEKEND.join('|')}`);
  if (at.workday !== undefined && !(Number.isInteger(at.workday) && at.workday >= 0)) errors.push(`${where}: at.workday — целое от 0`);
}

export function loadScenario(file = DEFAULT_SCENARIO, { assetsDir = ASSETS_DIR } = {}) {
  const errors = [];
  let raw;
  try {
    raw = parse(readFileSync(file, 'utf8'));
  } catch (err) {
    throw new Error(`Сценарий ${file}: не читается как YAML: ${err.message}`);
  }
  if (!isObj(raw) || !Array.isArray(raw.steps)) throw new Error(`Сценарий ${file}: нет списка steps`);

  const cfg = {
    utc_offset_min: raw.utc_offset_min ?? 180,
    start_offset_days: raw.start_offset_days ?? 3,
    office_tags: raw.office_tags ?? [],
    pause_ms: raw.pause_ms ?? 1200,
  };
  const actions = { contact_hr: 'Связаться с HR', report_problem: 'Передать проблему', ...(raw.actions ?? {}) };
  const texts = raw.texts ?? {};
  const blocks = raw.blocks ?? {};
  const imageOk = (n) => Number.isInteger(n) && existsSync(path.join(assetsDir, 'photos', `${n}.jpg`));

  const compileList = (nodes, out, where, ids, inBlock = false) => {
    if (!Array.isArray(nodes)) { errors.push(`${where}: nodes должен быть списком`); return; }
    nodes.forEach((node, i) => {
      const at = `${where}[${i}]`;
      if (!isObj(node)) { errors.push(`${at}: узел должен быть объектом`); return; }
      const keys = Object.keys(node).filter((k) => OPS.includes(k));
      if (keys.length !== 1) { errors.push(`${at}: нужен ровно один из ${OPS.join('/')}, найдено: ${keys.join(',') || 'ничего'}`); return; }
      const op = keys[0];
      const spec = op === 'use' ? { name: node.use } : (typeof node[op] === 'string' ? { text: node[op] } : node[op]);
      if (!isObj(spec)) { errors.push(`${at}: описание ${op} должно быть объектом`); return; }
      const common = { tags: spec.tags, office: spec.office, when: spec.when };
      for (const t of spec.tags ?? []) if (!TAGS.includes(t)) errors.push(`${at}: неизвестный тег ${t}`);
      if (spec.img !== undefined && !imageOk(spec.img)) errors.push(`${at}: нет картинки ${spec.img} (assets/photos/${spec.img}.jpg)`);

      if (op === 'use') {
        if (!blocks[spec.name]) errors.push(`${at}: нет блока ${spec.name}`);
        else if (!inBlock) compileList(blocks[spec.name], out, `blocks.${spec.name}`, ids, true);
        return;
      }
      if (op === 'say') {
        if (!spec.text && !spec.slot) errors.push(`${at}: say без text и slot`);
        out.push({ op, ...common, text: spec.text, img: spec.img, slot: spec.slot, format: spec.format,
          buttons: (spec.buttons ?? []).map((b) => normButton(b, actions, at, errors)).filter(Boolean) });
        return;
      }
      if (op === 'rag') {
        if (!spec.query || !spec.fallback) errors.push(`${at}: rag требует query и fallback`);
        out.push({ op, ...common, query: spec.query, fallback: spec.fallback });
        return;
      }
      if (op === 'email') {
        if (!spec.subject || !spec.text) errors.push(`${at}: email требует subject и text`);
        out.push({ op, ...common, subject: spec.subject, text: spec.text });
        return;
      }
      if (op === 'text') {
        if (!spec.id || !spec.text) errors.push(`${at}: text требует id и text`);
        if (spec.done && !texts[spec.done]) errors.push(`${at}: нет texts.${spec.done}`);
        out.push({ op, ...common, id: spec.id, text: spec.text, img: spec.img, photo: !!spec.photo, forward: spec.forward,
          optional_label: spec.optional_label, mood: !!spec.mood, done: spec.done,
          buttons: (spec.buttons ?? []).map((b) => normButton(b, actions, at, errors)).filter(Boolean) });
        return;
      }
      if (op === 'scale') {
        if (!spec.id || !spec.text) errors.push(`${at}: scale требует id и text`);
        ids.push({ id: spec.id, at, tags: spec.tags, office: spec.office });
        out.push({ op, ...common, id: spec.id, text: spec.text, img: spec.img, min: spec.min ?? 1, max: spec.max ?? 10, low: spec.low ?? 8 });
        return;
      }
      // ask / multi
      if (!spec.id || !spec.text) errors.push(`${at}: ${op} требует id и text`);
      if (!Array.isArray(spec.options) || spec.options.length < 2) { errors.push(`${at}: нужно минимум 2 варианта`); return; }
      const keys2 = spec.options.map((o) => o.key);
      if (new Set(keys2).size !== keys2.length) errors.push(`${at}: повторяются key вариантов`);
      for (const o of spec.options) if (!o.key || !o.label) errors.push(`${at}: у варианта нет key/label`);
      ids.push({ id: spec.id, at, tags: spec.tags, office: spec.office });
      if (op === 'multi') {
        out.push({ op, ...common, id: spec.id, text: spec.text, img: spec.img, mood: !!spec.mood, done_label: spec.done_label ?? 'Готово',
          options: spec.options.map((o) => ({ key: o.key, label: o.label, negative: !!o.negative })) });
        return;
      }
      if (!['choice', 'yesno'].includes(spec.kind ?? 'choice')) errors.push(`${at}: kind: choice|yesno`);
      if (spec.default !== undefined && !keys2.includes(spec.default)) errors.push(`${at}: default не из вариантов`);
      const instr = { op, ...common, id: spec.id, kind: spec.kind ?? 'choice', text: spec.text, img: spec.img, other: spec.other,
        reason: !!spec.reason, mood: !!spec.mood, timeout_h: spec.timeout_h, default: spec.default, options: [], next: 0 };
      out.push(instr);
      const jumps = [];
      for (const o of spec.options) {
        const opt = { key: o.key, label: o.label, negative: !!o.negative, goto: null };
        if (Array.isArray(o.do) && o.do.length) {
          opt.goto = out.length;
          compileList(o.do, out, `${at}.${o.key}.do`, ids, inBlock);
          const jmp = { op: 'jmp', to: null };
          out.push(jmp);
          jumps.push(jmp);
        }
        instr.options.push(opt);
      }
      instr.next = out.length;
      for (const j of jumps) j.to = instr.next;
      for (const opt of instr.options) if (opt.goto === null) opt.goto = instr.next;
    });
  };

  const steps = [];
  const seen = new Set();
  raw.steps.forEach((s, i) => {
    const where = `steps[${i}]${s?.id ? ` (${s.id})` : ''}`;
    if (!isObj(s) || !s.id) { errors.push(`${where}: нет id`); return; }
    if (seen.has(s.id)) errors.push(`${where}: id повторяется`);
    seen.add(s.id);
    if (!/^[a-z0-9_]+$/.test(s.id)) errors.push(`${where}: id — латиница, цифры, _`);
    checkAt(s.at, where, errors);
    for (const t of s.tags ?? []) if (!TAGS.includes(t)) errors.push(`${where}: неизвестный тег ${t}`);
    const program = [];
    const ids = [];
    compileList(s.nodes, program, `${where}.nodes`, ids);
    // id вопросов не должны повторяться у одного и того же сотрудника (разные теги — разные варианты, это допустимо)
    for (const tag of TAGS) {
      const office = cfg.office_tags.includes(tag);
      const seenIds = new Set();
      for (const q of ids) {
        if (q.tags && !q.tags.includes(tag)) continue;
        if (q.office !== undefined && q.office !== office) continue;
        if (seenIds.has(q.id)) errors.push(`${where}: id вопроса «${q.id}» повторяется для тега ${tag}`);
        seenIds.add(q.id);
      }
    }
    // условия when ссылаются на вопросы этого же шага
    const qids = new Set(ids.map((q) => q.id));
    program.forEach((ins) => {
      for (const k of Object.keys(ins.when ?? {})) if (!qids.has(k)) errors.push(`${where}: when ссылается на неизвестный вопрос ${k}`);
    });
    steps.push({ id: s.id, title: s.title ?? s.id, at: s.at, tags: s.tags, office: s.office, survey: s.survey, program });
  });

  for (const [name, nodes] of Object.entries(blocks)) {
    const probe = [];
    compileList(nodes, probe, `blocks.${name}`, [], true);
    for (const ins of probe) if (!['say', 'rag'].includes(ins.op)) errors.push(`blocks.${name}: в блоках допустимы только say и rag`);
  }
  const uq = raw.unknown_question;
  if (!uq?.text) errors.push('unknown_question: нет text');
  const sr = raw.survey_reminder;
  if (!sr?.text) errors.push('survey_reminder: нет text');
  if (errors.length) throw new Error(`Ошибки в сценарии ${file}:\n - ${errors.join('\n - ')}`);

  const blockPrograms = {};
  for (const [name, nodes] of Object.entries(blocks)) {
    blockPrograms[name] = [];
    compileList(nodes, blockPrograms[name], `blocks.${name}`, [], true);
  }
  const buttons = (list) => (list ?? []).map((b) => normButton(b, actions, 'unknown_question', [])).filter(Boolean);
  return {
    cfg, actions, texts, steps, blocks: blockPrograms,
    unknown_question: { text: uq.text, img: uq.img, buttons: buttons(uq.buttons) },
    survey_reminder: { text: sr.text, img: sr.img, after_h: sr.after_h ?? 24, max: sr.max ?? 2 },
    assetsDir,
  };
}

// применимость инструкции/шага к сотруднику (тег и офис/предприятие)
export function applies(item, emp, scenario) {
  if (item.tags && !item.tags.includes(emp.tag)) return false;
  if (item.office !== undefined && item.office !== isOffice(emp.tag, scenario)) return false;
  return true;
}

export const isOffice = (tag, scenario) => scenario.cfg.office_tags.includes(tag);

export const imagePath = (scenario, n) => path.join(scenario.assetsDir, 'photos', `${n}.jpg`);

// необязательный файл-вложение: assets/content/<slot>.<тег>.<расширение> или assets/content/<slot>.<расширение>
export function slotFile(scenario, slot, tag) {
  const dir = path.join(scenario.assetsDir, 'content');
  if (!existsSync(dir)) return null;
  const files = readdirSync(dir);
  const find = (prefix) => files.find((f) => f.startsWith(`${prefix}.`) && !f.endsWith('.md'));
  const hit = find(`${slot}.${tag}`) ?? files.find((f) => f.startsWith(`${slot}.`) && !TAGS.includes(f.split('.')[1]) && !f.endsWith('.md'));
  return hit ? path.join(dir, hit) : null;
}

let cached;
export function getScenario() {
  cached ??= loadScenario();
  return cached;
}
