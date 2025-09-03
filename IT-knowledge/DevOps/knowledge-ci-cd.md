# CI/CD Best Practices (Demo)

Pipelines
- Use short-lived, reproducible runners; cache dependencies responsibly
- Fail fast on lint/type/test; separate build and deploy stages
- Promote artifacts across environments (dev → test → prod) — no rebuilds

Security
- Prefer workload identity/OIDC over long-lived secrets
- Sign artifacts and verify provenance; enable supply-chain security (SLSA)
- Scan dependencies and containers; block critical CVEs

Quality
- Trunk-based development; PR checks with required reviews
- Flaky test quarantine + retry policy; track test stability
- Enforce code coverage thresholds with trend monitoring

Operations
- Blue/green or canary deployments with automated rollback
- Observability baked in: metrics, logs, traces; SLOs and error budgets
- Infra as Code with linting/formatting and policy-as-code gates

Notes
- This is a demo knowledge doc; tailor to your org and toolchain.
