"""Durable per-user job records and detached process launching."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .core import Plan


_CHILDREN: dict[int, subprocess.Popen[bytes]] = {}


def _validate_execution_host(plan: Plan, platform_name: str | None = None) -> None:
    platform_name = platform_name or sys.platform
    workflow = plan.spec.workflow
    command_name = Path(plan.command[0]).name.lower() if plan.command else ""
    requires_posix = plan.spec.execution_engine == "nextflow" or command_name in {"bash", "bash.exe"}
    if workflow in {"anat", "bold", "dwi"} and platform_name == "win32" and requires_posix:
        raise RuntimeError(
            "Anatomy, BOLD, and DWI launchers require a POSIX shell and are not supported "
            "by the native Windows controller. Run them on Linux/HPC; remote execution "
            "from Windows is not available in this release."
        )


def _worker_process_options(platform_name: str | None = None) -> dict[str, int | bool]:
    platform_name = platform_name or sys.platform
    if platform_name == "win32":
        flags = getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
        flags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
        return {"creationflags": flags, "close_fds": True}
    return {"start_new_session": True, "close_fds": True}


def _windows_process_running(pid: int) -> bool:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return ctypes.get_last_error() == 5  # Access denied still indicates a live process.
    try:
        exit_code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return True
        return exit_code.value == 259  # STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def _try_acquire_lock(lock_file: Any) -> bool:
    if os.name == "nt":
        import msvcrt

        lock_file.seek(0, os.SEEK_END)
        if lock_file.tell() == 0:
            lock_file.write(b"\0")
            lock_file.flush()
        lock_file.seek(0)
        try:
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True

    import fcntl

    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    return True


def _reap_children() -> None:
    for pid, process in list(_CHILDREN.items()):
        if process.poll() is not None:
            del _CHILDREN[pid]


def _write_json(path: Path, value: dict[str, Any]) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, indent=2) + "\n")
    temp.replace(path)


def _time() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobStore:
    def __init__(self, root: Path):
        self.root = root.expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root.chmod(0o700)

    def launch(self, plan: Plan) -> str:
        _validate_execution_host(plan)
        _reap_children()
        job_id = uuid.uuid4().hex
        job_dir = self.root / job_id
        job_dir.mkdir(mode=0o700)
        record = {"id": job_id, "created_at": _time(), "plan": plan.as_dict(), "pid": None}
        _write_json(job_dir / "job.json", record)
        environment = os.environ.copy()
        environment.update(plan.environment)
        python_bin = str(Path(sys.executable).parent)
        environment["PATH"] = os.pathsep.join(
            part for part in (python_bin, environment.get("PATH", "")) if part
        )
        try:
            process = subprocess.Popen(
                [sys.executable, "-m", "neuroflow.worker", str(job_dir)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                **_worker_process_options(),
                env=environment,
            )
        except OSError as exc:
            (job_dir / "job.log").write_text(f"Worker could not start: {exc}\n")
            _write_json(job_dir / "result.json", {
                "status": "failed", "exit_code": 1, "started_at": _time(),
                "finished_at": _time(),
            })
            raise RuntimeError(f"Worker could not start: {exc}") from exc
        record["pid"] = process.pid
        _CHILDREN[process.pid] = process
        _write_json(job_dir / "job.json", record)
        return job_id

    def get(self, job_id: str) -> dict[str, Any]:
        if len(job_id) != 32 or any(c not in "0123456789abcdef" for c in job_id):
            raise FileNotFoundError(job_id)
        job_dir = self.root / job_id
        record = json.loads((job_dir / "job.json").read_text())
        result = job_dir / "result.json"
        if result.exists():
            record.update(json.loads(result.read_text()))
        else:
            progress = job_dir / "progress.json"
            if progress.exists():
                record.update(json.loads(progress.read_text()))
            pid = record.get("pid")
            try:
                if pid is None:
                    record["status"] = "queued"
                elif sys.platform == "win32":
                    record["status"] = "running" if _windows_process_running(pid) else "interrupted"
                else:
                    os.kill(pid, 0)
                    record["status"] = "running"
            except ProcessLookupError:
                record["status"] = "interrupted"
            except PermissionError:
                record["status"] = "unknown"
        return record

    def list(self) -> list[dict[str, Any]]:
        result = []
        for item in self.root.iterdir():
            if item.is_dir() and (item / "job.json").is_file():
                try:
                    result.append(self.get(item.name))
                except (OSError, ValueError, KeyError):
                    continue
        return sorted(result, key=lambda item: item["created_at"], reverse=True)

    def log(self, job_id: str) -> str:
        self.get(job_id)
        path = self.root / job_id / "job.log"
        return path.read_text(errors="replace") if path.exists() else ""

    def pipeline_log(self, job_id: str) -> str:
        record = self.get(job_id)
        path = self.root / job_id / "pipeline.log"
        if path.exists() and path.stat().st_size:
            sections = [_read_log_tail(path)]
            process_snapshot = _worker_process_snapshot(record)
            if process_snapshot:
                sections.append(process_snapshot)
            return "\n\n".join(sections)

        spec = record["plan"]["spec"]
        output = Path(spec["output_dir"]).expanduser()
        participant = str(spec.get("participant", ""))
        subject = participant if participant.startswith("sub-") else f"sub-{participant}"
        workflow = spec.get("workflow")
        if workflow == "bold":
            candidates = [
                output / "mefmriprep" / "logs" / "fmriprep.log",
                output / ".work" / "mefmriprep" / "freesurfer_subjects" / subject
                / "scripts" / "recon-all.log",
            ]
        elif workflow == "anat":
            candidates = [
                output / "logs" / f"{subject}.log",
                output / "freesurfer" / "subjects" / subject / "scripts" / "recon-all.log",
            ]
        elif workflow == "dwi":
            log_dir = output / "dwiprep" / "logs"
            candidates = sorted(log_dir.glob("*.log"), key=lambda item: item.stat().st_mtime)[-4:] \
                if log_dir.is_dir() else []
        else:
            candidates = []
        sections = []
        for candidate in candidates:
            if candidate.is_file():
                sections.append(f"=== {candidate.name} ===\n{_read_log_tail(candidate)}")
        process_snapshot = _worker_process_snapshot(record)
        if process_snapshot:
            sections.append(process_snapshot)
        return "\n\n".join(sections)


def _worker_process_snapshot(record: dict[str, Any], limit: int = 12) -> str:
    pid = record.get("pid")
    if record.get("status") != "running" or not isinstance(pid, int):
        return ""
    try:
        result = subprocess.run(
            ["ps", "-ww", "-axo", "pid=,ppid=,pgid=,etime=,%cpu=,%mem=,comm=,args="],
            capture_output=True,
            text=True,
            timeout=2,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return ""

    processes = []
    for line in result.stdout.splitlines():
        fields = line.split(None, 7)
        if len(fields) < 8:
            continue
        try:
            process_group = int(fields[2])
            cpu = float(fields[4])
        except ValueError:
            continue
        if process_group == pid:
            command = fields[7][:300]
            processes.append((cpu, f"PID {fields[0]}  {fields[3]}  CPU {fields[4]}%  {command}"))
    if not processes:
        return "=== Active worker processes ===\nNo process details are currently available."
    processes.sort(key=lambda item: item[0], reverse=True)
    lines = ["=== Active worker processes ==="]
    lines.extend(line for _, line in processes[:limit])
    return "\n".join(lines)


def _read_log_tail(path: Path, limit: int = 24000) -> str:
    with path.open("rb") as stream:
        stream.seek(0, os.SEEK_END)
        size = stream.tell()
        stream.seek(max(0, size - limit))
        return stream.read().decode("utf-8", errors="replace")


def run_worker(job_dir: Path) -> int:
    record = json.loads((job_dir / "job.json").read_text())
    spec = record["plan"]["spec"]
    commands = record["plan"].get("commands") or [record["plan"]["command"]]
    stages = spec.get("stages") or [spec["stage"]]
    lock_key = f"{spec['output_dir']}|{spec['workflow']}|{spec['participant']}|{spec['session']}"
    lock_name = hashlib.sha256(lock_key.encode()).hexdigest() + ".lock"
    lock_path = job_dir.parent / lock_name
    started = _time()
    exit_code = 75
    try:
        with lock_path.open("a+b") as lock:
            if not _try_acquire_lock(lock):
                with (job_dir / "job.log").open("a") as log:
                    log.write("Another job for this workflow, subject, and output root is running.\n")
            else:
                with (job_dir / "job.log").open("ab", buffering=0) as log:
                    env = os.environ.copy()
                    env.update(record["plan"].get("environment", {}))
                    if spec.get("execution_engine") == "nextflow":
                        env["NEUROFLOW_TASK_LOG"] = str(job_dir / "pipeline.log")
                    for index, command in enumerate(commands):
                        stage = stages[index]
                        _write_json(job_dir / "progress.json", {
                            "current_stage": stage, "completed_stages": index,
                            "total_stages": len(commands),
                        })
                        log.write(f"\n=== Stage {index + 1}/{len(commands)}: {stage} ===\n".encode())
                        run_cwd = None
                        if spec.get("execution_engine") == "nextflow":
                            run_cwd = Path(spec["output_dir"]).expanduser().resolve() / ".neuroflow" / "launch"
                            run_cwd.mkdir(parents=True, exist_ok=True)
                        exit_code = subprocess.call(command, stdout=log, stderr=subprocess.STDOUT, env=env, cwd=run_cwd)
                        if exit_code:
                            log.write(f"Stage {stage} failed with exit code {exit_code}; remaining stages skipped.\n".encode())
                            break
                    if exit_code == 0:
                        _write_json(job_dir / "progress.json", {
                            "current_stage": "", "completed_stages": len(commands),
                            "total_stages": len(commands),
                        })
                    if exit_code == 0 and spec["workflow"] == "dicom" and spec["stage"] == "convert":
                        output = Path(spec["output_dir"])
                        subject = output / f"sub-{spec['participant']}"
                        target = subject / f"ses-{spec['session']}" if spec["session"] else subject
                        has_images = any(target.rglob("*.nii.gz")) or any(target.rglob("*.nii"))
                        if not (output / "dataset_description.json").is_file() or not has_images:
                            log.write(b"Conversion produced no BIDS imaging files; validation not attempted.\n")
                            exit_code = 1
                    post = record["plan"].get("post_command", [])
                    if exit_code == 0 and post:
                        log.write(b"\nRunning BIDS validator after conversion.\n")
                        exit_code = subprocess.call(post, stdout=log, stderr=subprocess.STDOUT, env=env)
    except OSError as exc:
        with (job_dir / "job.log").open("a") as log:
            log.write(f"Job launch failed: {exc}\n")
        exit_code = 1
    _write_json(job_dir / "result.json", {
        "status": "completed" if exit_code == 0 else "failed",
        "exit_code": exit_code,
        "started_at": started,
        "finished_at": _time(),
        "completed_stages": json.loads((job_dir / "progress.json").read_text())["completed_stages"] if (job_dir / "progress.json").exists() else 0,
        "total_stages": len(commands),
    })
    return exit_code
