#!/usr/bin/env python3
"""Open Nova operator CLI.

This is a thin product-facing wrapper around existing service boundaries. It
does not execute arbitrary shell commands.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from data_foundation.external_agent_memory import compact_memory_results, search_memory
from data_foundation.daily_completeness import evaluate_daily_completeness
from data_foundation.diary_metrics import (
    write_diary_metrics_readiness_report,
    write_diary_metrics_table_mismatch_approval,
)
from data_foundation.llm_provider_test import check_llm_provider_availability
from data_foundation.nova_task import diary_tasks_snapshot, pending_candidate_count
from data_foundation.paths import default_oneliner_runtime_home, initialize_home, load_paths, runtime_paths_for_home
from data_foundation.pipeline import run_daily_pipeline
from data_foundation.scheduler_reconcile import reconcile_pipeline_schedule
from data_foundation.onboarding_plan import (
    dump_onboarding_approval_packet_json,
    dump_onboarding_apply_blocked_json,
    dump_onboarding_one_liner_dry_run_json,
    dump_onboarding_one_liner_status_json,
    dump_onboarding_one_liner_validation_matrix_json,
    dump_onboarding_release_gate_json,
    dump_onboarding_rollback_plan_status_json,
    dump_onboarding_subsystem_plan_json,
    format_onboarding_approval_packet,
    format_onboarding_apply_blocked,
    format_onboarding_one_liner_dry_run,
    format_onboarding_one_liner_status,
    format_onboarding_one_liner_validation_matrix,
    format_onboarding_release_gate,
    format_onboarding_rollback_plan_status,
    format_onboarding_subsystem_plan,
    onboarding_approval_packet,
    onboarding_apply_blocked,
    onboarding_apply_runtime_bootstrap,
    onboarding_apply_sandbox,
    onboarding_apply_scheduler_register,
    onboarding_apply_scheduler_plist_write,
    onboarding_apply_scheduler_sandbox,
    onboarding_apply_scheduler_unregister,
    onboarding_one_liner_apply,
    onboarding_one_liner_dry_run,
    onboarding_one_liner_release_gate,
    onboarding_one_liner_status,
    onboarding_one_liner_validation_matrix,
    onboarding_release_gate,
    onboarding_rollback_plan_status,
    onboarding_subsystem_plan,
)
from data_foundation.onboarding_status import (
    dump_nova_onboarding_status_json,
    format_nova_onboarding_status,
    nova_onboarding_status,
)
from data_foundation.settings_status import (
    dump_nova_settings_status_json,
    format_nova_settings_status,
    nova_settings_status,
)
from data_foundation.settings import (
    OPERATOR_SETTINGS_WRITE_ALLOWED_TOP_LEVEL,
    OPERATOR_SETTINGS_WRITE_PROTECTED_TOP_LEVEL,
    read_llm_provider,
    read_settings,
    runtime_authority_contract,
    write_llm_api_key_secret,
    write_llm_provider,
    write_operator_settings,
)
from data_foundation.sqlite_cache_rebuild import (
    SQLITE_CACHE_REBUILD_CONFIRMATION,
    plan_sqlite_cache_rebuild,
    rebuild_sqlite_cache,
)
from advanced.dashboard.dashboard_launch_agent import (
    dashboard_launch_defaults,
    restart_service as restart_dashboard_service,
)
from agentic_rag.rag_settings import resolve_rag_settings
from agentic_rag.rag_v2_sync import plan_v2_production_sync, sync_v2_production_index


RAG_REBUILD_CONFIRMATION = "REBUILD AND PROMOTE OPEN NOVA RAG"
RAG_UPDATE_CONFIRMATION = "UPDATE AND PROMOTE OPEN NOVA RAG"
DIARY_METRICS_APPROVAL_CONFIRMATION = "APPROVE OPEN NOVA DIARY METRICS MISMATCH"
DEFAULT_UPDATE_SOURCE_URL = "https://github.com/Neo-Isshin/open-nova.git"
UPDATE_FULL_COMMIT_RE = re.compile(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})\Z")
LATEST_STABLE_RELEASE_POLICY = "resolve latest stable Release and pin the resolved commit"


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    handler = getattr(args, "handler", None)
    if handler is None:
        sys.stdout.write(_product_command_guide())
        return 0
    return handler(args)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="open-nova", description=__doc__)
    subcommands = parser.add_subparsers(dest="command")

    doctor = subcommands.add_parser("doctor", help="Check Open Nova subsystem status.")
    _add_doctor_args(doctor)
    doctor.set_defaults(handler=_settings_status)

    model = subcommands.add_parser("model", help="Inspect or configure the diary-generation LLM provider.")
    model_subcommands = model.add_subparsers(dest="model_command")
    model.set_defaults(handler=_model_show)
    model_show = model_subcommands.add_parser("show", help="Print the current LLM provider.")
    _add_status_args(model_show)
    model_show.set_defaults(handler=_model_show)
    model_list = model_subcommands.add_parser("list", help="List configured LLM provider catalog entries.")
    _add_status_args(model_list)
    model_list.set_defaults(handler=_model_list)
    model_set = model_subcommands.add_parser("set", help="Update non-secret LLM provider metadata.")
    _add_status_args(model_set)
    model_set.add_argument("--provider", help="Provider id from the catalog, or custom.")
    model_set.add_argument("--model", help="Model id used for diary generation.")
    model_set.add_argument("--endpoint", help="Provider endpoint URL.")
    model_set.add_argument("--api", help="Transport API, such as openai-compatible or anthropic-messages.")
    model_set.add_argument("--context-window", type=int, help="Optional context window tokens.")
    model_set.add_argument("--max-tokens", type=int, help="Optional max output tokens.")
    model_set.add_argument("--pipeline-concurrency", type=int, help="Optional pipeline concurrency.")
    model_set.add_argument("--timeout-seconds", type=int, help="Optional provider timeout.")
    model_set.add_argument("--api-key-env", help="Environment variable name used for process-local key injection.")
    model_set.set_defaults(handler=_model_set)
    model_key = model_subcommands.add_parser("key", help="Store the LLM API key from stdin.")
    _add_status_args(model_key)
    model_key.add_argument("--value-stdin", action="store_true", help="Read the secret value from stdin.")
    model_key.set_defaults(handler=_secrets_set_llm_api_key)
    model_test = model_subcommands.add_parser("test", help="Probe the configured LLM provider without persisting changes.")
    _add_status_args(model_test)
    model_test.set_defaults(handler=_model_test)

    onboard = subcommands.add_parser("onboard", help="Product alias for onboarding setup commands.")
    onboard_subcommands = onboard.add_subparsers(dest="onboard_command")
    onboard.set_defaults(handler=_onboarding_doctor)
    onboard_status = onboard_subcommands.add_parser("status", help="Print onboarding status.")
    _add_onboarding_args(onboard_status)
    onboard_status.set_defaults(handler=_onboarding_doctor)
    onboard_doctor = onboard_subcommands.add_parser("doctor", help="Run onboarding doctor checks.")
    _add_onboarding_args(onboard_doctor)
    onboard_doctor.set_defaults(handler=_onboarding_doctor)
    onboard_plan = onboard_subcommands.add_parser("plan", help="Print an onboarding plan.")
    _add_onboarding_args(onboard_plan)
    onboard_plan.set_defaults(handler=_onboarding_plan)
    onboard_apply = onboard_subcommands.add_parser("apply", help="Run guarded onboarding apply.")
    _add_onboarding_apply_args(onboard_apply)
    onboard_apply.set_defaults(handler=_onboarding_apply_blocked)

    config = subcommands.add_parser("config", help="Inspect or update operator settings through nova-settings.")
    config_subcommands = config.add_subparsers(dest="config_command")
    config.set_defaults(handler=_config_show)
    config_show = config_subcommands.add_parser("show", help="Print current settings.")
    _add_status_args(config_show)
    config_show.set_defaults(handler=_config_show)
    config_doctor = config_subcommands.add_parser("doctor", help="Run settings doctor checks.")
    _add_doctor_args(config_doctor)
    config_doctor.set_defaults(handler=_settings_status)
    config_keys = config_subcommands.add_parser("keys", help="List writable and protected settings groups.")
    _add_status_args(config_keys)
    config_keys.set_defaults(handler=_config_keys)
    config_get = config_subcommands.add_parser("get", help="Read one settings path.")
    _add_status_args(config_get)
    config_get.add_argument("path", help="Dot path, for example general.timezone.")
    config_get.set_defaults(handler=_config_get)
    config_set = config_subcommands.add_parser("set", help="Write one supported operator settings path.")
    _add_status_args(config_set)
    config_set.add_argument("path", help="Dot path under an operator-writable settings group.")
    config_set.add_argument("value", help="Value. JSON scalars/objects are accepted.")
    config_set.set_defaults(handler=_config_set)

    update = subcommands.add_parser(
        "update",
        help="Plan or apply a guarded Open Nova upgrade.",
        description=(
            "Plan or apply a guarded Open Nova upgrade. By default, the bootstrap resolves "
            "the latest stable Release and pins its full commit before installation."
        ),
    )
    _add_status_args(update)
    update_mode = update.add_mutually_exclusive_group()
    update_mode.add_argument("--apply", action="store_true", help="Run the guarded installer upgrade now.")
    update_mode.add_argument("--dry-run", action="store_true", help="Run bootstrap/installer dry-run without mutations.")
    update.add_argument(
        "--ref",
        help=(
            "Explicit remote commit: a full 40- or 64-character hexadecimal object ID. "
            "Omit to resolve the latest stable Release and pin its commit."
        ),
    )
    update.add_argument("--source-url", default=DEFAULT_UPDATE_SOURCE_URL, help="Git source URL for update bootstrap.")
    update.add_argument(
        "--source-root",
        help="Use an existing local source checkout instead of fetching; cannot be combined with --ref.",
    )
    update.add_argument("--cache-root", help="Installer source cache root. Defaults to ~/.cache/open-nova/installer.")
    update.set_defaults(handler=_update_run)

    search = subcommands.add_parser("search", help="Search nova-RAG memory through the external read-only facade.")
    _add_rag_search_args(search)
    search.set_defaults(handler=_search_memory)

    task = subcommands.add_parser("task", help="Print Nova-Task v2 authority counts.")
    _add_status_args(task)
    task.set_defaults(handler=_task_counts)

    rag_rebuild = subcommands.add_parser("rag-rebuild", help="Rebuild nova-RAG v2 and promote the candidate after confirmation.")
    _add_status_args(rag_rebuild)
    rag_rebuild.add_argument("--dry-run", action="store_true", help="Print the rebuild plan without mutations.")
    rag_rebuild.add_argument("--confirm", dest="confirmation_text", help=f'Exact confirmation phrase: "{RAG_REBUILD_CONFIRMATION}"')
    rag_rebuild.set_defaults(handler=_rag_rebuild)

    rag_update = subcommands.add_parser(
        "rag-update",
        help="Build a nova-RAG v2 candidate with active embedding reuse and promote after confirmation.",
    )
    _add_status_args(rag_update)
    rag_update.add_argument("--dry-run", action="store_true", help="Print the candidate sync plan without mutations.")
    rag_update.add_argument("--confirm", dest="confirmation_text", help=f'Exact confirmation phrase: "{RAG_UPDATE_CONFIRMATION}"')
    rag_update.set_defaults(handler=_rag_update)

    settings = subcommands.add_parser("settings", help="Inspect runtime settings.")
    settings_subcommands = settings.add_subparsers(dest="settings_command")
    status = settings_subcommands.add_parser("status", help="Print read-only settings status.")
    _add_status_args(status)
    status.set_defaults(handler=_settings_status)

    doctor = settings_subcommands.add_parser("doctor", help="Run read-only settings doctor checks.")
    _add_doctor_args(doctor)
    doctor.set_defaults(handler=_settings_status)

    onboarding = subcommands.add_parser("onboarding", help="Inspect new-user onboarding readiness.")
    onboarding_subcommands = onboarding.add_subparsers(dest="onboarding_command")
    onboarding_doctor = onboarding_subcommands.add_parser("doctor", help="Run read-only onboarding doctor checks.")
    _add_onboarding_args(onboarding_doctor)
    onboarding_doctor.set_defaults(handler=_onboarding_doctor)
    onboarding_plan = onboarding_subcommands.add_parser("plan", help="Print a read-only selectable subsystem plan.")
    _add_onboarding_args(onboarding_plan)
    onboarding_plan.set_defaults(handler=_onboarding_plan)
    onboarding_one_liner = onboarding_subcommands.add_parser(
        "runtime-dry-run",
        help="Print a read-only runtime bootstrap dry-run schema draft.",
    )
    _add_onboarding_args(onboarding_one_liner)
    onboarding_one_liner.set_defaults(handler=_onboarding_one_liner_dry_run)
    onboarding_one_liner_apply = onboarding_subcommands.add_parser(
        "runtime-apply",
        help="Apply runtime bootstrap; scheduler registration is explicit opt-in.",
    )
    _add_onboarding_args(onboarding_one_liner_apply)
    onboarding_one_liner_apply.add_argument(
        "--confirmation-text",
        help="Exact runtime bootstrap confirmation phrase.",
    )
    onboarding_one_liner_apply.add_argument(
        "--confirm",
        dest="confirmation_text",
        help="Alias for --confirmation-text. Exact phrase is still required.",
    )
    onboarding_one_liner_apply.add_argument(
        "--select-active-runtime",
        action="store_true",
        help="Persist the resolved runtime as the active NOVA_HOME pointer.",
    )
    onboarding_one_liner_apply.add_argument(
        "--with-scheduler",
        action="store_true",
        help="Also write/register managed LaunchAgent scheduler jobs after scheduler confirmation.",
    )
    onboarding_one_liner_apply.add_argument(
        "--scheduler-confirmation-text",
        help="Exact scheduler registration phrase required with --with-scheduler.",
    )
    onboarding_one_liner_apply.add_argument(
        "--use-default-runtime",
        action="store_true",
        help="Use ~/.open-nova when --runtime is not provided.",
    )
    onboarding_one_liner_apply.add_argument(
        "--language",
        help="Install-time language profile for runtime bootstrap: zh-CN or en-US.",
    )
    onboarding_one_liner_apply.set_defaults(handler=_onboarding_one_liner_apply)
    onboarding_one_liner_status = onboarding_subcommands.add_parser(
        "runtime-status",
        help="Inspect runtime bootstrap artifacts without writes.",
    )
    _add_status_args(onboarding_one_liner_status)
    onboarding_one_liner_status.set_defaults(handler=_onboarding_one_liner_status)
    onboarding_one_liner_release = onboarding_subcommands.add_parser(
        "runtime-release-gate",
        help="Print release gates for runtime bootstrap minimal or scheduler opt-in surface.",
    )
    _add_onboarding_args(onboarding_one_liner_release)
    onboarding_one_liner_release.add_argument(
        "--with-scheduler",
        action="store_true",
        help="Include scheduler opt-in gates.",
    )
    onboarding_one_liner_release.set_defaults(handler=_onboarding_one_liner_release_gate)
    onboarding_one_liner_matrix = onboarding_subcommands.add_parser(
        "runtime-validation-matrix",
        help="Print the read-only runtime bootstrap clean-machine validation matrix.",
    )
    _add_status_args(onboarding_one_liner_matrix)
    onboarding_one_liner_matrix.set_defaults(handler=_onboarding_one_liner_validation_matrix)
    onboarding_rollback_plan = onboarding_subcommands.add_parser(
        "rollback-plan",
        help="Print read-only onboarding rollback plan artifacts; does not execute rollback.",
    )
    _add_status_args(onboarding_rollback_plan)
    onboarding_rollback_plan.set_defaults(handler=_onboarding_rollback_plan_status)
    onboarding_release = onboarding_subcommands.add_parser(
        "release-gate",
        help="Print read-only release gates for future onboarding apply.",
    )
    _add_onboarding_args(onboarding_release)
    onboarding_release.add_argument(
        "--confirmation-text",
        help="Exact future apply confirmation phrase. Checked by release-gate preflight only.",
    )
    onboarding_release.add_argument(
        "--confirm",
        dest="confirmation_text",
        help="Alias for --confirmation-text. Does not enable writes.",
    )
    onboarding_release.set_defaults(handler=_onboarding_release_gate)
    onboarding_approval = onboarding_subcommands.add_parser(
        "approval-checklist",
        help="Print read-only operator approvals required before write-capable onboarding apply.",
    )
    _add_onboarding_args(onboarding_approval)
    onboarding_approval.add_argument(
        "--confirmation-text",
        help="Exact future apply confirmation phrase. Checked by included release-gate evidence only.",
    )
    onboarding_approval.add_argument(
        "--confirm",
        dest="confirmation_text",
        help="Alias for --confirmation-text. Does not enable writes.",
    )
    onboarding_approval.set_defaults(handler=_onboarding_approval_packet)
    onboarding_apply = onboarding_subcommands.add_parser(
        "apply",
        help="Blocked skeleton for future onboarding apply; performs no writes.",
    )
    _add_onboarding_apply_args(onboarding_apply)
    onboarding_apply.set_defaults(handler=_onboarding_apply_blocked)

    pipeline = subcommands.add_parser("pipeline", help="Run the daily production pipeline.")
    _add_status_args(pipeline)
    pipeline.add_argument("date", nargs="?", help="Optional business date, YYYY-MM-DD or YYMMDD.")
    pipeline.add_argument("--date", dest="date_flag", help="Optional business date, YYYY-MM-DD or YYMMDD.")
    pipeline.add_argument(
        "--force",
        action="store_true",
        help="Regenerate an already complete day using frozen Foundation inputs.",
    )
    pipeline.set_defaults(handler=_pipeline_run)

    dashboard = subcommands.add_parser("dashboard", help="Operate the local Dashboard service.")
    dashboard_subcommands = dashboard.add_subparsers(dest="dashboard_command")
    dashboard_restart = dashboard_subcommands.add_parser("restart", help="Restart the managed Dashboard LaunchAgent.")
    _add_status_args(dashboard_restart)
    dashboard_restart.add_argument("--label", help="LaunchAgent service label; defaults to configured dashboard service label.")
    dashboard_restart.set_defaults(handler=_dashboard_restart)

    scheduler = subcommands.add_parser("scheduler", help="Operate scheduler reconciliation checks.")
    scheduler_subcommands = scheduler.add_subparsers(dest="scheduler_command")
    scheduler_reconcile = scheduler_subcommands.add_parser(
        "reconcile",
        help="Check for missed daily pipeline runs and optionally apply catch-up rules.",
    )
    _add_status_args(scheduler_reconcile)
    scheduler_reconcile.add_argument("--apply", action="store_true", help="Apply catch-up when missing days are within the automatic limit.")
    scheduler_reconcile.add_argument("--lookback-days", type=int, default=7, help="Number of recent days to inspect.")
    scheduler_reconcile.add_argument("--auto-limit-days", type=int, default=3, help="Maximum missing days to auto catch up.")
    scheduler_reconcile.set_defaults(handler=_scheduler_reconcile)

    foundation = subcommands.add_parser("foundation", help="Operate Foundation SQLite read-model caches.")
    foundation_subcommands = foundation.add_subparsers(dest="foundation_command")
    sqlite_rebuild = foundation_subcommands.add_parser(
        "rebuild-sqlite-cache",
        help="Dangerously replace and rebuild the SQLite read-model cache from current configured sources.",
    )
    _add_status_args(sqlite_rebuild)
    sqlite_rebuild.add_argument("--start-date", help="Optional rebuild start date, YYYY-MM-DD.")
    sqlite_rebuild.add_argument("--end-date", help="Optional rebuild end date, YYYY-MM-DD.")
    sqlite_rebuild.add_argument("--dry-run", action="store_true", help="Preview the rebuild plan without writes.")
    sqlite_rebuild.add_argument(
        "--confirm",
        dest="confirmation_text",
        help=f'Exact confirmation phrase required to execute: "{SQLITE_CACHE_REBUILD_CONFIRMATION}"',
    )
    sqlite_rebuild.set_defaults(handler=_foundation_rebuild_sqlite_cache)
    approve_diary_metrics = foundation_subcommands.add_parser(
        "approve-diary-metrics",
        help="Approve the current diary metrics table mismatch for a business date.",
    )
    _add_status_args(approve_diary_metrics)
    approve_diary_metrics.add_argument("date", help="Business date, YYYY-MM-DD or YYMMDD.")
    approve_diary_metrics.add_argument("--dry-run", action="store_true", help="Preview the approval without writes.")
    approve_diary_metrics.add_argument("--operator", default="operator", help="Operator name recorded in the approval log.")
    approve_diary_metrics.add_argument("--note", default="", help="Operator note recorded in the approval log.")
    approve_diary_metrics.add_argument(
        "--confirm",
        dest="confirmation_text",
        help=f'Exact confirmation phrase required to execute: "{DIARY_METRICS_APPROVAL_CONFIRMATION}"',
    )
    approve_diary_metrics.set_defaults(handler=_foundation_approve_diary_metrics)

    secrets = subcommands.add_parser("secrets", help="Manage local secret references.")
    secrets_subcommands = secrets.add_subparsers(dest="secrets_command")
    set_llm_key = secrets_subcommands.add_parser(
        "set-llm-api-key",
        help="Store the LLM API key in the local secret store; reads the value from stdin.",
    )
    _add_status_args(set_llm_key)
    set_llm_key.add_argument("--value-stdin", action="store_true", help="Read the secret value from stdin.")
    set_llm_key.set_defaults(handler=_secrets_set_llm_api_key)

    return parser


def _product_command_guide() -> str:
    return """Open Nova CLI

Usage:
  open-nova <command> [options]

Common commands:
  open-nova doctor                         Check subsystem status
  open-nova model show                     Show diary-generation LLM provider
  open-nova model list                     List provider catalog entries
  open-nova model set --provider P --model M
  open-nova model set --api-key-env LLM_API_KEY
  open-nova model key --value-stdin        Store LLM API key from stdin
  open-nova onboard status                 Check initialization readiness
  open-nova config show                    Show nova-settings summary
  open-nova config keys                    Show writable settings groups
  open-nova search "query"                 Search nova-RAG memory
  open-nova task                           Show Nova-Task counts
  open-nova pipeline [YYMMDD|YYYY-MM-DD]   Generate diary for today or a date
  open-nova dashboard restart              Restart the managed Dashboard service

Guarded maintenance:
  open-nova rag-update                     Plan nova-RAG candidate sync
  open-nova rag-rebuild                    Plan full nova-RAG rebuild
  open-nova update --apply                 Upgrade from latest stable Release pinned to a commit

Advanced command groups:
  open-nova settings ...
  open-nova onboarding ...
  open-nova foundation ...
  open-nova secrets ...
  open-nova rag search-memory ...

Run `open-nova <command> --help` for command-specific options.
"""


def _add_status_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--runtime", help="Inspect a candidate NOVA_HOME without selecting it.")
    parser.add_argument("--legacy-diary-root", help="Legacy diary root used when --runtime initializes a path object.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")


def _add_doctor_args(parser: argparse.ArgumentParser) -> None:
    _add_status_args(parser)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--installer", action="store_const", const="installer", dest="doctor_profile", help="Show installer/runtime bootstrap checks.")
    group.add_argument("--pipeline", action="store_const", const="pipeline", dest="doctor_profile", help="Show daily pipeline and external tool checks.")
    group.add_argument("--scheduler", action="store_const", const="scheduler", dest="doctor_profile", help="Show launchd scheduler/service checks.")
    group.add_argument("--rag", action="store_const", const="rag", dest="doctor_profile", help="Show nova-RAG service checks.")


def _add_onboarding_args(parser: argparse.ArgumentParser) -> None:
    _add_status_args(parser)
    parser.add_argument(
        "--profile",
        action="append",
        dest="profiles",
        help="Select an onboarding subsystem profile. May be repeated.",
    )


def _add_onboarding_apply_args(parser: argparse.ArgumentParser) -> None:
    _add_onboarding_args(parser)
    parser.add_argument(
        "--confirmation-text",
        help="Exact future apply confirmation phrase. Checked by preflight only; does not enable writes.",
    )
    parser.add_argument(
        "--confirm",
        dest="confirmation_text",
        help="Alias for --confirmation-text. Exact phrase is still required and apply remains blocked.",
    )
    parser.add_argument(
        "--sandbox-apply",
        action="store_true",
        help="Apply to an explicit --runtime sandbox only; never registers scheduler or installs dependencies.",
    )
    parser.add_argument(
        "--runtime-bootstrap-apply",
        action="store_true",
        help="Apply runtime directory/settings/audit bootstrap to explicit --runtime only; scheduler remains blocked.",
    )
    parser.add_argument(
        "--scheduler-sandbox-apply",
        action="store_true",
        help="Write managed launchd plists to a fake --scheduler-home only; never calls launchctl.",
    )
    parser.add_argument(
        "--scheduler-plist-apply",
        action="store_true",
        help="Write managed LaunchAgent plists under ~/Library/LaunchAgents; never calls launchctl.",
    )
    parser.add_argument(
        "--scheduler-register-apply",
        action="store_true",
        help="Register existing managed LaunchAgent plists with launchctl after exact confirmation.",
    )
    parser.add_argument(
        "--scheduler-unregister-apply",
        action="store_true",
        help="Unregister managed LaunchAgent jobs with launchctl bootout after exact confirmation.",
    )
    parser.add_argument(
        "--scheduler-home",
        help="Fake HOME used by --scheduler-sandbox-apply for Library/LaunchAgents writes.",
    )
    parser.add_argument(
        "--select-active-runtime",
        action="store_true",
        help="With --runtime-bootstrap-apply, persist the resolved runtime as the active NOVA_HOME pointer.",
    )
    parser.add_argument(
        "--use-default-runtime",
        action="store_true",
        help="With --runtime-bootstrap-apply, use ~/.open-nova when --runtime is not provided.",
    )
    parser.add_argument(
        "--language",
        help="Install-time language profile for explicit runtime bootstrap/sandbox apply: zh-CN or en-US.",
    )


def _add_rag_search_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("query", help="Search query")
    parser.add_argument("--top-k", type=int, default=5, help="Maximum results, capped at 20")
    parser.add_argument("--dashboard-url", default=None, help="Dashboard base URL; defaults to active Runtime settings")
    parser.add_argument("--timeout", type=float, default=65, help="HTTP timeout in seconds (default: 65)")
    parser.add_argument("--date", default="", help="Optional single business date filter")
    parser.add_argument("--date-from", default="", help="Optional date range start")
    parser.add_argument("--date-to", default="", help="Optional date range end")
    parser.add_argument("--project", default="", help="Optional project filter")
    parser.add_argument("--role", default="", help="Optional role/agent filter")
    parser.add_argument("--source-set", action="append", default=[], help="Optional sourceSet filter; may repeat")
    parser.add_argument("--json", action="store_true", help="Print raw JSON response")


def _settings_status(args: argparse.Namespace) -> int:
    paths = _paths_from_args(args)
    payload = nova_settings_status(paths, doctor_profile=getattr(args, "doctor_profile", None) or "all")
    output = dump_nova_settings_status_json(payload) if args.json else format_nova_settings_status(payload)
    sys.stdout.write(output)
    if not output.endswith("\n"):
        sys.stdout.write("\n")
    return 1 if payload.get("summary", {}).get("errors") else 0


def _model_show(args: argparse.Namespace) -> int:
    provider = read_llm_provider(_paths_from_args(args), persist_defaults=False)
    if args.json:
        sys.stdout.write(json.dumps(provider, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    else:
        sys.stdout.write(
            "Open Nova model: "
            f"{provider.get('provider', '-')} / {provider.get('model', '-')} "
            f"api={provider.get('api', '-')} "
            f"apiKey={'set' if provider.get('hasApiKey') else 'missing'}\n"
        )
        endpoint = provider.get("endpoint")
        if endpoint:
            sys.stdout.write(f"Endpoint: {endpoint}\n")
    return 0


def _model_list(args: argparse.Namespace) -> int:
    provider = read_llm_provider(_paths_from_args(args), persist_defaults=False)
    catalog = provider.get("catalog") if isinstance(provider.get("catalog"), list) else []
    payload = {
        "providers": catalog,
        "count": len(catalog),
        "current": {
            "provider": provider.get("provider"),
            "model": provider.get("model"),
        },
    }
    if args.json:
        sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    else:
        sys.stdout.write(f"Open Nova model catalog: {len(catalog)} provider(s)\n")
        for item in catalog:
            if not isinstance(item, dict):
                continue
            models = item.get("models") if isinstance(item.get("models"), list) else []
            sample = ", ".join(str(model.get("id")) for model in models[:3] if isinstance(model, dict) and model.get("id"))
            suffix = f" models={len(models)}"
            if sample:
                suffix += f" [{sample}]"
            sys.stdout.write(f"- {item.get('id', '-')}: {item.get('name', '-')} api={item.get('api', '-')}{suffix}\n")
    return 0


def _model_set(args: argparse.Namespace) -> int:
    update = {
        key: value
        for key, value in {
            "provider": args.provider,
            "model": args.model,
            "endpoint": args.endpoint,
            "api": args.api,
            "contextWindow": args.context_window,
            "maxTokens": args.max_tokens,
            "pipelineConcurrency": args.pipeline_concurrency,
            "timeoutSeconds": args.timeout_seconds,
            "apiKeyEnv": args.api_key_env,
        }.items()
        if value is not None
    }
    if not update:
        sys.stderr.write("model set requires at least one provider field\n")
        return 2
    try:
        provider = write_llm_provider(update, _paths_from_args(args))
    except Exception as exc:
        sys.stderr.write(f"{exc}\n")
        return 1
    if args.json:
        sys.stdout.write(json.dumps(provider, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    else:
        sys.stdout.write(f"Updated Open Nova model: {provider.get('provider', '-')} / {provider.get('model', '-')}\n")
    return 0


def _model_test(args: argparse.Namespace) -> int:
    result = check_llm_provider_availability(_paths_from_args(args))
    if args.json:
        sys.stdout.write(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    else:
        status = "ok" if result.get("ok") else result.get("status", "failed")
        sys.stdout.write(f"Open Nova model test: {status}\n")
        if result.get("error"):
            sys.stdout.write(f"Error: {result['error']}\n")
    return 0 if result.get("ok") else 1


def _secrets_set_llm_api_key(args: argparse.Namespace) -> int:
    if not args.value_stdin:
        sys.stderr.write("set-llm-api-key requires --value-stdin\n")
        return 2
    value = sys.stdin.read().strip()
    if not value:
        sys.stderr.write("secret value from stdin is empty\n")
        return 2
    try:
        paths = _paths_from_args(args) or load_paths()
        provider = write_llm_api_key_secret(value, paths)
    except Exception as exc:
        sys.stderr.write(f"{exc}\n")
        return 1
    secret_ref = provider.get("secretRef") if isinstance(provider.get("secretRef"), dict) else {}
    if args.json:
        import json

        sys.stdout.write(
            json.dumps(
                {
                    "status": "stored",
                    "runtime": str(paths.home),
                    "backend": secret_ref.get("backend"),
                    "service": secret_ref.get("service"),
                    "account": secret_ref.get("account"),
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        )
    else:
        sys.stdout.write(f"Stored LLM API key reference in {secret_ref.get('backend', 'secret-store')}\n")
    return 0


def _config_show(args: argparse.Namespace) -> int:
    payload = read_settings(_paths_from_args(args), persist_defaults=False)
    if args.json:
        sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    else:
        sys.stdout.write(
            f"Open Nova config: {payload.get('settingsPath', '-')}\n"
            f"Runtime sources: {json.dumps(payload.get('runtimeSources', {}), ensure_ascii=False, sort_keys=True)}\n"
            f"Features: {json.dumps(payload.get('features', {}), ensure_ascii=False, sort_keys=True)}\n"
        )
    return 0


def _config_get(args: argparse.Namespace) -> int:
    payload = read_settings(_paths_from_args(args), persist_defaults=False)
    try:
        value = _get_dot_path(payload, args.path)
    except KeyError:
        sys.stderr.write(f"config path not found: {args.path}\n")
        return 1
    if args.json:
        sys.stdout.write(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    else:
        sys.stdout.write(f"{_format_scalar(value)}\n")
    return 0


def _config_keys(args: argparse.Namespace) -> int:
    contract = runtime_authority_contract(_paths_from_args(args))
    authority = contract.get("settingsAuthority") if isinstance(contract.get("settingsAuthority"), dict) else {}
    groups = authority.get("groups") if isinstance(authority.get("groups"), list) else []
    payload = {
        "settingsPath": contract.get("settingsPath"),
        "writableGroups": sorted(OPERATOR_SETTINGS_WRITE_ALLOWED_TOP_LEVEL),
        "protectedGroups": sorted(OPERATOR_SETTINGS_WRITE_PROTECTED_TOP_LEVEL),
        "dedicatedCommands": {
            "llmProvider": "open-nova model ...",
            "rag": "open-nova rag-update / open-nova rag-rebuild / Dashboard RAG controls",
        },
        "authorityGroups": groups,
    }
    if args.json:
        sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    else:
        sys.stdout.write("Open Nova config keys\n")
        sys.stdout.write("Writable groups: " + ", ".join(payload["writableGroups"]) + "\n")
        sys.stdout.write("Protected groups: " + ", ".join(payload["protectedGroups"]) + "\n")
        sys.stdout.write("Dedicated commands: llmProvider -> open-nova model; rag -> RAG controls\n")
    return 0


def _config_set(args: argparse.Namespace) -> int:
    try:
        update = _nested_update(args.path, _parse_config_value(args.value))
        saved = write_operator_settings(update, _paths_from_args(args))
    except Exception as exc:
        sys.stderr.write(f"{exc}\n")
        return 2
    value = _get_dot_path(saved, args.path)
    if args.json:
        sys.stdout.write(json.dumps({"path": args.path, "value": value}, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    else:
        sys.stdout.write(f"Updated {args.path}: {_format_scalar(value)}\n")
    return 0


def _update_run(args: argparse.Namespace) -> int:
    try:
        _validate_update_source_selection(args)
        paths = _paths_from_args(args) or load_paths()
        command = _update_bootstrap_command(args, paths.home)
    except (FileNotFoundError, ValueError) as exc:
        sys.stderr.write(f"Open Nova update blocked: {exc}\n")
        return 2
    should_execute = bool(args.apply or args.dry_run)
    source_selection = _update_source_selection(args)
    payload = {
        "status": "ready" if not should_execute else "running",
        "dryRun": bool(args.dry_run),
        "apply": bool(args.apply),
        "updateConfigured": True,
        "runtime": str(paths.home),
        "sourceUrl": None if args.source_root else args.source_url,
        "sourceRoot": args.source_root,
        "ref": args.ref,
        "sourceSelection": source_selection,
        "command": command,
        "mutationPolicy": {
            "settingsMutated": bool(args.apply),
            "dependenciesInstalled": bool(args.apply),
            "sourceUpdated": bool(args.apply),
            "managedServicesStoppedBeforePortSelection": False,
            "managedServicesStoppedAfterPreflight": bool(args.apply),
            "schedulerChanged": bool(args.apply),
            "reusesRuntimeVenv": True,
            "preservesSettingsAndUserData": True,
        },
    }
    if not should_execute:
        payload["reason"] = "use --apply to run the guarded installer upgrade, or --dry-run to preview installer actions"
        if args.json:
            sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        else:
            sys.stdout.write(f"Open Nova update {payload['status']}: {payload['reason']}\n")
            sys.stdout.write(f"Source selection: {source_selection['policy']}\n")
            sys.stdout.write("Command preview: " + _shell_join(command) + "\n")
        return 0

    if args.json:
        result = subprocess.run(command, text=True, capture_output=True, check=False)
        payload["status"] = "completed" if result.returncode == 0 else "failed"
        payload["returncode"] = result.returncode
        payload["stdout"] = result.stdout
        payload["stderr"] = result.stderr
        sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        return result.returncode

    sys.stdout.write("Running Open Nova update: " + _shell_join(command) + "\n")
    result = subprocess.run(command, text=True, check=False)
    return result.returncode


def _update_bootstrap_command(args: argparse.Namespace, runtime_home: Path) -> list[str]:
    _validate_update_source_selection(args)
    bootstrap = _update_bootstrap_path(args, runtime_home)
    zsh = shutil.which("zsh") or "/bin/zsh"
    command = [zsh, str(bootstrap)]
    if args.dry_run:
        command.append("--dry-run")
    if args.source_root:
        command.extend(["--source-root", str(Path(args.source_root).expanduser())])
    else:
        command.extend(["--source-url", str(args.source_url or DEFAULT_UPDATE_SOURCE_URL)])
    if args.ref:
        command.extend(["--ref", str(args.ref)])
    if args.cache_root:
        command.extend(["--cache-root", str(Path(args.cache_root).expanduser())])
    command.append("--")
    command.extend(["--upgrade", "--runtime", str(runtime_home), "--yes"])
    command.extend(_update_preserved_installer_args(runtime_home))
    return command


def _update_bootstrap_path(args: argparse.Namespace, runtime_home: Path) -> Path:
    if args.source_root:
        source_bootstrap = Path(args.source_root).expanduser() / "install" / "bootstrap.sh"
        if source_bootstrap.is_file():
            return source_bootstrap
        raise FileNotFoundError(f"installer bootstrap not found under --source-root: {source_bootstrap}")
    checkout_bootstrap = ROOT / "install" / "bootstrap.sh"
    if checkout_bootstrap.is_file():
        return checkout_bootstrap
    runtime_bootstrap = runtime_home.expanduser() / "app" / "source" / "install" / "bootstrap.sh"
    if runtime_bootstrap.is_file():
        return runtime_bootstrap
    raise FileNotFoundError(
        "installer bootstrap is unavailable in both the Open Nova package checkout and active Runtime app/source; "
        "reinstall Open Nova or provide --source-root PATH"
    )


def _validate_update_source_selection(args: argparse.Namespace) -> None:
    if args.source_root and args.ref:
        raise ValueError("--source-root cannot be combined with --ref")
    if not args.source_root and not args.ref and str(args.source_url or "") != DEFAULT_UPDATE_SOURCE_URL:
        raise ValueError("a custom --source-url requires an explicit full commit via --ref")
    if args.ref and not UPDATE_FULL_COMMIT_RE.fullmatch(str(args.ref)):
        raise ValueError(
            "--ref must be a full 40- or 64-character hexadecimal commit ID for remote updates; "
            "branches, tags, and abbreviated commit IDs are not accepted"
        )


def _update_source_selection(args: argparse.Namespace) -> dict:
    if args.source_root:
        return {
            "mode": "local-source-root",
            "policy": "use the supplied local checkout without remote ref resolution",
            "resolvedBy": "caller",
            "commitPinnedByBootstrap": False,
        }
    if args.ref:
        return {
            "mode": "explicit-remote-commit",
            "policy": "fetch and pin the explicitly requested full commit",
            "resolvedBy": "bootstrap",
            "commitPinnedByBootstrap": True,
            "requestedCommit": str(args.ref),
        }
    return {
        "mode": "latest-stable-release",
        "policy": LATEST_STABLE_RELEASE_POLICY,
        "resolvedBy": "bootstrap",
        "commitPinnedByBootstrap": True,
    }


def _update_preserved_installer_args(runtime_home: Path) -> list[str]:
    try:
        settings = read_settings(
            runtime_paths_for_home(runtime_home),
            redact_secrets=False,
            persist_defaults=False,
        )
    except Exception:
        return []
    preserved: list[str] = []
    rag = settings.get("rag") if isinstance(settings.get("rag"), dict) else {}
    features = settings.get("features") if isinstance(settings.get("features"), dict) else {}
    if rag.get("enabled") or features.get("rag"):
        preserved.append("--enable-rag")
        embedding = rag.get("embedding") if isinstance(rag.get("embedding"), dict) else {}
        mode = str(embedding.get("mode") or "").strip()
        if mode in {"local", "cloud"}:
            preserved.extend(["--rag-embedding-mode", mode])
        external_tools = settings.get("externalTools") if isinstance(settings.get("externalTools"), dict) else {}
        skill_registration = external_tools.get("installerV2SkillRegistration")
        if isinstance(skill_registration, dict) and skill_registration.get("supportedNow") is True:
            preserved.append("--register-rag-skills")
    return preserved


def _shell_join(command: list[str]) -> str:
    return " ".join(_shell_quote(part) for part in command)


def _shell_quote(value: str) -> str:
    text = str(value)
    if re.fullmatch(r"[A-Za-z0-9_@%+=:,./-]+", text):
        return text
    return "'" + text.replace("'", "'\"'\"'") + "'"


def _search_memory(args: argparse.Namespace) -> int:
    filters = {
        "date": args.date,
        "dateFrom": args.date_from,
        "dateTo": args.date_to,
        "project": args.project,
        "role": args.role,
        "sourceSets": args.source_set,
    }
    try:
        result = search_memory(
            args.query,
            top_k=args.top_k,
            dashboard_url=args.dashboard_url,
            timeout_seconds=args.timeout,
            filters=filters,
        )
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(compact_memory_results(result, max_results=args.top_k))
    return 0


def _task_counts(args: argparse.Namespace) -> int:
    try:
        paths = _paths_from_args(args) or load_paths()
        snapshot = diary_tasks_snapshot(paths)
        pending = pending_candidate_count(paths)
    except Exception as exc:
        sys.stderr.write(f"{exc}\n")
        return 1
    payload = {
        "authority": "Nova-Task v2 SQLite",
        "runtime": str(paths.home),
        "inProgress": int(snapshot.get("InProgress", 0)),
        "completed": int(snapshot.get("Completed", 0)),
        "pendingCandidates": int(pending),
    }
    payload["total"] = payload["inProgress"] + payload["completed"]
    if args.json:
        sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    else:
        sys.stdout.write(
            "Open Nova tasks: "
            f"total={payload['total']} inProgress={payload['inProgress']} "
            f"completed={payload['completed']} pendingCandidates={payload['pendingCandidates']}\n"
        )
    return 0


def _onboarding_doctor(args: argparse.Namespace) -> int:
    paths = _paths_from_args(args)
    try:
        payload = nova_onboarding_status(paths, selected_profiles=args.profiles)
    except ValueError as exc:
        sys.stderr.write(f"{exc}\n")
        return 2
    output = dump_nova_onboarding_status_json(payload) if args.json else format_nova_onboarding_status(payload)
    sys.stdout.write(output)
    if not output.endswith("\n"):
        sys.stdout.write("\n")
    return 1 if ((payload.get("readiness") or {}).get("status") == "error") else 0


def _onboarding_plan(args: argparse.Namespace) -> int:
    paths = _paths_from_args(args)
    try:
        payload = onboarding_subsystem_plan(args.profiles, paths)
    except ValueError as exc:
        sys.stderr.write(f"{exc}\n")
        return 2
    output = dump_onboarding_subsystem_plan_json(payload) if args.json else format_onboarding_subsystem_plan(payload)
    sys.stdout.write(output)
    if not output.endswith("\n"):
        sys.stdout.write("\n")
    return 0


def _onboarding_one_liner_dry_run(args: argparse.Namespace) -> int:
    paths = _paths_from_args(args)
    try:
        payload = onboarding_one_liner_dry_run(args.profiles, paths)
    except ValueError as exc:
        sys.stderr.write(f"{exc}\n")
        return 2
    output = dump_onboarding_one_liner_dry_run_json(payload) if args.json else format_onboarding_one_liner_dry_run(payload)
    sys.stdout.write(output)
    if not output.endswith("\n"):
        sys.stdout.write("\n")
    return 0


def _onboarding_one_liner_apply(args: argparse.Namespace) -> int:
    try:
        if args.use_default_runtime and args.runtime:
            raise ValueError("--use-default-runtime and --runtime cannot be used together")
        paths = _runtime_bootstrap_paths_from_args(args)
        if paths is None:
            raise ValueError("runtime apply requires --use-default-runtime or an explicit --runtime path")
        payload = onboarding_one_liner_apply(
            args.profiles,
            paths,
            confirmation_text=args.confirmation_text,
            select_active_runtime=args.select_active_runtime,
            language_profile=args.language,
            with_scheduler=args.with_scheduler,
            scheduler_confirmation_text=args.scheduler_confirmation_text,
        )
    except ValueError as exc:
        sys.stderr.write(f"{exc}\n")
        return 2
    output = dump_onboarding_apply_blocked_json(payload) if args.json else format_onboarding_apply_blocked(payload)
    sys.stdout.write(output)
    if not output.endswith("\n"):
        sys.stdout.write("\n")
    return int(payload.get("exitCode", 1))


def _onboarding_one_liner_status(args: argparse.Namespace) -> int:
    paths = _candidate_paths_from_args(args)
    payload = onboarding_one_liner_status(paths)
    output = dump_onboarding_one_liner_status_json(payload) if args.json else format_onboarding_one_liner_status(payload)
    sys.stdout.write(output)
    if not output.endswith("\n"):
        sys.stdout.write("\n")
    return 0 if payload.get("status") == "initialized" else 1


def _onboarding_one_liner_release_gate(args: argparse.Namespace) -> int:
    paths = _paths_from_args(args)
    try:
        payload = onboarding_one_liner_release_gate(args.profiles, paths, with_scheduler=args.with_scheduler)
    except ValueError as exc:
        sys.stderr.write(f"{exc}\n")
        return 2
    output = dump_onboarding_release_gate_json(payload) if args.json else format_onboarding_release_gate(payload)
    sys.stdout.write(output)
    if not output.endswith("\n"):
        sys.stdout.write("\n")
    return 0 if payload.get("status") == "passed" else 1


def _onboarding_one_liner_validation_matrix(args: argparse.Namespace) -> int:
    paths = _paths_from_args(args)
    payload = onboarding_one_liner_validation_matrix(paths)
    output = (
        dump_onboarding_one_liner_validation_matrix_json(payload)
        if args.json
        else format_onboarding_one_liner_validation_matrix(payload)
    )
    sys.stdout.write(output)
    if not output.endswith("\n"):
        sys.stdout.write("\n")
    return 0 if payload.get("status") == "passed" else 1


def _onboarding_rollback_plan_status(args: argparse.Namespace) -> int:
    paths = _candidate_paths_from_args(args)
    payload = onboarding_rollback_plan_status(paths)
    output = dump_onboarding_rollback_plan_status_json(payload) if args.json else format_onboarding_rollback_plan_status(payload)
    sys.stdout.write(output)
    if not output.endswith("\n"):
        sys.stdout.write("\n")
    return 0 if payload.get("status") == "available" else 1


def _onboarding_apply_blocked(args: argparse.Namespace) -> int:
    try:
        apply_modes = [
            args.sandbox_apply,
            args.runtime_bootstrap_apply,
            args.scheduler_sandbox_apply,
            args.scheduler_plist_apply,
            args.scheduler_register_apply,
            args.scheduler_unregister_apply,
        ]
        if sum(1 for enabled in apply_modes if enabled) > 1:
            raise ValueError("--sandbox-apply, --runtime-bootstrap-apply, --scheduler-sandbox-apply, --scheduler-plist-apply, --scheduler-register-apply and --scheduler-unregister-apply cannot be used together")
        if args.select_active_runtime and not args.runtime_bootstrap_apply:
            raise ValueError("--select-active-runtime requires --runtime-bootstrap-apply")
        if args.use_default_runtime and not args.runtime_bootstrap_apply:
            raise ValueError("--use-default-runtime requires --runtime-bootstrap-apply")
        if args.use_default_runtime and args.runtime:
            raise ValueError("--use-default-runtime and --runtime cannot be used together")
        if args.runtime_bootstrap_apply:
            paths = _runtime_bootstrap_paths_from_args(args)
            payload = onboarding_apply_runtime_bootstrap(
                args.profiles,
                paths,
                confirmation_text=args.confirmation_text,
                select_active_runtime=args.select_active_runtime,
                language_profile=args.language,
            )
        elif args.scheduler_sandbox_apply:
            paths = _sandbox_paths_from_args(args) if args.runtime else None
            payload = onboarding_apply_scheduler_sandbox(
                args.profiles,
                paths,
                scheduler_home=Path(args.scheduler_home).expanduser() if args.scheduler_home else None,
                confirmation_text=args.confirmation_text,
            )
        elif args.scheduler_plist_apply:
            paths = _sandbox_paths_from_args(args) if args.runtime else None
            payload = onboarding_apply_scheduler_plist_write(
                args.profiles,
                paths,
                confirmation_text=args.confirmation_text,
            )
        elif args.scheduler_register_apply:
            paths = _sandbox_paths_from_args(args) if args.runtime else None
            payload = onboarding_apply_scheduler_register(
                args.profiles,
                paths,
                confirmation_text=args.confirmation_text,
            )
        elif args.scheduler_unregister_apply:
            paths = _sandbox_paths_from_args(args) if args.runtime else None
            payload = onboarding_apply_scheduler_unregister(
                args.profiles,
                paths,
                confirmation_text=args.confirmation_text,
            )
        elif args.sandbox_apply:
            paths = _sandbox_paths_from_args(args) if args.runtime else None
            payload = onboarding_apply_sandbox(
                args.profiles,
                paths,
                confirmation_text=args.confirmation_text,
                language_profile=args.language,
            )
        else:
            payload = onboarding_apply_blocked(args.profiles, confirmation_text=args.confirmation_text)
    except ValueError as exc:
        sys.stderr.write(f"{exc}\n")
        return 2
    output = dump_onboarding_apply_blocked_json(payload) if args.json else format_onboarding_apply_blocked(payload)
    sys.stdout.write(output)
    if not output.endswith("\n"):
        sys.stdout.write("\n")
    return int(payload["exitCode"]) if "exitCode" in payload else 1


def _onboarding_release_gate(args: argparse.Namespace) -> int:
    paths = _paths_from_args(args)
    try:
        payload = onboarding_release_gate(args.profiles, paths, confirmation_text=args.confirmation_text)
    except ValueError as exc:
        sys.stderr.write(f"{exc}\n")
        return 2
    output = dump_onboarding_release_gate_json(payload) if args.json else format_onboarding_release_gate(payload)
    sys.stdout.write(output)
    if not output.endswith("\n"):
        sys.stdout.write("\n")
    return 1 if payload.get("status") != "passed" else 0


def _onboarding_approval_packet(args: argparse.Namespace) -> int:
    paths = _paths_from_args(args)
    try:
        payload = onboarding_approval_packet(args.profiles, paths, confirmation_text=args.confirmation_text)
    except ValueError as exc:
        sys.stderr.write(f"{exc}\n")
        return 2
    output = dump_onboarding_approval_packet_json(payload) if args.json else format_onboarding_approval_packet(payload)
    sys.stdout.write(output)
    if not output.endswith("\n"):
        sys.stdout.write("\n")
    return 1 if payload.get("status") != "approved" else 0


def _paths_from_args(args: argparse.Namespace):
    paths = None
    if args.runtime:
        current = load_paths()
        paths = runtime_paths_for_home(
            Path(args.runtime).expanduser(),
            legacy_diary_root=Path(args.legacy_diary_root).expanduser() if args.legacy_diary_root else current.legacy_diary_root,
        )
    return paths


def _candidate_paths_from_args(args: argparse.Namespace):
    if args.runtime:
        current = load_paths()
        return runtime_paths_for_home(
            Path(args.runtime).expanduser(),
            legacy_diary_root=Path(args.legacy_diary_root).expanduser() if args.legacy_diary_root else current.legacy_diary_root,
        )
    return None


def _sandbox_paths_from_args(args: argparse.Namespace):
    current = load_paths()
    return runtime_paths_for_home(
        Path(args.runtime).expanduser(),
        legacy_diary_root=Path(args.legacy_diary_root).expanduser() if args.legacy_diary_root else current.legacy_diary_root,
    )


def _runtime_bootstrap_paths_from_args(args: argparse.Namespace):
    if args.runtime:
        return _sandbox_paths_from_args(args)
    if args.use_default_runtime:
        current = load_paths()
        return runtime_paths_for_home(
            default_oneliner_runtime_home(),
            legacy_diary_root=Path(args.legacy_diary_root).expanduser() if args.legacy_diary_root else current.legacy_diary_root,
        )
    return None


def _pipeline_run(args: argparse.Namespace) -> int:
    if getattr(args, "date_flag", None) and getattr(args, "date", None):
        sys.stderr.write("pipeline accepts either a positional date or --date, not both\n")
        return 2
    try:
        target_date = _normalize_cli_date(getattr(args, "date_flag", None) or getattr(args, "date", None))
    except ValueError as exc:
        sys.stderr.write(f"{exc}\n")
        return 2
    paths = _paths_from_args(args)
    force = bool(getattr(args, "force", False))
    if not force and _daily_diary_complete_for_cli(paths, target_date):
        sys.stderr.write(
            f"Daily diary for {target_date} is already complete. "
            "Use --force to regenerate it with frozen Foundation inputs.\n"
        )
        return 2
    if force:
        result = run_daily_pipeline(
            target_date,
            paths=paths,
            trigger="manual-regeneration-frozen",
            reuse_foundation_inputs=True,
        )
    else:
        result = run_daily_pipeline(target_date, paths=paths)
    sys.stdout.write(
        f"Open Nova pipeline: date={result.business_date} "
        f"steps={result.succeeded_steps}/{result.total_steps} success={result.success}\n"
    )
    if result.failed_step:
        sys.stdout.write(f"Failed step: {result.failed_step}\n")
    return 0 if result.success else 1


def _scheduler_reconcile(args: argparse.Namespace) -> int:
    paths = _paths_from_args(args)
    payload = reconcile_pipeline_schedule(
        paths,
        apply=bool(args.apply),
        lookback_days=args.lookback_days,
        auto_limit_days=args.auto_limit_days,
    )
    if args.json:
        sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    else:
        sys.stdout.write(
            "Open Nova scheduler reconcile: "
            f"missing={payload.get('missingCount', 0)} "
            f"range={payload.get('targetStart')}..{payload.get('targetEnd')} "
            f"status={payload.get('status')}\n"
        )
        missing = payload.get("missingDates") or []
        if missing:
            sys.stdout.write("Missing dates: " + ", ".join(map(str, missing)) + "\n")
        if payload.get("requiresConfirmation"):
            sys.stdout.write("Catch-up requires user confirmation because missing dates exceed the automatic limit.\n")
        for run in payload.get("runs") or []:
            sys.stdout.write(
                f"Catch-up {run.get('date')}: success={run.get('success')} "
                f"steps={run.get('steps')} failedStep={run.get('failedStep') or ''}\n"
            )
    return 1 if payload.get("status") == "partial" else 0


def _daily_diary_complete_for_cli(paths, target_date: str) -> bool:
    try:
        selected = paths or load_paths()
        return bool(evaluate_daily_completeness(selected, date.fromisoformat(target_date)).get("ready"))
    except Exception:
        return False


def _dashboard_restart(args: argparse.Namespace) -> int:
    defaults = dashboard_launch_defaults()
    label = getattr(args, "label", None) or str(defaults.get("label") or "com.open-nova.dashboard")
    code = restart_dashboard_service(label)
    if code == 0:
        sys.stdout.write(f"Dashboard restart requested: {label}\n")
    return code


def _rag_rebuild(args: argparse.Namespace) -> int:
    return _rag_sync_command(
        args,
        action="rag-rebuild",
        confirmation=RAG_REBUILD_CONFIRMATION,
        requested_by="open-nova-cli-rag-rebuild",
    )


def _rag_update(args: argparse.Namespace) -> int:
    return _rag_sync_command(
        args,
        action="rag-update",
        confirmation=RAG_UPDATE_CONFIRMATION,
        requested_by="open-nova-cli-rag-update",
    )


def _rag_sync_command(
    args: argparse.Namespace,
    *,
    action: str,
    confirmation: str,
    requested_by: str,
) -> int:
    rag_settings = resolve_rag_settings(_paths_from_args(args))
    if not args.dry_run and args.confirmation_text is not None and str(args.confirmation_text or "") != confirmation:
        sys.stderr.write(f"confirmationText must be exactly: {confirmation}\n")
        return 2
    if args.dry_run or str(args.confirmation_text or "") != confirmation:
        plan = plan_v2_production_sync(
            rag_settings,
            action=action,
            requested_by=requested_by,
            promote=True,
            confirmation_text=confirmation,
        )
        if args.json:
            sys.stdout.write(json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        else:
            sys.stdout.write(f"Open Nova {action} plan: {plan['reason']}\n")
            sys.stdout.write(f"To execute, rerun with --confirm \"{confirmation}\"\n")
        return 0
    try:
        result = sync_v2_production_index(rag_settings, requested_by=requested_by, promote=True)
    except Exception as exc:
        sys.stderr.write(f"{exc}\n")
        return 1
    if args.json:
        sys.stdout.write(json.dumps({"action": action, **result}, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    else:
        sys.stdout.write(f"Open Nova {action}: {result.get('status', 'unknown')}\n")
        if result.get("reason"):
            sys.stdout.write(f"Reason: {result['reason']}\n")
    return 0 if result.get("status") == "promoted" else 1


def _parse_optional_date(value: str | None):
    if not value:
        return None

    return date.fromisoformat(value)


def _normalize_cli_date(value: str | None) -> str | None:
    if not value:
        return None
    raw = str(value).strip()
    if re.fullmatch(r"\d{6}", raw):
        normalized = f"20{raw[0:2]}-{raw[2:4]}-{raw[4:6]}"
        date.fromisoformat(normalized)
        return normalized
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        date.fromisoformat(raw)
        return raw
    raise ValueError("date must be YYYY-MM-DD or YYMMDD")


def _parse_config_value(value: str):
    raw = str(value)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def _nested_update(path: str, value) -> dict:
    parts = [part for part in str(path or "").split(".") if part]
    if len(parts) < 2:
        raise ValueError("config set path must include a settings group and field")
    root: dict = {}
    cursor = root
    for part in parts[:-1]:
        cursor = cursor.setdefault(part, {})
    cursor[parts[-1]] = value
    return root


def _get_dot_path(payload: dict, path: str):
    cursor = payload
    for part in [part for part in str(path or "").split(".") if part]:
        if not isinstance(cursor, dict) or part not in cursor:
            raise KeyError(path)
        cursor = cursor[part]
    return cursor


def _format_scalar(value) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    if value is True:
        return "true"
    if value is False:
        return "false"
    if value is None:
        return "null"
    return str(value)


def _foundation_rebuild_sqlite_cache(args: argparse.Namespace) -> int:
    paths = _paths_from_args(args) or load_paths()
    try:
        start_date = _parse_optional_date(args.start_date)
        end_date = _parse_optional_date(args.end_date)
        if args.dry_run or not args.confirmation_text:
            payload = plan_sqlite_cache_rebuild(paths, start_date=start_date, end_date=end_date)
        else:
            payload = rebuild_sqlite_cache(
                paths,
                confirmation_text=args.confirmation_text,
                start_date=start_date,
                end_date=end_date,
            )
    except ValueError as exc:
        sys.stderr.write(f"{exc}\n")
        return 2
    if args.json:
        sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    else:
        sys.stdout.write(
            "Open Nova SQLite cache rebuild "
            + ("plan" if payload.get("dryRun") else payload.get("status", "completed"))
            + f": runtime={payload.get('runtime')} database={payload.get('database')}\n"
        )
        if payload.get("dryRun"):
            sys.stdout.write(f"To execute, rerun with --confirm \"{SQLITE_CACHE_REBUILD_CONFIRMATION}\"\n")
        elif payload.get("backup"):
            sys.stdout.write(f"Backup: {payload['backup'].get('backupDir')}\n")
    return 0


def _foundation_approve_diary_metrics(args: argparse.Namespace) -> int:
    paths = _paths_from_args(args) or load_paths()
    try:
        business_date = date.fromisoformat(_normalize_cli_date(args.date) or "")
        plan = _diary_metrics_approval_plan(paths, business_date)
        if (
            not args.dry_run
            and args.confirmation_text is not None
            and str(args.confirmation_text or "") != DIARY_METRICS_APPROVAL_CONFIRMATION
        ):
            sys.stderr.write(f"confirmationText must be exactly: {DIARY_METRICS_APPROVAL_CONFIRMATION}\n")
            return 2
        if args.dry_run or str(args.confirmation_text or "") != DIARY_METRICS_APPROVAL_CONFIRMATION:
            payload = plan
        else:
            approval = write_diary_metrics_table_mismatch_approval(
                paths,
                business_date,
                operator=args.operator,
                note=args.note,
            )
            report = write_diary_metrics_readiness_report(
                paths,
                business_date,
                approve_model_usage_normalization=True,
                approve_session_count_normalization=True,
            )
            payload = {
                **plan,
                "dryRun": False,
                "status": "approved",
                "mutationPolicy": {
                    "sourceFactsChanged": False,
                    "sqliteUsageRowsChanged": False,
                    "approvalAuditAppended": True,
                    "readinessReportRegenerated": True,
                },
                "approval": approval,
                "readiness": {
                    "status": report.get("status"),
                    "canEnable": report.get("canEnable"),
                    "path": str(paths.state_dir / "migration" / f"diary-metrics-readiness-{business_date.isoformat()}.json"),
                },
            }
    except (ValueError, FileNotFoundError) as exc:
        sys.stderr.write(f"{exc}\n")
        return 2
    if args.json:
        sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    else:
        sys.stdout.write(
            "Open Nova diary metrics approval "
            + ("plan" if payload.get("dryRun") else payload.get("status", "approved"))
            + f": date={payload.get('businessDate')} runtime={payload.get('runtime')}\n"
        )
        digest = payload.get("differencesDigest")
        if digest:
            sys.stdout.write(f"Differences digest: {digest}\n")
        if payload.get("dryRun"):
            sys.stdout.write(f"To execute, rerun with --confirm \"{DIARY_METRICS_APPROVAL_CONFIRMATION}\"\n")
    return 0


def _diary_metrics_approval_plan(paths, business_date: date) -> dict:
    report_path = paths.state_dir / "migration" / f"diary-metrics-readiness-{business_date.isoformat()}.json"
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"missing diary metrics readiness report: {report_path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid diary metrics readiness report: {report_path}") from exc
    differences = ((report.get("tableMetrics") or {}).get("differences") or {})
    can_enable = (report.get("canEnable") or {}).get("diaryMetricsSourceFoundation")
    from data_foundation.diary_metrics import _stable_json_digest

    return {
        "action": "approve-diary-metrics",
        "businessDate": business_date.isoformat(),
        "runtime": str(paths.home),
        "reportPath": str(report_path),
        "dryRun": True,
        "status": "plan",
        "currentReadinessStatus": report.get("status"),
        "alreadyEnabled": bool(can_enable),
        "hasTableDifferences": bool(differences),
        "differencesDigest": _stable_json_digest(differences) if differences else "",
        "differences": differences,
        "confirmationTextRequired": DIARY_METRICS_APPROVAL_CONFIRMATION,
        "mutationPolicy": {
            "sourceFactsChanged": False,
            "sqliteUsageRowsChanged": False,
            "approvalAuditAppended": False,
            "readinessReportRegenerated": False,
        },
    }


if __name__ == "__main__":
    raise SystemExit(main())
