"""Small authentication and prompt-safety helpers used at the API boundary."""

import hmac
from dataclasses import dataclass
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from enterprise_rag.config import Settings, get_settings

bearer_scheme = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class Principal:
    """Authenticated caller identity; tenant is mandatory for data isolation."""

    tenant_id: str
    role: str


def parse_api_keys(raw_keys: str) -> dict[str, Principal]:
    """Parse development credentials in `key:tenant:role` format from the environment."""

    credentials: dict[str, Principal] = {}
    for item in raw_keys.split(","):
        try:
            key, tenant_id, role = item.strip().split(":", maxsplit=2)
        except ValueError as error:
            raise ValueError("API_KEYS entries must be key:tenant:role") from error
        credentials[key] = Principal(tenant_id=tenant_id, role=role)
    return credentials


def current_principal(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    settings: Settings = Depends(get_settings),
) -> Principal:
    """Authenticate a bearer key. Production deployments should replace this with OIDC/JWT."""

    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token")
    # Avoid a direct dictionary lookup for comparison so token checks do not leak
    # timing information that could help an attacker guess a valid key.
    principal = next(
        (
            candidate_principal
            for api_key, candidate_principal in parse_api_keys(settings.api_keys).items()
            if hmac.compare_digest(api_key, credentials.credentials)
        ),
        None,
    )
    if principal is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid bearer token")
    return principal


def require_admin(principal: Principal = Depends(current_principal)) -> Principal:
    """Restrict ingestion to administrators; readers may only query existing knowledge."""

    if principal.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin role required")
    return principal


def reject_prompt_injection(text: str) -> None:
    """Block obvious instruction-overriding language before it reaches retrieval or an LLM.

    This is deliberately a lightweight first layer, not a complete security solution.
    Production systems should add model-based detection and red-team tests.
    """

    suspicious_phrases = ("ignore previous instructions", "reveal system prompt", "jailbreak")
    if any(phrase in text.lower() for phrase in suspicious_phrases):
        raise HTTPException(status_code=400, detail="Question was blocked by prompt-safety policy")
