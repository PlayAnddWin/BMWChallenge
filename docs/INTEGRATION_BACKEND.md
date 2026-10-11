# Backend merge into integration

`integration` starts from `main` (5485277) and merges `backend` (495bba2).
Main already includes the backend through a squash merge, followed by data,
AI and workbench integration changes. Those production interfaces, dependency
pins, required corporate-report evidence and container settings are retained.

The merge adds backend real-data acceptance tests and the optional original-byte
integrity adapters. `services.providers.Role01SourceProvider` is re-exported for
compatibility; its implementation lives in `services.role01_provider` alongside
the established production `GraphSourceProvider`.

The optional `services.role01_runtime:build_adapters` factory uses the current
prepared-bundle importer and shared `EvidenceManifest` transaction barrier.
RealEvidenceResolver supplies the governance `assert_current` hook, so source
changes between resolution and commit reject the mutation. The default runtime
continues to use the integrated AI/evidence adapters on main.

Requirement exports include decision history. Whole-line proxy reach remains
provisional, and legacy reconciliation supplies defaults for newly added draft
fields while retaining the original historical payload.

Run `python -m pytest tests/data tests/backend tests/intelligence tests/frontend`.
Graph and model tests remain explicitly opt-in.
