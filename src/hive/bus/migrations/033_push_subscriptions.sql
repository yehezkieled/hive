-- Ticket 041: Web Push subscription persistence. Stores the browser push
-- subscriptions (endpoint + the p256dh/auth keys the VAPID delivery path needs)
-- so the server can fan a notification out to every installed PWA. The endpoint
-- is the natural PRIMARY KEY — re-subscribing the same browser upserts on it
-- rather than duplicating. user_agent is captured for debugging which device a
-- subscription came from; nullable since the client may not send it.

CREATE TABLE IF NOT EXISTS push_subscriptions (
    endpoint    TEXT PRIMARY KEY,
    p256dh      TEXT NOT NULL,
    auth        TEXT NOT NULL,
    user_agent  TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
