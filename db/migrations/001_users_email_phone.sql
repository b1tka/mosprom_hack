ALTER TABLE users
    ADD COLUMN IF NOT EXISTS email varchar,
    ADD COLUMN IF NOT EXISTS phone varchar;
