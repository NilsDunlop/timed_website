"""Slurm command parsing and data-server job script rendering."""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from importlib import resources

from jinja2 import Environment, StrictUndefined

from .dataserver_args import collection_arguments
from .models import ExistingJob, Profile


@dataclass(frozen=True)
class QueueStatus:
    state: str
    reason: str | None
    node: str | None


def _lines(output: str) -> list[str]:
    return [line.strip() for line in output.splitlines() if line.strip()]


def parse_existing_jobs(output: str) -> list[ExistingJob]:
    jobs = []
    for line in _lines(output):
        parts = line.split(maxsplit=2)
        if len(parts) < 2:
            raise ValueError(f"Unexpected squeue output: {line}")
        jobs.append(
            ExistingJob(
                job_id=parts[0],
                state=parts[1],
                node=parts[2] if len(parts) == 3 and parts[2] not in ("-", "(null)") else None,
            )
        )
    return jobs


def parse_queue_status(output: str) -> QueueStatus | None:
    lines = _lines(output)
    if not lines:
        return None
    parts = lines[0].split(maxsplit=2)
    state = parts[0]
    reason = parts[1] if len(parts) >= 2 and parts[1] not in ("None", "-") else None
    node = parts[2] if len(parts) == 3 and parts[2] not in ("-", "(null)") else None
    return QueueStatus(state=state, reason=reason, node=node)


def parse_job_id(output: str) -> str:
    value = output.strip().split(";", 1)[0]
    if not value or not value.isdigit():
        raise ValueError(f"Unexpected sbatch output: {output.strip()}")
    return value


def shell_join(command: list[str]) -> str:
    return " ".join(shlex.quote(value) for value in command)


def render_job_script(
    profile: Profile,
    *,
    executable: str,
    shell_state_dir: str,
) -> str:
    """Render the fixed data-server Slurm template with quoted values."""
    if profile.slurm is None:
        raise ValueError("A Slurm profile is required")
    environment = Environment(
        undefined=StrictUndefined,
        autoescape=False,
        keep_trailing_newline=True,
    )
    environment.filters["shellquote"] = shlex.quote
    template_source = (
        resources.files("timed_connector")
        .joinpath("templates", "dataserver.sbatch.j2")
        .read_text(encoding="utf-8")
    )
    template = environment.from_string(template_source)
    command = [
        executable,
        "serve",
        "--host",
        "${TIMED_NODE_HOST:-$(hostname)}",
        "--port",
        "0",
        "--environment",
        "hpc",
    ]
    for root in profile.data_roots:
        command.extend(("--data-root", root))
    if profile.metadata_root is not None:
        command.extend(("--metadata-root", profile.metadata_root))
    command.extend(collection_arguments(profile.collections))
    command.extend(
        (
            "--connection-file",
            f"{shell_state_dir}/sessions/${{SLURM_JOB_ID}}.json",
            "--state-dir",
            shell_state_dir,
        )
    )
    # Preserve the two intentional shell expansions after quoting every other
    # argument. They are controlled by Slurm/the checked-in test harness.
    rendered_command = shell_join(command)
    rendered_command = rendered_command.replace(
        shlex.quote("${TIMED_NODE_HOST:-$(hostname)}"), '"${TIMED_NODE_HOST:-$(hostname)}"'
    )
    rendered_command = rendered_command.replace(
        shlex.quote(f"{shell_state_dir}/sessions/${{SLURM_JOB_ID}}.json"),
        f"{shlex.quote(shell_state_dir)}/sessions/${{SLURM_JOB_ID}}.json",
    )
    return template.render(
        account=profile.slurm.account,
        partition=profile.slurm.partition,
        time=profile.slurm.time,
        cpus=profile.slurm.cpus,
        mem=profile.slurm.mem,
        output=f"{shell_state_dir}/logs/%j.out",
        module_loads=profile.module_loads,
        command=rendered_command,
    )
