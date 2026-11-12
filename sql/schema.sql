PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS requests (
 id INTEGER PRIMARY KEY, started_at TEXT NOT NULL, date_kst TEXT NOT NULL,
 dow TEXT NOT NULL, provider TEXT NOT NULL, dataset TEXT NOT NULL,
 target TEXT NOT NULL, outcome TEXT NOT NULL DEFAULT 'started',
 http_status INTEGER, row_count INTEGER, raw_path TEXT, error_code TEXT
);
CREATE INDEX IF NOT EXISTS request_budget ON requests(date_kst, provider);
CREATE TABLE IF NOT EXISTS checkpoints (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS gaps (
 id INTEGER PRIMARY KEY, dataset TEXT NOT NULL, target TEXT NOT NULL,
 start_at TEXT, end_at TEXT NOT NULL, reason TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS positions (
 event_id TEXT PRIMARY KEY, date_kst TEXT NOT NULL, dow TEXT NOT NULL,
 session TEXT NOT NULL, collected_at TEXT NOT NULL, observed_at TEXT,
 line TEXT NOT NULL, train_no TEXT NOT NULL, station_id TEXT,
 station_name TEXT, direction TEXT, train_status TEXT,
 quality TEXT NOT NULL, payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS position_trajectory ON positions(date_kst,line,train_no,observed_at);
CREATE TABLE IF NOT EXISTS arrivals (
 event_id TEXT PRIMARY KEY, date_kst TEXT NOT NULL, dow TEXT NOT NULL,
 line TEXT NOT NULL, train_no TEXT NOT NULL, station_id TEXT,
 station_name TEXT, direction TEXT, observed_at TEXT NOT NULL,
 lower_bound_at TEXT, uncertainty_seconds REAL, quality TEXT NOT NULL,
 position_event_id TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS timetable (
 event_id TEXT PRIMARY KEY, fetched_date TEXT NOT NULL, line TEXT,
 train_no TEXT, station_id TEXT, station_name TEXT, direction TEXT,
 day_type TEXT, arrival_time TEXT, departure_time TEXT, payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS timetable_match ON timetable(fetched_date,train_no,station_name);
CREATE TABLE IF NOT EXISTS delays (
 arrival_id TEXT PRIMARY KEY, date_kst TEXT NOT NULL, dow TEXT NOT NULL,
 line TEXT NOT NULL, train_no TEXT NOT NULL, station_name TEXT,
 observed_at TEXT NOT NULL, scheduled_at TEXT, delay_seconds REAL,
 lower_delay_seconds REAL, match_status TEXT NOT NULL, timetable_event_id TEXT,
 match_method TEXT
);
CREATE TABLE IF NOT EXISTS congestion (
 event_id TEXT PRIMARY KEY, fetched_date TEXT NOT NULL, collected_at TEXT NOT NULL,
 dow TEXT NOT NULL, hour INTEGER NOT NULL, minute INTEGER NOT NULL,
 line TEXT, station_code TEXT, station_name TEXT, direction TEXT,
 prev_station_code TEXT, start_station_code TEXT, end_station_code TEXT,
 car_no INTEGER, congestion_percent REAL,
 measurement_kind TEXT NOT NULL CHECK(measurement_kind = 'historical_statistic'),
 stat_start_date TEXT, stat_end_date TEXT, payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS notices (
 event_id TEXT PRIMARY KEY, fetched_date TEXT NOT NULL, dow TEXT NOT NULL,
 collected_at TEXT NOT NULL, occurred_at TEXT, lines TEXT,
 stations TEXT, title TEXT, content TEXT, payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS arrival_samples (
 event_id TEXT PRIMARY KEY, date_kst TEXT NOT NULL, dow TEXT NOT NULL,
 session TEXT NOT NULL, collected_at TEXT NOT NULL, observed_at TEXT,
 line TEXT NOT NULL, train_no TEXT, station_id TEXT, station_name TEXT,
 direction TEXT, arrival_code TEXT, payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS arrival_sample_match ON arrival_samples(date_kst,line,train_no,station_name);
CREATE TABLE IF NOT EXISTS boarding_volume (
 event_id TEXT PRIMARY KEY, use_month TEXT NOT NULL, fetched_date TEXT NOT NULL,
 payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS boarding_month ON boarding_volume(use_month);
CREATE TABLE IF NOT EXISTS reference_files (
 sha256 TEXT PRIMARY KEY, dataset TEXT NOT NULL, file_name TEXT NOT NULL,
 encoding TEXT NOT NULL, loaded_at TEXT NOT NULL, row_count INTEGER NOT NULL,
 columns_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reference_rows (
 sha256 TEXT NOT NULL, dataset TEXT NOT NULL, row_no INTEGER NOT NULL,
 payload_json TEXT NOT NULL, PRIMARY KEY (sha256, row_no)
);
CREATE INDEX IF NOT EXISTS reference_dataset ON reference_rows(dataset);
