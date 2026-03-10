"""Router for analytics endpoints.

Each endpoint performs SQL aggregation queries on the interaction data
populated by the ETL pipeline. All endpoints require a `lab` query
parameter to filter results by lab (e.g., "lab-01").
"""

from fastapi import APIRouter, Depends, Query
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select
from sqlalchemy import func, case, distinct

from app.database import get_session
from app.models.item import ItemRecord
from app.models.interaction import InteractionLog
from app.models.learner import Learner

router = APIRouter()


@router.get("/scores")
async def get_scores(
    lab: str = Query(..., description="Lab identifier, e.g. 'lab-01'"),
    session: AsyncSession = Depends(get_session),
):
    """
    Score distribution histogram for a given lab.
    """
    # Find lab by title containing lab code (e.g. "Lab 04" for "lab-04")
    lab_num = lab.split("-")[-1]
    lab_title_like = f"Lab {lab_num}"
    lab_stmt = select(ItemRecord).where(
        ItemRecord.type == "lab", ItemRecord.title.contains(lab_title_like)
    )
    lab_res = await session.exec(lab_stmt)
    lab_row = lab_res.first()
    if not lab_row:
        # Always return all buckets, even if lab not found
        return [
            {"bucket": "0-25", "count": 0},
            {"bucket": "26-50", "count": 0},
            {"bucket": "51-75", "count": 0},
            {"bucket": "76-100", "count": 0},
        ]
    # Find all tasks for this lab
    task_stmt = select(ItemRecord.id).where(
        ItemRecord.type == "task", ItemRecord.parent_id == lab_row.id
    )
    task_ids = (await session.exec(task_stmt)).all()
    if not task_ids:
        return [
            {"bucket": "0-25", "count": 0},
            {"bucket": "26-50", "count": 0},
            {"bucket": "51-75", "count": 0},
            {"bucket": "76-100", "count": 0},
        ]
    # Build bucket cases
    bucket_case = case(
        (InteractionLog.score <= 25, "0-25"),
        (InteractionLog.score <= 50, "26-50"),
        (InteractionLog.score <= 75, "51-75"),
        (InteractionLog.score <= 100, "76-100"),
        else_="other",
    )
    stmt = (
        select(bucket_case.label("bucket"), func.count())
        .where(InteractionLog.item_id.in_(task_ids), InteractionLog.score != None)
        .group_by("bucket")
    )
    res = await session.exec(stmt)
    rows = res.all()
    # Always return all four buckets, even if count is 0
    bucket_map = {row[0]: row[1] for row in rows}
    return [
        {"bucket": "0-25", "count": bucket_map.get("0-25", 0)},
        {"bucket": "26-50", "count": bucket_map.get("26-50", 0)},
        {"bucket": "51-75", "count": bucket_map.get("51-75", 0)},
        {"bucket": "76-100", "count": bucket_map.get("76-100", 0)},
    ]


@router.get("/pass-rates")
async def get_pass_rates(
    lab: str = Query(..., description="Lab identifier, e.g. 'lab-01'"),
    session: AsyncSession = Depends(get_session),
):
    """
    Per-task pass rates for a given lab.
    """
    lab_num = lab.split("-")[-1]
    lab_title_like = f"Lab {lab_num}"
    lab_stmt = select(ItemRecord).where(
        ItemRecord.type == "lab", ItemRecord.title.contains(lab_title_like)
    )
    lab_res = await session.exec(lab_stmt)
    lab_row = lab_res.first()
    if not lab_row:
        return []
    # Find all tasks for this lab
    task_stmt = select(ItemRecord.id, ItemRecord.title).where(
        ItemRecord.type == "task", ItemRecord.parent_id == lab_row.id
    )
    task_rows = (await session.exec(task_stmt)).all()
    if not task_rows:
        return []
    task_id_to_title = {row[0]: row[1] for row in task_rows}
    stmt = (
        select(
            InteractionLog.item_id,
            func.avg(InteractionLog.score).label("avg_score"),
            func.count().label("attempts"),
        )
        .where(
            InteractionLog.item_id.in_(task_id_to_title.keys()),
            InteractionLog.score != None,
        )
        .group_by(InteractionLog.item_id)
    )
    res = await session.exec(stmt)
    rows = res.all()
    stats = {
        row[0]: {
            "avg_score": round(row[1], 1) if row[1] is not None else 0.0,
            "attempts": row[2],
        }
        for row in rows
    }
    # Always return all tasks, even if no attempts
    result = []
    for task_id, title in sorted(task_id_to_title.items(), key=lambda x: x[1]):
        stat = stats.get(task_id, {"avg_score": 0.0, "attempts": 0})
        result.append(
            {
                "task": title,
                "avg_score": stat["avg_score"],
                "attempts": stat["attempts"],
            }
        )
    return result


@router.get("/timeline")
async def get_timeline(
    lab: str = Query(..., description="Lab identifier, e.g. 'lab-01'"),
    session: AsyncSession = Depends(get_session),
):
    """
    Submissions per day for a given lab.
    """
    lab_num = lab.split("-")[-1]
    lab_title_like = f"Lab {lab_num}"
    lab_stmt = select(ItemRecord).where(
        ItemRecord.type == "lab", ItemRecord.title.contains(lab_title_like)
    )
    lab_res = await session.exec(lab_stmt)
    lab_row = lab_res.first()
    if not lab_row:
        return []
    # Find all tasks for this lab
    task_stmt = select(ItemRecord.id).where(
        ItemRecord.type == "task", ItemRecord.parent_id == lab_row.id
    )
    task_ids = (await session.exec(task_stmt)).all()
    if not task_ids:
        return []
    # Group by date
    stmt = (
        select(
            func.date(InteractionLog.created_at).label("date"),
            func.count().label("submissions"),
        )
        .where(InteractionLog.item_id.in_(task_ids))
        .group_by("date")
        .order_by("date")
    )
    res = await session.exec(stmt)
    rows = res.all()
    return [{"date": str(row[0]), "submissions": row[1]} for row in rows]


@router.get("/groups")
async def get_groups(
    lab: str = Query(..., description="Lab identifier, e.g. 'lab-01'"),
    session: AsyncSession = Depends(get_session),
):
    """
    Per-group performance for a given lab.
    """
    lab_num = lab.split("-")[-1]
    lab_title_like = f"Lab {lab_num}"
    lab_stmt = select(ItemRecord).where(
        ItemRecord.type == "lab", ItemRecord.title.contains(lab_title_like)
    )
    lab_res = await session.exec(lab_stmt)
    lab_row = lab_res.first()
    if not lab_row:
        return []
    # Find all tasks for this lab
    task_stmt = select(ItemRecord.id).where(
        ItemRecord.type == "task", ItemRecord.parent_id == lab_row.id
    )
    task_ids = (await session.exec(task_stmt)).all()
    if not task_ids:
        return []
    # Join interactions with learners, group by group
    stmt = (
        select(
            Learner.student_group.label("group"),
            func.avg(InteractionLog.score).label("avg_score"),
            func.count(distinct(InteractionLog.learner_id)).label("students"),
        )
        .join(Learner, Learner.id == InteractionLog.learner_id)
        .where(InteractionLog.item_id.in_(task_ids), InteractionLog.score != None)
        .group_by(Learner.student_group)
        .order_by(Learner.student_group)
    )
    res = await session.exec(stmt)
    rows = res.all()
    return [
        {
            "group": row[0],
            "avg_score": round(row[1], 1) if row[1] is not None else 0.0,
            "students": row[2],
        }
        for row in rows
    ]
