CREATE TABLE role (
    id   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name varchar NOT NULL UNIQUE  -- employee | director | hr | hr_head
);

CREATE TABLE tag (
    id   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name varchar NOT NULL UNIQUE
);

CREATE TABLE users (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name        varchar NOT NULL,
    surname     varchar NOT NULL,
    tag_id      uuid REFERENCES tag(id) ON DELETE SET NULL,
    role_id     uuid NOT NULL REFERENCES role(id) ON DELETE RESTRICT,
    director_id uuid REFERENCES users(id) ON DELETE RESTRICT,  -- NULL у руководителей
    hr_id       uuid REFERENCES users(id) ON DELETE SET NULL,  -- закреплённый HR сотрудника (автор приглашения)
    max_id      bigint NOT NULL UNIQUE,
    email       varchar,
    phone       varchar,
    created_at  timestamp NOT NULL DEFAULT now(),
    updated_at  timestamp NOT NULL DEFAULT now()
);

CREATE INDEX users_director_id_idx ON users(director_id);
CREATE INDEX users_role_id_idx ON users(role_id);
CREATE INDEX users_hr_id_idx ON users(hr_id);

CREATE TABLE invites (
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

CREATE INDEX invites_phone_idx ON invites(phone);

CREATE FUNCTION set_updated_at() RETURNS trigger AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER users_set_updated_at
    BEFORE UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
