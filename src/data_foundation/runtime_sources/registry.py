"""One factory shared by ingestion, narrative collection and Dashboard readers."""

from pathlib import Path

from ..external_tool_definitions import FOUNDATION_TO_CATALOG, TOOL_CATALOG
from .antigravity import AntigravityRuntime
from .cursor import CursorRuntime
from .opencode import OpenCodeRuntime
from .zcode import ZCodeRuntime
from .transcripts import AiderRuntime, ClineRuntime, ContinueRuntime, CopilotCliRuntime, GrokBotRuntime, QwenCodeRuntime

NEW_RUNTIME_IDS = ("zcode", "qwenCode", "copilotCli", "cline", "continue", "aider", "grokBot")
LOCAL_RUNTIME_IDS = ("opencode", "antigravity", "cursor", *NEW_RUNTIME_IDS)


def build_runtime(tool_id, fields):
    tool_id = FOUNDATION_TO_CATALOG.get(tool_id, tool_id)
    p = lambda key: Path(fields[key]).expanduser()
    many = lambda key: tuple(Path(v).expanduser() for v in fields.get(key, ()))
    if tool_id == "opencode":
        return OpenCodeRuntime(p("home"))
    if tool_id == "antigravity":
        return AntigravityRuntime({key: p(field) for key, field in (("cli", "cliHome"), ("ide", "ideHome"), ("app", "appHome"))})
    if tool_id == "cursor":
        return CursorRuntime(p("home"), ide_state_dbs=many("ideStateDbCandidates"), workspace_storage_roots=many("workspaceStorageRoots"))
    if tool_id == "zcode":
        return ZCodeRuntime(p("home"), p("databasePath"))
    if tool_id == "qwenCode":
        return QwenCodeRuntime(p("projectsRoot"), p("exportsRoot"))
    if tool_id == "copilotCli":
        return CopilotCliRuntime(p("sessionsRoot"))
    if tool_id == "cline":
        return ClineRuntime((p("tasksRoot"), *many("taskRootCandidates")))
    if tool_id == "continue":
        return ContinueRuntime(p("sessionsRoot"))
    if tool_id == "aider":
        return AiderRuntime(p("importsRoot"), many("historyFiles"))
    if tool_id == "grokBot":
        return GrokBotRuntime(many("cacheRoots"))
    raise ValueError("Unsupported local runtime")


def configured_runtime(tool_id, paths=None):
    from ..settings import resolve_external_tool_paths
    tool_id = FOUNDATION_TO_CATALOG.get(tool_id, tool_id)
    return build_runtime(tool_id, resolve_external_tool_paths(paths)[tool_id])


def usage_status(tool_id):
    tool_id = FOUNDATION_TO_CATALOG.get(tool_id, tool_id)
    capabilities = TOOL_CATALOG.get(tool_id, {}).get("capabilities", ())
    return "unavailable" if "usage-unavailable" in capabilities else "local-partial" if "usage-partial" in capabilities else "available"
