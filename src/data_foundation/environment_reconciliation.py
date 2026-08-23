"""Independent LLM reconciliation for Technical environment observations.

Technical Pass discovers evidence-bound observations.  This stage is the only
model call allowed to decide whether an observation updates a known entity,
creates a new entity, remains deferred, or is dropped.  Deterministic code then
reopens current authority, validates cited evidence, applies the projection,
and writes the encrypted reconciled ledger.
"""

from __future__ import annotations

import json
import hashlib
import re
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable, Mapping

from .diary_paths import diary_report_paths
from .engineering_artifacts import (
    ARTIFACT_ACTIONS,
    ARTIFACT_CATALOG_ACTIONS,
    ARTIFACT_STATUSES,
    apply_engineering_artifact_reconciliation,
    engineering_artifact_catalog,
)
from .environment_assets import (
    EnvironmentAssetError,
    apply_environment_asset_projection,
    bind_environment_asset_authority,
    read_environment_observation_ledger,
    write_environment_asset_ledger,
    write_environment_reconciliation_audit,
)
from .infrastructure import (
    CATALOG_ACTIONS,
    CATALOG_HEALTH_STATUSES,
    CATALOG_LIFECYCLE_STATUSES,
    apply_infrastructure_catalog_actions,
    infrastructure_entity_catalog,
)
from .llm_execution import execute_llm_message
from .paths import RuntimePaths, load_paths


RECONCILIATION_SCHEMA = "actanara.environment-reconciliation.v2"
ASSET_RECONCILIATION_SCHEMA = "actanara.asset-reconciliation.v1"
ACTIONS = frozenset({"update_existing", "create_new", "defer", "drop"})
CONFIDENCE_VALUES = frozenset({"high", "medium", "low"})
MAX_REASON_CHARS = 1_000

SYSTEM_PROMPT = """你是统一资产语义复核器。一次调用内分别处理 Infrastructure 与 Engineering Artifacts；两个域共享证据但绝不共享分类、目录或写入权限。

Infrastructure 域：Technical Pass 只发现了对象名称、简短观察和证据引用；你负责依据所引证据与当前目录完成分类、状态、变更类型、私有 locator、身份和目录维护裁决。

你不负责重新发现事实，不编写技术报告，不维护任务，不生成 Skill，也不补写输入中不存在的设备、服务、路径、端点或版本。

Technical 的 assetClass 只是发现提示，不是权威。service 必须是证据明确表明常驻运行、受服务管理器托管、已部署监听或已配置自动启动的进程、daemon、API、数据库、worker、scheduler、dashboard runtime 等。仅在源码中存在、仅被手动执行一次、Pipeline 定义、模块、按钮、函数、提示词、CLI 命令和一般产品能力必须 drop。device 必须是真实物理设备、虚拟机、云实例、网络或存储设备。commit、报告、环境账本、构建物与普通工程产出一律 drop，它们不属于基础设施。

对每条观察必须且只能选择一个动作，并在 accepted 动作中填写 normalized：
- update_existing：它是目录中某个已存在的同类型设备或服务；复制该 entityId。
- create_new：证据确实描述一个当前目录没有的新设备或运行服务。
- defer：证据不足、别名或宿主关系含糊，暂不固化。
- drop：它不是设备/常驻服务，或只是工程产出、计划、代码符号、配置键、文档路径、任务标签、临时诊断对象。

你还可以维护与本次观察直接相关的现有目录：
- merge_existing：两个活动实体确实是同一设备/服务的重复记录；保留 canonical，归档 duplicate，并迁移别名和历史关联。
- archive_existing：现有记录是错误、过时副本或已被明确替代；归档但绝不删除。仅“今天未出现”不是归档证据。
- transition_state：证据明确表明既有设备/服务进入 active/suspended/retired 或 healthy/degraded/unhealthy/offline 状态。

normalized 里的每个事实必须能从该 observation 的 evidence 中直接找到，或者（仅 update_existing）逐字继承目录已有字段；不得猜测版本、宿主、endpoint、路径、状态或项目。目录维护必须引用至少一个本次 observationId，且只有 high confidence 才会执行。停止运行是状态迁移，不等于归档。谨慎区分同名但不同宿主的服务，也不要因名字不同就忽略明显别名。

Engineering Artifacts 域：从 Technical 报告及其引用证据中识别当天真正形成或更新、可在任务结束后继续定位和复用的工程/工作产物，例如程序、工具、自动化脚本、设计/RFC、专项技术报告、数据集、模型、索引、部署包、配置模板、测试资产、Dashboard 或架构图。commit、单次命令、普通源码编辑、日志、缓存、lock、临时文件、每日 Technical 日记和环境私有账本只能作为证据，不得单独建档。运行中的服务属于 Infrastructure；只有其独立的软件包、部署制品或可复用配置本身才可能是 Artifact。

特别排除：以日期命名的“技术进展报告/日记”、git commit 本身、环境观察/复核账本、依赖 lock 文件本身。专项 RFC、面向复用的审计报告或真实生成的架构图可以是 Artifact，但必须与每日流水记录清楚区分。输出前删除所有落入这些排除项的 Artifact candidate。

Artifact 的 create_new/update_existing 必须引用至少一个 E000001 形式的原始证据；name、version、locator、project 不得脱离证据猜测。优先匹配现有 Artifact Catalog。只有具备明确交付物身份、后续定位价值或跨任务复用价值的结果才建档；普通工作进度留在 Nova-Task。可以维护重复、被替代或明确归档的旧产物，但“今天未出现”不是归档证据。

Artifact Catalog 不是第二个 Nova-Task：create_new 必须是已经 produced/verified 的具体载体，并填写证据中逐字出现的 locator（文件、目录、包、发布标识、URL 或其他稳定定位符）。只有设计想法、实现策略、产品功能概念或 observed/planned 工作，但没有具体载体时，必须 defer 或省略。update_existing 可以继承既有 locator。

只登记顶层独立交付物：同一发布物的安装脚本、marker、依赖锁和 release notes 应合并到该发布物，除非它们本身会被独立复用和检索。内部 stage contract、parser/prompt 拆分、按钮改动、一次 Pipeline 运行记录和普通 change set 属于实现证据或 Nova-Task，不是独立 Artifact。locator/version 必须从所引 E 证据逐字复制；若证据未出现，不得根据项目惯例拼接路径或版本，必须 defer/省略。

只输出一个严格 JSON 对象，不输出 Markdown。"""

PROMPT_TEMPLATE = """业务日期：{business_date}

当前环境资产目录（仅用于身份参照，不能证明今天发生了变化；完整目录保留，仅按与本次观察的名称/类别相关度排序）：
{catalog}

Technical Pass 的证据绑定观察：
{observations}

Technical 工程报告（Artifact 发现入口；报告本身不是 Artifact）：
{technical_report}

报告所引用的原始证据：
{artifact_evidence}

当前工程产物目录（只用于身份与生命周期参照，不能证明今天发生变化）：
{artifact_catalog}

输出契约：
{{
  "schema": "actanara.asset-reconciliation.v1",
  "businessDate": "{business_date}",
  "infrastructure": {{
    "decisions": [{{
      "observationId": "逐字复制输入 observationId",
      "action": "update_existing|create_new|defer|drop",
      "existingEntityId": "仅 update_existing 时填写",
      "reason": "简短理由",
      "confidence": "high|medium|low",
      "normalized": {{
        "kind": "", "state": "", "changeType": "", "host": "", "location": "",
        "endpoint": "", "port": "", "path": "", "version": "", "project": ""
      }}
    }}],
    "catalogActions": [{{
      "action": "merge_existing|archive_existing|transition_state",
      "entityId": "", "canonicalEntityId": "", "lifecycleStatus": "", "healthStatus": "",
      "observationIds": [], "reason": "", "confidence": "high|medium|low"
    }}]
  }},
  "artifacts": {{
    "decisions": [{{
      "candidateId": "从 A000001 开始，本响应内唯一",
      "action": "update_existing|create_new|defer|drop",
      "existingArtifactId": "仅 update_existing 时填写",
      "name": "证据支持的产物名称",
      "artifactType": "简短开放类别，不受固定枚举限制",
      "summary": "证据支持的产物及用途",
      "evidenceRefs": ["E000001"],
      "reason": "为什么值得或不值得进入产物目录",
      "confidence": "high|medium|low",
      "normalized": {{"status": "observed|produced|verified|superseded|archived", "version": "", "locator": "", "project": ""}}
    }}],
    "catalogActions": [{{
      "action": "merge_existing|archive_existing|transition_state",
      "artifactId": "", "canonicalArtifactId": "", "status": "",
      "candidateIds": ["A000001"], "reason": "", "confidence": "high|medium|low"
    }}]
  }}
}}

Infrastructure 必须为每个 observationId 输出恰好一条 decision，不得遗漏、重复或增加观察；accepted 的 normalized 十个字段完整，defer/drop 全为空字符串。Artifact 没有可建档结果时 decisions=[]，不得为了填充输出；每个 Artifact decision 必须使用唯一 candidateId，defer/drop 的 normalized 四字段全部为空字符串。

两个 catalogActions 分别只引用本域 ID。没有安全维护动作时输出空数组。"""

LlmSender = Callable[..., str]


@dataclass(frozen=True)
class EnvironmentReconciliationResult:
    business_date: str
    observation_count: int
    accepted_count: int
    deferred_count: int
    dropped_count: int
    validation_rejected_count: int
    action_counts: dict[str, int]
    projection: dict[str, int]
    catalog_action_count: int
    catalog_deferred_count: int
    catalog_projection: dict[str, int]
    artifact_candidate_count: int
    artifact_accepted_count: int
    artifact_rejected_count: int
    artifact_action_counts: dict[str, int]
    artifact_projection: dict[str, Any]


def build_environment_reconciliation_prompt(
    paths: RuntimePaths,
    *,
    business_date: str,
) -> tuple[str, dict[str, dict[str, Any]], Any]:
    prompt, catalog, ledger, _artifact_catalog, _artifact_evidence = (
        build_asset_reconciliation_prompt(paths, business_date=business_date)
    )
    return prompt, catalog, ledger


def build_asset_reconciliation_prompt(
    paths: RuntimePaths,
    *,
    business_date: str,
) -> tuple[
    str,
    dict[str, dict[str, Any]],
    Any,
    dict[str, dict[str, Any]],
    dict[str, str],
]:
    ledger = read_environment_observation_ledger(paths, business_date)
    if ledger is None:
        raise EnvironmentAssetError("environment observation ledger is unavailable")
    catalog = infrastructure_entity_catalog(paths)
    artifact_catalog = engineering_artifact_catalog(paths)
    catalog_rows = [
        {
            "entityId": entity_id,
            "entityType": entity.get("entityType"),
            "name": entity.get("name"),
            "aliases": list(entity.get("aliases") or ()),
            "kind": entity.get("kind"),
            "status": entity.get("status"),
            "lifecycleStatus": entity.get("lifecycleStatus"),
            "healthStatus": entity.get("healthStatus"),
            "hostScope": _host_scope(_catalog_host(entity)),
        }
        for entity_id, entity in sorted(catalog.items())
    ]
    observations = [
        {
            "observationId": item["assetId"],
            "assetClass": item["assetClass"],
            "name": item["name"],
            "summary": item["summary"],
            "evidenceRefs": list(item["evidenceRefs"]),
            "evidence": [ledger.evidence_by_ref[ref] for ref in item["evidenceRefs"]],
        }
        for item in ledger.observations
    ]
    catalog_rows.sort(
        key=lambda item: (
            -_catalog_relevance(item, observations),
            str(item.get("name") or "").casefold(),
            str(item.get("entityId") or ""),
        )
    )
    technical_report = _technical_report(paths, business_date)
    # The Technical report is intentionally concise and older reports do not
    # reliably retain E ids.  The encrypted observation ledger is the bounded,
    # sanitized authority for this same Technical run, so Artifact discovery
    # receives that evidence directly instead of treating LLM prose as proof.
    artifact_evidence = dict(sorted(ledger.evidence_by_ref.items()))
    artifact_catalog_rows = [
        {
            "artifactId": artifact_id,
            "name": item.get("name"),
            "aliases": list(item.get("aliases") or ()),
            "artifactType": item.get("artifactType"),
            "status": item.get("status"),
            "version": item.get("version"),
            "project": item.get("project"),
            "locator": item.get("locator"),
            "lastSeenDate": item.get("lastSeenDate"),
        }
        for artifact_id, item in artifact_catalog.items()
    ]
    artifact_catalog_rows.sort(
        key=lambda item: (
            -_artifact_catalog_relevance(item, technical_report),
            str(item.get("name") or "").casefold(),
            str(item.get("artifactId") or ""),
        )
    )
    prompt = PROMPT_TEMPLATE.format(
        business_date=business_date,
        catalog=json.dumps(catalog_rows, ensure_ascii=False, indent=2),
        observations=json.dumps(observations, ensure_ascii=False, indent=2),
        technical_report=technical_report or "(technical report unavailable)",
        artifact_evidence=json.dumps(artifact_evidence, ensure_ascii=False, indent=2),
        artifact_catalog=json.dumps(artifact_catalog_rows, ensure_ascii=False, indent=2),
    )
    return prompt, catalog, ledger, artifact_catalog, artifact_evidence


def run_environment_reconciliation(
    paths: RuntimePaths | None = None,
    *,
    business_date: date | str,
    sender: LlmSender | None = None,
) -> EnvironmentReconciliationResult:
    selected = paths or load_paths()
    date_text = business_date.isoformat() if isinstance(business_date, date) else str(business_date)
    date.fromisoformat(date_text)
    prompt, catalog, ledger, artifact_catalog, artifact_evidence = build_asset_reconciliation_prompt(
        selected,
        business_date=date_text,
    )
    if ledger.observations or artifact_evidence:
        if sender is None:
            response = execute_llm_message(
                paths=selected,
                system=SYSTEM_PROMPT,
                prompt=prompt,
                temperature=0.05,
                max_tokens=8192,
                thinking_mode="off",
                pass_id="asset-reconciliation",
                label="Infrastructure and engineering asset reconciliation",
            ).text
        else:
            response = sender(
                system=SYSTEM_PROMPT,
                prompt=prompt,
                temperature=0.05,
                max_tokens=8192,
                thinking_mode="off",
            )
        decisions, catalog_actions, artifact_decisions, artifact_catalog_actions = _parse_asset_reconciliation(
            response,
            business_date=date_text,
            observation_ids={item["assetId"] for item in ledger.observations},
            catalog_ids=set(catalog),
            artifact_catalog_ids=set(artifact_catalog),
            artifact_evidence_ids=set(artifact_evidence),
        )
    else:
        decisions = {}
        catalog_actions = []
        artifact_decisions = []
        artifact_catalog_actions = []

    accepted: list[dict[str, Any]] = []
    accepted_observation_ids: set[str] = set()
    validation_rejected = 0
    action_counts = {action: 0 for action in sorted(ACTIONS)}
    by_id = {item["assetId"]: item for item in ledger.observations}
    archived_entity_ids = {
        item["entityId"]
        for item in catalog_actions
        if item["confidence"] == "high" and item["action"] in {"merge_existing", "archive_existing"}
    }
    for observation_id, decision in decisions.items():
        action = decision["action"]
        action_counts[action] += 1
        observation = by_id[observation_id]
        if action in {"defer", "drop"}:
            continue
        if action == "update_existing":
            if decision["existingEntityId"] in archived_entity_ids:
                validation_rejected += 1
                continue
            identity_mode = "existing"
            existing_entity_id = decision["existingEntityId"]
        elif action == "create_new":
            identity_mode = "new"
            existing_entity_id = ""
        try:
            if observation["assetClass"] not in {"device", "service"}:
                raise EnvironmentAssetError("observation is outside the infrastructure scope")
            normalized = decision["normalized"]
            candidate = {
                "assetClass": observation["assetClass"],
                "name": observation["name"],
                "summary": observation["summary"],
                "evidenceRefs": list(observation["evidenceRefs"]),
                "confidence": decision["confidence"],
                **normalized,
            }
            accepted.append(
                bind_environment_asset_authority(
                    candidate,
                    business_date=date_text,
                    evidence_by_ref=ledger.evidence_by_ref,
                    infrastructure_catalog=catalog,
                    identity_mode=identity_mode,
                    existing_entity_id=existing_entity_id,
                )
            )
            accepted_observation_ids.add(observation_id)
        except EnvironmentAssetError:
            validation_rejected += 1
            continue

    executable_catalog_actions = [
        item
        for item in catalog_actions
        if item["confidence"] == "high"
        and set(item["observationIds"]).issubset(accepted_observation_ids)
    ]
    catalog_projection = apply_infrastructure_catalog_actions(
        selected,
        date_text,
        executable_catalog_actions,
        source="environment-reconciliation",
    )
    projection = apply_environment_asset_projection(
        selected,
        business_date=date_text,
        assets=accepted,
        source="environment-reconciliation",
    )
    write_environment_asset_ledger(
        selected,
        business_date=date_text,
        assets=accepted,
    )
    artifact_projection = apply_engineering_artifact_reconciliation(
        selected,
        business_date=date_text,
        decisions=artifact_decisions,
        catalog_actions=artifact_catalog_actions,
        evidence_by_ref=artifact_evidence,
        source="asset-reconciliation",
    )
    artifact_action_counts = {
        action: sum(1 for item in artifact_decisions if item.get("action") == action)
        for action in sorted(ARTIFACT_ACTIONS)
    }
    result = EnvironmentReconciliationResult(
        business_date=date_text,
        observation_count=len(ledger.observations),
        accepted_count=len(accepted),
        deferred_count=action_counts["defer"] + validation_rejected,
        dropped_count=action_counts["drop"],
        validation_rejected_count=validation_rejected,
        action_counts=action_counts,
        projection=projection,
        catalog_action_count=len(catalog_actions),
        catalog_deferred_count=len(catalog_actions) - len(executable_catalog_actions),
        catalog_projection=catalog_projection,
        artifact_candidate_count=len(artifact_decisions),
        artifact_accepted_count=int(artifact_projection.get("accepted") or 0),
        artifact_rejected_count=int(artifact_projection.get("rejected") or 0),
        artifact_action_counts=artifact_action_counts,
        artifact_projection=artifact_projection,
    )
    write_environment_reconciliation_audit(
        selected,
        business_date=date_text,
        observation_decisions=(
            {"observationId": observation_id, **decision}
            for observation_id, decision in decisions.items()
        ),
        catalog_actions=catalog_actions,
        result={
            "observationCount": result.observation_count,
            "acceptedCount": result.accepted_count,
            "deferredCount": result.deferred_count,
            "droppedCount": result.dropped_count,
            "validationRejectedCount": result.validation_rejected_count,
            "actionCounts": result.action_counts,
            "projection": result.projection,
            "catalogActionCount": result.catalog_action_count,
            "catalogDeferredCount": result.catalog_deferred_count,
            "catalogProjection": result.catalog_projection,
            "artifactCandidateCount": result.artifact_candidate_count,
            "artifactAcceptedCount": result.artifact_accepted_count,
            "artifactRejectedCount": result.artifact_rejected_count,
            "artifactActionCounts": result.artifact_action_counts,
            "artifactDecisions": artifact_decisions,
            "artifactCatalogActions": artifact_catalog_actions,
            "artifactProjection": result.artifact_projection,
        },
    )
    return result


def _parse_asset_reconciliation(
    raw: str,
    *,
    business_date: str,
    observation_ids: set[str],
    catalog_ids: set[str],
    artifact_catalog_ids: set[str],
    artifact_evidence_ids: set[str],
) -> tuple[
    dict[str, dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    """Parse the combined response while retaining read compatibility with v2."""

    text = str(raw or "").strip()
    fenced = re.fullmatch(r"```json\s*\n([\s\S]*?)\n```", text)
    if fenced is not None:
        text = fenced.group(1)
    try:
        payload = json.loads(text, object_pairs_hook=_unique_object)
    except (json.JSONDecodeError, ValueError) as exc:
        raise EnvironmentAssetError("asset reconciliation returned malformed JSON") from exc
    if isinstance(payload, dict) and payload.get("schema") == RECONCILIATION_SCHEMA:
        decisions, catalog_actions = _parse_reconciliation(
            text,
            business_date=business_date,
            observation_ids=observation_ids,
            catalog_ids=catalog_ids,
        )
        return decisions, catalog_actions, [], []
    if not isinstance(payload, dict) or set(payload) != {
        "schema", "businessDate", "infrastructure", "artifacts",
    }:
        raise EnvironmentAssetError("asset reconciliation has an invalid top-level contract")
    if payload.get("schema") != ASSET_RECONCILIATION_SCHEMA or payload.get("businessDate") != business_date:
        raise EnvironmentAssetError("asset reconciliation context does not match the run")
    infrastructure = payload.get("infrastructure")
    artifacts = payload.get("artifacts")
    if not isinstance(infrastructure, dict) or set(infrastructure) != {"decisions", "catalogActions"}:
        raise EnvironmentAssetError("asset reconciliation infrastructure contract is invalid")
    if not isinstance(artifacts, dict) or set(artifacts) != {"decisions", "catalogActions"}:
        raise EnvironmentAssetError("asset reconciliation artifact contract is invalid")
    legacy_payload = json.dumps(
        {
            "schema": RECONCILIATION_SCHEMA,
            "businessDate": business_date,
            "decisions": infrastructure["decisions"],
            "catalogActions": infrastructure["catalogActions"],
        },
        ensure_ascii=False,
    )
    decisions, catalog_actions = _parse_reconciliation(
        legacy_payload,
        business_date=business_date,
        observation_ids=observation_ids,
        catalog_ids=catalog_ids,
    )
    artifact_decisions = _parse_artifact_decisions(
        artifacts["decisions"],
        catalog_ids=artifact_catalog_ids,
        evidence_ids=artifact_evidence_ids,
    )
    artifact_actions = _parse_artifact_catalog_actions(
        artifacts["catalogActions"],
        catalog_ids=artifact_catalog_ids,
        candidate_ids={item["candidateId"] for item in artifact_decisions},
    )
    return decisions, catalog_actions, artifact_decisions, artifact_actions


def _parse_artifact_decisions(
    rows: Any,
    *,
    catalog_ids: set[str],
    evidence_ids: set[str],
) -> list[dict[str, Any]]:
    if not isinstance(rows, list):
        raise EnvironmentAssetError("asset reconciliation artifact decisions must be a list")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict) or not {
            "candidateId", "action", "name", "artifactType", "summary", "evidenceRefs"
        }.issubset(row):
            continue
        candidate_id = str(row.get("candidateId") or "")
        action = str(row.get("action") or "")
        existing_id = str(row.get("existingArtifactId") or "")
        raw_confidence = str(row.get("confidence") or "")
        confidence = raw_confidence if raw_confidence in CONFIDENCE_VALUES else "low"
        refs = row.get("evidenceRefs")
        normalized = row.get("normalized")
        if not re.fullmatch(r"A\d{6}", candidate_id) or candidate_id in seen:
            continue
        if action not in ARTIFACT_ACTIONS:
            continue
        if (action == "update_existing") != bool(existing_id) or (existing_id and existing_id not in catalog_ids):
            continue
        if (
            not isinstance(refs, list)
            or len(refs) != len(set(refs))
            or any(not isinstance(ref, str) or ref not in evidence_ids for ref in refs)
        ):
            continue
        normalized_shape_valid = isinstance(normalized, dict) and set(normalized) == {
            "status", "version", "locator", "project"
        }
        normalized_row = {
            key: str(normalized.get(key) or "").strip() if normalized_shape_valid else ""
            for key in ("status", "version", "locator", "project")
        }
        accepted = action in {"update_existing", "create_new"}
        reason = str(row.get("reason") or "").strip()
        if accepted and (
            not normalized_shape_valid
            or normalized_row["status"] not in ARTIFACT_STATUSES
            or not refs
            or not reason
            or raw_confidence not in CONFIDENCE_VALUES
            or (
                action == "create_new"
                and (
                    normalized_row["status"] not in {"produced", "verified"}
                    or not normalized_row["locator"]
                )
            )
        ):
            action = "defer"
            existing_id = ""
            confidence = "low" if confidence not in CONFIDENCE_VALUES else confidence
            reason = reason or "Model omitted required authority fields; candidate was deferred."
            normalized_row = {key: "" for key in normalized_row}
        elif not accepted and any(normalized_row.values()):
            normalized_row = {key: "" for key in normalized_row}
        text_values = {
            "name": str(row.get("name") or "").strip(),
            "artifactType": str(row.get("artifactType") or "").strip(),
            "summary": str(row.get("summary") or "").strip(),
            "reason": reason,
        }
        if not text_values["reason"] or any("\x00" in value or len(value) > 8_000 for value in text_values.values()):
            continue
        seen.add(candidate_id)
        result.append({
            "candidateId": candidate_id,
            "action": action,
            "existingArtifactId": existing_id,
            **text_values,
            "evidenceRefs": list(refs),
            "confidence": confidence,
            "normalized": normalized_row,
        })
    return result


def _parse_artifact_catalog_actions(
    rows: Any,
    *,
    catalog_ids: set[str],
    candidate_ids: set[str],
) -> list[dict[str, Any]]:
    if not isinstance(rows, list):
        raise EnvironmentAssetError("asset reconciliation artifact catalog actions must be a list")
    required = {
        "action", "artifactId", "canonicalArtifactId", "status",
        "candidateIds", "reason", "confidence",
    }
    result: list[dict[str, Any]] = []
    maintained: set[str] = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != required:
            continue
        action = str(row.get("action") or "")
        artifact_id = str(row.get("artifactId") or "")
        canonical_id = str(row.get("canonicalArtifactId") or "")
        status = str(row.get("status") or "")
        cited = row.get("candidateIds")
        reason = str(row.get("reason") or "").strip()
        confidence = str(row.get("confidence") or "")
        if action not in ARTIFACT_CATALOG_ACTIONS or artifact_id not in catalog_ids or artifact_id in maintained:
            continue
        if (
            not isinstance(cited, list) or not cited or len(cited) != len(set(cited))
            or any(not isinstance(item, str) or item not in candidate_ids for item in cited)
        ):
            continue
        if not reason or confidence not in CONFIDENCE_VALUES:
            continue
        if action == "merge_existing" and (canonical_id not in catalog_ids or canonical_id == artifact_id or status):
            continue
        if action == "archive_existing" and (canonical_id or status):
            continue
        if action == "transition_state" and (canonical_id or status not in ARTIFACT_STATUSES):
            continue
        maintained.add(artifact_id)
        result.append({
            "action": action,
            "artifactId": artifact_id,
            "canonicalArtifactId": canonical_id,
            "status": status,
            "candidateIds": list(cited),
            "reason": reason,
            "confidence": confidence,
        })
    return result


def _parse_reconciliation(
    raw: str,
    *,
    business_date: str,
    observation_ids: set[str],
    catalog_ids: set[str],
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    text = str(raw or "").strip()
    fenced = re.fullmatch(r"```json\s*\n([\s\S]*?)\n```", text)
    if fenced is not None:
        text = fenced.group(1)
    try:
        payload = json.loads(text, object_pairs_hook=_unique_object)
    except (json.JSONDecodeError, ValueError) as exc:
        raise EnvironmentAssetError("environment reconciliation returned malformed JSON") from exc
    if not isinstance(payload, dict) or set(payload) != {
        "schema", "businessDate", "decisions", "catalogActions"
    }:
        raise EnvironmentAssetError("environment reconciliation has an invalid top-level contract")
    if payload.get("schema") != RECONCILIATION_SCHEMA or payload.get("businessDate") != business_date:
        raise EnvironmentAssetError("environment reconciliation context does not match the run")
    rows = payload.get("decisions")
    if not isinstance(rows, list):
        raise EnvironmentAssetError("environment reconciliation decisions must be a list")
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict) or not {"observationId", "action"}.issubset(row):
            raise EnvironmentAssetError("environment reconciliation decision is malformed")
        observation_id = str(row.get("observationId") or "")
        action = str(row.get("action") or "")
        existing_entity_id = str(row.get("existingEntityId") or "")
        reason = str(row.get("reason") or "").strip()
        confidence = str(row.get("confidence") or "low")
        normalized = row.get("normalized")
        if observation_id not in observation_ids or observation_id in result:
            raise EnvironmentAssetError("environment reconciliation contains an unknown or duplicate observation")
        if action not in ACTIONS:
            raise EnvironmentAssetError("environment reconciliation contains an invalid decision")
        authority_fields_complete = bool(reason) and confidence in CONFIDENCE_VALUES
        forced_defer = False
        if reason and (len(reason) > MAX_REASON_CHARS or "\x00" in reason):
            raise EnvironmentAssetError("environment reconciliation reason is invalid")
        if not authority_fields_complete or (action == "update_existing") != bool(existing_entity_id):
            action = "defer"
            existing_entity_id = ""
            confidence = "low" if confidence not in CONFIDENCE_VALUES else confidence
            reason = reason or "Model omitted required authority fields; observation was deferred."
            forced_defer = True
        normalized_keys = {
            "kind", "state", "changeType", "host", "location", "endpoint",
            "port", "path", "version", "project",
        }
        normalized_shape_valid = isinstance(normalized, dict) and set(normalized) == normalized_keys
        normalized_row = (
            {
                key: str(normalized.get(key) or "").strip()
                for key in sorted(normalized_keys)
            }
            if normalized_shape_valid
            else {key: "" for key in sorted(normalized_keys)}
        )
        if forced_defer:
            normalized_row = {key: "" for key in normalized_row}
        if any("\x00" in value or len(value) > 8_000 for value in normalized_row.values()):
            raise EnvironmentAssetError("environment reconciliation normalized asset is invalid")
        accepted_action = action in {"update_existing", "create_new"}
        if accepted_action:
            if (
                not normalized_shape_valid
                or not normalized_row["kind"]
                or normalized_row["state"] not in {
                    "observed", "planned", "active", "suspended", "retired",
                    "produced", "verified", "superseded",
                }
                or normalized_row["changeType"] not in {
                    "observed", "planned", "created", "configured", "deployed",
                    "updated", "started", "stopped", "recovered", "degraded",
                    "retired", "produced", "verified", "superseded",
                }
            ):
                # A structurally complete but unusable semantic row must not
                # invalidate unrelated decisions.  Keep it visible in the
                # audit as a conservative defer; never guess replacement
                # enums or grant catalog authority in deterministic code.
                action = "defer"
                existing_entity_id = ""
                normalized_row = {key: "" for key in normalized_row}
        elif any(normalized_row.values()):
            raise EnvironmentAssetError("deferred environment observation must not carry normalized facts")
        result[observation_id] = {
            "action": action,
            "existingEntityId": existing_entity_id,
            "reason": reason,
            "confidence": confidence,
            "normalized": normalized_row,
        }
    if set(result) != observation_ids:
        raise EnvironmentAssetError("environment reconciliation omitted an observation")
    catalog_rows = payload.get("catalogActions")
    if not isinstance(catalog_rows, list):
        raise EnvironmentAssetError("environment reconciliation catalog actions must be a list")
    catalog_actions: list[dict[str, Any]] = []
    maintained_ids: set[str] = set()
    for row in catalog_rows:
        if not isinstance(row, dict) or set(row) != {
            "action", "entityId", "canonicalEntityId", "lifecycleStatus",
            "healthStatus", "observationIds", "reason", "confidence",
        }:
            continue
        action = str(row.get("action") or "")
        entity_id = str(row.get("entityId") or "").strip()
        canonical_id = str(row.get("canonicalEntityId") or "").strip()
        lifecycle = str(row.get("lifecycleStatus") or "").strip()
        health = str(row.get("healthStatus") or "").strip()
        cited = row.get("observationIds")
        reason = str(row.get("reason") or "").strip()
        confidence = str(row.get("confidence") or "")
        if action not in CATALOG_ACTIONS or entity_id not in catalog_ids or entity_id in maintained_ids:
            continue
        if canonical_id and canonical_id not in catalog_ids:
            continue
        if (
            not isinstance(cited, list)
            or not cited
            or any(not isinstance(item, str) or item not in observation_ids for item in cited)
            or len(cited) != len(set(cited))
        ):
            continue
        if not reason or len(reason) > MAX_REASON_CHARS or "\x00" in reason:
            continue
        if confidence not in CONFIDENCE_VALUES:
            continue
        if action == "merge_existing" and (not canonical_id or lifecycle or health):
            continue
        if action == "archive_existing" and (lifecycle or health):
            continue
        if action == "transition_state" and (
            canonical_id
            or (not lifecycle and not health)
            or (lifecycle and lifecycle not in CATALOG_LIFECYCLE_STATUSES)
            or (health and health not in CATALOG_HEALTH_STATUSES)
        ):
            continue
        maintained_ids.add(entity_id)
        catalog_actions.append({
            "action": action,
            "entityId": entity_id,
            "canonicalEntityId": canonical_id,
            "lifecycleStatus": lifecycle,
            "healthStatus": health,
            "observationIds": list(cited),
            "reason": reason,
            "confidence": confidence,
        })
    return result, catalog_actions


def _catalog_host(entity: Mapping[str, Any]) -> str:
    metadata = entity.get("metadata")
    return str(metadata.get("host") or "") if isinstance(metadata, Mapping) else ""


def _catalog_relevance(
    entity: Mapping[str, Any],
    observations: list[dict[str, Any]],
) -> int:
    """Rank without filtering; the LLM still receives the complete catalog."""

    entity_name = _identity_text(entity.get("name"))
    entity_kind = _identity_text(entity.get("kind"))
    entity_tokens = set(entity_name.split())
    best = 0
    for observation in observations:
        if observation.get("assetClass") not in {"device", "service"}:
            continue
        observation_name = _identity_text(observation.get("name"))
        observation_kind = _identity_text(observation.get("kind"))
        score = 0
        if entity_name and entity_name == observation_name:
            score += 100
        shared = entity_tokens & set(observation_name.split())
        score += min(40, len(shared) * 20)
        if entity_kind and observation_kind and (
            entity_kind == observation_kind
            or entity_kind in observation_kind
            or observation_kind in entity_kind
        ):
            score += 30
        if entity.get("entityType") == observation.get("assetClass"):
            score += 5
        best = max(best, score)
    return best


def _artifact_catalog_relevance(entity: Mapping[str, Any], technical_report: str) -> int:
    report = _identity_text(technical_report)
    name = _identity_text(entity.get("name"))
    aliases = [_identity_text(value) for value in entity.get("aliases") or ()]
    project = _identity_text(entity.get("project"))
    score = 100 if name and name in report else 0
    score += 60 if any(alias and alias in report for alias in aliases) else 0
    score += 10 if project and project in report else 0
    return score


def _technical_report(paths: RuntimePaths, business_date: str) -> str:
    candidates = diary_report_paths(paths.diary_dir, business_date, "technical", language_profile="zh")
    if not candidates:
        return ""
    try:
        return candidates[0].read_text(encoding="utf-8")[:160_000]
    except OSError:
        return ""


def _identity_text(value: Any) -> str:
    return " ".join(re.findall(r"[a-z0-9]+|[\u3400-\u9fff]+", str(value or "").casefold()))


def _host_scope(value: Any) -> str:
    normalized = re.sub(r"\s+", " ", str(value or "").strip()).casefold()
    return "host-" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12] if normalized else ""


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


__all__ = [
    "EnvironmentReconciliationResult",
    "build_environment_reconciliation_prompt",
    "run_environment_reconciliation",
]
