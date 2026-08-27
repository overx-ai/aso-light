"""Syncing an app under a second credential must REBIND it, not duplicate it.

The upsert used to key on ``(credential_id, asc_app_id)``, so pointing a second
(typically narrower) App Store Connect key at the same app inserted a second
``App`` row. The app then appeared twice, and keyword tracking, competitors and
the IAP cache forked across the two rows and never merged. Migrating to a
least-privilege key is exactly the workflow that triggered it.
"""
from __future__ import annotations

import uuid

from app.core.security import encrypt_value, hash_password
from app.db.base import Base
from app.db.session import async_session_factory, engine
from app.models.app import App
from app.models.credential import ASCCredential
from app.models.user import User
from app.services.asc.apps import sync_apps_for_credentials
from sqlalchemy import select
from tests._async_harness import run_async

ASC_APP_ID = "6759679041"


class _FakeAppsService:
    """Stands in for ASCAppsService — returns one app, as ASC would."""

    def __init__(self, client):  # noqa: D107 - signature parity only
        pass

    async def list_apps(self):
        return [{
            "id": ASC_APP_ID,
            "attributes": {"name": "Refresher", "bundleId": "ai.overx.refresher",
                           "platform": "IOS"},
        }]


class _FakeClient:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    @classmethod
    def from_credential(cls, cred):
        return cls()


def _patch(monkeypatch):
    import app.services.asc.apps as mod
    import app.services.asc.client as client_mod
    monkeypatch.setattr(mod, "ASCAppsService", _FakeAppsService)
    monkeypatch.setattr(client_mod, "ASCClient", _FakeClient)


async def _seed_two_credentials(session) -> tuple[ASCCredential, ASCCredential]:
    """One user holding two ASC credentials — the least-privilege migration."""
    suffix = uuid.uuid4().hex[:8]
    user = User(email=f"rebind-{suffix}@x.x", name="Rebind Test",
                password_hash=hash_password("xxxxxxxx"))
    session.add(user)
    await session.flush()

    creds = []
    for name in ("broad-build-key", "narrow-key"):
        cred = ASCCredential(user_id=user.id, name=name, issuer_id=f"iss-{suffix}",
                             key_id=f"{name}-{suffix}",
                             private_key_encrypted=encrypt_value("k"))
        session.add(cred)
        creds.append(cred)
    await session.flush()
    return creds[0], creds[1]


def test_second_credential_rebinds_instead_of_duplicating(monkeypatch):
    _patch(monkeypatch)

    async def go():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async with async_session_factory() as session:
            broad, narrow = await _seed_two_credentials(session)
            user_id = broad.user_id

            await sync_apps_for_credentials(session, [broad])
            await sync_apps_for_credentials(session, [narrow])
            await session.commit()

            rows = (await session.execute(
                select(App)
                .join(ASCCredential, App.credential_id == ASCCredential.id)
                .where(ASCCredential.user_id == user_id,
                       App.asc_app_id == ASC_APP_ID)
            )).scalars().all()
            return rows, narrow.id, broad.id

    rows, narrow_id, broad_id = run_async(go())

    assert len(rows) == 1, f"expected one App row, got {len(rows)} — duplicated"
    assert rows[0].credential_id == narrow_id, "sync did not rebind to the new key"
    assert rows[0].credential_id != broad_id


def test_resync_with_the_same_credential_is_idempotent(monkeypatch):
    _patch(monkeypatch)

    async def go():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async with async_session_factory() as session:
            broad, _ = await _seed_two_credentials(session)
            for _ in range(3):
                await sync_apps_for_credentials(session, [broad])
            await session.commit()

            return (await session.execute(
                select(App).where(App.credential_id == broad.id,
                                  App.asc_app_id == ASC_APP_ID)
            )).scalars().all()

    assert len(run_async(go())) == 1
