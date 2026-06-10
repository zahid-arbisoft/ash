"""`manage.py build --project plane --issue 9213` — run the engine via the control plane.

Bridges Django ↔ engine: loads the project's YAML config, runs the build-team pipeline, and persists
the result as a Run row (and ensures Client/Project rows exist). Same engine, now with durable
multi-tenant records.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from ash.config import load_project
from ash.pipeline import run_build
from apps.house.models import Client, Project, Run


class Command(BaseCommand):
    help = "Run the build pipeline for one issue and persist a Run."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--project", required=True, help="projects/<name>.yaml config name")
        parser.add_argument("--issue", type=int, required=True)
        parser.add_argument("--client", default="default", help="client slug (tenant)")
        parser.add_argument("--keep", action="store_true", help="keep the worktree")

    def handle(self, *args, **opts) -> None:
        try:
            cfg = load_project(opts["project"])
        except FileNotFoundError as exc:
            raise CommandError(str(exc)) from exc

        client, _ = Client.objects.get_or_create(
            slug=opts["client"], defaults={"name": opts["client"].title()}
        )
        project, _ = Project.objects.get_or_create(
            config_name=cfg.name, defaults={"client": client, "name": cfg.name}
        )

        self.stdout.write(
            f"Running build for {cfg.name}#{opts['issue']} (client={client.slug}) ..."
        )
        state = run_build(cfg, opts["issue"], keep_worktree=opts["keep"])

        run = Run.objects.create(
            project=project,
            issue_number=state.issue_number,
            issue_title=state.issue_title,
            branch=state.branch or "",
            pr_url=state.pr_url or "",
            stage=state.stage.value,
            status=state.status.value,
            error=state.error or "",
            state=state.model_dump(mode="json"),
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"Run #{run.id}: stage={run.stage}/{run.status} pr={run.pr_url or '(none)'}"
            )
        )
