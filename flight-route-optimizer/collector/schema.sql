PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS imports (
    sha256 TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    imported_at_utc TEXT NOT NULL,
    source_filename TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS airports (
    ourairports_id INTEGER PRIMARY KEY,
    ident TEXT NOT NULL UNIQUE,
    iata TEXT,
    icao TEXT,
    faa_lid TEXT,
    name TEXT NOT NULL,
    country TEXT NOT NULL,
    region TEXT,
    airport_type TEXT,
    scheduled_service INTEGER NOT NULL,
    latitude REAL NOT NULL,
    longitude REAL NOT NULL,
    timezone TEXT,
    source_sha256 TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS airport_iata ON airports(iata);

CREATE TABLE IF NOT EXISTS flight_history (
    flight_date TEXT NOT NULL,
    carrier TEXT NOT NULL,
    dot_airline_id TEXT,
    flight_number TEXT,
    origin_airport_id TEXT,
    origin TEXT NOT NULL,
    destination_airport_id TEXT,
    destination TEXT NOT NULL,
    scheduled_departure_hhmm INTEGER,
    scheduled_arrival_hhmm INTEGER,
    scheduled_elapsed_minutes REAL,
    cancelled INTEGER NOT NULL CHECK(cancelled IN(0,1)),
    diverted INTEGER NOT NULL CHECK(diverted IN(0,1)),
    arrival_delay_minutes REAL,
    arrival_delay_15 REAL,
    source_sha256 TEXT NOT NULL,
    period TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS history_route ON flight_history(origin,destination,flight_date);
CREATE INDEX IF NOT EXISTS history_period ON flight_history(period);

-- Future schedule records must come from a separately verified provider.
-- Historical rows are never copied into this table by these scripts.
CREATE TABLE IF NOT EXISTS future_flights (
    provider TEXT NOT NULL,
    provider_flight_id TEXT NOT NULL,
    operating_carrier TEXT NOT NULL,
    flight_number TEXT NOT NULL,
    origin TEXT NOT NULL,
    destination TEXT NOT NULL,
    departure_utc TEXT NOT NULL,
    arrival_utc TEXT NOT NULL,
    status TEXT,
    retrieved_at_utc TEXT NOT NULL,
    source_url TEXT NOT NULL,
    PRIMARY KEY(provider,provider_flight_id)
);
