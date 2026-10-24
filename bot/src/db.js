import pg from 'pg';
import { config } from './config.js';

export const pool = new pg.Pool({ connectionString: config.databaseUrl });

// bigint (max_id) приходит строкой, поэтому ищем по строковому значению
export async function findUserByMaxId(maxId) {
  const { rows } = await pool.query(
    `SELECT u.id, u.name, u.surname, u.max_id, u.director_id, u.hr_id,
            r.name AS role, t.name AS tag
       FROM users u
       JOIN role r ON r.id = u.role_id
       LEFT JOIN tag t ON t.id = u.tag_id
      WHERE u.max_id = $1`,
    [String(maxId)],
  );
  return rows[0] ?? null;
}

// Закреплённый HR сотрудника и его руководитель (для шапки тикета). hr — null, если HR не закреплён или уже не HR.
export async function findEmployeeContacts(userId) {
  const { rows } = await pool.query(
    `SELECT h.id AS hr_id, h.max_id AS hr_max_id, h.name AS hr_name, h.surname AS hr_surname,
            d.name AS director_name, d.surname AS director_surname
       FROM users u
       LEFT JOIN (users h JOIN role hr ON hr.id = h.role_id AND hr.name IN ('hr', 'hr_head')) ON h.id = u.hr_id
       LEFT JOIN users d ON d.id = u.director_id
      WHERE u.id = $1`,
    [userId],
  );
  const row = rows[0];
  if (!row) return null;
  return {
    hr: row.hr_id && row.hr_max_id ? { id: row.hr_id, maxId: row.hr_max_id, name: row.hr_name, surname: row.hr_surname } : null,
    director: row.director_name ? `${row.director_name} ${row.director_surname}` : null,
  };
}

// Сотрудник для уведомления о red_flag: карточка и закреплённый HR (null, если не закреплён или уже не HR)
export async function findEmployeeForAlert(userId) {
  const { rows } = await pool.query(
    `SELECT u.id, u.name, u.surname, u.hr_id, t.name AS tag, d.name AS director_name, d.surname AS director_surname,
            h.id AS pinned_hr_id, h.max_id AS pinned_hr_max_id
       FROM users u
       LEFT JOIN tag t ON t.id = u.tag_id
       LEFT JOIN users d ON d.id = u.director_id
       LEFT JOIN (users h JOIN role hr ON hr.id = h.role_id AND hr.name IN ('hr', 'hr_head')) ON h.id = u.hr_id
      WHERE u.id = $1`,
    [userId],
  );
  const r = rows[0];
  if (!r) return null;
  return {
    id: r.id,
    name: r.name,
    surname: r.surname,
    tag: r.tag,
    hrId: r.hr_id,
    director: r.director_name ? `${r.director_name} ${r.director_surname[0]}.` : null,
    pinnedHr: r.pinned_hr_id ? { id: r.pinned_hr_id, maxId: r.pinned_hr_max_id } : null,
  };
}

// Все HR и руководители HR: получатели, если у сотрудника нет закреплённого HR
export async function listHrRecipients() {
  const { rows } = await pool.query(
    `SELECT u.id, u.max_id FROM users u JOIN role r ON r.id = u.role_id WHERE r.name IN ('hr', 'hr_head') ORDER BY u.created_at`,
  );
  return rows.map((r) => ({ id: r.id, maxId: r.max_id }));
}

export async function listTags() {
  const { rows } = await pool.query('SELECT id, name FROM tag ORDER BY name');
  return rows;
}

export async function listDirectors() {
  const { rows } = await pool.query(
    `SELECT u.id, u.name, u.surname
       FROM users u JOIN role r ON r.id = u.role_id
      WHERE r.name = 'director'
      ORDER BY u.surname, u.name`,
  );
  return rows;
}

// ---- приглашения (HR создаёт, сотрудник активирует по QR) ----

export async function phoneTaken(phone) {
  const { rowCount } = await pool.query('SELECT 1 FROM users WHERE phone = $1', [phone]);
  return rowCount > 0;
}

// Старые неиспользованные приглашения на этот номер гасятся, действует только последнее
export async function createInvite({ tokenHash, name, surname, tagId, directorId, email, phone, createdBy, ttlSeconds }) {
  const client = await pool.connect();
  try {
    await client.query('BEGIN');
    await client.query('UPDATE invites SET expires_at = now() WHERE phone = $1 AND used_at IS NULL AND expires_at > now()', [phone]);
    const { rows } = await client.query(
      `INSERT INTO invites (token_hash, name, surname, tag_id, director_id, email, phone, created_by, expires_at)
       VALUES ($1,$2,$3,$4,$5,$6,$7,$8, now() + make_interval(secs => $9)) RETURNING id`,
      [tokenHash, name, surname, tagId, directorId, email, phone, createdBy, ttlSeconds],
    );
    await client.query('COMMIT');
    return rows[0].id;
  } catch (err) {
    await client.query('ROLLBACK');
    throw err;
  } finally {
    client.release();
  }
}

// Возвращает только действующее приглашение (не использовано, не истекло)
export async function findActiveInviteByHash(tokenHash) {
  const { rows } = await pool.query(
    'SELECT id, name, surname, phone FROM invites WHERE token_hash = $1 AND used_at IS NULL AND expires_at > now()',
    [tokenHash],
  );
  return rows[0] ?? null;
}

export async function findActiveInviteById(id) {
  const { rows } = await pool.query(
    'SELECT id, name, surname, phone FROM invites WHERE id = $1 AND used_at IS NULL AND expires_at > now()',
    [id],
  );
  return rows[0] ?? null;
}

// Атомарно: приглашение гасится и создаётся users. Возвращает { ok, creatorMaxId } или { ok: false, reason }.
export async function acceptInvite({ inviteId, maxId }) {
  const client = await pool.connect();
  try {
    await client.query('BEGIN');
    const { rows } = await client.query(
      `SELECT i.*, i.expires_at <= now() AS expired, c.max_id AS creator_max_id FROM invites i JOIN users c ON c.id = i.created_by
        WHERE i.id = $1 FOR UPDATE OF i`,
      [inviteId],
    );
    const inv = rows[0];
    if (!inv || inv.used_at || inv.expired) {
      await client.query('ROLLBACK');
      return { ok: false, reason: 'invalid' };
    }
    const ins = await client.query(
      `INSERT INTO users (name, surname, tag_id, role_id, director_id, hr_id, max_id, email, phone)
       SELECT $1,$2,$3,r.id,$4,$5,$6,$7,$8 FROM role r WHERE r.name = 'employee'
       ON CONFLICT (max_id) DO NOTHING`,
      [inv.name, inv.surname, inv.tag_id, inv.director_id, inv.created_by, String(maxId), inv.email, inv.phone],
    );
    if (ins.rowCount === 0) {
      await client.query('ROLLBACK');
      return { ok: false, reason: 'already_registered' };
    }
    await client.query('UPDATE invites SET used_at = now(), used_by_max_id = $2 WHERE id = $1', [inviteId, String(maxId)]);
    await client.query('COMMIT');
    return { ok: true, name: inv.name, creatorMaxId: inv.creator_max_id };
  } catch (err) {
    await client.query('ROLLBACK');
    throw err;
  } finally {
    client.release();
  }
}
