# DevOps Weekly Sync — 2025-09-02

Attendees: Dave P., Erin S., Hana Y., Frank L.
Facilitator: Erin
Notes Taker: Hana

Agenda
- Pipeline reliability and flaky tests
- Blue/green deployment status
- Infra-as-code linting baseline

Decisions
- Migrate canary percentage from 5% to 10% on staging for faster signal
- Enforce IaC linting in pre-commit and CI (soft-fail for 1 sprint)

Action Items
- [Erin] Add test quarantine and retry policy to CI — due 2025-09-05
- [Dave] Document rollback runbook for API service — due 2025-09-04
- [Hana] Enable artifact provenance metadata in builds — due 2025-09-06

Notes
- Mean build time: 11m 42s (goal: <10m)
- 2 flaky tests identified in payments module
