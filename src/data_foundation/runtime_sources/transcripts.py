"""Conservative, format-specific readers for documented local transcripts.

Only explicit user/assistant text is narrative material. Tool inputs/results,
reasoning, credentials and provider configuration are never imported here.
"""

import base64
import json
import re
from collections import defaultdict
from pathlib import Path

from .base import DialogueRecord, SessionRecord, UsageRecord, json_object, nonnegative_int, text_content, timestamp
from .files import CachedFileRuntime, discover, identity, markdown_document


class TranscriptRuntime(CachedFileRuntime):
    def record(self, sid, mid, role, content, at, path, *, cwd=None, title=None, model=None, metadata=None):
        if not sid or not mid or role not in {"user", "assistant"}:
            return
        at = timestamp(at)
        key = str(sid)
        old = self._sessions.get(key)
        times = [t for t in (at, old.started_at if old else None, old.last_active_at if old else None) if t]
        self._sessions[key] = SessionRecord(key, min(times) if times else None, max(times) if times else None,
            cwd or (old.initial_cwd if old else None), title or (old.title if old else None),
            agent_key=self.tool_key, model_key=model or (old.model_key if old else None),
            metadata={"coverage": self.coverage}, raw_locator={"path": str(path)})
        if not isinstance(content, str) or not content.strip():
            return
        self._dialogue[str(mid)] = DialogueRecord(str(mid), key, role, content.strip(), at,
            metadata=metadata or {}, raw_locator={"path": str(path), "message_id": str(mid)})

    def lines(self, path, root):
        for index, line in enumerate(self.read_text(path, root).splitlines()):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
                if isinstance(value, dict):
                    yield index, value
            except ValueError:
                # A running writer can leave a partial final line. Do not lose earlier records.
                self.problem(path, "invalid-json-line")


class QwenCodeRuntime(TranscriptRuntime):
    tool_key = "qwen-code"
    coverage = "local-records"
    usage_status = "local-partial"
    capabilities = TranscriptRuntime.capabilities | {"usage_events_partial"}

    def __init__(self, projects_root: Path, exports_root: Path):
        super().__init__()
        self.projects_root, self.exports_root = Path(projects_root), Path(exports_root)

    def artifacts(self):
        return tuple(sorted(set(discover(self.projects_root, "**/chats/*.jsonl", "**/chats/*.json") + discover(self.exports_root, "*.jsonl", "*.json"))))

    def _load(self):
        for path in self.artifacts():
            root = self.exports_root if path.is_relative_to(self.exports_root) else self.projects_root
            try:
                if path.suffix == ".jsonl":
                    rows = list(self.lines(path, root))
                else:
                    data = self.read_json(path, root)
                    rows = list(enumerate(data if isinstance(data, list) else data.get("messages", [])))
                for index, row in rows:
                    if not isinstance(row, dict):
                        continue
                    role = row.get("type")
                    if role not in {"user", "assistant"} or row.get("forkedFrom"):
                        continue
                    msg = json_object(row.get("message"))
                    parts = msg.get("parts", [])
                    content = "\n".join(p["text"] for p in parts if isinstance(p, dict) and isinstance(p.get("text"), str) and not p.get("thought"))
                    sid = str(row.get("sessionId") or "")
                    mid = str(row.get("uuid") or "")
                    if not sid or not mid:
                        continue
                    self.record(sid, mid, role, content, row.get("timestamp"), path, cwd=row.get("cwd"), model=row.get("model"))
                    usage = json_object(row.get("usageMetadata"))
                    at = timestamp(row.get("timestamp"))
                    if role == "assistant" and usage and at is not None:
                        prompt, cache = nonnegative_int(usage.get("promptTokenCount")), nonnegative_int(usage.get("cachedContentTokenCount"))
                        if cache > prompt:
                            self.problem(path, "invalid-token-usage")
                            continue
                        self._usage[mid] = UsageRecord(mid, sid, at, row.get("model"), input_tokens=max(0, prompt-cache),
                            output_tokens=nonnegative_int(usage.get("candidatesTokenCount")), cache_read_tokens=cache,
                            reasoning_tokens=nonnegative_int(usage.get("thoughtsTokenCount")),
                            protocol_total_tokens=nonnegative_int(usage.get("totalTokenCount")) or None,
                            metadata={"usage_basis": "message-usageMetadata"}, raw_locator={"path": str(path), "uuid": mid})
            except (OSError, ValueError, TypeError, AttributeError):
                self.problem(path)


class CopilotCliRuntime(TranscriptRuntime):
    tool_key = "copilot-cli"
    coverage = "cli-only"
    capabilities = TranscriptRuntime.capabilities | {"source_documents"}

    def __init__(self, sessions_root: Path):
        super().__init__()
        self.root = Path(sessions_root)

    def artifacts(self):
        return discover(self.root, "*/events.jsonl", "*/plan.md", "*/checkpoints/*.md")

    def _load(self):
        for path in self.artifacts():
            sid = path.relative_to(self.root).parts[0]
            if path.suffix == ".md":
                try:
                    self.add_document(markdown_document(path, self.root, sid, kind="plan" if path.name == "plan.md" else "checkpoint"))
                except (OSError, ValueError):
                    self.problem(path)
                continue
            try:
                events = list(self.lines(path, self.root))
                context = {}
                for _, event in events:
                    if event.get("type") == "session.start":
                        data = json_object(event.get("data")); context = json_object(data.get("context"))
                        at = timestamp(event.get("timestamp"))
                        self._sessions[sid] = SessionRecord(sid, at, at, context.get("cwd"), agent_key=self.tool_key,
                            metadata={"coverage": self.coverage}, raw_locator={"path": str(path)})
                for _, event in events:
                    if event.get("type") not in {"user.message", "assistant.message"}:
                        continue
                    data = json_object(event.get("data"))
                    if data.get("isAutopilotContinuation") or str(data.get("source") or "").startswith("skill-"):
                        continue
                    mid = event.get("id")
                    if mid:
                        self.record(sid, str(mid), event["type"].split('.')[0], text_content(data.get("content")),
                            event.get("timestamp"), path, cwd=context.get("cwd"))
            except (OSError, ValueError, TypeError):
                self.problem(path)


class ContinueRuntime(TranscriptRuntime):
    tool_key = "continue"
    coverage = "ide-history"

    def __init__(self, sessions_root: Path):
        super().__init__()
        self.root = Path(sessions_root)

    def artifacts(self):
        return tuple(p for p in discover(self.root, "*.json") if p.name != "sessions.json")

    def _load(self):
        for path in self.artifacts():
            try:
                data = self.read_json(path, self.root)
                if not isinstance(data, dict) or not isinstance(data.get("history"), list):
                    self.problem(path, "unsupported-schema")
                    continue
                sid = str(data.get("sessionId") or path.stem)
                self._sessions[sid] = SessionRecord(sid, initial_cwd=data.get("workspaceDirectory"), title=data.get("title"),
                    agent_key=self.tool_key, model_key=data.get("chatModelTitle"), metadata={"coverage": self.coverage}, raw_locator={"path": str(path)})
                for index, item in enumerate(data["history"]):
                    msg = json_object(item.get("message")) if isinstance(item, dict) else {}
                    self.record(sid, identity(sid, msg.get("id") or index), msg.get("role"), text_content(msg.get("content")),
                        (item.get("timestamp") if isinstance(item, dict) else None) or msg.get("timestamp"), path,
                        cwd=data.get("workspaceDirectory"), title=data.get("title"), model=data.get("chatModelTitle"))
                # The format often has no per-message timestamps. Keep those records undated,
                # rather than inventing a work date from the last file write.
            except (OSError, ValueError, TypeError):
                self.problem(path)


class ClineRuntime(TranscriptRuntime):
    tool_key = "cline"
    coverage = "extension-history"
    usage_status = "local-partial"
    capabilities = TranscriptRuntime.capabilities | {"usage_events_partial"}

    def __init__(self, roots):
        super().__init__()
        self.roots = tuple(dict.fromkeys(Path(p) for p in roots))

    def artifacts(self):
        return tuple(sorted({p for root in self.roots for p in discover(root, "*/ui_messages.json", "*/api_conversation_history.json", "*/task_metadata.json")}))

    def _load(self):
        for root in self.roots:
            directories = sorted({p.parent for p in discover(root, "*/ui_messages.json", "*/api_conversation_history.json")})
            for directory in directories:
                path = directory / "ui_messages.json"
                try:
                    rows = self.read_json(path, root) if path.is_file() else []
                    if not isinstance(rows, list):
                        self.problem(path, "unsupported-schema")
                        continue
                    sid = identity(root, directory.name)
                    api_path = directory / "api_conversation_history.json"
                    api = self.read_json(api_path, root) if api_path.is_file() else []
                    stamps = [timestamp(row.get("ts")) for row in rows if isinstance(row, dict)]
                    stamps = [at for at in stamps if at is not None]
                    self._sessions[sid] = SessionRecord(sid, min(stamps) if stamps else None, max(stamps) if stamps else None,
                        agent_key=self.tool_key, metadata={"coverage": self.coverage}, raw_locator={"path": str(directory)})
                    times_by_text = defaultdict(list)
                    for row in rows:
                        if isinstance(row, dict) and not row.get("partial") and isinstance(row.get("text"), str):
                            times_by_text[row["text"]].append(row.get("ts"))
                    # UI 'say:text' is also used for the initial USER prompt. Roles
                    # come from API history; never guess all UI text is assistant output.
                    for index, message in enumerate(api if isinstance(api, list) else []):
                        if not isinstance(message, dict):
                            continue
                        content = text_content(message.get("content"))
                        candidates = times_by_text.get(content, ())
                        at = candidates[0] if len(candidates) == 1 else None
                        self.record(sid, identity(sid, "api", index), message.get("role"), content, at, api_path)
                    seen_usage = set()
                    for index, row in enumerate(rows):
                        if not isinstance(row, dict) or row.get("partial"):
                            continue
                        at, subtype = row.get("ts"), row.get("say") or row.get("ask")
                        mid = identity(sid, at, row.get("type"), subtype, index)
                        if not api and subtype in {"completion_result", "user_feedback"}:
                            role = "user" if subtype == "user_feedback" else "assistant"
                            self.record(sid, mid, role, row.get("text"), at, path)
                        elif subtype == "api_req_started":
                            usage = json_object(row.get("text"))
                            stamp = timestamp(at)
                            if stamp is not None and at not in seen_usage and any(k in usage for k in ("tokensIn", "tokensOut", "cacheReads", "cacheWrites")):
                                seen_usage.add(at)
                                self._usage[mid] = UsageRecord(mid, sid, stamp, input_tokens=nonnegative_int(usage.get("tokensIn")),
                                    output_tokens=nonnegative_int(usage.get("tokensOut")), cache_read_tokens=nonnegative_int(usage.get("cacheReads")),
                                    cache_write_tokens=nonnegative_int(usage.get("cacheWrites")), raw_locator={"path": str(path), "ts": at})
                except (OSError, ValueError, TypeError):
                    self.problem(path)


class AiderRuntime(TranscriptRuntime):
    tool_key = "aider"
    coverage = "explicit-history-files"
    capabilities = frozenset({"session_inventory", "workspace_metadata", "source_documents"})

    def __init__(self, imports_root: Path, history_files=()):
        super().__init__()
        self.root = Path(imports_root)
        self.files = tuple(Path(p) for p in history_files)

    def artifacts(self):
        return tuple(sorted(set(discover(self.root, "*.md") + tuple(p for p in self.files if p.is_file()))))

    def _load(self):
        for path in self.artifacts():
            try:
                body = self.read_text(path, self.root if path.is_relative_to(self.root) else path.parent)
                # Markdown headings can be part of either side's text. Preserve the
                # original export as evidence instead of fabricating message roles/times.
                if not re.search(r"(?m)^# aider chat started at ", body):
                    self.problem(path, "unsupported-history-format")
                    continue
                sid = identity("aider-file", path)
                self._sessions[sid] = SessionRecord(sid, initial_cwd=str(path.parent), title=path.name,
                    agent_key=self.tool_key, metadata={"coverage": self.coverage, "session_count_basis": "history-file"}, raw_locator={"path": str(path)})
                self.add_document(markdown_document(path, self.root if path.is_relative_to(self.root) else path.parent, sid, kind="conversation-export"))
            except (OSError, ValueError):
                self.problem(path)


class GrokBotRuntime(TranscriptRuntime):
    tool_key = "grok-bot"
    coverage = "cache-only"

    def __init__(self, roots):
        super().__init__()
        self.roots = tuple(Path(p) for p in roots)

    @staticmethod
    def _session_key(path):
        try:
            name = path.stem
            decoded = base64.b32decode(name.upper() + '=' * ((-len(name)) % 8)).decode('utf-8')
            if '.transcript.replicas.' not in decoded:
                return None
            return identity("grok-cache", decoded)
        except (ValueError, UnicodeError):
            return None

    def artifacts(self):
        return tuple(sorted({p for root in self.roots for p in discover(root, "*.blob") if self._session_key(p)}))

    def _load(self):
        for path in self.artifacts():
            root = next(r for r in self.roots if path.is_relative_to(r))
            sid = self._session_key(path)
            try:
                data = self.read_json(path, root)
                if not isinstance(data, dict) or data.get("schemaVersion") != 1:
                    self.problem(path, "unsupported-cache-schema")
                    continue
                entries = json_object(data.get("value")).get("entries", [])
                for entry in entries:
                    if not isinstance(entry, dict) or entry.get("isStreaming") or not entry.get("id"):
                        continue
                    role, content = None, None
                    if entry.get("kind") == "message":
                        role, content = entry.get("role"), entry.get("content")
                    elif entry.get("kind") == "send-message":
                        msg = json_object(entry.get("message"))
                        if msg.get("type") == "text":
                            role, content = "assistant", msg.get("content")
                    self.record(sid, identity(sid, entry["id"]), role, text_content(content), entry.get("timestampMs"), path,
                        metadata={"coverage": "cache-only", "experimental": True})
            except (OSError, ValueError, TypeError):
                self.problem(path)
