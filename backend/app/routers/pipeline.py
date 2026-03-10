"""Router for the ETL pipeline endpoint."""

from fastapi import APIRouter, Depends
from sqlmodel.ext.asyncio.session import AsyncSession

from app.database import get_session
from app.etl import sync

router = APIRouter()


@router.post("/sync")
async def post_sync(session: AsyncSession = Depends(get_session)):
    """Trigger a data sync from the autochecker API.

    Fetches the latest items and logs, loads them into the database,
    and returns a summary of what was synced.
    """
    return await sync(session)
1