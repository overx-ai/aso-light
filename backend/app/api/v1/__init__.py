from fastapi import APIRouter

from app.api.v1.apps import router as apps_router
from app.api.v1.auth import router as auth_router
from app.api.v1.availability import router as availability_router
from app.api.v1.credentials import router as credentials_router
from app.api.v1.export import router as export_router
from app.api.v1.indices import router as indices_router
from app.api.v1.keywords import router as keywords_router
from app.api.v1.presets import router as presets_router
from app.api.v1.pricing import router as pricing_router
from app.api.v1.territories import router as territories_router

router = APIRouter()

router.include_router(auth_router, prefix="/auth", tags=["auth"])
router.include_router(credentials_router, prefix="/credentials", tags=["credentials"])
router.include_router(apps_router, prefix="/apps", tags=["apps"])
router.include_router(pricing_router, prefix="/apps", tags=["pricing"])
router.include_router(availability_router, prefix="/apps", tags=["availability"])
router.include_router(keywords_router, tags=["keywords"])
router.include_router(territories_router, prefix="/territories", tags=["territories"])
router.include_router(indices_router, prefix="/indices", tags=["indices"])
router.include_router(presets_router, prefix="/presets", tags=["presets"])
router.include_router(export_router, prefix="/prices", tags=["export"])
