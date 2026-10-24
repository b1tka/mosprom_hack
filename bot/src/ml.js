import http from 'node:http';
import { config } from './config.js';

const { apiUrl, timeoutMs, indexTimeoutMs } = config.ml;

export class MlError extends Error {
  constructor(status, body) {
    super(`ML API HTTP ${status}: ${body}`);
    this.status = status;
  }
}

// Вопрос сотрудника в RAG ML-сервиса (POST /v1/ask). userId = users.id: по нему ML ведёт статистику настроения.
// Возвращает { code: 200 | 400, answer }. Любой другой исход
// (HTTP-ошибка, в т.ч. 503 «модель/база недоступна», неверное тело, сеть, таймаут) — исключение.
export async function ask({ userId, question, stage = null }) {
  const res = await fetch(`${apiUrl}/v1/ask`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ user_id: userId, question, stage }),
    signal: AbortSignal.timeout(timeoutMs),
  });
  if (!res.ok) throw new MlError(res.status, await res.text());
  const body = await res.json();
  if (![200, 400].includes(body?.code) || typeof body.answer !== 'string') {
    throw new Error(`ML API unexpected body: ${JSON.stringify(body)}`);
  }
  return body;
}

// Файл, загруженный в S3 (key = incoming/<id>/<имя>), -> база знаний (POST /v1/documents).
// Возвращает { source, chunks_added, replaced }. HTTP-ошибка -> MlError со status
// (400 — текст не извлёкся / неверный формат, 404 — нет файла в S3, 503 — S3 или база недоступны).
export const addDocument = ({ key }) => longRequest('POST', '/v1/documents', { key });

// Тема и категория обращения к HR: { title, category }. 503 (LLM недоступна) и прочее — исключение.
export async function ticketTitle(text) {
  const res = await fetch(`${apiUrl}/v1/ticket-title`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text }),
    signal: AbortSignal.timeout(timeoutMs),
  });
  if (!res.ok) throw new MlError(res.status, await res.text());
  return res.json();
}

// Имена документов базы знаний (и проиндексированных, и лежащих в S3).
export async function listDocuments() {
  const res = await fetch(`${apiUrl}/v1/documents`, { signal: AbortSignal.timeout(timeoutMs) });
  if (!res.ok) throw new MlError(res.status, await res.text());
  return (await res.json()).documents;
}

// Удаление документа из S3, knowledge_base.json и Chroma. 404 — документа уже нет.
// Долгий запрос: ждёт, пока ML закончит индексацию другого документа.
export const deleteDocument = (name) => longRequest('DELETE', `/v1/documents/${encodeURIComponent(name)}`);

// Через node:http, а не fetch: у fetch (undici) свой лимит ожидания ответа 5 минут, индексация бывает дольше.
function longRequest(method, urlPath, body) {
  const payload = body === undefined ? '' : JSON.stringify(body);
  return new Promise((resolve, reject) => {
    const req = http.request(`${apiUrl}${urlPath}`, {
      method,
      headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(payload) },
      signal: AbortSignal.timeout(indexTimeoutMs),
    }, (res) => {
      let text = '';
      res.setEncoding('utf8');
      res.on('data', (chunk) => { text += chunk; });
      res.on('end', () => {
        if (res.statusCode !== 200) return reject(new MlError(res.statusCode, text));
        try {
          resolve(JSON.parse(text));
        } catch (err) {
          reject(err);
        }
      });
    });
    req.on('error', reject);
    req.end(payload);
  });
}
