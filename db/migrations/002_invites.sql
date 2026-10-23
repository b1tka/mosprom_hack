CREATE TABLE IF NOT EXISTS invites (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    token_hash      varchar NOT NULL UNIQUE,  -- sha256(token); сам токен только в QR/ссылке
    name            varchar NOT NULL,
    surname         varchar NOT NULL,
    tag_id          uuid REFERENCES tag(id) ON DELETE SET NULL,
    director_id     uuid REFERENCES users(id) ON DELETE SET NULL,
    email           varchar NOT NULL,
    phone           varchar NOT NULL,         -- только цифры, 7XXXXXXXXXX
    created_by      uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at      timestamp NOT NULL DEFAULT now(),
    expires_at      timestamp NOT NULL,
    used_at         timestamp,
    used_by_max_id  bigint
);

CREATE INDEX IF NOT EXISTS invites_phone_idx ON invites(phone);
