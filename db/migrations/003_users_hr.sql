-- Закреплённый HR сотрудника: тот, кто создал приглашение. Ему уходят обращения (тикеты) сотрудника.
ALTER TABLE users ADD COLUMN hr_id uuid REFERENCES users(id) ON DELETE SET NULL;
CREATE INDEX users_hr_id_idx ON users(hr_id);
