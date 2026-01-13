CREATE TABLE IF NOT EXISTS users (
    telegram_user_id INTEGER PRIMARY KEY,
    timezone TEXT DEFAULT 'UTC',
    eating_start TEXT DEFAULT '12:00',
    eating_end TEXT DEFAULT '20:00',
    water_goal_ml INTEGER DEFAULT 3000,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_user_id INTEGER,
    type TEXT,
    amount_ml INTEGER,
    timestamp TEXT
);

CREATE TABLE IF NOT EXISTS state (
    telegram_user_id INTEGER PRIMARY KEY,
    is_eating INTEGER DEFAULT 0,
    last_meal_time TEXT,
    last_water_time TEXT
);
