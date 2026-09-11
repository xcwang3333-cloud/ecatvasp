# IPC v2 Operation Catalog Governance Test Specification

## Purpose

Verify that IPC v2 operation catalogs remain synchronized across implementation layers.

## Required checks

- Python operation catalog contains every supported operation.
- TypeScript operation contract contains every supported operation.
- Native validator rejects unknown operations.
- No wildcard operation dispatch is introduced.

## Acceptance

The test suite must fail when an operation exists in one contract layer but not another.
