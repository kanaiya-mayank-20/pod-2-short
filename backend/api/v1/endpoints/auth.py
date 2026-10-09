"""Authentication endpoints: register and log in."""

from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.security import OAuth2PasswordRequestForm

from api.deps import get_auth_service
from schemas.token import Token
from schemas.user import UserCreate, UserRead
from services.auth import DEFAULT_DEVICE_ID, AuthService

router = APIRouter(prefix="/auth", tags=["Auth"])


@router.post("/register", response_model=UserRead, status_code=status.HTTP_201_CREATED)
async def register(
    data: UserCreate,
    service: Annotated[AuthService, Depends(get_auth_service)],
) -> UserRead:
    user = await service.register(data)
    return UserRead.model_validate(user)


@router.post("/login", response_model=Token)
async def login(
    request: Request,
    form: Annotated[OAuth2PasswordRequestForm, Depends()],
    service: Annotated[AuthService, Depends(get_auth_service)],
    device_id: Annotated[str, Form(max_length=128, min_length=1)] = DEFAULT_DEVICE_ID,
) -> Token:
    """Log in with email + password (form encoded, per OAuth2).

    ``device_id`` names the device the refresh token is stored for; logging in again
    on the same device replaces the previous token.
    """
    return await service.login(
        email=form.username,
        password=form.password,
        device_id=device_id,
        ip_address=request.client.host if request.client else None,
    )
