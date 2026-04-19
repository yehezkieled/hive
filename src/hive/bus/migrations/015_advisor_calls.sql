CREATE TABLE advisor_calls (
    id SERIAL PRIMARY KEY,
    entity_name TEXT NOT NULL,
    called_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    context TEXT,
    response TEXT,
    input_tokens INT,
    output_tokens INT,
    cost_usd NUMERIC(10, 6),
    status TEXT NOT NULL
);
CREATE INDEX advisor_calls_entity_time ON advisor_calls (entity_name, called_at DESC);
