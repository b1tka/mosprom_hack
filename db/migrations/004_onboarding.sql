-- Дата выхода на работу: от неё считается расписание сценария онбординга (Mongo `onboarding`).
-- В приглашении фиксируется при создании QR (дата QR + 3 дня, выходной -> понедельник), в users копируется при активации.
ALTER TABLE users   ADD COLUMN IF NOT EXISTS start_date date;
ALTER TABLE invites ADD COLUMN IF NOT EXISTS start_date date;
