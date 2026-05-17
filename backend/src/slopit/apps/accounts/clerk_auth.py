from __future__ import annotations

import logging
import re
import time
from typing import TYPE_CHECKING, Any

import jwt
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import DataError
from jwt import PyJWKClient
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed

if TYPE_CHECKING:
    from rest_framework.request import Request

logger = logging.getLogger(__name__)
User = get_user_model()

# Module-level JWKS client — shared across requests, caches signing keys.
_jwks_client: PyJWKClient | None = None

# Per-user Clerk Backend API cache: {clerk_id: (monotonic_ts, api_claims_dict)}
_clerk_api_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_CLERK_API_CACHE_TTL = 300  # seconds

# Image-URL patterns used to identify the OAuth provider from the Clerk JWT.
_GOOGLE_PATTERN = re.compile(r"googleusercontent\.com", re.IGNORECASE)
_GITHUB_PATTERN = re.compile(r"avatars\.githubusercontent\.com", re.IGNORECASE)
_SOCIAL_AVATAR_FALLBACK_MAX_LEN = 200


def _claim_text(claims: dict[str, Any], *keys: str) -> str:
    """Return first non-empty string value for any key from claims (deep scan)."""

    wanted = {k.lower() for k in keys}

    def clean(value: Any) -> str:
        return value.strip() if isinstance(value, str) else ""

    for key in keys:
        direct = clean(claims.get(key))
        if direct:
            return direct

    def walk(value: Any) -> str:
        if isinstance(value, dict):
            for raw_key, raw_val in value.items():
                key_lower = str(raw_key).lower()
                if key_lower in wanted:
                    hit = clean(raw_val)
                    if hit:
                        return hit
                nested = walk(raw_val)
                if nested:
                    return nested
            return ""

        if isinstance(value, list):
            for item in value:
                nested = walk(item)
                if nested:
                    return nested
            return ""

        return ""

    return walk(claims)


def _extract_name_parts(claims: dict[str, Any]) -> tuple[str, str]:
    """Extract first/last name from common OIDC claim variants."""

    first_name = _claim_text(claims, "first_name", "given_name", "firstName")
    last_name = _claim_text(claims, "last_name", "family_name", "lastName")

    if not first_name and not last_name:
        full_name = _claim_text(claims, "name", "real_name", "full_name", "display_name")
        if full_name:
            parts = full_name.split()
            first_name = parts[0]
            last_name = " ".join(parts[1:]) if len(parts) > 1 else ""

    return first_name, last_name


def _extract_image_url(claims: dict[str, Any]) -> str:
    """Extract avatar URL from common OIDC claim variants."""

    return _claim_text(
        claims,
        "image_url",
        "picture",
        "avatar_url",
        "profile_image_url",
        "photo_url",
        "avatar",
    )


def _enrich_claims_from_clerk_api(
    clerk_id: str,
    claims: dict[str, Any],
) -> dict[str, Any]:

    secret_key: str = getattr(settings, "CLERK_SECRET_KEY", "")
    if not secret_key:
        return claims

    # Fast path — serve from cache while TTL is valid.
    cached = _clerk_api_cache.get(clerk_id)
    if cached and (time.monotonic() - cached[0]) < _CLERK_API_CACHE_TTL:
        enriched = {**claims, **cached[1]}
        logger.debug("[clerk_auth] Served Clerk API profile from cache for %s", clerk_id)
        return enriched

    try:
        import httpx

        resp = httpx.get(
            f"https://api.clerk.com/v1/users/{clerk_id}",
            headers={"Authorization": f"Bearer {secret_key}"},
            timeout=5.0,
        )
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()
    except Exception as exc:
        logger.warning("[clerk_auth] Clerk Backend API call failed for %s: %s", clerk_id, exc)
        return claims

    api_claims: dict[str, Any] = {}

    # 1. Prefer the top-level Clerk fields so the database stores Clerk's own
    #    default username/avatar when provider-specific profile data is missing.
    for field in ("image_url", "first_name", "last_name", "username"):
        val = data.get(field)
        if val and isinstance(val, str):
            api_claims[field] = val

    # 2. External accounts — preserve them for provider detection, but do not
    #    copy provider-specific name/photo fields into the user profile.
    external_accounts: list[dict[str, Any]] = data.get("external_accounts") or []
    if external_accounts:
        api_claims["external_accounts"] = external_accounts
        for ea in external_accounts:
            provider = str(ea.get("provider") or "").lower()
            if provider and not api_claims.get("provider"):
                api_claims["provider"] = provider

    # 3. Primary email — Clerk stores it as an array with a pointer.
    email_addresses: list[dict[str, Any]] = data.get("email_addresses") or []
    primary_id: str = data.get("primary_email_address_id") or ""
    for ea_email in email_addresses:
        if ea_email.get("id") == primary_id:
            addr = ea_email.get("email_address") or ""
            if addr and not addr.endswith("@no-email.local"):
                api_claims["email"] = addr
            break

    _clerk_api_cache[clerk_id] = (time.monotonic(), api_claims)

    enriched = {**claims, **api_claims}
    logger.info(
        "[clerk_auth] Enriched claims from Clerk API for %s: "
        "added=%s first_name=%r last_name=%r username=%r image_url=%r provider=%r",
        clerk_id,
        list(api_claims.keys()),
        api_claims.get("first_name"),
        api_claims.get("last_name"),
        api_claims.get("username"),
        api_claims.get("image_url"),
        api_claims.get("provider"),
    )
    return enriched


def _claim_provider_hint(claims: dict[str, Any]) -> str:

    provider_keys = {
        "provider",
        "providers",
        "strategy",
        "strategies",
        "oauth_provider",
        "oauth_providers",
        "social_provider",
        "social_providers",
        "identity_provider",
        "identity_providers",
        "external_provider",
        "external_providers",
        "connection",
        "connections",
        "external_accounts",
        "federation",
    }

    def walk(value: Any, key: str | None = None) -> str:
        if isinstance(value, dict):
            for child_key, child_value in value.items():
                child_key_str = str(child_key).lower()
                result = walk(
                    child_value,
                    child_key_str if child_key_str in provider_keys else key,
                )
                if result:
                    return result
            return ""

        if isinstance(value, list):
            for item in value:
                result = walk(item, key)
                if result:
                    return result
            return ""

        if not isinstance(value, str) or key is None:
            return ""

        lowered = value.lower()
        if "yandex" in lowered:
            return "yandex"
        if "github" in lowered:
            return "github"
        if "google" in lowered:
            return "google"
        if "telegram" in lowered:
            return "telegram"
        return ""

    return walk(claims)


def _get_jwks_client() -> PyJWKClient:
    global _jwks_client
    if _jwks_client is None:
        url: str = getattr(settings, "CLERK_JWKS_URL", "")
        if not url:
            raise AuthenticationFailed("CLERK_JWKS_URL is not configured.")
        # cache_keys=True keeps signing keys in memory; lifespan refreshes hourly.
        _jwks_client = PyJWKClient(url, cache_keys=True, lifespan=3600)
    return _jwks_client


def _verify_clerk_token(token: str) -> dict[str, Any]:
    """Validate a Clerk JWT and return its decoded claims."""
    client = _get_jwks_client()
    try:
        signing_key = client.get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            options={"verify_exp": True},
        )
        logger.debug(
            "[clerk_auth] Token verified. sub=%s email=%r image_url=%r",
            claims.get("sub"),
            claims.get("email"),
            claims.get("image_url"),
        )
        return claims
    except jwt.ExpiredSignatureError as exc:
        logger.warning(
            "[clerk_auth] Token expired. error=%s",
            exc,
            extra={"auth_error": "expired_token", "auth_method": None},
        )
        raise AuthenticationFailed("Clerk token has expired.") from exc
    except jwt.InvalidTokenError as exc:
        logger.warning(
            "[clerk_auth] Invalid token. error=%s",
            exc,
            extra={"auth_error": "invalid_token", "auth_method": None},
        )
        raise AuthenticationFailed(f"Invalid Clerk token: {exc}") from exc


def _detect_auth_method(
    claims: dict[str, Any],
    has_telegram_id: bool = False,
) -> str:

    if has_telegram_id:
        detected = "telegram"
    else:
        provider_hint = _claim_provider_hint(claims)
        image_url = _extract_image_url(claims)
        if provider_hint:
            detected = provider_hint
        elif _GITHUB_PATTERN.search(image_url):
            detected = "github"
        elif _GOOGLE_PATTERN.search(image_url):
            detected = "google"
        else:
            detected = ""

    logger.debug(
        "[clerk_auth] Auth method detected: %r  (sub=%s image_url=%r has_telegram=%s)",
        detected,
        claims.get("sub"),
        claims.get("image_url"),
        has_telegram_id,
    )
    return detected


def _derive_unique_username(base: str) -> str:
    """Return a unique Django username derived from *base*."""
    base = (base[:28] or "user").replace(" ", "_").lower()
    username, counter = base, 1
    while User.objects.filter(username=username).exists():
        username = f"{base}{counter}"
        counter += 1
    return username


def _sync_clerk_profile(user: Any, claims: dict[str, Any]) -> None:
    """Sync name, username, avatar URL, and auth_method from Clerk JWT claims.

    Called on every authenticated request so the display data stays current
    without requiring a separate webhook — cheap because we only write when
    the stored value actually differs from what Clerk sent.
    """
    from apps.accounts.models import Profile

    user_fields: list[str] = []

    first_name, last_name = _extract_name_parts(claims)
    if first_name and user.first_name != first_name:
        user.first_name = first_name
        user_fields.append("first_name")
    if last_name and user.last_name != last_name:
        user.last_name = last_name
        user_fields.append("last_name")

    # Prefer the Clerk username (human-readable slug) over the internal
    # user_xxx / clerk_user_xxx / k_user_xxx ID.  Only adopt it when it looks like a real username.
    clerk_username: str = claims.get("username") or ""
    is_real_username = clerk_username and not re.match(
        r"^(clerk_|k_)?user_[a-z0-9]{6,}", clerk_username, re.IGNORECASE
    )
    if is_real_username and user.username != clerk_username:
        if not User.objects.filter(username=clerk_username).exclude(pk=user.pk).exists():
            user.username = clerk_username
            user_fields.append("username")

    # Re-detect and persist auth_method on every request so it stays
    # accurate if the user later links a different OAuth provider.
    has_telegram = bool(getattr(user, "telegram_id", None))
    detected_method = _detect_auth_method(claims, has_telegram_id=has_telegram)
    if detected_method and user.auth_method != detected_method:
        old = user.auth_method
        user.auth_method = detected_method
        user_fields.append("auth_method")
        logger.info(
            "[clerk_auth] Updated auth_method for user pk=%s: %r -> %r",
            user.pk,
            old,
            detected_method,
        )

    if user_fields:
        user.save(update_fields=user_fields)
        logger.debug(
            "[clerk_auth] Synced user pk=%s fields=%s",
            user.pk,
            user_fields,
        )

    # Avatar URL — update Profile row only when the value changes.
    image_url = _extract_image_url(claims)
    if image_url:
        try:
            Profile.objects.filter(user=user).exclude(social_avatar_url=image_url).update(
                social_avatar_url=image_url
            )
        except DataError:
            truncated_url = image_url[:_SOCIAL_AVATAR_FALLBACK_MAX_LEN]
            Profile.objects.filter(user=user).exclude(social_avatar_url=truncated_url).update(
                social_avatar_url=truncated_url
            )
            logger.warning(
                "[clerk_auth] Truncated social avatar URL for user pk=%s from %d to %d chars",
                user.pk,
                len(image_url),
                len(truncated_url),
            )


def get_or_create_from_clerk(claims: dict[str, Any]) -> Any:
    """Map a verified Clerk JWT payload to a Django ``User``.

    Resolution order:
    1. Look up by ``clerk_id`` — fastest path after first login.
    2. Look up by email — links legacy allauth-created users on first Clerk login.
    3. Create a new ``User`` record.

    Profile data (name, avatar, auth_method) is synced from Clerk on every
    call so the frontend always sees up-to-date display info without a
    separate webhook.
    """
    clerk_id_raw: str = claims.get("sub", "")
    if not clerk_id_raw:
        logger.error(
            "[clerk_auth] Token missing 'sub' claim — cannot identify user.",
            extra={
                "auth_error": "missing_sub",
                "auth_method": None,
                "claims_keys": list(claims.keys()),
            },
        )
        raise AuthenticationFailed("Token missing required 'sub' claim.")

    # Clerk IDs are effectively case-insensitive for our identity mapping.
    # Canonicalize to lowercase so relogins with different letter casing still
    # resolve to the same Django user/profile.
    clerk_id = clerk_id_raw.lower()

    logger.debug(
        "[clerk_auth] Resolving user for clerk_id=%s email=%r",
        clerk_id,
        claims.get("email"),
    )

    # Enrich JWT claims with the full Clerk Backend API profile.
    # Keep Clerk's own default identity fields when provider data is sparse.
    claims = _enrich_claims_from_clerk_api(clerk_id_raw, claims)

    # 1. Fast path — already linked.
    try:
        user = User.objects.get(clerk_id__iexact=clerk_id)
        if user.clerk_id != clerk_id:
            user.clerk_id = clerk_id
            user.save(update_fields=["clerk_id"])
        logger.debug(
            "[clerk_auth] Fast-path hit: user pk=%s clerk_id=%s auth_method=%r",
            user.pk,
            clerk_id,
            user.auth_method,
        )
        _sync_clerk_profile(user, claims)
        return user
    except User.DoesNotExist:
        pass

    email: str = claims.get("email", "")

    # 2. Adopt an existing account by email (allauth / email-password users).
    if email:
        try:
            user = User.objects.get(email=email)
            user.clerk_id = clerk_id
            fields_to_save = ["clerk_id"]

            # Detect and set auth_method now that we know the clerk_id.
            detected = _detect_auth_method(claims, has_telegram_id=bool(user.telegram_id))
            if detected and user.auth_method != detected:
                user.auth_method = detected
                fields_to_save.append("auth_method")

            user.save(update_fields=fields_to_save)
            _sync_clerk_profile(user, claims)
            logger.info(
                "[clerk_auth] Linked clerk_id=%s to existing user pk=%s via email. auth_method=%r",
                clerk_id,
                user.pk,
                user.auth_method,
            )
            return user
        except User.DoesNotExist:
            pass

    # 3. Create a brand-new Django user.
    clerk_username: str = claims.get("username") or ""
    is_real_username = clerk_username and not re.match(
        r"^(clerk_|k_)?user_[a-z0-9]{6,}", clerk_username, re.IGNORECASE
    )
    base = clerk_username if is_real_username else (email.split("@")[0] if email else clerk_id)
    username = _derive_unique_username(base)

    # When Clerk provides no email use a unique sentinel so the DB unique
    # constraint on email (which disallows blank duplicates) is satisfied.
    email_to_store = email if email else f"clerk_{clerk_id}@no-email.local"

    # Detect auth method before creating the user.
    detected_method = _detect_auth_method(claims, has_telegram_id=False)
    created_first_name, created_last_name = _extract_name_parts(claims)

    try:
        user, created = User.objects.get_or_create(
            clerk_id=clerk_id,
            defaults=dict(
                username=username,
                email=email_to_store,
                auth_method=detected_method,
                is_active=True,
                first_name=created_first_name,
                last_name=created_last_name,
            ),
        )
    except Exception as exc:
        logger.error(
            "[clerk_auth] Failed to get_or_create user. clerk_id=%s email=%r "
            "detected_method=%r username=%r error=%s",
            clerk_id,
            email_to_store,
            detected_method,
            username,
            exc,
            extra={
                "auth_error": "user_create_failed",
                "auth_method": detected_method,
                "clerk_id": clerk_id,
            },
        )
        raise

    if created:
        logger.info(
            "[clerk_auth] Created new user pk=%s clerk_id=%s username=%r email=%r auth_method=%r",
            user.pk,
            clerk_id,
            username,
            email_to_store,
            detected_method,
        )
    else:
        logger.debug(
            "[clerk_auth] get_or_create returned existing user pk=%s for clerk_id=%s",
            user.pk,
            clerk_id,
        )

    _sync_clerk_profile(user, claims)
    return user


class ClerkJWTAuthentication(BaseAuthentication):
    """DRF authentication backend: verify Clerk Bearer tokens.

    Attach to ``REST_FRAMEWORK["DEFAULT_AUTHENTICATION_CLASSES"]`` before
    ``SessionAuthentication`` so API requests authenticated with Clerk tokens
    take precedence over browser sessions.
    """

    def authenticate(self, request: "Request") -> tuple[Any, dict[str, Any]] | None:
        auth_header: str = request.META.get("HTTP_AUTHORIZATION", "")
        if not auth_header.startswith("Bearer "):
            return None

        token = auth_header[7:]
        if not token:
            return None

        # Graceful no-op when Clerk is not configured (local dev / CI).
        if not getattr(settings, "CLERK_JWKS_URL", ""):
            logger.debug("[clerk_auth] CLERK_JWKS_URL not set — skipping Clerk auth.")
            return None

        path = getattr(request, "path", "?")
        method = getattr(request, "method", "?")
        logger.debug(
            "[clerk_auth] Authenticating %s %s (token length=%d)",
            method,
            path,
            len(token),
        )

        try:
            claims = _verify_clerk_token(token)
        except AuthenticationFailed as exc:
            logger.warning(
                "[clerk_auth] Token verification failed on %s %s: %s",
                method,
                path,
                exc,
                extra={
                    "auth_error": "verification_failed",
                    "auth_method": None,
                    "path": path,
                },
            )
            raise

        try:
            user = get_or_create_from_clerk(claims)
        except AuthenticationFailed:
            raise
        except Exception as exc:
            logger.error(
                "[clerk_auth] Unexpected error resolving user on %s %s: %s",
                method,
                path,
                exc,
                extra={
                    "auth_error": "resolution_failed",
                    "auth_method": None,
                    "clerk_id": claims.get("sub"),
                    "path": path,
                },
            )
            raise

        logger.debug(
            "[clerk_auth] Auth success — user pk=%s username=%r auth_method=%r path=%s",
            user.pk,
            user.username,
            user.auth_method,
            path,
        )
        return (user, claims)

    def authenticate_header(self, request: "Request") -> str:
        return 'Bearer realm="api"'
