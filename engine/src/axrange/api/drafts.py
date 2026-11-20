"""Drafts (`api/Spec.md` A3d): twin-config documents that FAIL validation,
kept for later repair. A separate resource from the twin catalog on purpose:
a draft lives under `drafts_dir/<id>/` (outside every twin search dir), so
no catalog listing, resolver or deploy path can ever see it, and the
twin-configs validation gate is untouched. Promotion is the ordinary
`PUT /api/twin-configs/{origin}` (validated there).

Layout: the document files plus `.draft.json` (dot-file, so
`edits.read_document` never counts it as part of the document).
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import shutil
from pathlib import Path
from typing import Any, Optional

from axrange.api import dtos
from axrange.api.service import (
    ApiError, _catalog_roots, _is_safe_catalog_ref, _normalized_files, _write_root,
)

META = ".draft.json"
SCHEMA = "axr-draft/1"


def drafts_root(settings: Any) -> Path:
    root = (
        Path(settings.drafts_dir) if settings.drafts_dir
        else _write_root(settings).parent / "drafts"
    )
    resolved = root.resolve()
    for d in _catalog_roots(settings):
        if d.resolve() == resolved:
            raise ApiError(
                503, f"drafts_dir (AXRANGE_DRAFTS_DIR) {str(root)!r} is a twin search "
                "directory; drafts must live outside the twin catalog",
            )
    return root


def _now() -> str:
    return _dt.datetime.now(tz=_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _folder(settings: Any, draft_id: str) -> Path:
    if not _is_safe_catalog_ref(draft_id):
        raise ApiError(404, f"draft not found: {draft_id}")
    return drafts_root(settings) / draft_id


def exists(settings: Any, draft_id: str) -> bool:
    """Is there a draft `draft_id`? A misconfigured drafts dir counts as
    "no" here: it must never break the twin catalog's own paths."""
    if not _is_safe_catalog_ref(draft_id):
        return False
    try:
        return (_folder(settings, draft_id) / META).is_file()
    except ApiError:
        return False


def _read_meta(folder: Path) -> dict:
    try:
        meta = json.loads((folder / META).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ApiError(404, f"draft not found: {folder.name}") from None
    except (OSError, ValueError) as e:
        raise ApiError(500, f"draft {folder.name!r}: unreadable {META}: {e}") from e
    return meta if isinstance(meta, dict) else {}


def _write_meta(folder: Path, meta: dict) -> None:
    from axrange.api.console_files import atomic_write

    atomic_write(folder / META, json.dumps(meta, indent=2, sort_keys=True) + "\n")


def _description(draft_id: str, files: dict[str, str]) -> Optional[str]:
    from axrange.api import edits

    try:
        raw = edits.parse_yaml(files[edits.te_file_of(draft_id, files)])
        tc = raw.get("twin_config") if isinstance(raw, dict) else None
        desc = tc.get("description") if isinstance(tc, dict) else None
        return str(desc).strip() if desc else None
    except Exception:  # noqa: BLE001 -- a draft is by definition possibly broken
        return None


def _summary(folder: Path, meta: dict, files: dict[str, str]) -> dict:
    lc = meta.get("last_check")
    return {
        "id": folder.name,
        "origin": meta.get("origin"),
        "description": _description(folder.name, files),
        "created_at": meta.get("created_at") or "",
        "updated_at": meta.get("updated_at") or meta.get("created_at") or "",
        "last_check": dtos.DraftCheckDTO(**lc) if isinstance(lc, dict) else None,
    }


def _document(draft_id: str, files: dict[str, str], skipped: list[str]) -> Optional[dtos.TwinConfigDocumentDTO]:
    from axrange.api import edits

    try:
        te = edits.te_file_of(draft_id, files)
    except edits.DocumentError:
        te = ""  # a draft may have no recognisable twin config yet
    return dtos.TwinConfigDocumentDTO(id=draft_id, te_file=te, files=files, skipped=skipped)


def list_drafts(settings: Any) -> dtos.DraftListDTO:
    from axrange.api import edits

    root = drafts_root(settings)
    out: list[dtos.DraftSummaryDTO] = []
    if root.is_dir():
        for folder in sorted(root.iterdir()):
            if not (folder / META).is_file():
                continue
            files, _s = edits.read_document(folder)
            out.append(dtos.DraftSummaryDTO(**_summary(folder, _read_meta(folder), files)))
    return dtos.DraftListDTO(drafts=out)


def draft_detail(settings: Any, draft_id: str, *, include_document: bool) -> dtos.DraftDetailDTO:
    from axrange.api import edits

    folder = _folder(settings, draft_id)
    meta = _read_meta(folder)
    files, skipped = edits.read_document(folder)
    return dtos.DraftDetailDTO(
        **_summary(folder, meta, files),
        document=_document(draft_id, files, skipped) if include_document else None,
    )


def _check_origin(origin: Optional[str]) -> Optional[str]:
    if origin is None:
        return None
    if not _is_safe_catalog_ref(origin):
        raise ApiError(400, f"'origin' {origin!r} is not a usable twin-config id")
    return origin


def _check_identity(draft_id: str, files: dict[str, str]) -> None:
    from axrange.api import edits

    try:
        edits.check_document_identity(draft_id, files)
    except edits.DocumentError as e:
        raise ApiError(400, str(e)) from e
    except Exception as e:  # noqa: BLE001 -- unparseable YAML is a 400, not a 500
        raise ApiError(400, f"draft {draft_id!r}: {e}") from e


def draft_create(settings: Any, req: dtos.DraftWriteRequest) -> dtos.DraftDetailDTO:
    from axrange.api import edits
    from axrange.twinconfig import TwinConfigNotFoundError
    from axrange.twinconfig.registry import resolve_twin_config_path

    draft_id = (req.id or "").strip()
    if not draft_id or not _is_safe_catalog_ref(draft_id):
        raise ApiError(400, f"'id' {req.id!r} is not a usable draft id")
    origin = _check_origin(req.origin)
    files = _normalized_files(req.files)
    _check_identity(draft_id, files)
    try:
        resolve_twin_config_path(draft_id, catalog_dir=settings.twin_configs_dir or None, warnings=[])
    except TwinConfigNotFoundError:
        pass
    except Exception:  # noqa: BLE001 -- a broken existing entry still EXISTS
        raise ApiError(409, f"twin-config {draft_id!r} already exists; a draft cannot share its id")
    else:
        raise ApiError(409, f"twin-config {draft_id!r} already exists; a draft cannot share its id")
    folder = drafts_root(settings) / draft_id
    try:
        folder.parent.mkdir(parents=True, exist_ok=True)
        os.mkdir(folder)  # the claim: exactly one writer wins
    except FileExistsError:
        raise ApiError(409, f"draft {draft_id!r} already exists") from None
    except OSError as e:
        raise ApiError(500, f"cannot create {folder}: {e}") from e
    now = _now()
    try:
        edits.write_document(folder, files, replace=False)
        _write_meta(folder, {
            "schema": SCHEMA, "id": draft_id, "origin": origin, "created_at": now,
            "updated_at": now,
            "last_check": req.last_check.model_dump() if req.last_check else None,
        })
    except BaseException as e:
        shutil.rmtree(folder, ignore_errors=True)
        if isinstance(e, edits.DocumentError):
            raise ApiError(400, str(e)) from e
        raise
    return draft_detail(settings, draft_id, include_document=True)


def draft_write(settings: Any, draft_id: str, req: dtos.DraftWriteRequest) -> dtos.DraftDetailDTO:
    from axrange.api import edits

    if req.id is not None and req.id != draft_id:
        raise ApiError(400, f"body id {req.id!r} does not match the path id {draft_id!r}")
    folder = _folder(settings, draft_id)
    meta = _read_meta(folder)  # 404 when absent
    files = _normalized_files(req.files)
    _check_identity(draft_id, files)
    if req.origin is not None:
        meta["origin"] = _check_origin(req.origin)
    try:
        edits.write_document(folder, files, replace=True)
    except edits.DocumentError as e:
        raise ApiError(400, str(e)) from e
    meta.update(
        schema=SCHEMA, id=draft_id, updated_at=_now(),
        last_check=req.last_check.model_dump() if req.last_check else None,
    )
    _write_meta(folder, meta)
    return draft_detail(settings, draft_id, include_document=True)


def draft_delete(settings: Any, draft_id: str) -> None:
    folder = _folder(settings, draft_id)
    _read_meta(folder)  # 404 when absent
    shutil.rmtree(folder)


def refuse_if_draft(settings: Any, twin_id: Optional[str]) -> None:
    """The deploy paths' guard: an id that is no twin but IS a draft is a
    409 `code: draft`, never a vague 404."""
    if not twin_id or not _is_safe_catalog_ref(twin_id) or not exists(settings, twin_id):
        return
    from axrange.twinconfig import TwinConfigNotFoundError
    from axrange.twinconfig.registry import resolve_twin_config_path

    try:
        resolve_twin_config_path(twin_id, catalog_dir=settings.twin_configs_dir or None, warnings=[])
        return  # resolvable as a twin: not our concern
    except TwinConfigNotFoundError:
        pass
    except Exception:  # noqa: BLE001 -- exists-but-broken twin: let the deploy path say so
        return
    raise ApiError(
        409,
        f"{twin_id!r} is a draft; drafts are never deployable -- replace its origin "
        "twin or Save As a new twin first",
        detail={"code": "draft"},
    )
