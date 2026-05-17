"""Domain models for user accounts, profiles, and second-factor credentials."""

from __future__ import annotations

from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth.models import AbstractUser
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class AuthMethod(models.TextChoices):
    """OAuth / external provider that was used to authenticate this user."""

    GOOGLE = "google", "Google"
    GITHUB = "github", "GitHub"
    YANDEX = "yandex", "Yandex"
    TELEGRAM = "telegram", "Telegram"


class User(AbstractUser):
    email = models.EmailField(unique=True)
    clerk_id = models.CharField(
        max_length=64,
        blank=True,
        null=True,
        unique=True,
        db_index=True,
        help_text="Clerk user ID — the 'sub' claim from a verified Clerk JWT.",
    )
    telegram_id = models.CharField(
        max_length=32,
        blank=True,
        null=True,
        unique=True,
        db_index=True,
        help_text="Telegram user ID from the Login Widget / OIDC callback.",
    )
    auth_method = models.CharField(
        max_length=16,
        blank=True,
        choices=AuthMethod.choices,
        default="",
        db_index=True,
        help_text=(
            "OAuth provider used on most recent login: google, github, yandex, or telegram. "
            "Blank means not yet determined."
        ),
    )

    class Meta:
        db_table = "accounts_user"
        verbose_name = "user"
        verbose_name_plural = "users"

    def __str__(self) -> str:
        return self.username


class Profile(models.Model):
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name="profile",
        primary_key=True,
    )
    display_name = models.CharField(
        max_length=100,
        blank=True,
        default="",
        help_text="User-set custom display name; takes priority over Clerk data.",
    )
    bio = models.TextField(blank=True, max_length=500)
    avatar = models.ImageField(
        upload_to="avatars/",
        null=True,
        blank=True,
    )
    website_url = models.URLField(blank=True, max_length=200)
    social_avatar_url = models.URLField(blank=True, max_length=500)
    feed_lifetime_hours = models.PositiveSmallIntegerField(
        default=10,
        validators=[
            MinValueValidator(10),
            MaxValueValidator(48),
        ],
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "accounts_profile"
        verbose_name = "profile"
        verbose_name_plural = "profiles"

    def __str__(self) -> str:
        return f"Profile({self.user_id})"


class Passkey(models.Model):
    """WebAuthn passkey credential stored for a user (optional 2FA, Stage 5)."""

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="passkeys",
    )
    credential_id = models.CharField(max_length=1024, unique=True, db_index=True)
    public_key = models.BinaryField()
    sign_count = models.PositiveBigIntegerField(default=0)
    aaguid = models.CharField(max_length=36, blank=True)
    name = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "accounts_passkey"
        verbose_name = "passkey"
        verbose_name_plural = "passkeys"

    def __str__(self) -> str:
        return f"Passkey({self.name or self.credential_id[:16]}…)"


class Passphrase(models.Model):
    """BIP39-like mnemonic passphrase as an optional second factor (Stage 5).

    The raw phrase is NEVER stored.  `phrase_hash` uses Django's password
    hasher (bcrypt) so it can be verified with ``check_password``.
    """

    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name="passphrase",
    )
    phrase_hash = models.CharField(max_length=256)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "accounts_passphrase"
        verbose_name = "passphrase"
        verbose_name_plural = "passphrases"

    def __str__(self) -> str:
        return f"Passphrase(user={self.user_id})"

    def set_phrase(self, raw_phrase: str) -> None:
        """Hash and store a raw mnemonic phrase."""
        self.phrase_hash = make_password(raw_phrase)

    def check_phrase(self, raw_phrase: str) -> bool:
        """Return True if `raw_phrase` matches the stored hash."""
        return check_password(raw_phrase, self.phrase_hash)
