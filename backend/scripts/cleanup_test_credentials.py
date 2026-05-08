"""One-shot cleanup of test-fixture ASC credentials accidentally written to the dev DB.

Origin: ``backend/tests/test_asa_joins.py`` (pre-isolation) inserted credentials
with ``name='asc'``, ``issuer_id='i'``, ``key_id='k'`` and a placeholder
``encrypt_value("k")`` private key. Every test run added another row, ending
with ~38 unusable credentials in the dev SQLite DB. A few additional outliers
(``name LIKE 'asc-%' AND key_id='KEY'``) appear to be one-off scripted inserts.

Real credentials (``name='aso'``, ``key_id='K2SKYUVANC'`` etc.) and the user's
own dev fixture (``OVERMIND ASC (fixture)``) are preserved — the user can
delete + re-upload that one via the UI.

Run:
    cd backend && uv run python -m scripts.cleanup_test_credentials --apply

Without ``--apply`` the script prints what would be deleted and exits.
"""
from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import and_, or_, select

from app.db.session import async_session_factory
from app.models.app import App
from app.models.credential import ASCCredential


_TEST_FIXTURE_FILTER = or_(
    and_(
        ASCCredential.name == "asc",
        ASCCredential.issuer_id == "i",
        ASCCredential.key_id == "k",
    ),
    and_(
        ASCCredential.name.like("asc-%"),
        ASCCredential.key_id == "KEY",
    ),
)


async def _run(apply: bool) -> int:
    async with async_session_factory() as session:
        creds = (
            await session.execute(
                select(ASCCredential).where(_TEST_FIXTURE_FILTER)
            )
        ).scalars().all()

        if not creds:
            print("No test-fixture credentials found. Nothing to do.")
            return 0

        cred_ids = [c.id for c in creds]
        cascaded_apps = (
            await session.execute(
                select(App).where(App.credential_id.in_(cred_ids))
            )
        ).scalars().all()

        print(f"Will delete {len(creds)} credential(s):")
        for c in creds:
            print(f"  id={c.id} user_id={c.user_id} name={c.name!r} key_id={c.key_id!r}")
        print(f"Cascade-deleting {len(cascaded_apps)} associated app row(s).")

        if not apply:
            print("\nDry run — re-run with --apply to actually delete.")
            return 0

        for c in creds:
            await session.delete(c)
        await session.commit()
        print(f"Deleted {len(creds)} credential(s) and cascaded their apps.")
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="actually delete")
    args = parser.parse_args()
    return asyncio.run(_run(apply=args.apply))


if __name__ == "__main__":
    sys.exit(main())
