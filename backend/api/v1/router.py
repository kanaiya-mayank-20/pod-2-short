"""All v1 endpoints, mounted once in ``main.py``."""

from fastapi import APIRouter

from api.v1.endpoints import auth, styles, titles, uploads

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(uploads.router)
api_router.include_router(styles.router)
api_router.include_router(titles.router)
