"""
관계(Relationship) API 라우트
엔티티 간 네트워크형 관계 관리 (Attio 스타일)
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func, or_, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.cc_web_interface.crm.database import get_db
from app.cc_web_interface.crm.models import Relationship, Contact, Company, Deal
from app.cc_web_interface.crm.schemas import (
    RelationshipCreate, RelationshipUpdate, RelationshipRead,
    PaginatedResponse, SuccessResponse, ErrorResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/relationships", tags=["관계"])


async def _resolve_name(entity_type: str, entity_id: int, db: AsyncSession) -> str:
    """엔티티 타입과 ID로 이름 조회"""
    if entity_type == "contact":
        obj = await db.get(Contact, entity_id)
        return f"{obj.first_name} {obj.last_name or ''}".strip() if obj else f"연락처 #{entity_id}"
    elif entity_type == "company":
        obj = await db.get(Company, entity_id)
        return obj.name if obj else f"회사 #{entity_id}"
    elif entity_type == "deal":
        obj = await db.get(Deal, entity_id)
        return obj.name if obj else f"딜 #{entity_id}"
    return f"{entity_type} #{entity_id}"


@router.post("", response_model=SuccessResponse)
async def create_relationship(data: RelationshipCreate, db: AsyncSession = Depends(get_db)):
    """관계 생성"""
    # 중복 체크
    existing = await db.execute(
        select(Relationship).where(
            and_(
                Relationship.from_type == data.from_type,
                Relationship.from_id == data.from_id,
                Relationship.to_type == data.to_type,
                Relationship.to_id == data.to_id,
                Relationship.relationship_type == data.relationship_type,
                Relationship.status == "active",
            )
        )
    )
    if existing.scalar_one_or_none():
        return ErrorResponse(message="이미 동일한 관계가 존재합니다.")

    rel = Relationship(
        from_type=data.from_type,
        from_id=data.from_id,
        to_type=data.to_type,
        to_id=data.to_id,
        relationship_type=data.relationship_type,
        role=data.role,
        extra_data=data.extra_data or {},
        is_primary=data.is_primary,
    )
    db.add(rel)
    await db.flush()

    result = RelationshipRead.model_validate(rel)
    result.from_name = await _resolve_name(data.from_type, data.from_id, db)
    result.to_name = await _resolve_name(data.to_type, data.to_id, db)

    return SuccessResponse(data=result)


@router.get("/entity/{entity_type}/{entity_id}", response_model=SuccessResponse)
async def get_entity_relationships(
    entity_type: str,
    entity_id: int,
    relationship_type: Optional[str] = Query(None),
    status: str = Query("active"),
    db: AsyncSession = Depends(get_db),
):
    """특정 엔티티의 모든 관계 조회 (from 또는 to)"""
    query = select(Relationship).where(
        and_(
            Relationship.status == status,
            or_(
                and_(Relationship.from_type == entity_type, Relationship.from_id == entity_id),
                and_(Relationship.to_type == entity_type, Relationship.to_id == entity_id),
            )
        )
    )
    if relationship_type:
        query = query.where(Relationship.relationship_type == relationship_type)

    result = await db.execute(query.order_by(Relationship.is_primary.desc(), Relationship.created_at.desc()))
    rels = result.scalars().all()

    items = []
    for r in rels:
        item = RelationshipRead.model_validate(r)
        item.from_name = await _resolve_name(r.from_type, r.from_id, db)
        item.to_name = await _resolve_name(r.to_type, r.to_id, db)
        items.append(item)

    return SuccessResponse(data=items)


@router.get("/network/{entity_type}/{entity_id}", response_model=SuccessResponse)
async def get_network(
    entity_type: str,
    entity_id: int,
    depth: int = Query(2, ge=1, le=3, description="탐색 깊이 (1~3)"),
    db: AsyncSession = Depends(get_db),
):
    """엔티티의 관계 네트워크 조회 (깊이 탐색)"""
    visited = set()
    nodes = []
    edges = []

    async def traverse(etype, eid, current_depth):
        key = f"{etype}:{eid}"
        if key in visited or current_depth > depth:
            return
        visited.add(key)

        name = await _resolve_name(etype, eid, db)
        nodes.append({"type": etype, "id": eid, "name": name, "depth": current_depth})

        # 이 엔티티와 연결된 관계 조회
        result = await db.execute(
            select(Relationship).where(
                and_(
                    Relationship.status == "active",
                    or_(
                        and_(Relationship.from_type == etype, Relationship.from_id == eid),
                        and_(Relationship.to_type == etype, Relationship.to_id == eid),
                    )
                )
            )
        )
        rels = result.scalars().all()

        for r in rels:
            from_name = await _resolve_name(r.from_type, r.from_id, db)
            to_name = await _resolve_name(r.to_type, r.to_id, db)
            edges.append({
                "from": {"type": r.from_type, "id": r.from_id, "name": from_name},
                "to": {"type": r.to_type, "id": r.to_id, "name": to_name},
                "relationship": r.relationship_type,
                "role": r.role,
                "is_primary": r.is_primary,
            })

            # 상대방 엔티티 탐색
            if r.from_type == etype and r.from_id == eid:
                await traverse(r.to_type, r.to_id, current_depth + 1)
            else:
                await traverse(r.from_type, r.from_id, current_depth + 1)

    await traverse(entity_type, entity_id, 0)

    return SuccessResponse(data={
        "root": {"type": entity_type, "id": entity_id},
        "nodes": nodes,
        "edges": edges,
        "total_nodes": len(nodes),
        "total_edges": len(edges),
    })


@router.put("/{relationship_id}", response_model=SuccessResponse)
async def update_relationship(
    relationship_id: int, data: RelationshipUpdate, db: AsyncSession = Depends(get_db),
):
    """관계 수정"""
    rel = await db.get(Relationship, relationship_id)
    if not rel:
        return ErrorResponse(message="관계를 찾을 수 없습니다.")

    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(rel, key, value)
    await db.flush()
    return SuccessResponse(data=RelationshipRead.model_validate(rel))


@router.delete("/{relationship_id}", response_model=SuccessResponse)
async def delete_relationship(relationship_id: int, db: AsyncSession = Depends(get_db)):
    """관계 삭제"""
    rel = await db.get(Relationship, relationship_id)
    if not rel:
        return ErrorResponse(message="관계를 찾을 수 없습니다.")
    await db.delete(rel)
    await db.flush()
    return SuccessResponse(data={"deleted": relationship_id})
