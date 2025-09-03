# Incident Response Quick Guide (Demo)

Scope: High-level, vendor-agnostic steps for common security incidents. This is a demo knowledge doc.

Phases
1) Preparation
   - Maintain asset inventory, backups, logging, and alerting baselines
   - Drill tabletop exercises quarterly
2) Identification
   - Validate alerts; collect indicators (hashes, IPs, users, hosts)
3) Containment
   - Short-term: isolate hosts, block IOCs, disable compromised accounts
   - Long-term: network segmentation, patching, hardening
4) Eradication
   - Remove malware/backdoors; rotate keys/secrets; close exploited vectors
5) Recovery
   - Restore from trusted backups; monitor closely; staged reintegration
6) Lessons Learned
   - Blameless postmortem; update playbooks; improve detections

Checklists
- Access control: MFA required, least privilege, JIT/JEA for admin tasks
- Logging: Forward to SIEM, time synchronized (NTP), retention ≥ 90 days
- Backups: Offline/immutable copies; test restores monthly

References (placeholder)
- NIST SP 800-61r2 Computer Security Incident Handling Guide
- CIS Controls v8
