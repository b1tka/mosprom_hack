// Расписание онбординга: из даты выхода и описания `at` из scenario.yaml считает момент отправки шага.
// Чистые функции без обращения к сети и часам (текущее время передаётся аргументом): их проверяют юнит-тесты.
// Локальное время = UTC + utc_offset_min (Москва без переходов на летнее время: +180).

const DAY_MS = 24 * 60 * 60 * 1000;

const pad = (n) => String(n).padStart(2, '0');

// локальная дата как UTC-полночь (удобно для арифметики по дням)
const toDay = (iso) => {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
  if (!m) throw new Error(`неверная дата: ${iso}`);
  return Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
};
export const dayToIso = (ms) => {
  const d = new Date(ms);
  return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}`;
};
const isWeekend = (dayMs) => [0, 6].includes(new Date(dayMs).getUTCDay());

const nextWorkday = (dayMs) => {
  let d = dayMs;
  while (isWeekend(d)) d += DAY_MS;
  return d;
};
const prevWorkday = (dayMs) => {
  let d = dayMs;
  while (isWeekend(d)) d -= DAY_MS;
  return d;
};

// +N месяцев с прижатием дня к концу месяца (31 января + 1 месяц = 28/29 февраля)
function addMonths(dayMs, months) {
  const d = new Date(dayMs);
  const total = d.getUTCFullYear() * 12 + d.getUTCMonth() + months;
  const y = Math.floor(total / 12);
  const m = total % 12;
  const last = new Date(Date.UTC(y, m + 1, 0)).getUTCDate();
  return Date.UTC(y, m, Math.min(d.getUTCDate(), last));
}

// локальная дата (по часам now) в ISO
export function localDate(now, offsetMin) {
  return dayToIso(Date.UTC(...(() => {
    const t = new Date(now.getTime() + offsetMin * 60000);
    return [t.getUTCFullYear(), t.getUTCMonth(), t.getUTCDate()];
  })()));
}

// Дата выхода по умолчанию: дата QR + start_offset_days; выходной -> следующий понедельник
export function defaultStartDate(now, cfg) {
  const base = toDay(localDate(now, cfg.utc_offset_min));
  return dayToIso(nextWorkday(base + cfg.start_offset_days * DAY_MS));
}

// Дата выхода, введённая HR: выходной тоже сдвигаем на понедельник (рабочие дни считаются от рабочего дня)
export const normalizeStartDate = (iso) => dayToIso(nextWorkday(toDay(iso)));

const parseTime = (t) => {
  const m = /^(\d{1,2}):(\d{2})$/.exec(t ?? '00:00');
  if (!m) throw new Error(`неверное время: ${t}`);
  return [Number(m[1]), Number(m[2])];
};

// Момент (UTC Date) локальной даты и времени
const localToUtc = (dayMs, time, offsetMin) => {
  const [h, mi] = parseTime(time);
  return new Date(dayMs + (h * 60 + mi - offsetMin) * 60000);
};

/**
 * at: описание из сценария; ctx: { start: 'YYYY-MM-DD', activatedAt: Date }.
 * rel: activation -> activatedAt + delay_min.
 * rel: start     -> workday W-й рабочий день после выхода, либо календарно (months, days); время `time`;
 *                   выходной: weekend next (по умолчанию) | prev | keep.
 */
export function dueAt(at, ctx, cfg) {
  if (at.rel === 'activation') return new Date(ctx.activatedAt.getTime() + (at.delay_min ?? 0) * 60000);
  if (at.rel !== 'start') throw new Error(`неизвестное rel: ${at.rel}`);
  const start = nextWorkday(toDay(ctx.start));
  let day;
  if (at.workday !== undefined) {
    day = start;
    for (let i = 0; i < at.workday; i += 1) day = nextWorkday(day + DAY_MS);
  } else {
    day = addMonths(start, at.months ?? 0) + (at.days ?? 0) * DAY_MS;
    const rule = at.weekend ?? 'next';
    if (isWeekend(day) && rule !== 'keep') day = rule === 'prev' ? prevWorkday(day) : nextWorkday(day);
  }
  return localToUtc(day, at.time ?? '10:00', cfg.utc_offset_min);
}

/**
 * План шагов сотрудника: для каждого шага момент отправки и начальный статус.
 * Шаги, время которых ушло больше чем на skip_after_h до момента активации, пропускаются; близкие по времени уходят сразу.
 */
export function planSteps(steps, ctx, cfg, { skipAfterH = 24 } = {}) {
  return steps.map((s) => {
    let due = dueAt(s.at, ctx, cfg);
    let state = 'pending';
    if (s.at.rel !== 'activation' && due < ctx.activatedAt) {
      if (ctx.activatedAt.getTime() - due.getTime() > skipAfterH * 3600 * 1000) state = 'skipped';
      else due = ctx.activatedAt;
    }
    return { id: s.id, dueAt: due, state };
  });
}

/** Этап для оценки настроения (контракт ML: ml/app/models.py STAGE_ORDER у hr_analytics) по дням от выхода */
export function stageFor(startIso, now, cfg) {
  const today = toDay(localDate(now, cfg.utc_offset_min));
  const start = nextWorkday(toDay(startIso));
  if (today < start) return 'preparing_for_start_job';
  const days = Math.round((today - start) / DAY_MS);
  if (days <= 4) return 'first_adaptation_days';
  if (days <= 14) return 'beggining_of_adaptation';
  if (days <= 75) return 'main_adaptation_part';
  return 'completion_of_adaptation';
}
