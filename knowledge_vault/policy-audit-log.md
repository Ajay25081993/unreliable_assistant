---
id: policy-audit-log
domain: governance
risk_tier: 2
version: 1
---
# Audit Log Policy

Every agent action is written to a hash chained audit log. Each entry includes the hash of the previous entry so tampering is detectable. Audit entries are scoped by tenant.
