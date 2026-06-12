"""Spec file upload view — SQLAdmin BaseView.

Accepts .md / .txt / .pdf / .docx, saves to runtime/uploads/<uuid>.<ext>,
and enqueues a spec_file run. The PM agent converts the file to Markdown
before passing it to the LLM. No project is required.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from sqladmin import BaseView, expose
from starlette.datastructures import UploadFile
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

from ash.config.settings import RUNTIME_DIR
from ash.db.base import get_sessionmaker
from ash.db.models import Integration, RunRecord
from ash.graph.runner import Runner
from ash.integrations.service import list_integrations
from ash.utils.file_extract import SUPPORTED_EXTENSIONS


def _runner(request: Request) -> Runner:
    # request.app is SQLAdmin's inner Starlette app; outer FastAPI app is stored on its state
    outer_app = request.app.state.outer_app
    runner: Runner = outer_app.state.runner
    return runner


class SpecUploadView(BaseView):
    name = "Upload Spec"
    icon = "fa-solid fa-file-arrow-up"

    async def _get_integrations(self) -> list[Integration]:
        async with get_sessionmaker()() as session:
            return await list_integrations(session)

    @expose("/spec-upload", methods=["GET", "POST"])
    async def spec_upload(self, request: Request) -> Response:
        integrations = await self._get_integrations()

        if request.method == "GET":
            return await self.templates.TemplateResponse(
                request,
                "spec_upload.html",
                {
                    "title": "Upload Spec",
                    "subtitle": "",
                    "integrations": integrations,
                },
            )

        async def _error(msg: str) -> Response:
            return await self.templates.TemplateResponse(
                request,
                "spec_upload.html",
                {
                    "title": "Upload Spec",
                    "subtitle": "",
                    "integrations": integrations,
                    "error": msg,
                },
                status_code=400,
            )

        form = await request.form()
        raw_int_id = str(form.get("integration_id") or "")
        integration_id = int(raw_int_id) if raw_int_id.isdigit() else None
        spec_file = form.get("spec_file")

        if not isinstance(spec_file, UploadFile):
            return await _error("A spec file is required.")

        ext = Path(spec_file.filename or "").suffix.lower()
        if ext not in SUPPORTED_EXTENSIONS:
            supported = ", ".join(sorted(SUPPORTED_EXTENSIONS))
            return await _error(f"Unsupported file type {ext!r}. Allowed: {supported}")

        uploads_dir = RUNTIME_DIR / "uploads"
        uploads_dir.mkdir(parents=True, exist_ok=True)

        file_name = f"{uuid.uuid4().hex}{ext}"
        dest = uploads_dir / file_name
        dest.write_bytes(await spec_file.read())

        run_id = await _runner(request).start_run(
            intake_mode="spec_file",
            spec_file_path=str(dest),
            integration_id=integration_id,
        )

        async with get_sessionmaker()() as session:
            session.add(
                RunRecord(
                    run_id=run_id,
                    project="",
                    item_id=None,
                    intake_mode="spec_file",
                    spec_file_path=str(dest),
                    integration_id=integration_id,
                )
            )
            await session.commit()

        return RedirectResponse(
            url=request.url_for("admin:list", identity="run-record"), status_code=303
        )
