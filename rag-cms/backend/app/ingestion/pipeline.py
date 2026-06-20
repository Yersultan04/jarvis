from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients import bm25 as bm25_client
from app.clients import qdrant as qdrant_client
from app.clients.embeddings import embed_documents_batched
from app.config import get_settings
from app.db import SessionLocal
from app.ingestion.chunker import chunk_pages
from app.ingestion.enrichment import enrich_chunks
from app.ingestion.events import IngestEventBroker
from app.ingestion.ocr import ocr_page
from app.ingestion.parser import ParsedPage, parse_file
from app.ingestion.table_describe import describe_tables_batched
from app.models import Chunk, File, FileStatus, IngestRun, IngestRunStatus, Rag, RagStatus
from app.presets import resolve_models_for_rag

log = logging.getLogger("ingestion")


async def _ocr_fallback(
    path: Path,
    pages: list[ParsedPage],
    broker: IngestEventBroker | None,
    file_id: uuid.UUID,
    vision_model: str | None = None,
) -> tuple[list[ParsedPage], int]:
    """Re-OCR pages whose extracted text is too short. PDF only."""
    s = get_settings()
    if not s.ingest_ocr_fallback or path.suffix.lower() != ".pdf":
        return pages, 0
    if not s.openrouter_api_key:
        log.info("OCR fallback skipped: OPENROUTER_API_KEY not set")
        return pages, 0

    out: list[ParsedPage] = []
    replaced = 0
    for p in pages:
        if len((p.text or "").strip()) < s.ingest_ocr_min_chars:
            if broker:
                await broker.publish(
                    "ocr_fallback",
                    {"file_id": str(file_id), "page": p.page_number},
                )
            text = await ocr_page(path, p.page_number, model=vision_model)
            if text and len(text) > len(p.text or ""):
                out.append(ParsedPage(page_number=p.page_number, text=text))
                replaced += 1
                continue
        out.append(p)
    return out, replaced


async def _drop_existing_chunks(
    db: AsyncSession,
    rag_id: uuid.UUID,
    file_id: uuid.UUID,
) -> int:
    """Drop any existing chunks for a file — used on reindex (sha changed)
    or before clean re-parse. Returns count dropped. Also clears Qdrant points."""
    rows = (
        await db.execute(
            select(Chunk.id, Chunk.qdrant_point_id).where(
                Chunk.rag_id == rag_id, Chunk.file_id == file_id
            )
        )
    ).all()
    if not rows:
        return 0
    point_ids = [r[1] for r in rows if r[1]]
    if point_ids:
        try:
            await qdrant_client.delete_points(rag_id, point_ids)
        except Exception as e:
            log.warning("failed to drop %d qdrant points for file %s: %s", len(point_ids), file_id, e)
    # Drop chunk rows in one statement
    from sqlalchemy import delete

    await db.execute(delete(Chunk).where(Chunk.rag_id == rag_id, Chunk.file_id == file_id))
    await db.flush()
    return len(rows)


async def _process_file(
    db: AsyncSession,
    rag: Rag,
    file_obj: File,
    run: IngestRun,
    broker: IngestEventBroker,
) -> int:
    """Parse + chunk + embed + persist a single file. Returns chunk count."""
    s = get_settings()
    path = Path(file_obj.storage_path)

    await broker.publish(
        "file_started",
        {
            "file_id": str(file_obj.id),
            "filename": file_obj.filename,
            "size_bytes": file_obj.size_bytes,
        },
    )

    # If we somehow have orphan chunks (re-index of a failed-once file), wipe first.
    dropped = await _drop_existing_chunks(db, rag.id, file_obj.id)
    if dropped:
        await broker.publish(
            "stale_chunks_dropped",
            {"file_id": str(file_obj.id), "count": dropped},
        )

    file_obj.status = FileStatus.parsing
    run.current_file_id = file_obj.id
    run.current_stage = "parsing"
    run.current_progress = 0.0
    await db.commit()
    await broker.publish("parsing", {"file_id": str(file_obj.id)})

    try:
        pages = parse_file(path, file_obj.mime_type)
    except Exception as e:
        file_obj.status = FileStatus.failed
        file_obj.error = f"parse failed: {e}"
        await db.commit()
        await broker.publish(
            "file_failed",
            {"file_id": str(file_obj.id), "stage": "parse", "error": str(e)},
        )
        raise

    rag_models = resolve_models_for_rag(rag)
    pages, ocr_replaced = await _ocr_fallback(
        path, pages, broker, file_obj.id, vision_model=rag_models["llm_vision_model"]
    )
    if ocr_replaced:
        log.info("OCR fallback applied to %d page(s) of %s", ocr_replaced, file_obj.filename)

    run.current_stage = "chunking"
    await db.commit()
    chunks = chunk_pages(pages)
    await broker.publish(
        "chunked",
        {"file_id": str(file_obj.id), "pages": len(pages), "chunks": len(chunks)},
    )

    if not chunks:
        file_obj.status = FileStatus.parsed
        file_obj.pages = len(pages)
        await db.commit()
        await broker.publish(
            "file_done", {"file_id": str(file_obj.id), "chunks": 0, "pages": len(pages)}
        )
        return 0

    file_obj.pages = len(pages)

    contexts: list[str] = []
    doc_summary = ""
    if s.contextual_enrichment and s.openrouter_api_key:
        run.current_stage = "enriching"
        await db.commit()
        await broker.publish("enriching", {"file_id": str(file_obj.id), "chunks": len(chunks)})
        enrich = await enrich_chunks(file_obj.filename, chunks)
        doc_summary = enrich.doc_summary
        contexts = enrich.contexts

    # Tables: ask an LLM to write a short description per table; that description
    # is what gets EMBEDDED (so retrieval finds the table by meaning), while the
    # chunk's `.text` remains the raw markdown so the agent sees actual cells.
    table_descriptions: dict[int, str] = {}  # chunk index → description
    table_items = [
        {
            "markdown": c.text,
            "filename": file_obj.filename,
            "page": c.page_start,
            "orientation": c.table_orientation,
            "labels": c.table_labels or [],
        }
        for c in chunks if c.is_table
    ]
    table_indices = [i for i, c in enumerate(chunks) if c.is_table]
    if table_items and rag_models.get("llm_model"):
        run.current_stage = "describing_tables"
        await db.commit()
        await broker.publish(
            "describing_tables",
            {"file_id": str(file_obj.id), "tables": len(table_items)},
        )
        descs = await describe_tables_batched(
            table_items,
            concurrency=4,
            model=rag_models.get("llm_model"),
            base_url=rag_models.get("llm_base_url"),
            api_key=rag_models.get("llm_api_key"),
            provider_order=rag_models.get("llm_provider_order"),
        )
        for idx, d in zip(table_indices, descs, strict=True):
            if d:
                table_descriptions[idx] = d

    embed_inputs: list[str] = []
    for i, c in enumerate(chunks):
        if c.is_table:
            desc = table_descriptions.get(i, "")
            labels_line = ""
            if c.table_labels:
                labels_line = "Поля: " + ", ".join(c.table_labels[:30])
            # Embed FIELD LABELS + LLM description so retrieval matches both
            # exact column/row names AND semantic content. Description alone
            # would miss queries that quote the verbatim field name.
            parts = [f"[Table from {file_obj.filename} p.{c.page_start}, "
                     f"{c.table_orientation}, {c.table_rows}×{c.table_cols}]"]
            if labels_line:
                parts.append(labels_line)
            if desc:
                parts.append(desc)
            embed_inputs.append("\n".join(parts))
        else:
            ctx = contexts[i] if (contexts and i < len(contexts)) else ""
            embed_inputs.append(f"{ctx}\n\n{c.text}" if ctx else c.text)

    run.current_stage = "embedding"
    await db.commit()
    await broker.publish(
        "embedding",
        {"file_id": str(file_obj.id), "chunks_total": len(embed_inputs)},
    )

    async def _on_batch(done: int, total: int) -> None:
        run.current_progress = (done / total) if total else 0.0
        await db.commit()
        await broker.publish(
            "embedding_batch",
            {"file_id": str(file_obj.id), "done": done, "total": total},
        )

    vectors = await embed_documents_batched(
        embed_inputs,
        batch_size=64,
        on_batch=_on_batch,
        rag_models=rag_models,
    )

    # Generate sparse BM25 vectors in parallel — fastembed runs in-process,
    # CPU-only, fast. Language picked from rag.settings.fts_language.
    fts_lang = (rag.settings or {}).get("fts_language", "english")
    # For the sparse vector we want to match query intent, so embed the *chunk
    # text* (not the contextual prefix or description) — sparse models do best
    # on the actual content tokens.
    sparse_inputs = [c.text for c in chunks]
    sparse_vectors = bm25_client.embed_documents(sparse_inputs, language=fts_lang)

    points: list[tuple[str, list[float], dict, dict]] = []
    chunk_rows: list[Chunk] = []
    for i, (c, vec, sparse_vec) in enumerate(zip(chunks, vectors, sparse_vectors, strict=True)):
        chunk_id = uuid.uuid4()
        extra: dict = {}
        if contexts and i < len(contexts) and contexts[i]:
            extra["context"] = contexts[i]
        if c.is_table:
            extra["is_table"] = True
            extra["table_rows"] = c.table_rows
            extra["table_cols"] = c.table_cols
            extra["table_orientation"] = c.table_orientation
            if c.table_labels:
                extra["table_labels"] = c.table_labels
            if table_descriptions.get(i):
                extra["table_description"] = table_descriptions[i]
        if doc_summary and i == 0:
            extra["doc_summary"] = doc_summary
        row = Chunk(
            id=chunk_id,
            rag_id=rag.id,
            file_id=file_obj.id,
            chunk_index=c.index,
            page_start=c.page_start,
            page_end=c.page_end,
            heading=c.heading,
            text=c.text,
            token_count=c.token_count,
            qdrant_point_id=str(chunk_id),
            extra=extra,
        )
        chunk_rows.append(row)
        points.append(
            (
                str(chunk_id),
                vec,
                sparse_vec,
                {
                    "chunk_id": str(chunk_id),
                    "rag_id": str(rag.id),
                    "file_id": str(file_obj.id),
                    "filename": file_obj.filename,
                    "page_start": c.page_start,
                    "page_end": c.page_end,
                    "heading": c.heading,
                },
            )
        )

    run.current_stage = "storing"
    await db.commit()
    await broker.publish("storing", {"file_id": str(file_obj.id), "chunks": len(chunk_rows)})

    db.add_all(chunk_rows)
    await db.flush()
    await qdrant_client.upsert_chunks(rag.id, points)

    file_obj.status = FileStatus.parsed
    file_obj.error = None
    await db.commit()
    await broker.publish(
        "file_done",
        {
            "file_id": str(file_obj.id),
            "chunks": len(chunk_rows),
            "pages": len(pages),
            "ocr_pages": ocr_replaced,
        },
    )
    return len(chunk_rows)


async def run_ingestion(rag_id: uuid.UUID, ingest_run_id: uuid.UUID) -> None:
    """Background task: parse + embed all unparsed files for a RAG.

    Caching: files already in `parsed` status are skipped — they keep their
    existing chunks in Postgres and Qdrant points. To force a re-index of a
    parsed file, set its status back to `uploaded` (e.g. on re-upload with
    different sha) or use the `force` flag on the API endpoint.
    """
    t0 = time.monotonic()
    broker = await IngestEventBroker.get_or_create(rag_id, ingest_run_id)

    async with SessionLocal() as db:
        run = (
            await db.execute(select(IngestRun).where(IngestRun.id == ingest_run_id))
        ).scalar_one()
        rag = (await db.execute(select(Rag).where(Rag.id == rag_id))).scalar_one()
        run.status = IngestRunStatus.running
        run.started_at = datetime.now(timezone.utc)
        rag.status = RagStatus.indexing
        await db.commit()

        await qdrant_client.ensure_collection(rag.id, rag.embed_dim)

        # Caching: only process files that haven't been successfully parsed yet.
        files_q = await db.execute(
            select(File)
            .where(
                File.rag_id == rag.id,
                File.status.in_([FileStatus.uploaded, FileStatus.failed]),
            )
            .order_by(File.created_at)
        )
        files = list(files_q.scalars().all())

        # Count already-parsed files (skipped via cache) for run telemetry.
        skipped_q = await db.execute(
            select(File).where(File.rag_id == rag.id, File.status == FileStatus.parsed)
        )
        skipped = list(skipped_q.scalars().all())

        run.files_total = len(files)
        await db.commit()
        await broker.publish(
            "run_started",
            {
                "run_id": str(ingest_run_id),
                "rag_id": str(rag_id),
                "files_total": len(files),
                "files_skipped_cached": len(skipped),
            },
        )

        total_chunks = 0
        try:
            for f in files:
                try:
                    n = await _process_file(db, rag, f, run, broker)
                    total_chunks += n
                except Exception as e:
                    log.exception("file %s failed: %s", f.id, e)
                run.files_done += 1
                run.chunks_total = total_chunks
                await db.commit()

            run.status = IngestRunStatus.succeeded
            run.finished_at = datetime.now(timezone.utc)
            run.current_file_id = None
            run.current_stage = None
            run.current_progress = None
            rag.status = RagStatus.ready
        except Exception as e:
            log.exception("ingestion run %s failed", run.id)
            run.status = IngestRunStatus.failed
            run.error = str(e)
            run.finished_at = datetime.now(timezone.utc)
            rag.status = RagStatus.failed
        finally:
            await db.commit()
            await broker.publish(
                "run_finished",
                {
                    "status": run.status.value,
                    "files_done": run.files_done,
                    "chunks_total": total_chunks,
                    "elapsed_sec": round(time.monotonic() - t0, 3),
                },
            )
            await broker.close()
            await IngestEventBroker.pop(ingest_run_id)
