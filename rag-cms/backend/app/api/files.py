from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_accessible_rag, get_owned_rag
from app.clients import qdrant as qdrant_client
from app.config import get_settings
from app.db import get_db
from app.models import Chunk
from app.models import File as FileModel
from app.models import FileStatus, Rag
from app.schemas import FileOut

router = APIRouter()

ALLOWED_SUFFIXES = {".pdf", ".txt", ".md", ".xlsx"}


def _file_dir(rag_id: uuid.UUID) -> Path:
    s = get_settings()
    p = s.data_dir / "rags" / str(rag_id) / "files"
    p.mkdir(parents=True, exist_ok=True)
    return p


@router.post("/{rag_id}/files", response_model=list[FileOut], status_code=201)
async def upload_files(
    files: list[UploadFile],
    rag: Rag = Depends(get_owned_rag),
    db: AsyncSession = Depends(get_db),
) -> list[FileModel]:
    s = get_settings()
    max_bytes = s.max_upload_mb * 1024 * 1024
    out: list[FileModel] = []

    target_dir = _file_dir(rag.id)

    for up in files:
        if not up.filename:
            raise HTTPException(400, "missing filename")
        suffix = Path(up.filename).suffix.lower()
        if suffix not in ALLOWED_SUFFIXES:
            raise HTTPException(400, f"unsupported file type: {suffix}")

        contents = await up.read()
        size = len(contents)
        if size == 0:
            raise HTTPException(400, f"{up.filename} is empty")
        if size > max_bytes:
            raise HTTPException(413, f"{up.filename} exceeds {s.max_upload_mb}MB")

        sha = hashlib.sha256(contents).hexdigest()

        existing = (
            await db.execute(
                select(FileModel).where(FileModel.rag_id == rag.id, FileModel.sha256 == sha)
            )
        ).scalar_one_or_none()
        if existing is not None:
            out.append(existing)
            continue

        file_id = uuid.uuid4()
        target = target_dir / f"{file_id}{suffix}"
        target.write_bytes(contents)

        row = FileModel(
            id=file_id,
            rag_id=rag.id,
            filename=up.filename,
            mime_type=up.content_type,
            size_bytes=size,
            sha256=sha,
            storage_path=str(target),
            status=FileStatus.uploaded,
        )
        db.add(row)
        out.append(row)

    await db.commit()
    for r in out:
        await db.refresh(r)
    return out


@router.get("/{rag_id}/files", response_model=list[FileOut])
async def list_files(
    rag: Rag = Depends(get_owned_rag), db: AsyncSession = Depends(get_db)
) -> list[FileModel]:
    res = await db.execute(
        select(FileModel).where(FileModel.rag_id == rag.id).order_by(FileModel.created_at.desc())
    )
    return list(res.scalars().all())


@router.get("/{rag_id}/files/{file_id}/blob")
async def get_file_blob(
    file_id: uuid.UUID,
    rag: Rag = Depends(get_accessible_rag),
    db: AsyncSession = Depends(get_db),
) -> FileResponse:
    """Stream the original file blob (PDF/XLSX/TXT/MD).
    Accessible by owner / admin / active member — same scope as chat answers.
    """
    row = (
        await db.execute(
            select(FileModel).where(FileModel.id == file_id, FileModel.rag_id == rag.id)
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "file not found")
    path = Path(row.storage_path)
    if not path.is_file():
        raise HTTPException(410, "file blob missing on disk")
    return FileResponse(
        path=path,
        media_type=row.mime_type or "application/octet-stream",
        filename=row.filename,
        headers={"Content-Disposition": f'inline; filename="{row.filename}"'},
    )


@router.delete("/{rag_id}/files/{file_id}", status_code=204)
async def delete_file(
    file_id: uuid.UUID,
    rag: Rag = Depends(get_owned_rag),
    db: AsyncSession = Depends(get_db),
) -> None:
    row = (
        await db.execute(
            select(FileModel).where(FileModel.id == file_id, FileModel.rag_id == rag.id)
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "file not found")

    # Drop any Qdrant points belonging to this file BEFORE deleting the chunk rows
    # (the file row's cascade nukes chunks, which is what tells us what to delete from Qdrant).
    point_ids_rows = (
        await db.execute(
            select(Chunk.qdrant_point_id).where(
                Chunk.rag_id == rag.id, Chunk.file_id == row.id
            )
        )
    ).all()
    point_ids = [r[0] for r in point_ids_rows if r[0]]
    if point_ids:
        try:
            await qdrant_client.delete_points(rag.id, point_ids)
        except Exception:
            # Don't fail the delete request — orphan points are recoverable via a maintenance sweep later.
            pass

    try:
        Path(row.storage_path).unlink(missing_ok=True)
    except OSError:
        pass
    await db.delete(row)
    await db.commit()
