"""First-run Web onboarding routes.

``GET /onboarding`` is redacted and contacts nobody: local readiness, the
fixed local-model catalogue (not a live scan), and environment/network
posture.  ``POST /onboarding/complete`` is an instance-global mutation and
is refused to non-admins in team mode.
"""

from __future__ import annotations

from typing import Any, Callable

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
    *,
    can_configure: bool = True,
    hide_endpoint: bool = False,
) -> dict[str, Any]:
    profiles, _unused = model_profiles.profiles_payload(hide_endpoint=hide_endpoint)
    payload = service.web_status(
        profiles=profiles,
        catalog=model_discovery.catalog(),
        environment=standard_profile_readiness(enabled=True),
    )
    if hide_endpoint:
        # The live endpoint is the admin's, like each profile's (decision 25).
        payload["base_url"] = ""
    # Whether this reader may save what the wizard asks for (TEAM-10). In team
    # mode the model setup is an admin's, and a member who was shown the
    # wizard could neither continue nor skip it: both are 403 `admin_only`.
    payload["can_configure"] = can_configure
    return payload


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
    model_auth_change: Callable[[Callable[[], Any]], Any] | None = None,
) -> bool:
    """Answer an onboarding route, or report that this group does not own it.

    ``model_auth_change`` wraps the write to the live model settings so the
    gateway can react to it as it does for every other model activation
    (DataPro session invalidation, Agent Plan defaults).
    """

    del q
    path = sub.split("?")[0]
    if path not in {"/onboarding", "/onboarding/complete"}:
        return False

    if _GET.match(method, sub):
        service = _service(cfg, store)
        self._json(
            _payload(
                service,
                model_profiles,
                model_discovery,
                can_configure=team_policy.may_change_instance_config(self),
                hide_endpoint=team_policy.hides_instance_endpoint(self),
            )
        )
        return True

    if _COMPLETE.match(method, sub):
        if not team_policy.may_change_instance_config(self):
            self._json({"error": "admin only", "code": "admin_only"}, 403)
            return True
        service = _service(cfg, store)
        wrap = model_auth_change or (lambda change: change())
        try:
            body = self._body()
            wrap(lambda: service.complete(body))
        except ValueError as error:
            self._json({"error": str(error)}, 400)
            return True
        self._json(_payload(service, model_profiles, model_discovery))
        return True

    return False
