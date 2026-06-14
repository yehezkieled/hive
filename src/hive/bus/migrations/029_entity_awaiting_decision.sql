-- Ticket 029: the maestro→user conversational decision channel. awaiting_decision
-- marks an entity parked on a request_decision to the user. Durable so a Hive
-- restart cannot make the entity forget it is waiting and let the scheduler poke
-- it into acting unconfirmed (ADR 0018).
ALTER TABLE entities
    ADD COLUMN IF NOT EXISTS awaiting_decision BOOLEAN NOT NULL DEFAULT FALSE;
