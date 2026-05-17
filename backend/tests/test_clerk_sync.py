from __future__ import annotations

from unittest.mock import patch

import pytest
from django.utils import timezone
from rest_framework.test import APIClient, APIRequestFactory


def test_enrich_claims_keeps_clerk_defaults_for_yandex_profile_data() -> None:
    from apps.accounts import clerk_auth

    class DummyResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "username": "user_clerk_default",
                "image_url": "https://clerk.example.com/default.png",
                "external_accounts": [
                    {
                        "provider": "yandex",
                        "first_name": "Ivan",
                        "last_name": "Petrov",
                        "username": "ivan.petrov",
                        "image_url": "https://avatars.yandex.net/get-yapic/123/islands-200",
                    }
                ],
            }

    with (
        patch.object(clerk_auth.settings, "CLERK_SECRET_KEY", "secret", create=True),
        patch(
            "httpx.get",
            return_value=DummyResponse(),
        ),
    ):
        claims = clerk_auth._enrich_claims_from_clerk_api(
            "user_clerk_default",
            {"sub": "user_clerk_default"},
        )

    assert claims["username"] == "user_clerk_default"
    assert claims["image_url"] == "https://clerk.example.com/default.png"
    assert claims["provider"] == "yandex"
    assert claims["external_accounts"][0]["username"] == "ivan.petrov"


@pytest.mark.django_db
def test_get_or_create_from_clerk_creates_user_and_related_rows() -> None:
    from apps.accounts.clerk_auth import get_or_create_from_clerk
    from apps.accounts.models import Profile, User
    from apps.feed.models import FeedPreferences

    claims = {
        "sub": "user_clerk_123",
        "email": "clerk-new@example.com",
        "username": "clerknew",
        "image_url": "https://images.example.com/avatar.png",
    }

    user = get_or_create_from_clerk(claims)

    stored = User.objects.get(pk=user.pk)
    profile = Profile.objects.get(user=stored)

    assert stored.clerk_id == "user_clerk_123"
    assert stored.email == "clerk-new@example.com"
    assert profile.social_avatar_url == "https://images.example.com/avatar.png"
    assert FeedPreferences.objects.filter(user=stored).exists()


@pytest.mark.django_db
def test_get_or_create_from_clerk_links_existing_user_by_email() -> None:
    from apps.accounts.clerk_auth import get_or_create_from_clerk
    from apps.accounts.models import User

    existing = User.objects.create_user(
        username="existinguser",
        email="existing@example.com",
        password="not-used-here",
    )

    user = get_or_create_from_clerk(
        {
            "sub": "user_clerk_existing",
            "email": "existing@example.com",
            "username": "ignored",
        }
    )

    existing.refresh_from_db()

    assert user.pk == existing.pk
    assert existing.clerk_id == "user_clerk_existing"


@pytest.mark.django_db
def test_clerk_authentication_persists_user_on_authenticated_request() -> None:
    from apps.accounts.clerk_auth import ClerkJWTAuthentication
    from apps.accounts.models import User

    request = APIRequestFactory().get(
        "/api/v1/auth/session/",
        HTTP_AUTHORIZATION="Bearer test-token",
    )

    auth = ClerkJWTAuthentication()

    from unittest.mock import patch

    with patch(
        "apps.accounts.clerk_auth._verify_clerk_token",
        return_value={
            "sub": "user_clerk_request",
            "email": "request@example.com",
            "username": "requestuser",
        },
    ):
        user, claims = auth.authenticate(request)

    assert user is not None
    assert claims["sub"] == "user_clerk_request"
    assert User.objects.filter(clerk_id="user_clerk_request", email="request@example.com").exists()


def _bearer_headers() -> dict[str, str]:
    return {"HTTP_AUTHORIZATION": "Bearer test-token"}


@pytest.mark.django_db
def test_auth_session_view_uses_clerk_bearer_user() -> None:
    from unittest.mock import patch

    client = APIClient()

    with patch(
        "apps.accounts.clerk_auth._verify_clerk_token",
        return_value={
            "sub": "user_clerk_session",
            "email": "session@example.com",
            "username": "sessionuser",
        },
    ):
        response = client.get("/api/v1/auth/session/", **_bearer_headers())

    assert response.status_code == 200
    assert response.data["authenticated"] is True
    assert response.data["user"]["username"] == "sessionuser"
    assert response.data["user"]["display_name"] == "sessionuser"
    assert response.data["user"]["profile"]["email"] == "session@example.com"


@pytest.mark.django_db
def test_create_post_uses_clerk_authenticated_user() -> None:
    from unittest.mock import patch

    from apps.accounts.models import User
    from apps.posts.models import Post

    client = APIClient()

    with patch(
        "apps.accounts.clerk_auth._verify_clerk_token",
        return_value={
            "sub": "user_clerk_post",
            "email": "post@example.com",
            "username": "postuser",
        },
    ):
        response = client.post(
            "/api/v1/posts/",
            {
                "title": "Clerk created post",
                "kind": "text",
                "body_markdown": "hello from clerk",
            },
            format="json",
            **_bearer_headers(),
        )

    assert response.status_code == 201
    user = User.objects.get(clerk_id="user_clerk_post")
    post = Post.objects.get(title="Clerk created post")
    assert post.author_id == user.pk


@pytest.mark.django_db
def test_create_comment_uses_clerk_authenticated_user() -> None:
    from unittest.mock import patch

    from apps.accounts.models import User
    from apps.posts.models import Post

    post_author = User.objects.create_user(
        username="author1",
        email="author1@example.com",
        password="unused",
    )
    post = Post.objects.create(
        author=post_author,
        title="Published post",
        kind=Post.Kind.TEXT,
        body_markdown="body",
        status=Post.Status.PUBLISHED,
        published_at=timezone.now(),
    )

    client = APIClient()

    with patch(
        "apps.accounts.clerk_auth._verify_clerk_token",
        return_value={
            "sub": "user_clerk_comment",
            "email": "comment@example.com",
            "username": "commentuser",
        },
    ):
        response = client.post(
            "/api/v1/comments/",
            {"post": post.pk, "body_markdown": "comment body"},
            format="json",
            **_bearer_headers(),
        )

    assert response.status_code == 201
    user = User.objects.get(clerk_id="user_clerk_comment")
    assert response.data["author"]["id"] == user.pk


@pytest.mark.django_db
def test_post_reaction_uses_clerk_authenticated_user() -> None:
    from unittest.mock import patch

    from apps.accounts.models import User
    from apps.posts.models import Post
    from apps.reactions.models import Reaction

    post_author = User.objects.create_user(
        username="author2",
        email="author2@example.com",
        password="unused",
    )
    post = Post.objects.create(
        author=post_author,
        title="Reactable post",
        kind=Post.Kind.TEXT,
        body_markdown="body",
        status=Post.Status.PUBLISHED,
        published_at=timezone.now(),
    )

    client = APIClient()

    with patch(
        "apps.accounts.clerk_auth._verify_clerk_token",
        return_value={
            "sub": "user_clerk_reaction",
            "email": "reaction@example.com",
            "username": "reactionuser",
        },
    ):
        response = client.post(
            f"/api/v1/posts/{post.pk}/react/",
            {"kind": Reaction.Kind.LIKE},
            format="json",
            **_bearer_headers(),
        )

    assert response.status_code == 201
    user = User.objects.get(clerk_id="user_clerk_reaction")
    assert Reaction.objects.filter(user=user, object_id=post.pk, kind=Reaction.Kind.LIKE).exists()


@pytest.mark.django_db
def test_feed_refresh_builds_snapshot_for_clerk_authenticated_user() -> None:
    from unittest.mock import patch

    from apps.accounts.models import User
    from apps.feed.models import FeedSnapshot, PostFeedMeta
    from apps.posts.models import Post

    post_author = User.objects.create_user(
        username="author3",
        email="author3@example.com",
        password="unused",
    )
    post = Post.objects.create(
        author=post_author,
        title="Feed post",
        kind=Post.Kind.TEXT,
        body_markdown="body",
        status=Post.Status.PUBLISHED,
        published_at=timezone.now(),
    )
    PostFeedMeta.objects.create(
        post=post,
        bucket=1,
        content_hash="1234567890abcdef",
        kind=post.kind,
        tag_ids=[],
        keyword_set=None,
        rotation_offset=7,
        published_at=post.published_at,
        is_eligible=True,
        version=1,
    )

    client = APIClient()

    with patch(
        "apps.accounts.clerk_auth._verify_clerk_token",
        return_value={
            "sub": "user_clerk_feed",
            "email": "feed@example.com",
            "username": "feeduser",
        },
    ):
        response = client.post(
            "/api/v1/feed/refresh/",
            {},
            format="json",
            **_bearer_headers(),
        )

    assert response.status_code == 200
    user = User.objects.get(clerk_id="user_clerk_feed")
    snapshot = FeedSnapshot.objects.get(id=response.data["snapshot_id"])
    assert snapshot.user_id == user.pk
    assert post.pk in snapshot.post_ids


@pytest.mark.django_db
def test_me_endpoint_returns_profile_for_clerk_authenticated_user() -> None:
    from unittest.mock import patch

    client = APIClient()

    with patch(
        "apps.accounts.clerk_auth._verify_clerk_token",
        return_value={
            "sub": "user_clerk_me",
            "email": "me@example.com",
            "username": "meuser",
            "image_url": "https://images.example.com/me.png",
        },
    ):
        response = client.get("/api/v1/me/", **_bearer_headers())

    assert response.status_code == 200
    assert response.data["username"] == "meuser"
    assert response.data["display_name"] == "meuser"
    assert response.data["email"] == "me@example.com"
    assert response.data["avatar_url"] == "https://images.example.com/me.png"


@pytest.mark.django_db
def test_me_patch_updates_profile_for_clerk_authenticated_user() -> None:
    from unittest.mock import patch

    client = APIClient()

    with patch(
        "apps.accounts.clerk_auth._verify_clerk_token",
        return_value={
            "sub": "user_clerk_me_patch",
            "email": "mepatch@example.com",
            "username": "mepatchuser",
        },
    ):
        response = client.patch(
            "/api/v1/me/",
            {
                "bio": "updated via clerk",
                "website_url": "https://example.com/profile",
            },
            format="json",
            **_bearer_headers(),
        )

    assert response.status_code == 200
    assert response.data["bio"] == "updated via clerk"
    assert response.data["website_url"] == "https://example.com/profile"


@pytest.mark.django_db
def test_mixed_case_clerk_sub_reuses_same_user_and_profile() -> None:
    from unittest.mock import patch

    from apps.accounts.models import Profile, User

    client = APIClient()

    with patch(
        "apps.accounts.clerk_auth._verify_clerk_token",
        return_value={
            "sub": "user_AbCdEf123456",
            "email": "case@example.com",
            "username": "user_abcdef123456",
        },
    ):
        first = client.patch(
            "/api/v1/me/",
            {
                "display_name": "Case User",
                "bio": "persists",
            },
            format="json",
            **_bearer_headers(),
        )

    assert first.status_code == 200
    user = User.objects.get(email="case@example.com")
    assert user.clerk_id == "user_abcdef123456"
    assert Profile.objects.get(user=user).display_name == "Case User"

    with patch(
        "apps.accounts.clerk_auth._verify_clerk_token",
        return_value={
            "sub": "user_abcdef123456",
            "email": "case@example.com",
            "username": "user_abcdef123456",
        },
    ):
        second = client.get("/api/v1/me/", **_bearer_headers())

    assert second.status_code == 200
    assert second.data["display_name"] == "Case User"
    assert second.data["bio"] == "persists"


@pytest.mark.django_db
def test_me_preferences_get_returns_preferences_for_clerk_authenticated_user() -> None:
    from unittest.mock import patch

    from apps.accounts.models import User
    from apps.feed.models import FeedPreferences

    user = User.objects.create_user(
        username="prefowner",
        email="prefowner@example.com",
        password="unused",
        clerk_id="user_clerk_prefs_get",
    )
    FeedPreferences.objects.update_or_create(
        user=user,
        defaults={
            "filter_words": ["spam"],
            "filter_post_types": ["video"],
            "muted_tag_ids": [3],
            "muted_user_ids": [9],
        },
    )

    client = APIClient()

    with patch(
        "apps.accounts.clerk_auth._verify_clerk_token",
        return_value={
            "sub": "user_clerk_prefs_get",
            "email": "prefowner@example.com",
            "username": "prefowner",
        },
    ):
        response = client.get("/api/v1/me/preferences/", **_bearer_headers())

    assert response.status_code == 200
    assert response.data["filter_words"] == ["spam"]
    assert response.data["filter_post_types"] == ["video"]
    assert response.data["muted_tag_ids"] == [3]
    assert response.data["muted_user_ids"] == [9]


@pytest.mark.django_db
def test_me_preferences_patch_updates_preferences_for_clerk_authenticated_user() -> None:
    from unittest.mock import patch

    from apps.accounts.models import User
    from apps.feed.models import FeedPreferences

    user = User.objects.create_user(
        username="prefpatch",
        email="prefpatch@example.com",
        password="unused",
        clerk_id="user_clerk_prefs_patch",
    )
    FeedPreferences.objects.update_or_create(
        user=user,
        defaults={
            "filter_words": [],
            "filter_post_types": [],
            "muted_tag_ids": [],
            "muted_user_ids": [],
        },
    )

    client = APIClient()

    with (
        patch(
            "apps.accounts.clerk_auth._verify_clerk_token",
            return_value={
                "sub": "user_clerk_prefs_patch",
                "email": "prefpatch@example.com",
                "username": "prefpatch",
            },
        ),
        patch("apps.feed.jobs.enqueue_invalidate_user_snapshots") as mock_invalidate,
    ):
        response = client.patch(
            "/api/v1/me/preferences/",
            {
                "filter_words": ["casino", "ads"],
                "filter_post_types": ["image"],
                "muted_tag_ids": [1, 2],
                "muted_user_ids": [77],
            },
            format="json",
            **_bearer_headers(),
        )

    user.refresh_from_db()
    prefs = FeedPreferences.objects.get(user=user)

    assert response.status_code == 200
    assert response.data["filter_words"] == ["casino", "ads"]
    assert response.data["filter_post_types"] == ["image"]
    assert response.data["muted_tag_ids"] == [1, 2]
    assert response.data["muted_user_ids"] == [77]
    assert prefs.filter_words == ["casino", "ads"]
    assert prefs.filter_post_types == ["image"]
    assert prefs.muted_tag_ids == [1, 2]
    assert prefs.muted_user_ids == [77]
    mock_invalidate.assert_called_once_with(user.pk)
