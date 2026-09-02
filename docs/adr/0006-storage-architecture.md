# ADR-006: Project Storage Architecture

- Status: Accepted
- Date: 2026-09-02

## Context

VASP projects contain many large files, while relational metadata, provenance, object identities, and lifecycle state need efficient querying and migration.

## Decision

Projects use file-first storage plus SQLite metadata. Large scientific artifacts remain files and are referenced through manifests containing paths, hashes, sizes, producers, and availability. Project data carries an explicit schema version and migrations are required for schema evolution.

Execution attempts are stored separately and never overwrite prior attempts. Large artifacts may be local, on-demand, or remote-preferred depending on downstream requirements.

## Consequences

Projects remain inspectable outside the application while retaining reliable relational metadata. Large WAVECAR/CHGCAR-like data do not bloat the database.
