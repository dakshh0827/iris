-- Runs once, when the Postgres container starts with an empty data folder.
-- Creates a second database for the tests, so they never touch your real data.
CREATE DATABASE reg_iris_test;
