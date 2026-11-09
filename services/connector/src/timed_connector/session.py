"""Single-session connector state machine shared by HTTP and terminal clients."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import Any, Protocol

from .dataserver_connection import DataServerConnection
from .local_backend import LocalBackend
from .models import (
    ExistingJob,
    ExistingJobChoice,
    Profile,
    Prompt,
    RespondRequest,
    SessionSnapshot,
)
from .profiles import ProfileError, ProfileStore, validate_profile
from .slurm import QueueStatus
from .ssh_backend import HostKeyStatus


class SessionError(RuntimeError):
    """Raised for invalid session actions."""


async def _cancel_task(task: asyncio.Task[None]) -> None:
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task


class SessionBackend(Protocol):
    async def inspect_host_key(self, profile: Profile) -> HostKeyStatus: ...

    async def accept_host_key(self, status: HostKeyStatus) -> HostKeyStatus: ...

    async def connect(
        self,
        profile: Profile,
        status: HostKeyStatus,
        prompt_callback: Callable[[list[Prompt]], Awaitable[list[str]]],
    ) -> Any: ...

    async def prepare(
        self, context: Any, profile: Profile, log: Callable[[str], None]
    ) -> None: ...

    async def list_jobs(self, context: Any) -> list[ExistingJob]: ...

    async def cancel_job(self, context: Any, job_id: str) -> None: ...

    async def submit_job(self, context: Any, profile: Profile) -> str: ...

    async def queue_status(self, context: Any, job_id: str) -> QueueStatus | None: ...

    async def connection_file_exists(self, context: Any, job_id: str) -> bool: ...

    async def read_connection(
        self, context: Any, job_id: str
    ) -> DataServerConnection: ...

    async def forward(self, context: Any, connection: DataServerConnection) -> Any: ...

    async def handshake(
        self, forward: Any, connection: DataServerConnection
    ) -> dict[str, Any]: ...

    async def log_tail(self, context: Any, job_id: str) -> list[str]: ...

    async def remove_connection_file(self, context: Any, job_id: str) -> None: ...

    async def close_forward(self, forward: Any | None) -> None: ...

    async def close(self, context: Any | None) -> None: ...

    async def wait_closed(self, context: Any) -> None: ...

    def proxy_target(
        self, forward: Any, connection: DataServerConnection
    ) -> tuple[str, str]: ...


class ConnectorSession:
    """Own exactly one in-flight or connected cluster session."""

    def __init__(
        self,
        profiles: ProfileStore,
        backend: SessionBackend,
        *,
        local_backend: LocalBackend | None = None,
        poll_interval: float = 2.0,
        startup_timeout: float = 180.0,
        cleanup_timeout: float = 10.0,
    ) -> None:
        self.profiles = profiles
        self.backend = backend
        self.local_backend = local_backend
        self.poll_interval = poll_interval
        self.startup_timeout = startup_timeout
        self.cleanup_timeout = cleanup_timeout
        self._snapshot = SessionSnapshot()
        self._task: asyncio.Task[None] | None = None
        self._monitor_task: asyncio.Task[None] | None = None
        self._pending_kind: str | None = None
        self._pending: asyncio.Future[Any] | None = None
        self._context: Any | None = None
        self._forward: Any | None = None
        self._connection: DataServerConnection | None = None
        self._profile: Profile | None = None
        self._disconnecting = False

    def snapshot(self) -> SessionSnapshot:
        return self._snapshot.model_copy(deep=True)

    def _log(self, line: str) -> None:
        clean = line.replace("\r", " ").replace("\n", " ").strip()
        if clean:
            self._snapshot.logs = [*self._snapshot.logs, clean][-200:]

    def _state(self, state: str, **changes: Any) -> None:
        self._snapshot.state = state  # type: ignore[assignment]
        for name, value in changes.items():
            setattr(self._snapshot, name, value)

    async def connect(self, profile_id: str, username: str | None = None) -> SessionSnapshot:
        if self._snapshot.state not in ("disconnected", "error"):
            raise SessionError("A connector session is already active")
        try:
            profile = validate_profile(self.profiles, profile_id)
        except ProfileError as exc:
            raise SessionError(str(exc)) from exc
        if profile.kind == "local" and self.local_backend is None:
            raise SessionError("Local data-server backend is not configured")
        if profile.kind == "slurm":
            login = username or profile.username
            if not login:
                raise SessionError(f"Enter your username to connect to {profile.name}")
            profile = profile.for_user(login)
        self._snapshot = SessionSnapshot(state="connecting", profile_id=profile.id)
        self._profile = profile
        self._disconnecting = False
        run = self._run_local(profile) if profile.kind == "local" else self._run_hpc(profile)
        self._task = asyncio.create_task(run)
        await asyncio.sleep(0)
        return self.snapshot()

    async def _request_host_confirmation(self, status: HostKeyStatus) -> bool:
        loop = asyncio.get_running_loop()
        future: asyncio.Future[bool] = loop.create_future()
        self._pending_kind = "host_key"
        self._pending = future
        self._state("host_key_confirm", host_key_fingerprint=status.fingerprint)
        try:
            return await future
        finally:
            self._pending = None
            self._pending_kind = None
            self._snapshot.host_key_fingerprint = None

    async def _request_prompts(self, prompts: list[Prompt]) -> list[str]:
        loop = asyncio.get_running_loop()
        future: asyncio.Future[list[str]] = loop.create_future()
        self._pending_kind = "answers"
        self._pending = future
        self._state("awaiting_input", prompts=prompts)
        try:
            return await future
        finally:
            self._pending = None
            self._pending_kind = None
            self._snapshot.prompts = []
            if self._snapshot.state == "awaiting_input":
                self._state("authenticating")

    async def _choose_job(self, jobs: list[ExistingJob]) -> ExistingJobChoice:
        loop = asyncio.get_running_loop()
        future: asyncio.Future[ExistingJobChoice] = loop.create_future()
        self._pending_kind = "existing_job"
        self._pending = future
        self._state("choose_existing_job", existing_jobs=jobs)
        try:
            return await future
        finally:
            self._pending = None
            self._pending_kind = None
            self._snapshot.existing_jobs = []

    async def respond(self, response: RespondRequest) -> SessionSnapshot:
        if self._pending is None or self._pending.done():
            raise SessionError("The session is not waiting for a response")
        if self._pending_kind == "host_key":
            if response.accept_host_key is None:
                raise SessionError("A host-key decision is required")
            self._pending.set_result(response.accept_host_key)
        elif self._pending_kind == "answers":
            if response.answers is None:
                raise SessionError("Authentication answers are required")
            if len(response.answers) != len(self._snapshot.prompts):
                raise SessionError("The number of answers does not match the prompts")
            self._pending.set_result(list(response.answers))
        elif self._pending_kind == "existing_job":
            if response.existing_job is None:
                raise SessionError("An existing-job decision is required")
            valid_ids = {job.job_id for job in self._snapshot.existing_jobs}
            if response.existing_job.job_id not in valid_ids:
                raise SessionError("The selected job is not available")
            self._pending.set_result(response.existing_job)
        else:
            raise SessionError("Unsupported pending response")
        await asyncio.sleep(0)
        return self.snapshot()

    async def _run_hpc(self, profile: Profile) -> None:
        submitted_job = False
        try:
            self._log(f"Checking host key for {profile.host}:{profile.port}")
            status = await self.backend.inspect_host_key(profile)
            if not status.known:
                accepted = await self._request_host_confirmation(status)
                if not accepted:
                    raise SessionError("Host key was not accepted")
                status = await self.backend.accept_host_key(status)
                self._log("Saved accepted host key in TIMED known_hosts")

            self._state("authenticating")
            self._log("Authenticating with SSH")
            self._context = await self.backend.connect(
                profile,
                status,
                self._request_prompts,
            )
            self._state("preparing_server")
            await self.backend.prepare(self._context, profile, self._log)

            job_id: str | None = None
            while True:
                jobs = await self.backend.list_jobs(self._context)
                if not jobs:
                    break
                choice = await self._choose_job(jobs)
                if choice.action == "reuse":
                    job_id = choice.job_id
                    self._log(f"Reusing Slurm job {job_id}")
                    break
                await self.backend.cancel_job(self._context, choice.job_id)
                self._log(f"Cancelled existing Slurm job {choice.job_id}")

            if job_id is None:
                self._state("submitting_job")
                job_id = await self.backend.submit_job(self._context, profile)
                submitted_job = True
                self._log(f"Submitted Slurm job {job_id}")
            self._snapshot.job_id = job_id

            if submitted_job:
                await self._wait_for_running(job_id)
            else:
                self._state("starting")
            await self._wait_for_connection_file(job_id)
            self._connection = await self.backend.read_connection(self._context, job_id)
            self._forward = await self.backend.forward(self._context, self._connection)
            handshake = await self.backend.handshake(self._forward, self._connection)
            target, _token = self.backend.proxy_target(self._forward, self._connection)
            port = int(target.rsplit(":", 1)[1])
            self._state(
                "connected",
                local_forward_port=port,
                handshake=handshake,
                message=None,
                hint=None,
            )
            self._log("Connected to the remote data server")
            self._monitor_task = asyncio.create_task(self._monitor_hpc(job_id))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._fail(str(exc))
        finally:
            if self._task is asyncio.current_task():
                self._task = None

    async def _run_local(self, profile: Profile) -> None:
        if self.local_backend is None:
            await self._fail("Local data-server backend is not configured")
            return
        try:
            self._state("preparing_server")
            self._log("Starting local data server")
            self._context = await self.local_backend.start(profile, self._log)
            self._state("starting")
            self._connection = await self.local_backend.wait_for_connection(
                self._context,
                timeout=self.startup_timeout,
                poll_interval=self.poll_interval,
            )
            handshake = await self.local_backend.handshake(
                self._context,
                self._connection,
            )
            target, _token = self.local_backend.proxy_target(
                self._context,
                self._connection,
            )
            port = int(target.rsplit(":", 1)[1])
            self._state(
                "connected",
                local_forward_port=port,
                handshake=handshake,
                message=None,
                hint=None,
            )
            self._log("Connected to the local data server")
            self._monitor_task = asyncio.create_task(self._monitor_local())
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._fail(str(exc))
        finally:
            if self._task is asyncio.current_task():
                self._task = None

    async def _wait_for_running(self, job_id: str) -> None:
        deadline = asyncio.get_running_loop().time() + self.startup_timeout
        while asyncio.get_running_loop().time() < deadline:
            status = await self.backend.queue_status(self._context, job_id)
            if status is None:
                tail = await self.backend.log_tail(self._context, job_id)
                for line in tail:
                    self._log(line)
                raise SessionError("Slurm job ended before the data server started")
            self._snapshot.slurm_state = status.state
            self._snapshot.pending_reason = status.reason
            self._snapshot.node = status.node
            if status.state.upper() == "RUNNING":
                self._state("starting")
                return
            if status.state.upper() not in ("PENDING", "CONFIGURING"):
                raise SessionError(f"Slurm job entered terminal state {status.state}")
            self._state("queued")
            await asyncio.sleep(self.poll_interval)
        raise SessionError("Timed out waiting for the Slurm job to run")

    async def _wait_for_connection_file(self, job_id: str) -> None:
        deadline = asyncio.get_running_loop().time() + self.startup_timeout
        while asyncio.get_running_loop().time() < deadline:
            if await self.backend.connection_file_exists(self._context, job_id):
                return
            status = await self.backend.queue_status(self._context, job_id)
            if status is None or status.state.upper() not in (
                "RUNNING",
                "PENDING",
                "CONFIGURING",
            ):
                tail = await self.backend.log_tail(self._context, job_id)
                for line in tail:
                    self._log(line)
                raise SessionError("Data-server connection file did not appear")
            await asyncio.sleep(self.poll_interval)
        tail = await self.backend.log_tail(self._context, job_id)
        for line in tail:
            self._log(line)
        raise SessionError("Timed out waiting for the data-server connection file")

    async def _monitor_hpc(self, job_id: str) -> None:
        try:
            while self._snapshot.state == "connected":
                await asyncio.sleep(max(self.poll_interval, 1.0))
                status = await self.backend.queue_status(self._context, job_id)
                if status is None:
                    raise SessionError("The data-server Slurm job ended")
                self._snapshot.slurm_state = status.state
                self._snapshot.node = status.node
        except asyncio.CancelledError:
            return
        except Exception as exc:
            if not self._disconnecting:
                await self._fail(str(exc))

    async def _monitor_local(self) -> None:
        if self.local_backend is None:
            return
        try:
            while self._snapshot.state == "connected":
                await asyncio.sleep(max(self.poll_interval, 1.0))
                if self.local_backend.returncode(self._context) is not None:
                    raise SessionError("The local data-server process ended")
        except asyncio.CancelledError:
            return
        except Exception as exc:
            if not self._disconnecting:
                await self._fail(str(exc))

    async def _fail(self, message: str) -> None:
        job_id = self._snapshot.job_id
        if (
            self._profile is not None
            and self._profile.kind == "slurm"
            and job_id
            and self._context is not None
        ):
            try:
                await self.backend.cancel_job(self._context, job_id)
            except Exception:
                self._log("Could not cancel failed Slurm job")
        await self._cleanup(remove_connection=job_id is not None)
        self._state(
            "error",
            message=message,
            hint=(
                "Check the session log and local data roots, then reconnect."
                if self._profile is not None and self._profile.kind == "local"
                else "Check the session log and cluster profile, then reconnect."
            ),
            local_forward_port=None,
            handshake=None,
        )
        self._log(f"Error: {message}")

    async def _cleanup_step(
        self,
        description: str,
        operation: Callable[[], Awaitable[None]],
    ) -> str | None:
        try:
            task = asyncio.ensure_future(operation())
        except Exception as exc:
            message = f"Failed while {description}: {exc}"
            self._log(message)
            return message
        done, _pending = await asyncio.wait({task}, timeout=self.cleanup_timeout)
        if task not in done:
            task.cancel()
            task.add_done_callback(
                lambda finished: finished.exception() if not finished.cancelled() else None
            )
            message = (
                f"Timed out while {description} after {self.cleanup_timeout:g} seconds"
            )
            self._log(message)
            return message
        try:
            task.result()
        except asyncio.CancelledError:
            message = f"Cancelled while {description}"
            self._log(message)
            return message
        except Exception as exc:
            message = f"Failed while {description}: {exc}"
            self._log(message)
            return message
        return None

    async def _cleanup(self, *, remove_connection: bool) -> list[str]:
        failures: list[str] = []
        if self._profile is not None and self._profile.kind == "local":
            self._connection = None
            context, self._context = self._context, None
            if self.local_backend is not None and context is not None:
                failure = await self._cleanup_step(
                    "closing the local data server",
                    lambda: self.local_backend.close(context),
                )
                if failure is not None:
                    failures.append(failure)
            return failures
        context, self._context = self._context, None
        forward, self._forward = self._forward, None
        self._connection = None
        job_id = self._snapshot.job_id
        if remove_connection and job_id and context is not None:
            failure = await self._cleanup_step(
                "removing the remote connection file",
                lambda: self.backend.remove_connection_file(context, job_id),
            )
            if failure is not None:
                failures.append(failure)
        if context is not None:
            failure = await self._cleanup_step(
                "closing the SSH connection",
                lambda: self.backend.close(context),
            )
            if failure is not None:
                failures.append(failure)
        if forward is not None:
            failure = await self._cleanup_step(
                "closing the SSH port forward",
                lambda: self.backend.close_forward(forward),
            )
            if failure is not None:
                failures.append(failure)
        return failures

    def _finish_disconnect(self, failures: list[str]) -> SessionSnapshot:
        self._task = None
        self._monitor_task = None
        self._profile = None
        self._disconnecting = False
        if not failures:
            self._snapshot = SessionSnapshot()
            return self.snapshot()
        self._state(
            "error",
            message="Disconnect cleanup failed: " + "; ".join(failures),
            hint="Reconnect after checking the connector session log.",
            local_forward_port=None,
            handshake=None,
        )
        return self.snapshot()

    async def disconnect(
        self,
        *,
        cancel_job: bool = True,
        close_proxy: Callable[[], Awaitable[None]] | None = None,
    ) -> SessionSnapshot:
        failures: list[str] = []
        was_disconnected = self._snapshot.state == "disconnected"
        if not was_disconnected:
            self._disconnecting = True
            self._state("disconnecting")
        if close_proxy is not None:
            failure = await self._cleanup_step(
                "closing pooled HTTP connections",
                close_proxy,
            )
            if failure is not None:
                failures.append(failure)
        if was_disconnected:
            return self._finish_disconnect(failures)
        if self._pending is not None and not self._pending.done():
            self._pending.cancel()
        if self._task is not None and not self._task.done():
            task = self._task
            failure = await self._cleanup_step(
                "stopping the connection task",
                lambda: _cancel_task(task),
            )
            if failure is not None:
                failures.append(failure)
        if self._monitor_task is not None:
            monitor_task = self._monitor_task
            failure = await self._cleanup_step(
                "stopping the session monitor",
                lambda: _cancel_task(monitor_task),
            )
            if failure is not None:
                failures.append(failure)
        job_id = self._snapshot.job_id
        if (
            self._profile is not None
            and self._profile.kind == "slurm"
            and cancel_job
            and job_id
            and self._context is not None
        ):
            context = self._context
            failure = await self._cleanup_step(
                f"cancelling Slurm job {job_id}",
                lambda: self.backend.cancel_job(context, job_id),
            )
            if failure is None:
                self._log(f"Cancelled Slurm job {job_id}")
            else:
                failures.append(failure)
        failures.extend(await self._cleanup(remove_connection=cancel_job))
        return self._finish_disconnect(failures)

    def proxy_target(self) -> tuple[str, str]:
        if self._snapshot.state != "connected" or self._connection is None:
            raise SessionError("No data server is connected")
        if self._profile is not None and self._profile.kind == "local":
            if self.local_backend is None or self._context is None:
                raise SessionError("No local data server is connected")
            return self.local_backend.proxy_target(self._context, self._connection)
        if self._forward is None:
            raise SessionError("No data server is connected")
        return self.backend.proxy_target(self._forward, self._connection)
