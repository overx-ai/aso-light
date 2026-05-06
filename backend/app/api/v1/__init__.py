from fastapi import APIRouter

from app.api.v1.apps import router as apps_router
from app.api.v1.aso_check import router as aso_check_router
from app.api.v1.auth import router as auth_router
from app.api.v1.availability import router as availability_router
from app.api.v1.clash import router as clash_router
from app.api.v1.clone import router as clone_router
from app.api.v1.credentials import router as credentials_router
from app.api.v1.export import router as export_router
from app.api.v1.indices import router as indices_router
from app.api.v1.keywords import router as keywords_router
from app.api.v1.metadata import (
    keywords_extra_router as metadata_keywords_router,
    router as metadata_router,
)
from app.api.v1.presets import router as presets_router
from app.api.v1.pricing import router as pricing_router
from app.api.v1.revenuecat import router as revenuecat_router
from app.api.v1.reviews import router as reviews_router
from app.api.v1.territories import router as territories_router
from app.api.v1.visibility import router as visibility_router

router = APIRouter()

router.include_router(auth_router, prefix="/auth", tags=["auth"])
router.include_router(credentials_router, prefix="/credentials", tags=["credentials"])
router.include_router(apps_router, prefix="/apps", tags=["apps"])
router.include_router(pricing_router, prefix="/apps", tags=["pricing"])
router.include_router(clone_router, prefix="/apps", tags=["clone"])
router.include_router(revenuecat_router, prefix="/apps", tags=["revenuecat"])
router.include_router(availability_router, prefix="/apps", tags=["availability"])
router.include_router(metadata_router, prefix="/apps", tags=["metadata"])
router.include_router(reviews_router, prefix="/apps", tags=["reviews"])
router.include_router(visibility_router, prefix="/apps", tags=["visibility"])
router.include_router(aso_check_router, prefix="/apps", tags=["aso-check"])
router.include_router(clash_router, prefix="/apps", tags=["clash"])
router.include_router(keywords_router, tags=["keywords"])
router.include_router(metadata_keywords_router, prefix="/keywords", tags=["keywords"])
router.include_router(territories_router, prefix="/territories", tags=["territories"])
router.include_router(indices_router, prefix="/indices", tags=["indices"])
router.include_router(presets_router, prefix="/presets", tags=["presets"])
router.include_router(export_router, prefix="/prices", tags=["export"])
