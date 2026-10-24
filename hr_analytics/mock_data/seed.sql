-- Роли
INSERT INTO role (name)
VALUES
    ('employee'),
    ('director'),
    ('hr'),
    ('hr_head')
ON CONFLICT (name) DO NOTHING;


-- Подразделения
INSERT INTO tag (name)
VALUES
    ('GTP'),
    ('GrPP'),
    ('Luzhniki')
ON CONFLICT (name) DO NOTHING;


-- Руководитель
INSERT INTO users (
    id,
    name,
    surname,
    tag_id,
    role_id,
    director_id,
    max_id
)
VALUES (
    '10000000-0000-0000-0000-000000000001',
    'Александр',
    'Смирнов',
    (SELECT id FROM tag WHERE name = 'GTP'),
    (SELECT id FROM role WHERE name = 'director'),
    NULL,
    900000001
)
ON CONFLICT (id) DO NOTHING;


-- HR
INSERT INTO users (
    id,
    name,
    surname,
    tag_id,
    role_id,
    director_id,
    max_id
)
VALUES (
    '10000000-0000-0000-0000-000000000002',
    'Мария',
    'Иванова',
    (SELECT id FROM tag WHERE name = 'GTP'),
    (SELECT id FROM role WHERE name = 'hr'),
    NULL,
    900000002
)
ON CONFLICT (id) DO NOTHING;


-- Руководитель по персоналу
INSERT INTO users (
    id,
    name,
    surname,
    tag_id,
    role_id,
    director_id,
    max_id
)
VALUES (
    '10000000-0000-0000-0000-000000000003',
    'Елена',
    'Петрова',
    (SELECT id FROM tag WHERE name = 'GTP'),
    (SELECT id FROM role WHERE name = 'hr_head'),
    NULL,
    900000003
)
ON CONFLICT (id) DO NOTHING;


-- Сотрудник 1
INSERT INTO users (
    id,
    name,
    surname,
    tag_id,
    role_id,
    director_id,
    max_id
)
VALUES (
    '20000000-0000-0000-0000-000000000001',
    'Иван',
    'Козлов',
    (SELECT id FROM tag WHERE name = 'GTP'),
    (SELECT id FROM role WHERE name = 'employee'),
    '10000000-0000-0000-0000-000000000001',
    910000001
)
ON CONFLICT (id) DO NOTHING;


-- Сотрудник 2
INSERT INTO users (
    id,
    name,
    surname,
    tag_id,
    role_id,
    director_id,
    max_id
)
VALUES (
    '20000000-0000-0000-0000-000000000002',
    'Анна',
    'Соколова',
    (SELECT id FROM tag WHERE name = 'GrPP'),
    (SELECT id FROM role WHERE name = 'employee'),
    '10000000-0000-0000-0000-000000000001',
    910000002
)
ON CONFLICT (id) DO NOTHING;


-- Сотрудник 3
INSERT INTO users (
    id,
    name,
    surname,
    tag_id,
    role_id,
    director_id,
    max_id
)
VALUES (
    '20000000-0000-0000-0000-000000000003',
    'Дмитрий',
    'Орлов',
    (SELECT id FROM tag WHERE name = 'Luzhniki'),
    (SELECT id FROM role WHERE name = 'employee'),
    '10000000-0000-0000-0000-000000000001',
    910000003
)
ON CONFLICT (id) DO NOTHING;