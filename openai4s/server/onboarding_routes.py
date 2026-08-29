"""First-run Web onboarding routes.

``GET /onboarding`` is redacted and contacts nobody: local readiness, the
fixed local-model catalogue (not a live scan), and environment/network
posture.  ``POST /onboarding/complete`` is an instance-global mutation and
is refused to non-admins in team mode.
"""

from __future__ import annotations

from typing import Any

from openai4s.kernel.readiness import standard_profile_readiness
from openai4s.onboarding import OnboardingService
from openai4s.server import contract, team_policy

_GET = contract.RouteSpec("onboarding.get", "GET", r"/onboarding", mutates=False)
_COMPLETE = contract.RouteSpec(
    "onboarding.complete", "POST", r"/onboarding/complete", mutates=True
)

ROUTES = contract.validate_routes((_GET, _COMPLETE))


def _service(cfg: Any, store: Any) -> OnboardingService:
    from openai4s.llm.registry import provider_specs

    return OnboardingService(cfg, store, provider_specs())


def _payload(
    service: OnboardingService,
    model_profiles: Any,
    model_discovery: Any,
) -> dict[str, Any]:
    profiles, _unused = model_profiles.profiles_payload()
    return service.web_status(
        profiles=profiles,
        catalog=model_discovery.catalog(),
        environment=standard_profile_readiness(enabled=True),
    )


def handle(
    self: Any,
    method: str,
    sub: str,
    q: dict,
    *,
    store: Any,
    cfg: Any,
    model_profiles: Any,
    model_discovery: Any,
) -> bool:
    """Answer an onboarding route, or report that this group does not own it."""

    del q
    path = sub.split("?")[0]
    if path not in {"/onboarding", "/onboarding/complete"}:
        return False

    if _GET.match(method, sub):
        service = _service(cfg, store)
        self._json(_payload(service, model_profiles, model_discovery))
        return True

    if _COMPLETE.match(method, sub):
        if not team_policy.may_change_instance_config(self):
            self._json({"error": "admin only", "code": "admin_only"}, 403)
            return True
        service = _service(cfg, store)
        try:
            service.complete(self._body())
        except ValueError as error:
            self._json({"error": str(error)}, 400)
            return True
        self._json(_payload(service, model_profiles, model_discovery))
        return True

    return False
