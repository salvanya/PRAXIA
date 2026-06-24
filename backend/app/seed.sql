INSERT INTO practices (id, name, type)
VALUES ('00000000-0000-0000-0000-000000000001', 'Práctica Demo', 'psicologia')
ON CONFLICT (id) DO NOTHING;
