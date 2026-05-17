from __future__ import annotations

import logging

from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from django.core.exceptions import MultipleObjectsReturned
from django.db import DataError

from apps.accounts.models import Profile

logger = logging.getLogger(__name__)
_SOCIAL_AVATAR_FALLBACK_MAX_LEN = 200


class SocialAccountAdapter(DefaultSocialAccountAdapter):
    """Custom allauth social adapter for SlopIt.

    - Redirects to FRONTEND_URL after every OAuth login (not backend root).
    - Syncs provider avatar URL into Profile.social_avatar_url on every login.
    """

    _AVATAR_KEYS: dict[str, str] = {
        "google": "picture",
        "github": "avatar_url",
        "telegram": "photo_url",
    }

    def get_login_redirect_url(self, request: object) -> str:
        from django.conf import settings

        frontend_url = getattr(settings, "FRONTEND_URL", "").rstrip("/")
        return f"{frontend_url}/home"

    def get_app(self, request: object, provider: object, client_id: object = None) -> object:
        try:
            return super().get_app(request, provider=provider, client_id=client_id)
        except MultipleObjectsReturned:
            apps = self.list_apps(request, provider=provider, client_id=client_id)
            if not apps:
                raise

            visible_apps = [app for app in apps if not app.settings.get("hidden")]
            candidates = visible_apps or apps

            db_backed = [app for app in candidates if getattr(app, "pk", None)]
            if db_backed:
                selected = max(db_backed, key=lambda app: int(getattr(app, "pk", 0)))
            else:
                selected = candidates[0]

            logger.warning(
                "Multiple social apps matched provider=%s client_id=%s; using app id=%s",
                provider,
                client_id,
                getattr(selected, "pk", None),
            )
            return selected

    def pre_social_login(self, request: object, sociallogin: object) -> None:
        super().pre_social_login(request, sociallogin)
        if sociallogin.is_existing:
            self._sync_avatar(sociallogin)

    def save_user(self, request: object, sociallogin: object, form: object = None) -> object:
        user = super().save_user(request, sociallogin, form)
        self._sync_avatar(sociallogin, user=user)
        return user

    def _sync_avatar(self, sociallogin: object, user: object = None) -> None:
        extra = sociallogin.account.extra_data
        provider = sociallogin.account.provider
        url = extra.get(self._AVATAR_KEYS.get(provider, ""), "")
        if not url:
            return
        target_user = user or sociallogin.user
        if target_user and target_user.pk:
            try:
                Profile.objects.filter(user_id=target_user.pk).update(social_avatar_url=url)
            except DataError:
                Profile.objects.filter(user_id=target_user.pk).update(
                    social_avatar_url=url[:_SOCIAL_AVATAR_FALLBACK_MAX_LEN]
                )
