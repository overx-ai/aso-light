import logging
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from sqlalchemy import inspect as sa_inspect

from app.core.security import decrypt_value, encrypt_value, get_current_user
from app.db.session import get_session
from app.models.credential import ASCCredential
from app.schemas.credential import CredentialResponse, CredentialTestResponse

logger = logging.getLogger(__name__)
router = APIRouter()


def _credential_to_response(credential: ASCCredential) -> CredentialResponse:
    return CredentialResponse(
        id=credential.id,
        name=credential.name,
        issuer_id=credential.issuer_id,
        key_id=credential.key_id,
        created_at=credential.created_at,
        apps_count=len(credential.apps) if "apps" not in sa_inspect(credential).unloaded else 0,
    )


@router.post("", response_model=CredentialResponse, status_code=status.HTTP_201_CREATED)
async def create_credential(
    name: str = Form(...),
    issuer_id: str = Form(...),
    key_id: str = Form(...),
    private_key_file: UploadFile = File(...),
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> CredentialResponse:
    content = await private_key_file.read()
    private_key_text = content.decode("utf-8").strip()

    if not private_key_text.startswith("-----BEGIN PRIVATE KEY-----"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid .p8 file: must be a PEM-encoded private key",
        )

    encrypted_key = encrypt_value(private_key_text)
    user_id = int(current_user["user_id"])

    credential = ASCCredential(
        user_id=user_id,
        name=name,
        issuer_id=issuer_id,
        key_id=key_id,
        private_key_encrypted=encrypted_key,
    )
    session.add(credential)
    await session.flush()

    logger.info("Credential created: id=%s user_id=%s", credential.id, user_id)
    return _credential_to_response(credential)


@router.get("", response_model=list[CredentialResponse])
async def list_credentials(
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[CredentialResponse]:
    user_id = int(current_user["user_id"])
    result = await session.execute(
        select(ASCCredential).where(ASCCredential.user_id == user_id)
    )
    credentials = result.scalars().all()
    return [_credential_to_response(c) for c in credentials]


@router.delete("/{credential_id}", status_code=status.HTTP_200_OK)
async def delete_credential(
    credential_id: int,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    user_id = int(current_user["user_id"])
    result = await session.execute(
        select(ASCCredential).where(ASCCredential.id == credential_id)
    )
    credential = result.scalar_one_or_none()
    if credential is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Credential not found",
        )
    if credential.user_id != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to delete this credential",
        )

    await session.delete(credential)
    logger.info("Credential deleted: id=%s user_id=%s", credential_id, user_id)
    return {"detail": "Credential deleted successfully"}


@router.post("/{credential_id}/test", response_model=CredentialTestResponse)
async def test_credential(
    credential_id: int,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> CredentialTestResponse:
    user_id = int(current_user["user_id"])
    result = await session.execute(
        select(ASCCredential).where(ASCCredential.id == credential_id)
    )
    credential = result.scalar_one_or_none()
    if credential is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Credential not found",
        )
    if credential.user_id != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to access this credential",
        )

    try:
        from app.services.asc.client import ASCClient
        from app.services.asc.apps import ASCAppsService
        from app.services.asc.errors import ASCAPIError

        async with ASCClient.from_credential(credential) as client:
            apps_service = ASCAppsService(client)
            apps_data = await apps_service.list_apps()
            return CredentialTestResponse(
                success=True,
                message=f"Connected successfully. Found {len(apps_data)} app(s).",
                apps_count=len(apps_data),
            )
    except ASCAPIError as exc:
        logger.warning("ASC API error testing credential id=%s: %s", credential_id, exc.message)
        return CredentialTestResponse(
            success=False,
            message=f"ASC API error: {exc.message}",
        )
    except Exception:
        logger.exception("Failed to test credential id=%s", credential_id)
        return CredentialTestResponse(
            success=False,
            message="Failed to connect to App Store Connect. Check your credentials.",
        )
