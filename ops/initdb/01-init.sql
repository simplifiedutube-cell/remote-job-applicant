-- Runs once, on first creation of the pgdata volume (see docker-compose.yml).
-- `make nuke` drops the volume and re-triggers this file.
--
-- Two schemas, one database, on purpose:
--   jobagent  -- application tables, owned by Alembic
--   langgraph -- checkpoint tables, owned by PostgresSaver.setup()
-- Keeping them in one database means a single pg_dump is a complete backup and
-- "did this run's rows and its checkpoint commit together" is an answerable question.
-- Keeping them in separate schemas means Alembic autogenerate can be told to ignore
-- LangGraph's tables -- without that filter it will happily emit DROP TABLE for them.

CREATE DATABASE jobagent_test OWNER jobagent;

\connect jobagent
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE SCHEMA IF NOT EXISTS jobagent AUTHORIZATION jobagent;
CREATE SCHEMA IF NOT EXISTS langgraph AUTHORIZATION jobagent;

\connect jobagent_test
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE SCHEMA IF NOT EXISTS jobagent AUTHORIZATION jobagent;
CREATE SCHEMA IF NOT EXISTS langgraph AUTHORIZATION jobagent;
