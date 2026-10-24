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
// Через node:http, а не fetch: у fetch (undici) свой лимит ожидания ответа 5 минут, индексация бывает дольше.
export function addDocument({ key }) {
  const payload = JSON.stringify({ key });
  return new Promise((resolve, reject) => {
    const req = http.request(`${apiUrl}/v1/documents`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(payload) },
      signal: AbortSignal.timeout(indexTimeoutMs),
    }, (res) => {
      let body = '';
      res.setEncoding('utf8');
      res.on('data', (chunk) => { body += chunk; });
      res.on('end', () => {
        if (res.statusCode !== 200) return reject(new MlError(res.statusCode, body));
        try {
          resolve(JSON.parse(body));
        } catch (err) {
          reject(err);
        }
      });
    });
    req.on('error', reject);
    req.end(payload);
  });
}
