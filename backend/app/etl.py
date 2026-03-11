"""ETL pipeline: fetch data from the autochecker API and load it into the database.

The autochecker dashboard API provides two endpoints:
- GET /api/items — lab/task catalog
- GET /api/logs  — anonymized check results (supports ?since= and ?limit= params)

Both require HTTP Basic Auth (email + password from settings).
"""

from datetime import datetime
from typing import Any

import httpx
from sqlmodel.ext.asyncio.session import AsyncSession

from app.settings import settings


# ---------------------------------------------------------------------------
# Extract — fetch data from the autochecker API
# ---------------------------------------------------------------------------


async def fetch_items() -> list[dict[str, Any]]:
    """Fetch the lab/task catalog from the autochecker API.

    - Use httpx.AsyncClient to GET {settings.autochecker_api_url}/api/items
    - Pass HTTP Basic Auth using settings.autochecker_email and
      settings.autochecker_password
    - The response is a JSON array of objects with keys:
      lab (str), task (str | null), title (str), type ("lab" | "task")
    - Return the parsed list of dicts
    - Raise an exception if the response status is not 200
    """
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{settings.autochecker_api_url}/api/items",
            auth=(settings.autochecker_email, settings.autochecker_password),
        )
        response.raise_for_status()
        return response.json()  # type: ignore[no-any-return]


async def fetch_logs(since: datetime | None = None) -> list[dict[str, Any]]:
    """Fetch check results from the autochecker API.

    - Use httpx.AsyncClient to GET {settings.autochecker_api_url}/api/logs
    - Pass HTTP Basic Auth using settings.autochecker_email and
      settings.autochecker_password
    - Query parameters:
      - limit=500 (fetch in batches)
      - since={iso timestamp} if provided (for incremental sync)
    - The response JSON has shape:
      {"logs": [...], "count": int, "has_more": bool}
    - Handle pagination: keep fetching while has_more is True
      - Use the submitted_at of the last log as the new "since" value
    - Return the combined list of all log dicts from all pages
    """
    all_logs: list[dict[str, Any]] = []
    current_since = since

    async with httpx.AsyncClient() as client:
        while True:
            params: dict[str, str | int] = {"limit": 500}
            if current_since is not None:
                params["since"] = current_since.isoformat()

            response = await client.get(
                f"{settings.autochecker_api_url}/api/logs",
                params=params,
                auth=(settings.autochecker_email, settings.autochecker_password),
            )
            response.raise_for_status()
            data = response.json()

            logs = data["logs"]
            all_logs.extend(logs)

            if not data["has_more"]:
                break

            # Use the submitted_at of the last log as the new "since" value
            current_since = datetime.fromisoformat(logs[-1]["submitted_at"])

        return all_logs


# ---------------------------------------------------------------------------
# Load — insert fetched data into the local database
# ---------------------------------------------------------------------------


async def load_items(
    items: list[dict[str, Any]], session: AsyncSession
) -> int:
    """Load items (labs and tasks) into the database.

    - Import ItemRecord from app.models.item
    - Process labs first (items where type="lab"):
      - For each lab, check if an item with type="lab" and matching title
        already exists (SELECT)
      - If not, INSERT a new ItemRecord(type="lab", title=lab_title)
      - Build a dict mapping the lab's short ID (the "lab" field, e.g.
        "lab-01") to the lab's database record, so you can look up
        parent IDs when processing tasks
    - Then process tasks (items where type="task"):
      - Find the parent lab item using the task's "lab" field (e.g.
        "lab-01") as the key into the dict you built above
      - Check if a task with this title and parent_id already exists
      - If not, INSERT a new ItemRecord(type="task", title=task_title,
        parent_id=lab_item.id)
    - Commit after all inserts
    - Return the number of newly created items
    """
    from app.models.item import ItemRecord
    from sqlmodel import select

    new_items_count = 0
    lab_id_to_record: dict[str, ItemRecord] = {}

    # Process labs first
    for item in items:
        if item["type"] != "lab":
            continue

        title = item["title"]
        # Check if lab already exists
        existing = await session.exec(
            select(ItemRecord).where(
                ItemRecord.type == "lab",
                ItemRecord.title == title,
            )
        )
        lab_record = existing.first()

        if lab_record is None:
            lab_record = ItemRecord(type="lab", title=title)
            session.add(lab_record)
            new_items_count += 1

        # Map the short lab ID (e.g., "lab-01") to the record
        lab_id_to_record[item["lab"]] = lab_record

    # Process tasks
    for item in items:
        if item["type"] != "task":
            continue

        title = item["title"]
        lab_short_id = item["lab"]
        parent_lab = lab_id_to_record.get(lab_short_id)

        if parent_lab is None:
            # Parent lab not found, skip this task
            continue

        # Check if task already exists
        existing = await session.exec(
            select(ItemRecord).where(
                ItemRecord.type == "task",
                ItemRecord.title == title,
                ItemRecord.parent_id == parent_lab.id,
            )
        )
        task_record = existing.first()

        if task_record is None:
            task_record = ItemRecord(type="task", title=title, parent_id=parent_lab.id)
            session.add(task_record)
            new_items_count += 1

    await session.commit()
    return new_items_count


async def load_logs(
    logs: list[dict[str, Any]],
    items_catalog: list[dict[str, Any]],
    session: AsyncSession,
) -> int:
    """Load interaction logs into the database.

    Args:
        logs: Raw log dicts from the API (each has lab, task, student_id, etc.)
        items_catalog: Raw item dicts from fetch_items() — needed to map
            short IDs (e.g. "lab-01", "setup") to item titles stored in the DB.
        session: Database session.

    - Import Learner from app.models.learner
    - Import InteractionLog from app.models.interaction
    - Import ItemRecord from app.models.item
    - Build a lookup from (lab_short_id, task_short_id) to item title
      using items_catalog. For labs, the key is (lab, None). For tasks,
      the key is (lab, task). The value is the item's title.
    - For each log dict:
      1. Find or create a Learner by external_id (log["student_id"])
         - If creating, set student_group from log["group"]
      2. Find the matching item in the database:
         - Use the lookup to get the title for (log["lab"], log["task"])
         - Query the DB for an ItemRecord with that title
         - Skip this log if no matching item is found
      3. Check if an InteractionLog with this external_id already exists
         (for idempotent upsert — skip if it does)
      4. Create InteractionLog with:
         - external_id = log["id"]
         - learner_id = learner.id
         - item_id = item.id
         - kind = "attempt"
         - score = log["score"]
         - checks_passed = log["passed"]
         - checks_total = log["total"]
         - created_at = parsed log["submitted_at"]
    - Commit after all inserts
    - Return the number of newly created interactions
    """
    from app.models.interaction import InteractionLog
    from app.models.item import ItemRecord
    from app.models.learner import Learner
    from sqlmodel import select

    # Build lookup: (lab_short_id, task_short_id) -> item title
    item_title_lookup: dict[tuple[str, str | None], str] = {}
    for item in items_catalog:
        key = (item["lab"], item.get("task"))
        item_title_lookup[key] = item["title"]

    new_interactions_count = 0

    for log in logs:
        # 1. Find or create Learner
        student_id = log["student_id"]
        learner = await session.exec(
            select(Learner).where(Learner.external_id == student_id)
        )
        learner_record = learner.first()

        if learner_record is None:
            learner_record = Learner(
                external_id=student_id,
                student_group=log.get("group", ""),
            )
            session.add(learner_record)
            # Flush to get the ID for the interaction log
            await session.flush()

        # 2. Find the matching item
        lab_short_id = log["lab"]
        task_short_id = log.get("task")
        item_title = item_title_lookup.get((lab_short_id, task_short_id))

        if item_title is None:
            # No matching item found, skip this log
            continue

        item_record = await session.exec(
            select(ItemRecord).where(ItemRecord.title == item_title)
        )
        item = item_record.first()

        if item is None:
            # Item not in database, skip this log
            continue

        # 3. Check if InteractionLog already exists (idempotent upsert)
        external_id = log["id"]
        existing_interaction = await session.exec(
            select(InteractionLog).where(InteractionLog.external_id == external_id)
        )
        if existing_interaction.first() is not None:
            # Already exists, skip
            continue

        # 4. Create InteractionLog
        interaction = InteractionLog(
            external_id=external_id,
            learner_id=learner_record.id,  # type: ignore[arg-type]
            item_id=item.id,  # type: ignore[arg-type]
            kind="attempt",
            score=log.get("score"),
            checks_passed=log.get("passed"),
            checks_total=log.get("total"),
            created_at=datetime.fromisoformat(log["submitted_at"]),
        )
        session.add(interaction)
        new_interactions_count += 1

    await session.commit()
    return new_interactions_count


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


async def sync(session: AsyncSession) -> dict[str, int]:
    """Run the full ETL pipeline.

    - Step 1: Fetch items from the API (keep the raw list) and load them
      into the database
    - Step 2: Determine the last synced timestamp
      - Query the most recent created_at from InteractionLog
      - If no records exist, since=None (fetch everything)
    - Step 3: Fetch logs since that timestamp and load them
      - Pass the raw items list to load_logs so it can map short IDs
        to titles
    - Return a dict: {"new_records": <number of new interactions>,
                      "total_records": <total interactions in DB>}
    """
    from app.models.interaction import InteractionLog
    from sqlmodel import desc, select

    # Step 1: Fetch and load items
    items = await fetch_items()
    await load_items(items, session)

    # Step 2: Determine the last synced timestamp
    last_interaction = await session.exec(
        select(InteractionLog).order_by(desc(InteractionLog.created_at)).limit(1)
    )
    last_record = last_interaction.first()
    since = last_record.created_at if last_record else None

    # Step 3: Fetch and load logs
    logs = await fetch_logs(since=since)
    new_records = await load_logs(logs, items, session)

    # Get total records count
    total_count = await session.exec(select(InteractionLog))
    total_records = len(total_count.all())

    return {"new_records": new_records, "total_records": total_records}
