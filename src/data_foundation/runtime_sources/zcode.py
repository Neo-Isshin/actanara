"""ZCode's local SQLite records. Never read settings, credentials or raw model IO."""

from pathlib import Path
import sqlite3

from .base import DialogueRecord, DocumentRecord, SessionRecord, UsageRecord, connect_sqlite_read_only, json_object, nonnegative_int, sqlite_columns, sqlite_tables, timestamp
from .files import CachedFileRuntime, MAX_TOTAL_BYTES, identity, safe_path
from ..token_semantics import normalize_cached_input_detail


class ZCodeRuntime(CachedFileRuntime):
    tool_key = "zcode"
    usage_status = "local-partial"
    capabilities = frozenset({"session_inventory", "dialogue", "usage_events_partial", "workspace_metadata", "model_metadata", "source_documents"})

    def __init__(self, home: Path, database_path: Path | None = None):
        super().__init__()
        self.home = Path(home)
        self.db_path = Path(database_path) if database_path else self.home / "cli/db/db.sqlite"

    def artifacts(self):
        return (self.db_path,) if self.db_path.is_file() and safe_path(self.db_path, self.db_path.parent) else ()

    def _load(self):
        if not self.artifacts():
            return
        try:
            with connect_sqlite_read_only(self.db_path) as c:
                c.execute("BEGIN")
                tables = sqlite_tables(c)
                if not {"session", "message", "part"} <= tables:
                    self.problem(self.db_path, "unsupported-schema")
                    return
                # Only source/session metadata columns. Never SELECT from credential/config tables.
                for row in c.execute('SELECT id, directory, title, parent_id, time_created, time_updated FROM session ORDER BY time_created, id'):
                    sid = "local:" + str(row["id"])
                    self._sessions[sid] = SessionRecord(
                        sid, timestamp(row["time_created"]), timestamp(row["time_updated"]), row["directory"], row["title"],
                        agent_key="zcode", metadata={"parent_session_id": row["parent_id"], "coverage": "local-only"},
                        raw_locator={"path": str(self.db_path), "table": "session", "id": row["id"]},
                    )
                text_bytes = 0
                for row in c.execute('''SELECT p.id, p.session_id, p.time_created, p.data, m.data AS message_data
                    FROM part p JOIN message m ON m.id = p.message_id
                    WHERE json_valid(p.data) AND json_valid(m.data)
                      AND json_extract(p.data, '$.type') = 'text'
                      AND json_extract(m.data, '$.role') IN ('user', 'assistant')
                    ORDER BY p.time_created, p.id'''):
                    part, message = json_object(row["data"]), json_object(row["message_data"])
                    if message.get("synthetic") or part.get("synthetic") or part.get("ignored") or message.get("visibility") in {"hidden", "internal"}:
                        continue
                    content = part.get("text")
                    if not isinstance(content, str) or not content.strip():
                        continue
                    text_bytes += len(content.encode("utf-8"))
                    if text_bytes > MAX_TOTAL_BYTES:
                        self.problem(self.db_path, "source-byte-budget")
                        break
                    mid, sid = "part:" + str(row["id"]), "local:" + str(row["session_id"])
                    if sid not in self._sessions:
                        continue
                    self._dialogue[mid] = DialogueRecord(mid, sid, message["role"], content,
                        timestamp(row["time_created"]), metadata={"source": "message-text"},
                        raw_locator={"path": str(self.db_path), "table": "part", "id": row["id"]})
                if "model_usage" in tables:
                    required = {"id", "session_id", "model_id", "started_at", "completed_at", "input_tokens", "output_tokens", "reasoning_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "provider_total_tokens", "status"}
                    if required <= sqlite_columns(c, "model_usage"):
                        columns = ', '.join(sorted(required))
                        for row in c.execute(f'SELECT {columns} FROM model_usage ORDER BY started_at, id'):
                            at = timestamp(row["completed_at"]) or timestamp(row["started_at"])
                            sid = "local:" + str(row["session_id"])
                            if at is None or sid not in self._sessions:
                                continue
                            values = [nonnegative_int(row[k]) for k in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens", "reasoning_tokens")]
                            if not any(values):
                                continue
                            raw_input = values[0]
                            values[0], values[2], cache_semantics = normalize_cached_input_detail(
                                input_tokens=raw_input, output_tokens=values[1], cache_read_tokens=values[2],
                                reported_total_tokens=row["provider_total_tokens"],
                            )
                            # Per-request rows are authoritative here. Do NOT also add turn_usage,
                            # assistant.tokens or step-finish totals (overlapping materializations).
                            key = "model-request:" + str(row["id"])
                            self._usage[key] = UsageRecord(key, sid, at, row["model_id"], *values,
                                metadata={"usage_basis": "model-request", "request_status": row["status"], "reported_provider_total": row["provider_total_tokens"], "raw_input_tokens": raw_input, "cache_input_semantics": cache_semantics, "reasoning_in_output": True},
                                raw_locator={"path": str(self.db_path), "table": "model_usage", "id": row["id"]})
                    else:
                        self.problem(self.db_path, "unsupported-usage-schema")
                else:
                    self.problem(self.db_path, "usage-table-missing")
                if "todo" in tables:
                    grouped = {}
                    for row in c.execute('SELECT session_id, content, status, position, time_updated FROM todo ORDER BY session_id, position'):
                        grouped.setdefault("local:" + str(row["session_id"]), []).append(dict(row))
                    for sid, items in grouped.items():
                        if sid not in self._sessions:
                            continue
                        content = "# ZCode task checklist\n\nSource-reported status; not independently verified by Actanara.\n\n" + "\n".join(
                            f"- [{'x' if item['status'] == 'completed' else ' '}] {item['content']} ({item['status']})" for item in items)
                        self.add_document(DocumentRecord(identity("zcode-todo", sid), sid, self._sessions[sid].title or "ZCode task checklist", content,
                            kind="task-checklist", occurred_at=timestamp(max(item["time_updated"] or 0 for item in items)),
                            raw_locator={"path": str(self.db_path), "table": "todo", "session_id": sid.removeprefix("local:"), "verification": "source-reported"}))
        except (OSError, ValueError, sqlite3.Error):
            self.problem(self.db_path)
