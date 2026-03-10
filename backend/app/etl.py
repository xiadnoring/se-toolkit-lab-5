"""ETL pipeline: fetch data from the autochecker API and load it into the database.

The autochecker dashboard API provides two endpoints:
- GET /api/items — lab/task catalog
- GET /api/logs  — anonymized check results (supports ?since= and ?limit= params)

Both require HTTP Basic Auth (email + password from settings).
"""

from datetime import datetime, timezone

import httpx
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select
from sqlalchemy import func

from app.settings import settings
from app.models.item import ItemRecord
from app.models.learner import Learner
from app.models.interaction import InteractionLog


# ---------------------------------------------------------------------------
# Extract — fetch data from the autochecker API
# ---------------------------------------------------------------------------


async def fetch_items() -> list[dict]:
    """Fetch the lab/task catalog from the autochecker API."""
    url = f"{settings.autochecker_api_url.rstrip('/')}/api/items"
    auth = (settings.autochecker_email, settings.autochecker_password)
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, auth=auth, timeout=30.0)
    if resp.status_code != 200:
        raise RuntimeError(
            f"fetch_items: unexpected status {resp.status_code}: {resp.text}"
        )
    data = resp.json()
    if not isinstance(data, list):
        raise RuntimeError("fetch_items: expected JSON array")
    return data


async def fetch_logs(since: datetime | None = None) -> list[dict]:
    """Fetch check results from the autochecker API.

    - Uses httpx.AsyncClient to GET logs with HTTP Basic Auth
    - Handles pagination with has_more and limit=500
    - Uses submitted_at of last log as new since value
    - Returns combined list of all log dicts
    """
    base_url = f"{settings.autochecker_api_url.rstrip('/')}/api/logs"
    auth = (settings.autochecker_email, settings.autochecker_password)
    params = {"limit": 500}
    if since is not None:
        # send ISO timestamp (naive UTC assumed)
        params["since"] = since.replace(tzinfo=timezone.utc).isoformat()
    all_logs: list[dict] = []
    async with httpx.AsyncClient() as client:
        while True:
            resp = await client.get(base_url, auth=auth, params=params, timeout=60.0)
            if resp.status_code != 200:
                raise RuntimeError(
                    f"fetch_logs: unexpected status {resp.status_code}: {resp.text}"
                )
            payload = resp.json()
            logs = payload.get("logs", [])
            if not isinstance(logs, list):
                raise RuntimeError("fetch_logs: invalid response shape")
            all_logs.extend(logs)
            has_more = payload.get("has_more", False)
            if not has_more or not logs:
                break
            last = logs[-1].get("submitted_at")
            if not last:
                break
            params["since"] = last.replace("Z", "+00:00")
    return all_logs


# ---------------------------------------------------------------------------
# Load — insert fetched data into the local database
# ---------------------------------------------------------------------------


async def load_items(items: list[dict], session: AsyncSession) -> int:
    """Load items (labs and tasks) into the database."""
    created = 0
    lab_map: dict[str, ItemRecord] = {}

    # Process labs first
    for it in filter(lambda i: i.get("type") == "lab", items):
        lab_short = it.get("lab")
        lab_title = it.get("title")
        if not lab_short or not lab_title:
            continue
        q = select(ItemRecord).where(
            ItemRecord.type == "lab", ItemRecord.title == lab_title
        )
        res = await session.exec(q)
        existing = res.first()
        if existing is None:
            new = ItemRecord(
                type="lab",
                parent_id=None,
                title=lab_title,
                description=it.get("description") or "",
            )
            session.add(new)
            await session.commit()
            await session.refresh(new)
            lab_map[lab_short] = new
            created += 1
        else:
            lab_map[lab_short] = existing

    # Process tasks
    for it in filter(lambda i: i.get("type") == "task", items):
        lab_short = it.get("lab")
        task_title = it.get("title")
        if not task_title or not lab_short:
            continue
        parent = lab_map.get(lab_short)
        parent_id = parent.id if parent else None
        q = select(ItemRecord).where(
            ItemRecord.type == "task",
            ItemRecord.title == task_title,
            ItemRecord.parent_id == parent_id,
        )
        res = await session.exec(q)
        existing = res.first()
        if existing is None:
            new = ItemRecord(
                type="task",
                parent_id=parent_id,
                title=task_title,
                description=it.get("description") or "",
            )
            session.add(new)
            created += 1

    if created:
        await session.commit()
    return created


async def load_logs(
    logs: list[dict], items_catalog: list[dict], session: AsyncSession
) -> int:
    """Load interaction logs into the database."""
    # Build lookup from (lab_short, task_short) -> title
    lookup: dict[tuple[str, str | None], str] = {}
    for it in items_catalog:
        lab = it.get("lab")
        task = it.get("task")  # may be None
        title = it.get("title")
        if lab and title:
            lookup[(lab, task)] = title

    created = 0
    for log in logs:
        ext_id = log.get("id")
        student_id = log.get("student_id")
        if ext_id is None or student_id is None:
            continue
        group = log.get("group", "") or ""
        lab_short = log.get("lab")
        task_short = log.get("task")  # may be None

        # Find or create learner
        ql = select(Learner).where(Learner.external_id == student_id)
        res_l = await session.exec(ql)
        learner = res_l.first()
        if learner is None:
            learner = Learner(external_id=student_id, student_group=group)
            session.add(learner)
            await session.commit()
            await session.refresh(learner)

        # Find matching item by title (using catalog lookup)
        title = lookup.get((lab_short, task_short)) or lookup.get((lab_short, None))
        if not title:
            continue
        qi = select(ItemRecord).where(ItemRecord.title == title)
        res_i = await session.exec(qi)
        item = res_i.first()
        if item is None:
            continue

        # Skip if interaction with this external_id already exists
        qexists = select(InteractionLog).where(InteractionLog.external_id == ext_id)
        rex = await session.exec(qexists)
        if rex.first() is not None:
            continue

        # Parse created_at
        submitted_at = log.get("submitted_at")
        created_at = None
        if submitted_at:
            try:
                created_at = (
                    datetime.fromisoformat(submitted_at.replace("Z", "+00:00"))
                    .astimezone(timezone.utc)
                    .replace(tzinfo=None)
                )
            except Exception:
                created_at = None

        interaction = InteractionLog(
            external_id=ext_id,
            learner_id=learner.id,
            item_id=item.id,
            kind="attempt",
            score=log.get("score"),
            checks_passed=log.get("passed"),
            checks_total=log.get("total"),
            created_at=created_at
            if created_at is not None
            else datetime.now(timezone.utc).replace(tzinfo=None),
        )
        session.add(interaction)
        created += 1

    if created:
        await session.commit()
    return created


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


async def sync(session: AsyncSession) -> dict:
    """Run the full ETL pipeline."""
    # Step 1: fetch and load items
    items = await fetch_items()
    await load_items(items, session)

    # Step 2: determine last synced timestamp
    qlast = select(InteractionLog).order_by(InteractionLog.created_at.desc()).limit(1)
    res = await session.exec(qlast)
    last = res.first()
    since = last.created_at if last is not None else None

    # Step 3: fetch logs since last and load them
    logs = await fetch_logs(since)
    new_records = await load_logs(logs, items, session)

    # Total interactions count
    qtotal = select(func.count(InteractionLog.id))
    total_res = await session.exec(qtotal)
    row = total_res.first()
    total = row if row is not None else 0

    return {"new_records": new_records, "total_records": total}
