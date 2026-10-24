CREATE TABLE IF NOT EXISTS role (
    id SERIAL PRIMARY KEY,
    name VARCHAR(50) NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS tag (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    name VARCHAR(100) NOT NULL,
    surname VARCHAR(100) NOT NULL,

    tag_id INTEGER REFERENCES tag(id),
    role_id INTEGER NOT NULL REFERENCES role(id),

    director_id UUID REFERENCES users(id),

    max_id BIGINT UNIQUE,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_users_director_id
    ON users(director_id);

CREATE INDEX IF NOT EXISTS idx_users_role_id
    ON users(role_id);