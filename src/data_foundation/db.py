"""SQLite connection and versioned migration utilities."""

from __future__ import annotations

import json
import fcntl
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterable, Iterator

from .paths import RuntimePaths, load_paths

MIGRATIONS_DIR = Path(__file__).with_name("migrations")
MIGRATION_TRANSACTION_RE = re.compile(
    r"(?im)^\s*(?:BEGIN(?:\s+TRANSACTION)?|COMMIT|ROLLBACK|END\s+TRANSACTION)\b"
)
MIGRATION_FOREIGN_KEYS_RE = re.compile(r"(?im)^\s*PRAGMA\s+foreign_keys\s*=\s*(?:ON|OFF)\s*;?\s*$")


@contextmanager
def connect(paths: RuntimePaths | None = None, *, read_only: bool = False) -> Iterator[sqlite3.Connection]:
    db_path = (paths or load_paths()).db_path
    if read_only:
        connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    else:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(db_path)
        connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.row_factory = sqlite3.Row
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def migrate(paths: RuntimePaths | None = None) -> list[str]:
    selected = paths or load_paths()
    applied: list[str] = []
    lock_path = selected.state_dir / "locks" / "foundation-migration.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as lock_handle:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        with connect(selected) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
            connection.commit()
            known = {row[0] for row in connection.execute("SELECT version FROM schema_migrations")}
            for migration in sorted(MIGRATIONS_DIR.glob("*.sql")):
                if migration.stem in known:
                    continue
                script = migration.read_text(encoding="utf-8")
                if MIGRATION_TRANSACTION_RE.search(script):
                    raise ValueError(f"migration must not manage its own transaction: {migration.name}")
                disables_foreign_keys = bool(re.search(r"(?im)^\s*PRAGMA\s+foreign_keys\s*=\s*OFF\s*;?\s*$", script))
                transactional_script = MIGRATION_FOREIGN_KEYS_RE.sub("", script)
                try:
                    if disables_foreign_keys:
                        connection.execute("PRAGMA foreign_keys=OFF")
                    # sqlite3.executescript() commits any pending transaction
                    # before it runs. Put BEGIN in the same script invocation so
                    # every DDL/DML statement remains open until the version row
                    # is inserted and the unit is explicitly committed below.
                    connection.executescript("BEGIN IMMEDIATE;\n" + transactional_script + "\n")
                    connection.execute(
                        "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                        (migration.stem, datetime.now().astimezone().isoformat()),
                    )
                    if disables_foreign_keys and connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
                        raise sqlite3.IntegrityError(f"foreign key check failed after migration: {migration.name}")
                    connection.commit()
                except Exception:
                    connection.rollback()
                    raise
                finally:
                    if disables_foreign_keys:
                        connection.execute("PRAGMA foreign_keys=ON")
                applied.append(migration.stem)
    return applied


def seed_projects(paths: RuntimePaths, projects: Iterable[dict[str, object]]) -> None:
    now = datetime.now().astimezone().isoformat()
    with connect(paths) as connection:
        for project in projects:
            connection.execute(
                """
                INSERT INTO projects(canonical_name, canonical_root, enabled, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(canonical_name) DO UPDATE SET
                    canonical_root=excluded.canonical_root,
                    enabled=excluded.enabled,
                    updated_at=excluded.updated_at
                """,
                (
                    str(project["canonical_name"]),
                    str(Path(str(project["canonical_root"])).expanduser().absolute()),
                    int(bool(project.get("enabled", True))),
                    now,
                    now,
                ),
            )
            project_id = connection.execute(
                "SELECT id FROM projects WHERE canonical_name = ?", (str(project["canonical_name"]),)
            ).fetchone()["id"]
            for alias in project.get("aliases", []):
                connection.execute(
                    "INSERT OR IGNORE INTO project_aliases(project_id, alias, alias_type) VALUES (?, ?, ?)",
                    (project_id, str(alias), "manual"),
                )


def seed_projects_from_registry(paths: RuntimePaths) -> int:
    registry_path = paths.config_dir / "projects-registry.json"
    try:
        data = json.loads(registry_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return 0
    projects = data.get("projects", []) if isinstance(data, dict) else []
    valid = [
        project
        for project in projects
        if isinstance(project, dict) and project.get("canonical_name") and project.get("canonical_root")
    ]
    seed_projects(paths, valid)
    return len(valid)
