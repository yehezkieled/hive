-- Ticket 003 #23: persist the user's chosen AskUserQuestion option index so an
-- ask-gate decision can select a non-default option. NULL for plan/mode
-- requests and for ask gates approved without an explicit choice (those default
-- to the highlighted first option at inject time).
ALTER TABLE mode_requests
    ADD COLUMN IF NOT EXISTS chosen_option INTEGER;
