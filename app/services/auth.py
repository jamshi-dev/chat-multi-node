"""Auth use-cases: signup, login, refresh. Sits between routes and the repo/security."""

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.repositories.user import UserRepository
from app.schemas.auth import TokenPair
from app.security import (
    TokenError,
    create_token,
    decode_token,
    hash_password,
    verify_password,
)


class AuthError(Exception):
    """Domain error for auth failures (bad credentials, duplicate email, bad token)."""


def _issue_pair(user: User) -> TokenPair:
    return TokenPair(
        access_token=create_token(user.id, "access"),
        refresh_token=create_token(user.id, "refresh"),
    )


class AuthService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.users = UserRepository(session)

    async def signup(self, email: str, password: str) -> tuple[User, TokenPair]:
        if await self.users.get_by_email(email):
            raise AuthError("email already registered")
        try:
            user = await self.users.create(email, hash_password(password))
            await self.session.flush()
        except IntegrityError as exc:  # race on the unique index
            raise AuthError("email already registered") from exc
        return user, _issue_pair(user)

    async def login(self, email: str, password: str) -> tuple[User, TokenPair]:
        user = await self.users.get_by_email(email)
        # Verify even when the user is missing would be ideal to avoid timing oracles;
        # argon2 is slow enough that we instead keep the message generic.
        if user is None or not verify_password(password, user.password_hash):
            raise AuthError("invalid email or password")
        return user, _issue_pair(user)

    async def refresh(self, refresh_token: str) -> TokenPair:
        try:
            user_id = decode_token(refresh_token, "refresh")
        except TokenError as exc:
            raise AuthError(str(exc)) from exc
        user = await self.users.get_by_id(user_id)
        if user is None:
            raise AuthError("user no longer exists")
        return _issue_pair(user)
