"""Serializers for user accounts and profiles."""

from __future__ import annotations

from rest_framework import serializers

from apps.accounts.avatar import generate_avatar_data_url
from apps.accounts.models import Profile, User


class UserBriefSerializer(serializers.ModelSerializer):
    """Minimal user representation embedded in posts and comments."""

    avatar_url = serializers.SerializerMethodField()
    display_name = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id",
            "username",
            "display_name",
            "first_name",
            "last_name",
            "email",
            "avatar_url",
        ]
        read_only_fields = fields

    def get_avatar_url(self, obj: User) -> str | None:
        """Return the best available avatar URL for this user.

        Priority: user-uploaded avatar > social/Clerk avatar URL > local generated avatar.
        """
        profile = getattr(obj, "profile", None)
        if profile:
            if profile.avatar:
                request = self.context.get("request")
                if request:
                    return request.build_absolute_uri(profile.avatar.url)
            if profile.social_avatar_url:
                return profile.social_avatar_url
        seed = self._avatar_seed_for_user(obj)
        return generate_avatar_data_url(seed)

    @staticmethod
    def _avatar_seed_for_user(user: User) -> str:
        """Return the best seed for avatar generation — avoids Clerk user_xxx IDs."""
        import re

        is_clerk_id = lambda s: bool(
            s and re.match(r"^(clerk_|k_)?user_[a-z0-9]{6,}", s, re.IGNORECASE)
        )
        profile = getattr(user, "profile", None)
        if profile and profile.display_name:
            return profile.display_name
        full = " ".join(filter(None, [user.first_name, user.last_name])).strip()
        if full:
            return full
        if user.username and not is_clerk_id(user.username):
            return user.username
        if user.email:
            local = user.email.split("@")[0]
            if not is_clerk_id(local):
                return local
        return str(user.pk)

    def get_display_name(self, obj: User) -> str | None:
        """Return the best available human-readable name for this user.

        Priority: user-set profile.display_name > Clerk full name >
        username > email prefix > fallback.
        """
        import re

        is_clerk_id = lambda s: bool(
            s and re.match(r"^(clerk_|k_)?user_[a-z0-9]{6,}", s, re.IGNORECASE)
        )
        # Auto-generated placeholder usernames from migration 0006 (e.g. "user38")
        is_placeholder = lambda s: bool(s and re.match(r"^user\d+$", s, re.IGNORECASE))
        # Sentinel email domain used when Clerk has no real email
        is_sentinel_email = lambda e: bool(
            e and (e.endswith("@no-email.local") or is_clerk_id(e.split("@")[0]))
        )
        # 1. User-set custom display name takes top priority
        profile = getattr(obj, "profile", None)
        if profile and profile.display_name:
            return profile.display_name
        full = " ".join(filter(None, [obj.first_name, obj.last_name])).strip()
        if full:
            return full
        if obj.username and not is_placeholder(obj.username):
            return obj.username
        if obj.email and not is_sentinel_email(obj.email):
            return obj.email.split("@")[0]
        # Absolute last resort — always return something human-readable
        return f"User {obj.pk}"


class ProfileSerializer(serializers.ModelSerializer):
    """Full profile read/write (owner only via MeViewSet)."""

    username = serializers.CharField(source="user.username", read_only=True)
    email = serializers.EmailField(source="user.email", read_only=True)
    avatar_url = serializers.SerializerMethodField()

    class Meta:
        model = Profile
        fields = [
            "username",
            "display_name",
            "email",
            "bio",
            "avatar",
            "avatar_url",
            "social_avatar_url",
            "website_url",
            "feed_lifetime_hours",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "username",
            "email",
            "avatar_url",
            "social_avatar_url",
            "created_at",
            "updated_at",
        ]
        extra_kwargs = {
            "avatar": {"write_only": True},
            "display_name": {"required": False, "allow_blank": True},
        }

    def get_avatar_url(self, obj: Profile) -> str | None:
        request = self.context.get("request")
        if obj.avatar and request:
            return request.build_absolute_uri(obj.avatar.url)
        if obj.social_avatar_url:
            return obj.social_avatar_url
        seed = self._avatar_seed_for_profile(obj)
        return generate_avatar_data_url(seed)

    def to_representation(self, instance: Profile) -> dict[str, object]:
        """Expose a stable display_name in API responses.

        Keep ``display_name`` writable for PATCH, but when it's blank return an
        effective fallback derived from user fields so the client never receives
        an empty name for authenticated users.
        """
        data = super().to_representation(instance)
        if not data.get("display_name"):
            data["display_name"] = UserBriefSerializer(instance.user).get_display_name(
                instance.user
            )
        return data

    @staticmethod
    def _avatar_seed_for_profile(profile: "Profile") -> str:  # type: ignore[name-defined]
        """Pick a human-readable seed for avatar, skipping Clerk user_xxx IDs."""
        import re

        is_clerk_id = lambda s: bool(
            s and re.match(r"^(clerk_|k_)?user_[a-z0-9]{6,}", s, re.IGNORECASE)
        )
        if profile.display_name:
            return profile.display_name
        user = profile.user
        full = " ".join(filter(None, [user.first_name, user.last_name])).strip()
        if full:
            return full
        if user.username and not is_clerk_id(user.username):
            return user.username
        if user.email:
            local = user.email.split("@")[0]
            if not is_clerk_id(local):
                return local
        return str(profile.user_id)


class PublicProfileSerializer(serializers.ModelSerializer):
    """Read-only public profile for any user — used by UserProfileViewSet."""

    username = serializers.CharField(source="user.username", read_only=True)
    display_name = serializers.SerializerMethodField()
    avatar_url = serializers.SerializerMethodField()
    post_count = serializers.IntegerField(read_only=True, default=0)
    karma_score = serializers.SerializerMethodField()

    class Meta:
        model = Profile
        fields = [
            "username",
            "display_name",
            "avatar_url",
            "bio",
            "website_url",
            "post_count",
            "karma_score",
            "created_at",
        ]
        read_only_fields = fields

    def get_karma_score(self, obj: Profile) -> int:
        """Compute karma: sum of likes received on user's posts + comments."""
        from django.contrib.contenttypes.models import ContentType
        from django.db.models import Count

        from apps.comments.models import Comment
        from apps.posts.models import Post
        from apps.reactions.models import Reaction

        post_ct = ContentType.objects.get_for_model(Post)
        comment_ct = ContentType.objects.get_for_model(Comment)

        post_ids = list(
            Post.objects.filter(author=obj.user, status="published").values_list("id", flat=True)
        )
        comment_ids = list(
            Comment.objects.filter(author=obj.user, is_deleted=False).values_list("id", flat=True)
        )

        post_likes = (
            Reaction.objects.filter(
                content_type=post_ct, object_id__in=post_ids, kind="like"
            ).count()
            if post_ids
            else 0
        )
        comment_likes = (
            Reaction.objects.filter(
                content_type=comment_ct, object_id__in=comment_ids, kind="like"
            ).count()
            if comment_ids
            else 0
        )

        return post_likes * 2 + comment_likes

    def get_display_name(self, obj: Profile) -> str:
        """Priority: user-set display_name > Clerk name > username > email prefix."""
        import re

        if obj.display_name:
            return obj.display_name
        user = obj.user
        is_clerk_id = lambda s: bool(
            s and re.match(r"^(clerk_|k_)?user_[a-z0-9]{6,}", s, re.IGNORECASE)
        )
        full = " ".join(filter(None, [user.first_name, user.last_name])).strip()
        if full:
            return full
        if user.username:
            return user.username
        if user.email:
            return user.email.split("@")[0]
        return f"User {obj.user_id}"

    def get_avatar_url(self, obj: Profile) -> str | None:
        """Priority: user-uploaded avatar > social/Clerk avatar URL > local generated avatar."""
        if obj.avatar:
            request = self.context.get("request")
            if request:
                return request.build_absolute_uri(obj.avatar.url)
        if obj.social_avatar_url:
            return obj.social_avatar_url
        seed = ProfileSerializer._avatar_seed_for_profile(obj)
        return generate_avatar_data_url(seed)
