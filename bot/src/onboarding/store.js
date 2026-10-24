// Хранилище онбординга в Mongo.
// onboarding     — по документу на сотрудника: план шагов, ожидаемые ответы (waits), ответы по шагам.
// survey_answers — журнал ответов на опросы (для HR и админки).
import { mdb } from '../mongo.js';

export const onb = () => mdb.collection('onboarding');
export const surveyAnswers = () => mdb.collection('survey_answers');

export async function ensureOnboardingIndexes() {
  await Promise.all([
    onb().createIndex({ userId: 1 }, { unique: true }),
    onb().createIndex({ maxId: 1 }),
    onb().createIndex({ status: 1, 'steps.state': 1, 'steps.dueAt': 1 }),
    onb().createIndex({ 'waits.id': 1 }),
    surveyAnswers().createIndex({ userId: 1, at: -1 }),
    surveyAnswers().createIndex({ negative: 1, at: -1 }),
  ]);
}

export const findByUser = (userId) => onb().findOne({ userId });
export const findByMaxId = (maxId) => onb().findOne({ maxId: String(maxId) });

// патч полей одного шага: setStep(id, 'day1_10', { state: 'sent' })
export async function setStep(obId, stepId, patch) {
  const $set = Object.fromEntries(Object.entries(patch).map(([k, v]) => [`steps.$.${k}`, v]));
  await onb().updateOne({ _id: obId, 'steps.id': stepId }, { $set: { ...$set, updatedAt: new Date() } });
}

export async function saveAnswer({ ob, stepId, survey, qid, key = null, label = null, value = null, text = null, negative = false, now }) {
  const stored = key !== null ? key : value !== null ? value : text;
  await onb().updateOne({ _id: ob._id, 'steps.id': stepId }, { $set: { [`steps.$.answers.${qid}`]: stored } });
  await surveyAnswers().insertOne({
    userId: ob.userId, maxId: ob.maxId, name: ob.name, tag: ob.tag, stepId, survey: survey ?? null, qid,
    key, label, value, text, negative, at: now,
  });
}
