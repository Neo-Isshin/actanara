import os
import builtins
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "dashboard"))

from app.services import diary
from data_foundation.db import connect, migrate
from data_foundation.diary_markdown import materialize_diary_markdown_day
from data_foundation.infrastructure import apply_infrastructure_updates
from data_foundation.jobs import begin_ingestion_run
from data_foundation.paths import initialize_home, update_runtime_manifest_paths
from data_foundation.settings import write_settings


class DiaryPageFoundationTests(unittest.TestCase):
    def test_diary_list_prefers_no_activity_file_for_same_day(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            diary_root = root / "Diary"
            day = diary_root / "diary-2026" / "diary-2026-06" / "06-20"
            day.mkdir(parents=True)
            (day / "日记-260620.md").write_text("# standard\n", encoding="utf-8")
            (day / "日记-260620-no-activity.md").write_text("# empty\n", encoding="utf-8")
            paths = update_runtime_manifest_paths(initialize_home(root / "NovaDiary", legacy_diary_root=diary_root).home, generated_diary_root=diary_root, legacy_diary_root=diary_root)

            with patch.dict(os.environ, {"NOVA_HOME": str(paths.home)}):
                items = diary.get_diary_list()

        matching = [item for item in items if item["fullDate"] == "2026-06-20"]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0]["filename"], "日记-260620-no-activity.md")
        self.assertTrue(matching[0]["isBlankDay"])

    def test_diary_list_uses_foundation_activity_to_disambiguate_same_day(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            diary_root = root / "Diary"
            day = diary_root / "diary-2026" / "diary-2026-06" / "06-20"
            day.mkdir(parents=True)
            (day / "日记-260620.md").write_text("# standard\n", encoding="utf-8")
            (day / "日记-260620-no-activity.md").write_text("# stale empty\n", encoding="utf-8")
            paths = update_runtime_manifest_paths(
                initialize_home(root / "NovaDiary", legacy_diary_root=diary_root).home,
                generated_diary_root=diary_root,
                legacy_diary_root=diary_root,
            )
            migrate(paths)
            run_id = begin_ingestion_run(paths, trigger_type="fixture", business_date=date(2026, 6, 20))
            with connect(paths) as connection:
                connection.execute(
                    """
                    INSERT INTO tool_sources(
                        tool_key, display_name, adapter_version, capabilities_json,
                        enabled, created_at, updated_at
                    ) VALUES ('codex', 'Codex', 'test', '{}', 1,
                              '2026-06-20T00:00:00+00:00', '2026-06-20T00:00:00+00:00')
                    """
                )
                connection.execute(
                    """
                    INSERT INTO daily_tool_usage(
                        business_date, tool_key, tokens, messages, sessions, api_calls, source_run_id
                    ) VALUES ('2026-06-20', 'codex', 42, 1, 1, 1, ?)
                    """,
                    (run_id,),
                )

            with patch.dict(os.environ, {"NOVA_HOME": str(paths.home)}):
                item = next(row for row in diary.get_diary_list() if row["fullDate"] == "2026-06-20")

        self.assertFalse(item["isBlankDay"])
        self.assertEqual(item["activityStateSource"], "foundation-daily-tool-usage")

    def test_diary_list_uses_english_narrative_filename_when_profile_is_en(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            diary_root = root / "Diary"
            day = diary_root / "diary-2026" / "diary-2026-06" / "06-20"
            day.mkdir(parents=True)
            (day / "日记-260620.md").write_text("# zh\n", encoding="utf-8")
            (day / "diary-260620.md").write_text("# en\n", encoding="utf-8")
            other_day = diary_root / "diary-2026" / "diary-2026-06" / "06-21"
            other_day.mkdir(parents=True)
            (other_day / "narrative-260621.md").write_text("# retired alias\n", encoding="utf-8")
            paths = update_runtime_manifest_paths(
                initialize_home(root / "NovaDiary", legacy_diary_root=diary_root).home,
                generated_diary_root=diary_root,
                legacy_diary_root=diary_root,
            )
            write_settings({"pipeline": {"languageProfile": "en", "englishEnabled": True}}, paths)

            with patch.dict(os.environ, {"NOVA_HOME": str(paths.home)}):
                items = diary.get_diary_list()

        matching = [item for item in items if item["fullDate"] == "2026-06-20"]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0]["filename"], "diary-260620.md")
        self.assertFalse(any(item["fullDate"] == "2026-06-21" for item in items))

    def test_parse_diary_uses_english_narrative_filename_when_profile_is_en(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            diary_root = root / "Diary"
            day = diary_root / "diary-2026" / "diary-2026-06" / "06-20"
            day.mkdir(parents=True)
            (day / "日记-260620.md").write_text("# Chinese diary\n\n## 今日概要\nwrong\n", encoding="utf-8")
            (day / "diary-260620.md").write_text("# English diary\n\n## Daily Overview\nright\n", encoding="utf-8")
            paths = update_runtime_manifest_paths(
                initialize_home(root / "NovaDiary", legacy_diary_root=diary_root).home,
                generated_diary_root=diary_root,
                legacy_diary_root=diary_root,
            )
            write_settings({"pipeline": {"languageProfile": "en", "englishEnabled": True}}, paths)

            with patch.dict(os.environ, {"NOVA_HOME": str(paths.home)}), patch.object(
                diary, "_get_jsonl_stats", return_value={"hourlyTokens": {}, "agentStats": {}, "sessionStats": {"sessions": 0, "messages": 0}}
            ), patch.object(diary, "detect_cron_tasks", return_value=[]):
                page = diary.parse_diary("2026-06-20")

        self.assertIsNotNone(page)
        self.assertIn("English diary", page["rawContent"])
        self.assertNotIn("Chinese diary", page["rawContent"])

    def test_parse_diary_reads_english_raw_overview_lessons_and_infra(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            diary_root = root / "Diary"
            day = diary_root / "diary-2026" / "diary-2026-06" / "06-20"
            day.mkdir(parents=True)
            (day / "diary-260620.md").write_text(
                """# 2026-06-20 Diary

## Weather
Cloudy, 28 C

## Daily Overview
* **Runtime language gate**: English profile stayed isolated.
  - Chinese production pipeline remained unchanged.

## Daily Stats
| Metric | codex | Total |
| --- | --- | --- |
| messages_count | 5 | 5 |
| total_tokens | 120 | 120 |

## Agent Work
### codex
- **[Implementation 10:18-11:05] - English prompt payloads**
  - Added isolated English Narrative, Technical, and Learning payloads.
- **[Model Config Inspection & Upgrade 11:37-11:37] - Upgraded default agent model**
  - Confirmed bracketed English task titles are not agent headings.

## Important Notices
1. **Install gate**: Language is fixed after installation.

## Scheduled Jobs
| Time | Task | Status | Duration | Note |
| --- | --- | --- | --- | --- |
| 04:03 | daily-pipeline | Success | 10s | completed |

## Notes
**Artifacts**
- No generated artifacts were written.
""",
                encoding="utf-8",
            )
            (day / "learning-260620.md").write_text(
                """# 2026-06-20 Learning and Infrastructure Audit

## Lessons
### [codex] Prompt drift
#### Problem
Partial packets emitted YAML.
#### Root Cause
The prompt did not forbid intermediate YAML.
#### Recommendation
Only final integration should emit Nova-Task YAML.

## Infrastructure Updates
| Object | Change | Current Value |
| --- | --- | --- |
| Pipeline language gate | English profile install-only gate | ready |
""",
                encoding="utf-8",
            )
            paths = update_runtime_manifest_paths(
                initialize_home(root / "NovaDiary", legacy_diary_root=diary_root).home,
                generated_diary_root=diary_root,
                legacy_diary_root=diary_root,
            )
            write_settings({"pipeline": {"languageProfile": "en", "englishEnabled": True}}, paths)

            with patch.dict(os.environ, {"NOVA_HOME": str(paths.home)}), patch.object(
                diary, "_get_jsonl_stats", return_value={"hourlyTokens": {}, "agentStats": {}, "sessionStats": {"sessions": 0, "messages": 0}}
            ), patch.object(diary, "detect_cron_tasks", return_value=[]):
                page = diary.parse_diary("2026-06-20")
                direct = diary._parse_raw((day / "diary-260620.md").read_text(encoding="utf-8"), "2026-06-20", {}, use_live_sources=False)

        self.assertIsNotNone(page)
        self.assertEqual(page["languageProfile"], "en")
        self.assertEqual(page["weather"], "Cloudy, 28 C")
        self.assertEqual(page["summaryTopics"][0]["title"], "Runtime language gate: English profile stayed isolated.")
        self.assertEqual(page["parsedKpi"]["total_tokens"], 120)
        self.assertEqual(sorted(page["agentWorkNew"].keys()), ["codex"])
        self.assertEqual(page["agentWorkNew"]["codex"][0]["period"], "Implementation")
        self.assertEqual(page["agentWorkNew"]["codex"][0]["main_task"], "English prompt payloads")
        self.assertEqual(page["agentWorkNew"]["codex"][1]["main_task"], "Upgraded default agent model")
        self.assertEqual(page["reminders"][0]["title"], "Install gate")
        self.assertEqual(page["notes"][0]["title"], "Artifacts")
        self.assertEqual(direct["cronTasks"][0]["task"], "daily-pipeline")
        self.assertEqual(direct["cronTasks"][0]["time"], "04:03")
        self.assertEqual(page["lessons"][0]["agent"], "codex")
        self.assertEqual(page["lessons"][0]["rootCause"], "The prompt did not forbid intermediate YAML.")
        self.assertEqual(page["infraChanges"][0]["target"], "Pipeline language gate")

    def test_parse_raw_agent_work_accepts_bracket_agent_and_bullet_bold_tasks(self):
        raw = """# 2026年06月22日 日记

## Agent工作
### [codex]
- **[上午 04:00-12:00] - Dashboard 周报页修复**
- 周报页长列表扫描体验：日期筛选
- 空白日记生成：assemble_final
- **错误处理**：测试 `networkidle` 改为兼容等待。
* **[凌晨 00:00-04:00] - Issue 批量修复**
- Issue #83：修复 token 归因。
- Issue #84：补 defaults 对齐。

## 备注
None
"""

        parsed = diary._parse_raw(raw, "2026-06-22", {}, use_live_sources=False)

        self.assertEqual(sorted(parsed["agentWorkNew"].keys()), ["codex"])
        entries = parsed["agentWorkNew"]["codex"]
        self.assertEqual(entries[0]["period"], "上午")
        self.assertEqual(entries[0]["main_task"], "Dashboard 周报页修复")
        self.assertEqual(
            entries[0]["sub_items"],
            ["周报页长列表扫描体验：日期筛选", "空白日记生成：assemble_final"],
        )
        self.assertEqual(entries[1]["main_task"], "错误处理")
        self.assertEqual(entries[1]["sub_items"], ["测试 `networkidle` 改为兼容等待。"])
        self.assertEqual(entries[2]["period"], "凌晨")
        self.assertEqual(entries[2]["main_task"], "Issue 批量修复")
        self.assertEqual(entries[2]["sub_items"], ["Issue #83：修复 token 归因。", "Issue #84：补 defaults 对齐。"])

    def test_parse_raw_reads_english_dash_bold_daily_overview(self):
        raw = """## Daily Overview

- **[Model Upgrade Strategy]**: Upgraded the default agent model.
  - Updated `agents.defaults.model.primary`.
  - Applies to new sessions only.

## Agent Work
### coder
- **[Review 11:37-11:37] - Checked model config**
  - Confirmed provider stayed unchanged.
"""

        parsed = diary._parse_raw(raw, "2026-06-01", {}, use_live_sources=False)

        self.assertEqual(parsed["summaryTopics"][0]["title"], "Model Upgrade Strategy: Upgraded the default agent model.")
        self.assertEqual(
            parsed["summaryTopics"][0]["items"],
            ["Updated `agents.defaults.model.primary`.", "Applies to new sessions only."],
        )
        self.assertEqual(parsed["agentWorkNew"]["coder"][0]["main_task"], "Checked model config")

    def test_detect_cron_tasks_returns_empty_when_croniter_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cron = root / ".openclaw" / "cron"
            cron.mkdir(parents=True)
            (cron / "jobs.json").write_text(
                json.dumps({"jobs": [{"id": "job", "enabled": True, "schedule": {"expr": "* * * * *"}}]}),
                encoding="utf-8",
            )
            real_import = builtins.__import__

            def fake_import(name, *args, **kwargs):
                if name == "croniter":
                    raise ModuleNotFoundError("No module named 'croniter'")
                return real_import(name, *args, **kwargs)

            with patch.dict(os.environ, {"HOME": str(root)}), patch.object(builtins, "__import__", side_effect=fake_import):
                self.assertEqual(diary.detect_cron_tasks("2026-05-19"), [])

    def test_diary_memory_stats_use_configured_openclaw_memory_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = initialize_home(root / "NovaDiary", legacy_diary_root=root / "Diary")
            memory_root = root / "configured-tools" / "openclaw" / "memory"
            memory_root.mkdir(parents=True)
            (memory_root / "daily-note.md").write_text("memory\n", encoding="utf-8")
            write_settings({"externalTools": {"openclaw": {"memoryRoot": str(memory_root)}}}, paths)

            with patch.dict(os.environ, {"NOVA_HOME": str(paths.home)}):
                _, memory = diary._get_rag_memory_stats(include_rag=False)

        self.assertEqual(memory["sessionFiles"], 1)

    def test_diary_rag_stats_use_active_v2_index_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            index_path = root / "reserved" / "rag" / "v2" / "indexes" / "active" / "run-1" / "index.jsonl"
            index_path.parent.mkdir(parents=True)
            index_path.write_text("{}\n{}\n", encoding="utf-8")
            status = {
                "v2": {
                    "ready": True,
                    "activeIndexPath": str(index_path),
                    "chunkCount": 2,
                    "updatedAt": "2026-06-25T15:53:40+08:00",
                },
                "activeIndex": {"indexPath": str(index_path)},
            }

            with patch("agentic_rag.rag_status.read_rag_status", return_value=status):
                rag, _ = diary._get_rag_memory_stats(include_memory=False)

        self.assertEqual(rag["entries"], 2)
        self.assertEqual(rag["source"], "rag-v2-active")
        self.assertEqual(rag["indexPath"], str(index_path))
        self.assertEqual(rag["updatedAt"], "2026-06-25T15:53:40+08:00")

    def test_diary_rag_stats_do_not_fall_back_to_legacy_index_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            diary_root = root / "Diary"
            legacy_index = diary_root / "__diary_rag" / "index.jsonl"
            legacy_index.parent.mkdir(parents=True)
            legacy_index.write_text("{}\n{}\n{}\n", encoding="utf-8")
            paths = update_runtime_manifest_paths(
                initialize_home(root / "NovaDiary", legacy_diary_root=diary_root).home,
                generated_diary_root=diary_root,
                legacy_diary_root=diary_root,
            )

            with (
                patch.dict(os.environ, {"NOVA_HOME": str(paths.home)}),
                patch("agentic_rag.rag_status.read_rag_status", side_effect=RuntimeError("v2 unavailable")),
            ):
                rag, _ = diary._get_rag_memory_stats(include_memory=False)

        self.assertEqual(rag["entries"], 0)
        self.assertEqual(rag["source"], "rag-v2-unavailable")
        self.assertEqual(rag["reason"], "rag-status-unavailable")
        self.assertNotIn("indexPath", rag)

    def test_single_diary_page_reads_foundation_snapshot_without_markdown_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            diary_root = root / "Diary"
            day = diary_root / "diary-2026-05-19"
            day.mkdir(parents=True)
            (day / "日记-260519.md").write_text(
                """# 2026年05月19日 日记

## 今日概要

### TokenClock 修复完成
- 修复设置窗口崩溃
- 推送 2 个 commit

```json
{"date": "2026-05-19", "metrics": {"total": {"active_sessions": 2, "sessions_total": 5}, "total_tokens": 42, "input_tokens": 20, "output_tokens": 22, "api_calls": 3}}
```
""",
                encoding="utf-8",
            )
            (day / "智慧沉淀-260519.md").write_text(
                """# 2026-05-19 智慧沉淀与基建审计

## 🧠 黄金教训 (Lessons)
### 【codex】快照刷新后缺少展示验证
#### 问题
快照刷新后缺少展示验证。
#### 根因
单日页面只检查了 Foundation 入库，未验证 Dashboard 可读性。
#### 建议
补端到端检查。

## 基建变动
| 对象 | 变动 | 当前状态 |
| --- | --- | --- |
| Dashboard | 改为 Foundation 日记页 | ready |
""",
                encoding="utf-8",
            )
            paths = update_runtime_manifest_paths(initialize_home(root / "NovaDiary", legacy_diary_root=diary_root).home, generated_diary_root=diary_root, legacy_diary_root=diary_root)
            migrate(paths)
            apply_infrastructure_updates(
                paths,
                "2026-05-19",
                [
                    {
                        "entityType": "service",
                        "name": "Dashboard server",
                        "eventType": "port_changed",
                        "field": "port",
                        "change": "Dashboard server graph event",
                        "currentValue": "3036",
                    }
                ],
            )
            materialize_diary_markdown_day(paths, date(2026, 5, 19), source_run_id=None)

            with patch.dict(os.environ, {"NOVA_HOME": str(paths.home)}), patch.object(
                diary, "_get_jsonl_stats", side_effect=AssertionError("Foundation diary page must not scan JSONL")
            ), patch.object(
                diary, "detect_cron_tasks", side_effect=AssertionError("Foundation diary page must not scan cron files")
            ):
                result = diary.get_diary_page("2026-05-19")

            self.assertEqual(result["dataFreshness"]["diaryPage"]["source"], "foundation")
            self.assertEqual(result["summaryTopics"][0]["title"], "TokenClock 修复完成")
            self.assertEqual(result["lessons"][0]["agent"], "codex")
            self.assertEqual(result["lessons"][0]["rootCause"], "单日页面只检查了 Foundation 入库，未验证 Dashboard 可读性。")
            self.assertEqual(result["infraChanges"][0]["target"], "Dashboard server")
            self.assertEqual(result["infraChanges"][0]["eventType"], "port_changed")
            self.assertEqual(result["infraChanges"][0]["current"], "3036")
            self.assertEqual(result["parsedKpi"]["total_tokens"], 42)
            self.assertEqual(result["hourlyTokens"], {})

    def test_single_diary_page_reads_english_foundation_lessons_and_infra(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            diary_root = root / "Diary"
            day = diary_root / "diary-2026-05-19"
            day.mkdir(parents=True)
            (day / "diary-260519.md").write_text(
                """# 2026-05-19 Diary

## Daily Overview
* **Runtime language gate**: ready
""",
                encoding="utf-8",
            )
            (day / "learning-260519.md").write_text(
                """# 2026-05-19 Learning and Infrastructure Audit

## Lessons
### [codex] Prompt drift
#### Problem
Drift.
#### Root Cause
Missing English field aliases.
#### Recommendation
Normalize semantic headings.

## Infrastructure Updates
| Object | Change | Current Value |
| --- | --- | --- |
| Dashboard Daily page | English learning fallback | ready |
""",
                encoding="utf-8",
            )
            paths = update_runtime_manifest_paths(
                initialize_home(root / "NovaDiary", legacy_diary_root=diary_root).home,
                generated_diary_root=diary_root,
                legacy_diary_root=diary_root,
            )
            write_settings({"pipeline": {"languageProfile": "en", "englishEnabled": True}}, paths)
            migrate(paths)
            materialize_diary_markdown_day(paths, date(2026, 5, 19), source_run_id=None)

            with patch.dict(os.environ, {"NOVA_HOME": str(paths.home)}), patch.object(
                diary, "detect_cron_tasks", side_effect=AssertionError("Foundation diary page must not scan cron files")
            ):
                result = diary.get_diary_page("2026-05-19")

        self.assertEqual(result["summaryTopics"][0]["title"], "Runtime language gate: ready")
        self.assertEqual(result["languageProfile"], "en")
        self.assertEqual(result["lessons"][0]["agent"], "codex")
        self.assertEqual(result["lessons"][0]["suggestion"], "Normalize semantic headings.")
        self.assertEqual(result["infraChanges"][0]["change"], "English learning fallback")

    def test_single_diary_page_uses_embedded_kpis_cron_and_foundation_hourly_tokens(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            diary_root = root / "Diary"
            day = diary_root / "diary-2026-05-19"
            day.mkdir(parents=True)
            (day / "日记-260519.md").write_text(
                """# 2026年05月19日 日记

## 今日概要
* **多Agent并行作业**
  - 核心进展：跨平台工具部署完成
  - 技术多样性：覆盖 Rust 与 Python
  - 交付成果：同步 2 个 commit

* **前端工程化突破**
  - 核心变更：Dashboard 静态资源解耦
  - 资源解耦：CSS 与 JS 分离
  - 待验证项：确认静态服务路由

* **安全运维实践**
  - 工具选型：RustNet
  - 编译链路：依赖补齐
  - 技术栈：eBPF 与 TUI

## 本日统计
| 指标 | openclaw | **合计** |
| :--- | :--- | :--- |
| messages_count | 0 | **0** |
| total_tokens | 0 | **0** |

## 定时任务情况
| 时间 | 任务ID | 状态 | 耗时 | 执行结论 |
| :--- | :--- | :--- | :--- | :--- |
| 04:03 | abc | Success | 10s | ✅ 完成 |

```json
{"date": "2026-05-19", "metrics": {"total": {"active_sessions": 2, "sessions_total": 5, "messages_count": 149, "total_tokens": 119, "input_tokens": 100, "output_tokens": 10, "cache_read": 9, "api_calls": 7}}, "cronTasks": [{"time": "04:03", "taskId": "abc", "status": "Success", "duration": "10s", "conclusion": "✅ 完成"}]}
```
""",
                encoding="utf-8",
            )
            paths = update_runtime_manifest_paths(initialize_home(root / "NovaDiary", legacy_diary_root=diary_root).home, generated_diary_root=diary_root, legacy_diary_root=diary_root)
            write_settings({"general": {"timezone": "Asia/Hong_Kong"}}, paths)
            migrate(paths)
            materialize_diary_markdown_day(paths, date(2026, 5, 19), source_run_id=None)
            _insert_usage_event(paths, "2026-05-19T00:30:00+00:00", 50)
            _insert_usage_event(paths, "2026-05-19T15:30:00+00:00", 70)

            with patch.dict(os.environ, {"NOVA_HOME": str(paths.home)}), patch.object(
                diary, "_get_jsonl_stats", side_effect=AssertionError("Foundation diary page must not scan JSONL")
            ), patch.object(
                diary, "detect_cron_tasks", side_effect=AssertionError("Foundation diary page must not scan cron files")
            ):
                result = diary.get_diary_page("2026-05-19")

        self.assertEqual(len(result["summaryTopics"]), 3)
        self.assertEqual(result["summaryTopics"][0]["title"], "多Agent并行作业")
        self.assertEqual(len(result["summaryTopics"][0]["items"]), 3)
        self.assertEqual(result["parsedKpi"]["messages_count"], 149)
        self.assertEqual(result["parsedKpi"]["total_tokens"], 119)
        self.assertEqual(
            result["cronTasks"],
            [{
                "time": "04:03",
                "taskId": "abc",
                "status": "Success",
                "duration": "10s",
                "conclusion": "✅ 完成",
                "task": "abc",
                "note": "✅ 完成",
            }],
        )
        self.assertEqual(result["hourlyTokens"], {"08": 50, "23": 70})

    def test_single_diary_page_prefers_foundation_rollup_sessions_over_embedded_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            diary_root = root / "Diary"
            day = diary_root / "diary-2026-05-19"
            day.mkdir(parents=True)
            (day / "日记-260519.md").write_text(
                """# 2026年05月19日 日记

## 今日概要
* **Dashboard 数据校正**
  - 使用 Foundation rollup。

```json
{"date": "2026-05-19", "metrics": {"total": {"active_sessions": 21, "sessions_total": 21, "messages_count": 149, "total_tokens": 119}, "openclaw": {"active_sessions": 18, "sessions_total": 18}, "claude-code": {"active_sessions": 3, "sessions_total": 3}}}
```
""",
                encoding="utf-8",
            )
            paths = update_runtime_manifest_paths(
                initialize_home(root / "NovaDiary", legacy_diary_root=diary_root).home,
                generated_diary_root=diary_root,
                legacy_diary_root=diary_root,
            )
            migrate(paths)
            run_id = begin_ingestion_run(paths, trigger_type="fixture", business_date=date(2026, 5, 19))
            materialize_diary_markdown_day(paths, date(2026, 5, 19), source_run_id=None)
            with connect(paths) as connection:
                connection.executemany(
                    """
                    INSERT OR IGNORE INTO tool_sources(
                        tool_key, display_name, adapter_version, capabilities_json,
                        enabled, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, 1, ?, ?)
                    """,
                    [
                        ("codex", "Codex", "test", "[]", "2026-05-19T00:00:00+00:00", "2026-05-19T00:00:00+00:00"),
                        ("claude-code", "Claude Code", "test", "[]", "2026-05-19T00:00:00+00:00", "2026-05-19T00:00:00+00:00"),
                    ],
                )
                connection.executemany(
                    """
                    INSERT INTO daily_tool_usage(
                        business_date, tool_key, tokens, messages, sessions, api_calls, source_run_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        ("2026-05-19", "codex", 100, 10, 4, 10, run_id),
                        ("2026-05-19", "claude-code", 50, 5, 2, 5, run_id),
                    ],
                )

            with patch.dict(os.environ, {"NOVA_HOME": str(paths.home)}):
                result = diary.get_diary_page("2026-05-19")

        self.assertEqual(sorted(result["sessionBySource"]), ["claude-code", "codex"])
        self.assertEqual(result["sessionBySource"]["codex"]["active_sessions"], 4)
        self.assertEqual(result["parsedKpi"]["active_sessions"], 6)
        self.assertEqual(result["parsedKpi"]["sessions_total"], 6)

    def test_foundation_hourly_tokens_follow_configured_0400_business_day(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = initialize_home(root / "NovaDiary", legacy_diary_root=root / "Diary")
            migrate(paths)
            _insert_usage_event(paths, "2026-05-19T19:30:00+00:00", 30, business_date="2026-05-19")
            _insert_usage_event(paths, "2026-05-19T20:30:00+00:00", 40, business_date="2026-05-20")

            with patch.dict(os.environ, {"NOVA_HOME": str(paths.home), "TARGET_TIMEZONE": "Asia/Hong_Kong"}):
                result = diary._foundation_hourly_tokens(paths, date(2026, 5, 19))

        self.assertEqual(result, {"03": 30})

    def test_single_diary_page_exposes_blank_activity_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            diary_root = root / "Diary"
            day = diary_root / "diary-2026-05-19"
            day.mkdir(parents=True)
            (day / "日记-260519.md").write_text(
                """# 2026年05月19日 日记

## 天气

weather

## 今日概要
今日无活动

## 定时任务情况
| 时间 | 任务ID | 状态 | 耗时 | 执行结论 |
| :--- | :--- | :--- | :--- | :--- |
| 04:03 | daily | Success | 10s | ✅ 完成 |

```json
{"date": "2026-05-19", "activityState": "empty", "metrics": {"total": {"messages_count": 0, "total_tokens": 0}}, "cronTasks": [{"time": "04:03", "taskId": "daily", "status": "Success", "duration": "10s", "conclusion": "✅ 完成"}]}
```
""",
                encoding="utf-8",
            )
            paths = update_runtime_manifest_paths(initialize_home(root / "NovaDiary", legacy_diary_root=diary_root).home, generated_diary_root=diary_root, legacy_diary_root=diary_root)
            migrate(paths)
            materialize_diary_markdown_day(paths, date(2026, 5, 19), source_run_id=None)

            with patch.dict(os.environ, {"NOVA_HOME": str(paths.home)}), patch.object(
                diary, "detect_cron_tasks", side_effect=AssertionError("Foundation diary page must not scan cron files")
            ):
                result = diary.get_diary_page("2026-05-19")

        self.assertEqual(result["activityState"], "empty")
        self.assertEqual(result["weather"], "weather")
        self.assertEqual(result["summary"], "今日无活动")
        self.assertEqual(result["cronTasks"][0]["task"], "daily")

    def test_single_diary_page_returns_snapshot_missing_without_markdown_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            diary_root = root / "Diary"
            day = diary_root / "diary-2026-05-19"
            day.mkdir(parents=True)
            (day / "日记-260519.md").write_text("# 2026年05月19日 日记\n\n## 今日概要\n不应被读取\n", encoding="utf-8")
            paths = update_runtime_manifest_paths(initialize_home(root / "NovaDiary", legacy_diary_root=diary_root).home, generated_diary_root=diary_root, legacy_diary_root=diary_root)
            migrate(paths)

            with patch.dict(os.environ, {"NOVA_HOME": str(paths.home)}):
                result = diary.get_diary_page("2026-05-19")

            self.assertEqual(result["dataFreshness"]["diaryPage"]["source"], "snapshot-missing")
            self.assertTrue(result["dataFreshness"]["diaryPage"]["refreshRequired"])
            self.assertEqual(result["summaryTopics"], [])
            self.assertEqual(result["rawContent"], "")

def _insert_usage_event(paths, occurred_at: str, protocol_total: int, *, business_date: str = "2026-05-19") -> None:
    with connect(paths) as connection:
        connection.execute(
            """
            INSERT OR IGNORE INTO tool_sources(
                tool_key, display_name, adapter_version, capabilities_json,
                enabled, created_at, updated_at
            ) VALUES ('codex', 'Codex', 'test', '{}', 1, '2026-05-19T00:00:00+00:00', '2026-05-19T00:00:00+00:00')
            """
        )
        cursor = connection.execute(
            """
            INSERT INTO sessions(tool_key, external_session_key, started_at, last_active_at, initial_cwd, metadata_json)
            VALUES ('codex', ?, ?, ?, '/workspace/example/open-nova', '{}')
            """,
            (f"session-{occurred_at}", occurred_at, occurred_at),
        )
        connection.execute(
            """
            INSERT INTO usage_events(
                tool_key, session_id, external_event_key, occurred_at, business_date,
                protocol_total_tokens, message_count, raw_locator_json, metadata_json
            ) VALUES ('codex', ?, ?, ?, ?, ?, 1, '{}', '{}')
            """,
            (cursor.lastrowid, f"event-{occurred_at}", occurred_at, business_date, protocol_total),
        )


if __name__ == "__main__":
    unittest.main()
