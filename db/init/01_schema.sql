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
    max_id      bigint NOT NULL UNIQUE,
    created_at  timestamp NOT NULL DEFAULT now(),
    updated_at  timestamp NOT NULL DEFAULT now()
);

CREATE INDEX users_director_id_idx ON users(director_id);
CREATE INDEX users_role_id_idx ON users(role_id);

CREATE FUNCTION set_updated_at() RETURNS trigger AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER users_set_updated_at
    BEFORE UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
