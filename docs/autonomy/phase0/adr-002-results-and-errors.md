# ADR-002: Result and error model

Status: accepted for Phase 1.

## Decision

Canonical outcomes are `succeeded`, `rejected`, `failed`, `cancelled`, and
`unknown`.

| Error category | Meaning | Typical outcome |
|---|---|---|
| validation | Request is outside the versioned schema | rejected |
| unsupported | Skill/capability is not implemented | rejected |
| precondition | Safe starting state is absent | rejected |
| busy | Robot writer lock is held | rejected |
| conflict | Idempotency key reused with different parameters | rejected |
| transport | Delivery/response certainty was lost | unknown |
| timeout | Deadline elapsed after dispatch | unknown |
| execution | Backend confirmed action failure | failed |
| verification | Backend returned but success evidence failed | failed |
| cancelled | Backend was not called, or stop was positively confirmed | cancelled |
| unknown | No safe classification exists | unknown |

`SkillResult` always carries correlation IDs, timestamps, error details,
output, state, verification evidence, and metrics. Existing callers receive a
compatibility projection with `status=ok|error`; an `unknown` outcome is never
projected as success. A verifier with status `failed` overrides a backend
`status=ok` response.

