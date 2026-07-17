"""Fail-closed authorization for managed Atlas workers."""

from __future__ import annotations

import secrets
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

from .repository import ControlPlaneRepository
from .security import InvalidLease, LeaseClaims, LeaseSigner


def not_expired(value: str | None) -> bool:
    if not value:
        return True
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    if parsed.tzinfo is None:
        return False
    return parsed.astimezone(UTC) > datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    allowed: bool
    code: str
    tenant_id: str | None
    store_id: str | None
    agent_id: str | None
    device_id: str | None
    capability: str | None
    capabilities: tuple[str, ...] = ()
    lease_claims: LeaseClaims | None = None


class PolicyDenied(PermissionError):
    def __init__(self, decision: PolicyDecision) -> None:
        super().__init__(decision.code)
        self.decision = decision


class PolicyEngine:
    """Revalidates live control-plane state for every privileged action."""

    def __init__(
        self,
        repository: ControlPlaneRepository,
        lease_signer: LeaseSigner,
        *,
        lease_ttl_seconds: int,
    ) -> None:
        self.repository = repository
        self.lease_signer = lease_signer
        self.lease_ttl_seconds = lease_ttl_seconds

    def issue_lease(
        self,
        *,
        authenticated_device: dict[str, Any],
        store_id: str,
        agent_id: str,
        ttl_seconds: int | None = None,
        audit_action: str = "lease.issue",
    ) -> tuple[str, LeaseClaims]:
        decision = self._baseline(
            authenticated_device=authenticated_device,
            store_id=store_id,
            agent_id=agent_id,
        )
        if not decision.allowed:
            self._audit(decision, action=audit_action)
            raise PolicyDenied(decision)
        token, claims = self.lease_signer.issue(
            device_id=decision.device_id or "",
            tenant_id=decision.tenant_id or "",
            store_id=decision.store_id or "",
            agent_id=decision.agent_id or "",
            capabilities=decision.capabilities,
            ttl_seconds=ttl_seconds or self.lease_ttl_seconds,
            nonce=secrets.token_hex(16),
        )
        allowed = PolicyDecision(
            allowed=True,
            code="allowed",
            tenant_id=decision.tenant_id,
            store_id=decision.store_id,
            agent_id=decision.agent_id,
            device_id=decision.device_id,
            capability=None,
            capabilities=decision.capabilities,
            lease_claims=claims,
        )
        self._audit(allowed, action=audit_action)
        return token, claims

    def evaluate(
        self,
        *,
        authenticated_device: dict[str, Any],
        store_id: str,
        agent_id: str,
        capability: str,
        lease_token: str,
        audit_action: str = "policy.evaluate",
        job_id: str | None = None,
    ) -> PolicyDecision:
        baseline = self._baseline(
            authenticated_device=authenticated_device,
            store_id=store_id,
            agent_id=agent_id,
        )
        if not baseline.allowed:
            denied = replace(baseline, capability=capability)
            self._audit(denied, action=audit_action, job_id=job_id)
            return denied

        try:
            claims = self.lease_signer.verify(lease_token)
        except InvalidLease as exc:
            denied = self._decision(
                False,
                str(exc),
                authenticated_device,
                store_id,
                agent_id,
                capability,
            )
            self._audit(denied, action=audit_action, job_id=job_id)
            return denied

        context_matches = (
            claims.device_id == baseline.device_id
            and claims.tenant_id == baseline.tenant_id
            and claims.store_id == store_id
            and claims.agent_id == agent_id
        )
        if not context_matches:
            denied = self._decision(
                False,
                "lease_context_mismatch",
                authenticated_device,
                store_id,
                agent_id,
                capability,
            )
            self._audit(denied, action=audit_action, job_id=job_id)
            return denied
        if capability not in claims.capabilities:
            denied = self._decision(
                False,
                "lease_capability_missing",
                authenticated_device,
                store_id,
                agent_id,
                capability,
            )
            self._audit(denied, action=audit_action, job_id=job_id)
            return denied

        entitlement = self.repository.get_entitlement(
            claims.tenant_id, store_id, capability
        )
        if not entitlement:
            denied = self._decision(
                False,
                "entitlement_missing",
                authenticated_device,
                store_id,
                agent_id,
                capability,
            )
            self._audit(denied, action=audit_action, job_id=job_id)
            return denied
        if entitlement["status"] != "active" or not not_expired(
            entitlement.get("expires_at")
        ):
            denied = self._decision(
                False,
                "entitlement_inactive",
                authenticated_device,
                store_id,
                agent_id,
                capability,
            )
            self._audit(denied, action=audit_action, job_id=job_id)
            return denied

        allowed = PolicyDecision(
            allowed=True,
            code="allowed",
            tenant_id=baseline.tenant_id,
            store_id=store_id,
            agent_id=agent_id,
            device_id=baseline.device_id,
            capability=capability,
            capabilities=baseline.capabilities,
            lease_claims=claims,
        )
        self._audit(allowed, action=audit_action, job_id=job_id)
        return allowed

    def _baseline(
        self,
        *,
        authenticated_device: dict[str, Any],
        store_id: str,
        agent_id: str,
    ) -> PolicyDecision:
        device_id = authenticated_device.get("id")
        tenant_id = authenticated_device.get("tenant_id")
        if not all(
            isinstance(value, str) and value for value in (device_id, tenant_id)
        ):
            return self._decision(
                False,
                "device_identity_invalid",
                authenticated_device,
                store_id,
                agent_id,
                None,
            )
        # Always reload mutable records. A disable action must revoke an issued
        # lease immediately rather than waiting for its expiration.
        device = self.repository.get_device(device_id)
        if not device or device["status"] != "active":
            return self._decision(
                False, "device_inactive", authenticated_device, store_id, agent_id, None
            )
        if device["tenant_id"] != tenant_id or device["store_id"] != store_id:
            return self._decision(
                False,
                "device_context_mismatch",
                authenticated_device,
                store_id,
                agent_id,
                None,
            )
        tenant = self.repository.get_tenant(tenant_id)
        if not tenant or tenant["status"] != "active":
            return self._decision(
                False, "tenant_inactive", authenticated_device, store_id, agent_id, None
            )
        store = self.repository.get_store(store_id)
        if not store or store["tenant_id"] != tenant_id or store["status"] != "active":
            return self._decision(
                False, "store_inactive", authenticated_device, store_id, agent_id, None
            )
        agent = self.repository.get_agent(agent_id)
        if (
            not agent
            or agent["tenant_id"] != tenant_id
            or agent["store_id"] != store_id
            or agent["status"] != "active"
            or agent.get("device_id") not in {None, device_id}
        ):
            return self._decision(
                False, "agent_inactive", authenticated_device, store_id, agent_id, None
            )
        subscription = self.repository.get_active_subscription(tenant_id)
        if not subscription or not not_expired(subscription.get("current_period_end")):
            return self._decision(
                False,
                "subscription_inactive",
                authenticated_device,
                store_id,
                agent_id,
                None,
            )
        capabilities = tuple(
            self.repository.list_active_capabilities(tenant_id, store_id)
        )
        if not capabilities:
            return self._decision(
                False,
                "store_has_no_entitlements",
                authenticated_device,
                store_id,
                agent_id,
                None,
            )
        return PolicyDecision(
            allowed=True,
            code="allowed",
            tenant_id=tenant_id,
            store_id=store_id,
            agent_id=agent_id,
            device_id=device_id,
            capability=None,
            capabilities=capabilities,
        )

    @staticmethod
    def _decision(
        allowed: bool,
        code: str,
        device: dict[str, Any],
        store_id: str | None,
        agent_id: str | None,
        capability: str | None,
    ) -> PolicyDecision:
        return PolicyDecision(
            allowed=allowed,
            code=code,
            tenant_id=device.get("tenant_id"),
            store_id=store_id,
            agent_id=agent_id,
            device_id=device.get("id"),
            capability=capability,
        )

    def _audit(
        self, decision: PolicyDecision, *, action: str, job_id: str | None = None
    ) -> None:
        self.repository.record_audit(
            actor_type="worker",
            action=action,
            outcome="allowed" if decision.allowed else "denied",
            tenant_id=decision.tenant_id,
            store_id=decision.store_id,
            agent_id=decision.agent_id,
            device_id=decision.device_id,
            job_id=job_id,
            resource_type="capability" if decision.capability else "lease",
            resource_id=decision.capability,
            details={"reason": decision.code},
        )
