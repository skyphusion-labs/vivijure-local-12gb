# Security audit false positives

Documented dismissals for adversarial-audit (K2.7/K3) findings under the homelab GPU stack threat model.

## Homelab operator trust

The local-12gb stack runs on operator-controlled hardware with a single bearer token, cloudflared tunnel, and shared `/shared` volume. Findings that assume multi-tenant isolation, digest-pinned compose on every dev laptop, or SSH on shipped images are out of scope.

## Record

| Date | Audit | Finding | Rationale |
| --- | --- | --- | --- |
| 2026-07-23 | K3 verify ~18:04 | Project-slug collision in _safe() | Single-operator homelab; slug canonicalization documented in contract.py |
| 2026-07-23 | K3 verify ~18:04 | Runtime images pulled by mutable tag | Operator repins on release; homelab compose |
| 2026-07-23 | K3 verify ~18:04 | Token printed to banner logs | Homelab tunnel auth; operator reads token once at boot |
| 2026-07-23 | K3 verify ~18:04 | Optional INCLUDE_SSH build arg | Gated off in shipped/CI images |
| 2026-07-23 | K3 verify ~18:04 | RUNNER_GROUP_ADMIN_TOKEN in publish.yml | Org operator release workflow |
