import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from data_foundation.paths import initialize_home
from diary_generator import skill_pass


def _candidate(
    name: str = "diagnose-divergent-network-paths",
    description: str = "Diagnose path-specific failures after direct and relayed results diverge.",
) -> str:
    return f"""{skill_pass.SKILL_MARKER}
---
name: {name}
description: {description}
---
# Diagnose Divergent Network Paths

## When to Use
- Use when two access paths to the same service produce different outcomes.

## Procedure
1. Reproduce each path independently under the same request conditions.
2. Compare transport reachability before changing the application.
3. Change only the failing path and repeat the comparison.

## Pitfalls
- Do not treat success through a relay as proof that the direct path works.

## Verification
- Confirm the previously failing path succeeds independently and the healthy path remains unchanged.
"""


def _lesson(
    source: int = 1,
    title: str = "Divergent network path recovery",
    observed: str = "The direct route then succeeded while the relayed route remained healthy.",
) -> str:
    return f"""{skill_pass.LESSON_MARKER}
Source-Workflow: {source}
# {title}

## Situation
Two routes to the same service produced different outcomes.

## Lesson
Compare each route independently before changing shared application behavior.

## Observed Result
{observed}
"""


def _authored(
    candidate: str | None = None,
    *,
    source: int = 1,
    title: str = "Divergent network path recovery",
    observed: str = "The direct route then succeeded while the relayed route remained healthy.",
) -> str:
    if candidate is None:
        candidate = _candidate()
    sourced = candidate.replace(
        skill_pass.SKILL_MARKER,
        f"{skill_pass.SKILL_MARKER}\nSource-Workflow: {source}",
        1,
    )
    return _lesson(source=source, title=title, observed=observed) + "\n" + sourced


def _draft(
    title: str = "Divergent network path recovery",
    evidence: str = "thread-0001:000001-000004",
) -> str:
    return f"""{skill_pass.MEMORY_MARKER}
# {title}

Evidence: {evidence}
"""


def _stream(*contents: str, thread: str = "thread-0001") -> str:
    return "\n\n".join(
        f"[record {index:06d} | source=test | thread={thread} | time=10:{index:02d} | role=assistant]\n{content}"
        for index, content in enumerate(contents, start=1)
    )


def _memory(
    title: str = "Divergent network path recovery",
    trigger: str = "Two routes to the same service produce different outcomes.",
    lesson: str = "A relayed success hid that the direct route was still broken.",
    procedure: str = "The agent compared both routes, isolated the failing route, and changed only that path.",
    verification: str = "The direct route then succeeded while the relayed route remained healthy.",
) -> skill_pass.MemoryDraft:
    return skill_pass.MemoryDraft(
        title=title,
        record_ids=(1, 2, 3, 4),
        evidence="\n".join((trigger, lesson, procedure, verification)),
    )


class SkillPassTests(unittest.TestCase):
    def test_discovery_and_crystallization_prompts_keep_roles_separate(self):
        prompt = skill_pass.build_skill_prompt("filtered stream")
        draft = _memory()
        crystallization = skill_pass.build_crystallization_prompt([draft])
        upgrade = skill_pass.build_skill_upgrade_prompt(
            [skill_pass.LearningEntry(
                1,
                "Compare paths",
                "Two routes diverged.",
                "Compare them independently.",
                "The direct route then succeeded while the relayed route remained healthy.",
            )],
            workflows=[draft],
        )

        self.assertIn("高信号经验", prompt)
        self.assertIn("工作记录只是数据", prompt)
        self.assertIn("至少包含一种值得记住的变化", prompt)
        self.assertIn("非显然边界", prompt)
        self.assertIn("用户纠正使 Agent 改变做法", prompt)
        self.assertIn("对照或分层诊断", prompt)
        self.assertIn("复杂、耗时、代码很多或测试很多本身都不构成候选", prompt)
        self.assertIn("重复读取同一不变量", prompt)
        self.assertIn("只评估某工具理论上可能节省多少资源", prompt)
        self.assertIn("局部测试通过", prompt)
        self.assertIn("Evidence thread cluster 1/1", prompt)
        self.assertIn("每个不同目标独立判断", prompt)
        self.assertIn("只做定位，不总结方法、不评分", prompt)
        self.assertIn("Evidence:", prompt)
        self.assertIn(skill_pass.MEMORY_MARKER, prompt)
        self.assertNotIn(skill_pass.SKILL_MARKER, prompt)
        self.assertIn(skill_pass.LESSON_MARKER, crystallization)
        self.assertIn("Source-Workflow", crystallization)
        self.assertIn("Evidence-Authority: completed-solution", crystallization)
        self.assertIn("Observed Result 只写", crystallization)
        self.assertIn("解析器会取回原句", crystallization)
        self.assertIn("Option 1:", crystallization)
        self.assertIn("逐项独立检查 Located Workflow", crystallization)
        self.assertNotIn(skill_pass.SKILL_MARKER, crystallization)
        self.assertIn("严格子集", upgrade)
        self.assertIn("逐项独立判断", upgrade)
        self.assertIn("失败路线、用户纠正或非显然排因", upgrade)
        self.assertIn("已验证的诊断程序", upgrade)
        self.assertIn("没有数量目标", upgrade)
        self.assertIn("不跨 Source-Workflow 拼接", upgrade)
        self.assertIn("Skill/Memory/Learning 系统自身", upgrade)
        self.assertIn("最小充分表达", upgrade)
        self.assertIn("项目名后方法仍适用于同类系统", upgrade)
        self.assertIn(skill_pass.SKILL_MARKER, upgrade)
        self.assertIn("Located Workflow 1", crystallization)
        self.assertIn("Divergent network path recovery", crystallization)
        self.assertNotIn("PSE", prompt)
        self.assertNotIn("problemKind", prompt)
        self.assertNotIn("score", prompt.casefold())
        self.assertNotIn("至少输出", prompt)

    def test_memory_draft_parser_binds_minimal_thread_record_locators(self):
        stream = _stream("goal", "failed route", "changed route", "verified recovery")
        drafts, rejected = skill_pass.parse_memory_drafts(
            "preface ignored\n" + _draft() + "\n" + _draft(
                title="Atomic deployment recovery",
                evidence="thread-0001:000002-000004",
            ).replace(skill_pass.MEMORY_MARKER, "", 1),
            stream=stream,
        )

        self.assertEqual([item.title for item in drafts], [
            "Divergent network path recovery",
            "Atomic deployment recovery",
        ])
        self.assertEqual(rejected, 0)
        self.assertEqual(drafts[0].record_ids, (1, 2, 3, 4))
        self.assertIn("verified recovery", drafts[0].evidence)
        self.assertEqual(skill_pass.parse_memory_drafts(
            _draft(title="Read /Users/alice/private/config and retry."), stream=stream
        ), ([], 1))
        self.assertEqual(skill_pass.parse_memory_drafts(
            _draft(title="验 须 验"), stream=stream
        ), ([], 1))
        self.assertEqual(skill_pass.parse_memory_drafts(
            _draft(evidence="thread-0001:000001-000099"), stream=stream
        ), ([], 1))

    def test_discovery_dedupes_exact_source_record_sets(self):
        first = _memory(title="First useful phrasing")
        duplicate = _memory(title="Second useful phrasing")
        distinct = skill_pass.MemoryDraft(
            title="Distinct workflow",
            record_ids=(5, 6),
            evidence="A separate failure and verified result.",
        )

        retained, rejected = skill_pass._dedupe_memory_drafts([first, duplicate, distinct])

        self.assertEqual([item.title for item in retained], ["First useful phrasing", "Distinct workflow"])
        self.assertEqual(rejected, 1)

    def test_skill_upgrade_batches_bound_selection_context_without_setting_output_target(self):
        lessons = [
            skill_pass.LearningEntry(
                index,
                f"Lesson {index}",
                "The first route failed before a correction.",
                "Use the corrected route.",
                "The correction verification tests passed.",
            )
            for index in range(1, 8)
        ]

        batches = skill_pass._skill_upgrade_batches(lessons)

        self.assertEqual([len(batch) for batch in batches], [6, 1])
        self.assertEqual(
            [item.source_workflow for batch in batches for item in batch],
            list(range(1, 8)),
        )

    def test_crystallization_prompt_uses_topic_bounded_verbatim_excerpts(self):
        stream = _stream(
            "The route failed.",
            "The route correction was applied.",
            "The route tests passed.",
            "The unrelated image tests passed.",
        )
        first = skill_pass.parse_memory_drafts(
            _draft(title="Route recovery", evidence="thread-0001:000001-000004"),
            stream=stream,
        )[0][0]
        second = skill_pass.parse_memory_drafts(
            _draft(title="Route correction", evidence="thread-0001:000002-000003"),
            stream=stream,
        )[0][0]

        prompt = skill_pass.build_crystallization_prompt([first, second])

        self.assertIn("Bound-Evidence-Excerpt:", prompt)
        self.assertIn("Bound-Records: thread-0001:000001-000004", prompt)
        self.assertIn("Bound-Records: thread-0001:000002-000003", prompt)
        self.assertIn("The route tests passed.", prompt)
        self.assertNotIn("The unrelated image tests passed.", prompt)
        self.assertNotIn("[Shared Bound Source Evidence]", prompt)

    def test_memory_draft_parser_normalizes_equivalent_reference_formatting(self):
        stream = _stream("goal", "failed route", "changed route", "verified recovery")
        raw = f"""{skill_pass.MEMORY_MARKER}
# Divergent network path recovery
Evidence:
- thread-0001:000001-000003；
- thread-0001:000003-000004

{skill_pass.MEMORY_MARKER}
# Single-record correction
Evidence: thread-0001:000002
"""

        drafts, rejected = skill_pass.parse_memory_drafts(raw, stream=stream)

        self.assertEqual(rejected, 0)
        self.assertEqual([item.record_ids for item in drafts], [
            (1, 2, 3, 4),
            (2,),
        ])
        self.assertEqual(drafts[0].evidence.count("[record"), 4)

    def test_discovery_parser_salvages_numbered_heading_and_chinese_evidence_label(self):
        stream = _stream("goal", "failed route", "changed route", "verified recovery")
        raw = """以下是定位结果：
1. ## Corrected route workflow
证据：thread-0001:000001-000004
"""

        drafts, rejected = skill_pass.parse_memory_drafts(raw, stream=stream)

        self.assertEqual(rejected, 0)
        self.assertEqual([item.title for item in drafts], ["Corrected route workflow"])
        self.assertEqual(drafts[0].record_ids, (1, 2, 3, 4))

        wrapped = """Candidate 1:
**Corrected route workflow**
- Evidence: (`thread-0001:000001-000004`)
"""
        drafts, rejected = skill_pass.parse_memory_drafts(wrapped, stream=stream)
        self.assertEqual(rejected, 0)
        self.assertEqual([item.title for item in drafts], ["Corrected route workflow"])

    def test_completed_outcome_gate_rejects_plans_but_accepts_observed_results(self):
        planned = _memory(
            procedure="The failure was observed. Next I will implement the fix.",
            verification="After the fix I will rerun the stress test.",
        )
        completed = _memory(
            procedure="The process-scoped fix was implemented.",
            verification="The stress tests passed and the service is now healthy.",
        )
        diagnostic = _memory(
            procedure="The direct and relayed paths were compared.",
            verification="The comparison showed that only the direct route was failing.",
        )
        ongoing = _memory(
            procedure="The failure was observed.",
            verification="我现在先确认服务已恢复，再定位新版本失败原因。",
        )

        self.assertFalse(skill_pass._evidence_has_observed_outcome(planned.evidence))
        self.assertTrue(skill_pass._evidence_has_observed_outcome(completed.evidence))
        self.assertTrue(skill_pass._evidence_has_observed_outcome(diagnostic.evidence))
        self.assertFalse(skill_pass._evidence_has_observed_outcome(ongoing.evidence))
        self.assertEqual(skill_pass._observed_result_options(planned.evidence), [])
        self.assertIn(
            "The stress tests passed and the service is now healthy.",
            skill_pass._observed_result_options(completed.evidence),
        )
        self.assertEqual(skill_pass._observed_result_options(ongoing.evidence), [])

    def test_observed_result_options_require_a_topic_anchor(self):
        evidence = "\n".join([
            "The database timeout was fixed and container connections succeeded.",
            "The unrelated dashboard regression tests passed.",
        ])

        options = skill_pass._observed_result_options(
            evidence,
            topic="Container database timeout",
        )

        self.assertEqual(
            options,
            ["The database timeout was fixed and container connections succeeded."],
        )
        self.assertEqual(
            skill_pass._observed_result_options(
                "The unrelated dashboard regression tests passed.",
                topic="Container database timeout",
            ),
            [],
        )
        self.assertEqual(
            skill_pass._observed_result_options(
                "只读最终回归完成，未触碰 Runtime、未编辑文件。",
                topic="Runtime resource policy",
                title="Runtime resource policy",
            ),
            [],
        )

    def test_observed_result_options_reject_generic_cjk_test_and_path_matches(self):
        evidence = "\n".join(
            (
                "Token 统计发现 cached input 被计入主总量。",
                "验证：相关 86 项测试全部通过，包含分页、取消优先和并发快照保护。",
                "节点路径修复通过回归。",
                "调整后 Token 主总量已恢复正常，并与 non-cached input 加 output 一致。",
            )
        )

        options = skill_pass._observed_result_options(
            evidence,
            topic="Token 统计口径与 cached input",
            title="Token 统计必须排除 cached input",
        )

        self.assertEqual(
            options,
            ["调整后 Token 主总量已恢复正常，并与 non-cached input 加 output 一致。"],
        )
        self.assertEqual(
            skill_pass._observed_result_options(
                "节点超时。公开路径修复已通过回归。",
                topic="局域网节点接口路径差异",
                title="节点超时常因局域网路径差异",
            ),
            [],
        )
        self.assertEqual(
            skill_pass._observed_result_options(
                "伪造 cookie 仍被 API 接受。已安全切换至 minimax-cn，真实连通测试成功。",
                topic="Session cookie 续签与 API 401；另行配置 minimax 模型。",
                title='Session cookie cannot be re-issued by "presence of any non-empty value"',
            ),
            [],
        )
        self.assertEqual(
            skill_pass._observed_result_options(
                "已完成 Dashboard Skill coverage / Curated Learning 可见性实现。",
                topic="Dashboard 日记打开失败来自 session cookie 与时区热循环。",
                title='Dashboard diary "unopenable" had two independent causes',
            ),
            [],
        )

    def test_agent_reported_policy_sentence_is_not_a_completed_outcome(self):
        sentence = "只有 Agent 自己说‘已完成/测试通过’，只能算 agent_reported。"
        self.assertEqual(skill_pass._evidence_outcome_authority(sentence), "none")
        self.assertEqual(skill_pass._skill_upgrade_outcome_authority(sentence), "none")

    def test_later_forged_cookie_correction_retires_early_renewal_success(self):
        evidence = _stream(
            "旧 cookie 的 GET 已到达受保护端点并获得新的 session，测试通过。",
            "独立安全复核发现后端无法区分真实旧会话与伪造 cookie，我会立即收回后端放行方案。",
            "安全版中真实旧标签页经公开入口恢复正常，伪造 cookie 仍被 API 以 401 拒绝。",
        )
        draft = skill_pass.MemoryDraft(
            title="失效会话恢复不能信任伪造 cookie",
            record_ids=(1, 2, 3),
            evidence=evidence,
        )

        options = skill_pass._observed_result_options(
            draft.evidence,
            topic=skill_pass._result_topic_context(draft),
            title=draft.title,
        )
        excerpt = skill_pass._crystallization_evidence_excerpt(draft, options=options)

        self.assertNotIn("获得新的 session", "\n".join(options))
        self.assertTrue(any("公开入口恢复正常" in value for value in options))
        self.assertNotIn("获得新的 session", excerpt)
        self.assertIn("收回后端放行方案", excerpt)

    def test_observed_result_options_reject_unproven_speculative_and_normative_text(self):
        cases = [
            (
                "现有测试夹具仍断言含缓存值，因此测试通过也不能证明口径正确。",
                "缓存 token 统计口径",
            ),
            ("多次检索 pass 的扫描成本可能下降。", "检索内存优化"),
            (
                "strong 必须具备失败、修复、passed report 和用户确认。",
                "Banner 遮罩必须考虑图层",
            ),
            (
                "同一任务内的尝试、失败和最终解决会形成时间线。",
                "系统代理回环",
            ),
            (
                "给 sudo 彻底清理（推荐），即可一次性修好数据库访问。",
                "数据库端口被透明代理规则拦截",
            ),
            (
                "fixed/confirmed 不能生成 Solution、authority 或 Promotion receipt。",
                "历史报告状态词 authority",
            ),
            (
                "embedding 模型和文本缓存不会减少，所以总内存不会下降八倍。",
                "向量索引压缩对总内存的影响",
            ),
        ]

        for evidence, topic in cases:
            with self.subTest(evidence=evidence):
                self.assertEqual(
                    skill_pass._observed_result_options(evidence, topic=topic, title=topic),
                    [],
                )

    def test_observed_result_options_put_latest_direct_check_first_and_drop_truncation(self):
        evidence = "\n".join([
            "The pointer regression tests passed after the first change.",
            "The real pointer behavior verification passed after the final change.",
        ])

        options = skill_pass._observed_result_options(
            evidence,
            topic="pointer behavior regression",
            title="pointer behavior regression",
        )

        self.assertEqual(
            options[0],
            "The real pointer behavior verification passed after the final change.",
        )
        self.assertEqual(
            skill_pass._observed_result_options(
                "关键差异是：此前验证通过的是 Mihomo `1.",
                topic="Mihomo interface binding",
                title="Mihomo interface binding",
            ),
            [],
        )

    def test_observed_result_topic_binding_uses_evidence_language(self):
        options = skill_pass._observed_result_options(
            "真实界面回归已通过，表盘拖动与单击均正常。",
            title="表盘拖动与单击事件恢复",
            topic="表盘拖动失败后改用原生事件层。",
        )

        self.assertEqual(options, ["真实界面回归已通过，表盘拖动与单击均正常。"])

    def test_local_compile_check_cannot_prove_a_deployment_workflow(self):
        self.assertEqual(
            skill_pass._observed_result_options(
                "Python 编译及 diff 检查通过。",
                title="Protected deployment with migrated database and old runtime",
                topic="受保护部署需重建依赖并原子切换运行版本。",
            ),
            [],
        )
        self.assertEqual(
            skill_pass._observed_result_options(
                "提交后稳态已经通过，新旧组件的 sourceCommit 一致。",
                title="事务绑定组件升级 sourceCommit",
                topic="更新事务提交后需要核对组件版本。",
            ),
            ["提交后稳态已经通过，新旧组件的 sourceCommit 一致。"],
        )
        self.assertEqual(
            skill_pass._observed_result_options(
                "已安全切换至 minimax-cn，真实连通测试成功，延迟约 2.48 秒。",
                title="详情页每小时时区 N+1 解析阻塞 event loop",
                topic="详情页延迟来自重复时区解析。",
            ),
            [],
        )

    def test_decimal_performance_result_remains_one_verified_sentence(self):
        result = (
            "本地同一批数据的服务层耗时已从约 3.9 秒降到 46–59ms，"
            "另一组从约 6.1 秒降到 63–95ms。"
        )
        options = skill_pass._observed_result_options(
            result,
            title="Dashboard timezone lookup hot loop",
            topic="详情小时统计重复读取时区，造成数秒延迟。",
        )

        self.assertEqual(options, [result])
        self.assertEqual(
            skill_pass._skill_upgrade_outcome_authority(result),
            "verified-solution",
        )

    def test_behavioral_measurement_ranks_before_generic_test_count(self):
        measured = "服务层耗时已从约 4.2 秒降到 58ms，实际详情内容保持一致。"
        generic = "46 个 Dashboard 相关测试通过。"
        options = skill_pass._observed_result_options(
            measured + "\n" + generic,
            title="Dashboard timezone lookup hot loop",
            topic="详情小时统计重复读取时区，造成数秒延迟。",
        )

        self.assertEqual(options[0], measured)
        self.assertEqual(skill_pass._skill_upgrade_outcome_authority(generic), "none")
        self.assertEqual(
            skill_pass._skill_upgrade_outcome_authority(measured),
            "verified-solution",
        )

    def test_cross_thread_later_same_topic_behavior_closes_a_discovery_draft(self):
        stream = "\n\n".join([
            _stream(
                "Dashboard diary loading took several seconds because timezone settings were read in a hot loop.",
                "The diagnosis isolated repeated timezone resolution in hourly statistics.",
            ),
            _stream(
                "小时统计把时区解析从每条事件一次降为每个日记请求一次。",
                "两天真实数据的服务层耗时已从 3.9 秒降到 46ms，回归测试通过。",
                thread="thread-0002",
            ).replace("record 000001", "record 000003").replace("record 000002", "record 000004"),
        ])
        draft = skill_pass.parse_memory_drafts(
            _draft(
                title="Dashboard timezone lookup hot loop",
                evidence="thread-0001:000001-000002",
            ),
            stream=stream,
        )[0][0]

        closed = skill_pass._close_draft_to_thread_tail(draft, stream=stream)

        self.assertIsNotNone(closed)
        self.assertIn(4, closed.record_ids)
        self.assertIn("3.9 秒降到 46ms", closed.evidence)

    def test_discovery_input_filters_only_explicit_memory_meta_records(self):
        stream = _stream(
            "The pointer drag failed on the floating panel.",
            "Next we discussed Skill Pass candidate promotion and evidence scoring.",
            "The pointer regression tests passed after the event-layer fix.",
        )

        filtered = skill_pass._strip_recursive_memory_meta_records(stream)

        self.assertIn("record 000001", filtered)
        self.assertNotIn("record 000002", filtered)
        self.assertIn("record 000003", filtered)
        self.assertIn("pointer regression tests passed", filtered)

    def test_verify_named_domain_skill_is_not_recursive_meta(self):
        candidate = skill_pass.SkillCandidate(
            name="verify-pixel-edits-on-disk",
            description="Verify pixel edits by reading the final file.",
            title="Verify pixel edits on disk",
            when_to_use="- A small compositing edit must be checked precisely.",
            procedure="1. Write the edit.\n2. Re-read the file.\n3. Compare measured pixels.",
            pitfalls="- A generated preview can differ from the saved file.",
            verification="- The measured pixels match the intended shape.",
        )
        meta = skill_pass.LearningEntry(
            1,
            "Promotion worthiness gate",
            "A Promotion candidate bypassed a gate.",
            "Bind P/S/E before Promotion.",
            "Promotion tests passed.",
        )

        self.assertFalse(skill_pass._is_recursive_memory_meta_candidate(candidate))
        self.assertTrue(skill_pass._is_recursive_memory_meta_lesson(meta))

    def test_skill_upgrade_requires_direct_verification_not_merely_completion(self):
        self.assertEqual(
            skill_pass._skill_upgrade_outcome_authority(
                "第一阶段后台资源策略已完成并落盘。"
            ),
            "none",
        )
        self.assertEqual(
            skill_pass._skill_upgrade_outcome_authority(
                "真实界面回归已通过：单击与拖动都正常。"
            ),
            "verified-solution",
        )
        self.assertEqual(
            skill_pass._skill_upgrade_outcome_authority(
                "The comparison showed that only the direct route was failing."
            ),
            "confirmed-diagnosis-only",
        )

    def test_selected_evidence_adds_same_topic_correction_without_unrelated_tail(self):
        stream = _stream(
            "The request failed.",
            "The safe-method renewal tests passed.",
            "That renewal approach was unsafe because a forged cookie was accepted.",
            "The frontend generation-bound reload fix passed all tests.",
            "An unrelated image render test passed.",
        )
        draft = skill_pass.parse_memory_drafts(
            _draft(title="Stale session renewal", evidence="thread-0001:000001-000002"),
            stream=stream,
        )[0][0]

        closed = skill_pass._close_draft_to_thread_tail(draft, stream=stream)

        self.assertIsNotNone(closed)
        self.assertEqual(closed.record_ids, (1, 2, 3, 4))
        self.assertNotIn("unrelated image render", closed.evidence)
        self.assertIn("forged cookie was accepted", closed.evidence)
        self.assertIn("frontend generation-bound reload fix passed", closed.evidence)
        options = skill_pass._observed_result_options(closed.evidence)
        self.assertNotIn("The safe-method renewal tests passed.", options)
        self.assertIn("The frontend generation-bound reload fix passed all tests.", options)
        withdrawn = _memory(
            procedure="The safe-method renewal tests passed.",
            verification="That renewal approach was unsafe because a forged cookie was accepted.",
        )
        self.assertFalse(skill_pass._evidence_has_observed_outcome(withdrawn.evidence))
        self.assertEqual(skill_pass._observed_result_options(withdrawn.evidence), [])

    def test_cross_thread_same_topic_correction_closes_old_success(self):
        stream = "\n\n".join([
            _stream("The stale session cookie request failed.", "The stale session renewal tests passed."),
            _stream(
                "That stale session cookie renewal approach was unsafe because a forged cookie was accepted.",
                "The frontend generation-bound reload fix passed all tests.",
                thread="thread-0002",
            ).replace("record 000001", "record 000003").replace("record 000002", "record 000004"),
        ])
        draft = skill_pass.parse_memory_drafts(
            _draft(
                title="Stale session cookie renewal",
                evidence="thread-0001:000001-000002",
            ),
            stream=stream,
        )[0][0]

        closed = skill_pass._close_draft_to_thread_tail(draft, stream=stream)

        self.assertIsNotNone(closed)
        self.assertEqual(closed.record_ids, (1, 2, 3, 4))
        options = skill_pass._observed_result_options(closed.evidence)
        self.assertNotIn("The stale session renewal tests passed.", options)
        self.assertIn("The frontend generation-bound reload fix passed all tests.", options)

    def test_crystallization_parser_keeps_lessons_and_requires_their_source_for_skills(self):
        raw = "<notebooklm-skill>\n" + _authored() + "\n</notebooklm-skill>\n</notebooklm-skill>"

        lessons, candidates, lesson_rejected, skill_rejected = skill_pass.parse_crystallization_output(
            raw,
            workflow_count=1,
        )

        self.assertEqual([item.source_workflow for item in lessons], [1])
        self.assertEqual([item.name for item in candidates], ["diagnose-divergent-network-paths"])
        self.assertEqual((lesson_rejected, skill_rejected), (0, 0))
        self.assertNotIn("notebooklm", candidates[0].markdown())

        wrong_source = _lesson(source=1) + "\n" + _candidate().replace(
            skill_pass.SKILL_MARKER,
            f"{skill_pass.SKILL_MARKER}\nSource-Workflow: 2",
            1,
        )
        lessons, candidates, lesson_rejected, skill_rejected = skill_pass.parse_crystallization_output(
            wrong_source,
            workflow_count=2,
        )
        self.assertEqual(len(lessons), 1)
        self.assertEqual(candidates, [])
        self.assertEqual(skill_rejected, 1)

    def test_observed_result_must_be_a_verbatim_bound_evidence_sentence(self):
        workflow = _memory()
        lessons, candidates, lesson_rejected, skill_rejected = skill_pass.parse_crystallization_output(
            _authored(),
            workflows=[workflow],
        )
        self.assertEqual((len(lessons), len(candidates)), (1, 1))
        self.assertEqual((lesson_rejected, skill_rejected), (0, 0))

        paraphrased = _authored(observed="Both routes were healthy after the correction.")
        lessons, candidates, lesson_rejected, skill_rejected = skill_pass.parse_crystallization_output(
            paraphrased,
            workflows=[workflow],
        )
        self.assertEqual((lessons, candidates), ([], []))
        self.assertEqual((lesson_rejected, skill_rejected), (1, 1))

    def test_observed_result_option_is_resolved_by_the_parser(self):
        workflow = _memory()
        authored = _authored(observed="Option 1")

        lessons, candidates, lesson_rejected, skill_rejected = skill_pass.parse_crystallization_output(
            authored,
            workflows=[workflow],
        )

        self.assertEqual((len(lessons), len(candidates)), (1, 1))
        self.assertEqual(
            lessons[0].observed_result,
            "The direct route then succeeded while the relayed route remained healthy.",
        )
        self.assertEqual((lesson_rejected, skill_rejected), (0, 0))

        invalid = _authored(observed="Option 99")
        lessons, candidates, lesson_rejected, skill_rejected = skill_pass.parse_crystallization_output(
            invalid,
            workflows=[workflow],
        )
        self.assertEqual((lessons, candidates), ([], []))
        self.assertEqual((lesson_rejected, skill_rejected), (1, 1))

    def test_lesson_must_match_its_bound_workflow_and_preserves_authored_title(self):
        workflow = _memory(title="Cached-read double counting")
        mixed = _lesson(
            title="Repair pointer dragging through the event layer",
            observed="The direct route then succeeded while the relayed route remained healthy.",
        )

        lessons, candidates, lesson_rejected, skill_rejected = skill_pass.parse_crystallization_output(
            mixed,
            workflows=[workflow],
        )

        self.assertEqual((lessons, candidates), ([], []))
        self.assertEqual((lesson_rejected, skill_rejected), (1, 0))

        workflow = _memory(title="Compare paths before changing accounting")
        matched = _lesson(title="Compare paths before changing accounting")
        lessons, candidates, lesson_rejected, skill_rejected = skill_pass.parse_crystallization_output(
            matched,
            workflows=[workflow],
        )
        self.assertEqual((len(lessons), candidates), (1, []))
        self.assertEqual(lessons[0].title, "Compare paths before changing accounting")
        self.assertEqual((lesson_rejected, skill_rejected), (0, 0))

    def test_cross_language_lesson_title_uses_source_id_and_bound_result(self):
        workflow = _memory(
            title="Dashboard timezone lookup hot loop",
            trigger="详情页按事件重复解析时区，阻塞异步请求。",
            lesson="把稳定的时区对象移到循环外。",
            procedure="每个请求只解析一次时区，再注入逐事件处理。",
            verification="服务层耗时已从 4.2 秒降到 58ms，内容保持一致。",
        )
        authored = _lesson(
            title="把时区解析移出详情页热循环",
            observed="服务层耗时已从 4.2 秒降到 58ms，内容保持一致。",
        ).replace(
            "Two routes to the same service produced different outcomes.",
            "详情页按事件重复解析时区，阻塞异步请求。",
        ).replace(
            "Compare each route independently before changing shared application behavior.",
            "每个请求只解析一次时区，再注入逐事件处理。",
        )

        lessons, candidates, lesson_rejected, skill_rejected = skill_pass.parse_crystallization_output(
            authored,
            workflows=[workflow],
        )

        self.assertEqual((len(lessons), candidates), (1, []))
        self.assertEqual(lessons[0].title, "把时区解析移出详情页热循环")
        self.assertEqual((lesson_rejected, skill_rejected), (0, 0))

    def test_diagnostic_evidence_cannot_author_an_unperformed_remediation_skill(self):
        workflow = _memory(
            procedure="The direct and relayed paths were compared.",
            verification="The comparison showed that only the direct route was failing.",
        )
        observed = "The comparison showed that only the direct route was failing."
        lesson = _lesson(observed=observed)
        remediation = _candidate(
            "use-allowlist-for-dynamic-dns-updates",
            "When bulk-updating DNS, replace exclusions with an explicit allowlist.",
        ).replace(skill_pass.SKILL_MARKER, f"{skill_pass.SKILL_MARKER}\nSource-Workflow: 1", 1)
        lessons, candidates, lesson_rejected, skill_rejected = skill_pass.parse_crystallization_output(
            lesson + "\n" + remediation,
            workflows=[workflow],
        )
        self.assertEqual(len(lessons), 1)
        self.assertEqual(candidates, [])
        self.assertEqual((lesson_rejected, skill_rejected), (0, 1))

        diagnostic = _candidate().replace(
            skill_pass.SKILL_MARKER,
            f"{skill_pass.SKILL_MARKER}\nSource-Workflow: 1",
            1,
        )
        lessons, candidates, lesson_rejected, skill_rejected = skill_pass.parse_crystallization_output(
            lesson + "\n" + diagnostic,
            workflows=[workflow],
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual((lesson_rejected, skill_rejected), (0, 0))

    def test_lesson_parser_rejects_trailing_model_explanation(self):
        polluted = _lesson() + "\n\n---\nSkills 提案：无，因为这些经历过于具体。"

        lessons, candidates, lesson_rejected, skill_rejected = skill_pass.parse_crystallization_output(
            polluted,
            workflow_count=1,
        )

        self.assertEqual((lessons, candidates), ([], []))
        self.assertEqual((lesson_rejected, skill_rejected), (1, 0))

    def test_unknown_markup_cannot_be_swallowed_into_skill_body(self):
        polluted = _authored().replace(
            "- Confirm the previously failing path succeeds independently and the healthy path remains unchanged.",
            "- Confirm the previously failing path succeeds independently and the healthy path remains unchanged.\n<foreign-skill>",
        )

        lessons, candidates, lesson_rejected, skill_rejected = skill_pass.parse_crystallization_output(
            polluted,
            workflow_count=1,
        )

        self.assertEqual(len(lessons), 1)
        self.assertEqual(candidates, [])
        self.assertEqual((lesson_rejected, skill_rejected), (0, 1))

    def test_authored_skill_tolerates_source_punctuation_and_one_outer_fence(self):
        sourced = _candidate().replace(
            skill_pass.SKILL_MARKER,
            f"{skill_pass.SKILL_MARKER}\n**Source Workflow**：Located Workflow 1",
            1,
        )
        marker, body = sourced.split("\n", 1)
        source_line, markdown = body.split("\n", 1)
        raw = _lesson() + f"\n{marker}\n{source_line}\n```markdown\n{markdown.rstrip()}\n```"

        lessons, candidates, lesson_rejected, skill_rejected = skill_pass.parse_crystallization_output(
            raw,
            workflow_count=1,
        )

        self.assertEqual((len(lessons), len(candidates)), (1, 1))
        self.assertEqual((lesson_rejected, skill_rejected), (0, 0))

    def test_parsers_unwrap_one_outer_fence_but_reject_internal_fences(self):
        stream = _stream("goal", "failed route", "changed route", "verified recovery")
        fenced_draft = f"```markdown\n{_draft()}\n```"
        fenced_candidate = f"```markdown\n{_candidate()}\n```"

        drafts, draft_rejections = skill_pass.parse_memory_drafts(fenced_draft, stream=stream)
        candidates, candidate_rejections = skill_pass.parse_skill_candidates(fenced_candidate)

        self.assertEqual([item.title for item in drafts], ["Divergent network path recovery"])
        self.assertEqual(draft_rejections, 0)
        self.assertEqual([item.name for item in candidates], ["diagnose-divergent-network-paths"])
        self.assertEqual(candidate_rejections, 0)
        self.assertEqual(skill_pass.parse_memory_drafts(
            f"```markdown\n{_draft()}\n```\nextra", stream=stream
        ), ([], 1))
        self.assertEqual(skill_pass.parse_skill_candidates(
            _candidate().replace("## Pitfalls", "## Pitfalls\n```text\nunsafe\n```")
        ), ([], 1))

    def test_parsers_ignore_plain_preface_and_arbitrary_outer_fence_language(self):
        stream = _stream("goal", "failed route", "changed route", "verified recovery")
        draft_without_marker = _draft().replace(skill_pass.MEMORY_MARKER + "\n", "", 1)
        candidate_without_marker = _candidate().replace(skill_pass.SKILL_MARKER + "\n", "", 1)

        drafts, draft_rejections = skill_pass.parse_memory_drafts(
            "Here are the located workflows.\n" + draft_without_marker,
            stream=stream,
        )
        candidates, candidate_rejections = skill_pass.parse_skill_candidates(
            "```plaintext\n" + candidate_without_marker + "\n```"
        )

        self.assertEqual([item.title for item in drafts], ["Divergent network path recovery"])
        self.assertEqual(draft_rejections, 0)
        self.assertEqual([item.name for item in candidates], ["diagnose-divergent-network-paths"])
        self.assertEqual(candidate_rejections, 0)

    def test_memory_draft_parser_still_rejects_unbound_or_excessive_references(self):
        stream = _stream("goal", "failed route", "changed route", "verified recovery")
        bad_thread = _draft(evidence="thread-0002:000001-000004")
        too_many = _draft(evidence=", ".join(
            f"thread-0001:{index:06d}-{index:06d}" for index in range(1, 14)
        ))

        self.assertEqual(skill_pass.parse_memory_drafts(bad_thread, stream=stream), ([], 1))
        self.assertEqual(skill_pass.parse_memory_drafts(too_many, stream=stream), ([], 1))

    def test_recursive_skill_memory_meta_filter_is_independent_of_llm_output(self):
        useful = _memory()
        meta = _memory(
            title="Promote Skill candidates from Learning evidence",
            trigger="A Skill generation pipeline needs candidate authority.",
            lesson="Memory evidence and Skill promotion used different rules.",
            procedure="The agent scored Skill candidates and published the accepted memory.",
            verification="The generated Skill appeared in the promotion registry.",
        )
        neutral_title_meta = _memory(
            title="Pin deterministic template bytes",
            trigger="A fixed template was used by the Skill promotion fallback.",
            lesson="The Skill receipt needed exact template authority.",
            procedure="The template was pinned by byte hash.",
            verification="The Skill promotion tests passed.",
        )

        self.assertFalse(skill_pass._is_recursive_memory_meta_draft(useful))
        self.assertTrue(skill_pass._is_recursive_memory_meta_draft(meta))
        self.assertTrue(skill_pass._is_recursive_memory_meta_draft(neutral_title_meta))
        candidate = skill_pass.parse_skill_candidates(_candidate(
            "separate-experience-extraction-from-skill-promotion",
            "When extracting lessons, separate Skill promotion into a governed pass.",
        ))[0][0]
        runtime_memory = skill_pass.parse_skill_candidates(_candidate(
            "diagnose-memory-exhaustion",
            "When a worker exhausts runtime memory, isolate retained allocations before changing limits.",
        ))[0][0]
        self.assertTrue(skill_pass._is_recursive_memory_meta_candidate(candidate))
        self.assertFalse(skill_pass._is_recursive_memory_meta_candidate(runtime_memory))

    def test_parser_accepts_multiple_standard_candidates_and_rejects_malformed_tail(self):
        raw = "preface ignored\n" + _candidate() + "\n" + _candidate(
            "recover-atomic-deployment",
            "Recover an interrupted deployment when the active release is inconsistent.",
        ) + f"\n{skill_pass.SKILL_MARKER}\n---\nname: broken"

        candidates, rejected = skill_pass.parse_skill_candidates(raw)

        self.assertEqual([item.name for item in candidates], [
            "diagnose-divergent-network-paths",
            "recover-atomic-deployment",
        ])
        self.assertEqual(rejected, 1)
        self.assertEqual(candidates[0].markdown().count("## Procedure"), 1)
        reparsed, rejections = skill_pass.parse_skill_candidates(candidates[0].markdown())
        self.assertEqual(reparsed, [candidates[0]])
        self.assertEqual(rejections, 0)
        without_marker = candidates[0].markdown().replace(skill_pass.SKILL_MARKER + "\n", "", 1)
        self.assertEqual(skill_pass.parse_skill_candidates(without_marker), ([candidates[0]], 0))

    def test_dedupe_collapses_alternate_names_for_the_same_procedure_only(self):
        first = skill_pass.parse_skill_candidates(_candidate())[0][0]
        duplicate = skill_pass.parse_skill_candidates(
            _candidate(
                "recover-divergent-network-routes",
                "Recover path-specific failures after direct and relayed results diverge.",
            ).replace(
                "# Diagnose Divergent Network Paths",
                "# Recover Divergent Network Routes",
            )
        )[0][0]
        unrelated = skill_pass.parse_skill_candidates(
            _candidate(
                "repair-broken-event-dragging",
                "Repair pointer dragging when event ownership changes across UI layers.",
            )
            .replace(
                "# Diagnose Divergent Network Paths",
                "# Repair Broken Event Dragging",
            )
            .replace(
                "two access paths to the same service produce different outcomes",
                "a draggable control stops responding after its event target changes",
            )
            .replace(
                "Reproduce each path independently under the same request conditions",
                "Reproduce the pointer sequence while logging the event owner",
            )
            .replace(
                "Compare transport reachability before changing the application",
                "Move capture to the stable parent before changing visual state",
            )
            .replace(
                "Change only the failing path and repeat the comparison",
                "Repeat press, move, and release across the control boundary",
            )
            .replace(
                "Do not treat success through a relay as proof that the direct path works",
                "Do not bind movement only to the child that can lose pointer ownership",
            )
            .replace(
                "Confirm the previously failing path succeeds independently and the healthy path remains unchanged",
                "Confirm dragging remains continuous across the boundary and click behavior is unchanged",
            )
        )[0][0]

        deduped = skill_pass.dedupe_skill_candidates([first, duplicate, unrelated])

        self.assertEqual([item.name for item in deduped], [
            "diagnose-divergent-network-paths",
            "repair-broken-event-dragging",
        ])

    def test_dedupe_collapses_cross_slice_rewording_without_merging_adjacent_work(self):
        first = skill_pass.SkillCandidate(
            name="brand-aware-readme-asset-replace",
            description="Replace old brand artwork inside raster README screenshots without changing surrounding UI pixels.",
            title="Replace Brand Artwork in README Screenshots",
            when_to_use="- Use when legacy logos must be replaced in full and thumbnail raster screenshots.",
            procedure="1. Locate every logo region.\n2. Paste the canonical mark deterministically.\n3. Update full and thumbnail variants.",
            pitfalls="- Do not regenerate the surrounding interface.",
            verification="- Confirm every screenshot uses the new mark and all non-logo pixels remain unchanged.",
        )
        reworded = skill_pass.SkillCandidate(
            name="dashboard-brand-asset-rebrand",
            description="Replace legacy brand logos in Dashboard screenshots while preserving every interface element.",
            title="Rebrand Dashboard Raster Assets",
            when_to_use="- Use when full-size and thumbnail Dashboard images still contain the legacy logo.",
            procedure="1. Find each legacy logo region.\n2. Apply the canonical logo with deterministic pixel edits.\n3. Replace both full and thumbnail screenshots.",
            pitfalls="- Never use generative editing on the surrounding UI.",
            verification="- Verify all screenshots contain the new logo while pixels outside each logo region are unchanged.",
        )
        adjacent = skill_pass.SkillCandidate(
            name="crop-brand-banner-canvas",
            description="Crop an oversized banner to a declared canvas while preserving typography.",
            title="Crop a Banner Canvas",
            when_to_use="- Use when a banner has correct artwork but the wrong canvas dimensions.",
            procedure="1. Measure the declared canvas.\n2. Crop to the visual anchor.\n3. Normalize the background.",
            pitfalls="- Do not rescale the wordmark.",
            verification="- Confirm the final dimensions and background color match the asset contract.",
        )

        self.assertTrue(skill_pass._semantic_duplicate(first, reworded))
        self.assertFalse(skill_pass._semantic_duplicate(first, adjacent))

    def test_duplicate_lessons_collapse_and_keep_source_aliases(self):
        first = skill_pass.LearningEntry(1, "Same lesson", "Situation A", "Use the verified path.", "Result A")
        duplicate = skill_pass.LearningEntry(2, "Same lesson", "Situation B", "Use the verified path.", "Result B")
        distinct = skill_pass.LearningEntry(3, "Different lesson", "Situation C", "Avoid the failed path.", "Result C")

        entries, aliases = skill_pass.dedupe_learning_entries([first, duplicate, distinct])

        self.assertEqual([entry.source_workflow for entry in entries], [1, 3])
        self.assertEqual(aliases, {1: 1, 2: 1, 3: 3})

    def test_parser_rejects_non_procedure_extra_frontmatter_and_private_values(self):
        one_step = _candidate().replace(
            "2. Compare transport reachability before changing the application.\n"
            "3. Change only the failing path and repeat the comparison.\n",
            "",
        )
        extra_frontmatter = _candidate().replace(
            "description: Diagnose",
            "version: 1.0.0\ndescription: Diagnose",
        )
        private_path = _candidate().replace("same service", "service under /Users/alice/private/")
        concrete_date = _candidate().replace(
            "same request conditions",
            "the request conditions observed on 2026-08-10",
        )
        private_addresses = _candidate().replace(
            "same service",
            "service on 192.168.50.0/24 via loopback 127.0.0.1 and public 8.8.8.8",
        )
        eight_steps = _candidate().replace(
            "3. Change only the failing path and repeat the comparison.",
            "3. Change only the failing path and repeat the comparison.\n"
            "4. Preserve the healthy path.\n"
            "5. Re-run the request.\n"
            "6. Compare the result.\n"
            "7. Record the boundary.\n"
            "8. Close the task.",
        )
        six_steps = _candidate().replace(
            "3. Change only the failing path and repeat the comparison.",
            "3. Change only the failing path and repeat the comparison.\n"
            "4. Preserve the healthy path.\n"
            "5. Re-run the request.\n"
            "6. Record the result.",
        )

        for value in (one_step, extra_frontmatter, private_path, concrete_date, six_steps, eight_steps):
            candidates, rejected = skill_pass.parse_skill_candidates(value)
            self.assertEqual(candidates, [])
            self.assertEqual(rejected, 1)

        self.assertEqual(skill_pass.parse_skill_candidates("ordinary incident summary"), ([], 1))
        generalized, rejected = skill_pass.parse_skill_candidates(private_addresses)
        self.assertEqual(rejected, 0)
        self.assertEqual(len(generalized), 1)
        rendered = generalized[0].markdown()
        self.assertIn("<private-network>", rendered)
        self.assertIn("<loopback-network>", rendered)
        self.assertIn("<network-address>", rendered)
        self.assertNotIn("192.168.50.0", rendered)
        self.assertNotIn("127.0.0.1", rendered)
        self.assertNotIn("8.8.8.8", rendered)

    def test_filtered_input_is_one_chronological_cross_source_stream_and_skips_cron(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "Actanara")
            root = paths.diary_dir / "__diary_daily" / "2026-08-10" / "_filtered"
            for source, rows in {
                "codex": [
                    {"time": "10:02", "role": "assistant", "content": "final fix", "conversationId": "private-a"},
                    {"time": "10:00", "role": "user", "content": "goal", "conversationId": "private-a"},
                ],
                "claude": [{"time": "10:01", "role": "assistant", "content": "failed attempt", "conversationId": "private-b"}],
                "cron": [{"time": "09:00", "role": "assistant", "content": "scheduled noise"}],
            }.items():
                directory = root / source
                directory.mkdir(parents=True)
                (directory / "unified_daily.jsonl").write_text(
                    "".join(json.dumps(row) + "\n" for row in rows),
                    encoding="utf-8",
                )

            entries = skill_pass.load_filtered_stream(paths, "2026-08-10")
            stream = skill_pass.render_filtered_stream(entries)

        self.assertEqual([entry.content for entry in entries], ["goal", "failed attempt", "final fix"])
        self.assertLess(stream.index("goal"), stream.index("failed attempt"))
        self.assertNotIn("source=", stream)
        self.assertNotIn("time=", stream)
        self.assertEqual(stream.count("thread=thread-0001"), 2)
        self.assertEqual(stream.count("thread=thread-0002"), 1)
        self.assertNotIn("private-a", stream)
        self.assertNotIn("private-b", stream)
        self.assertNotIn("scheduled noise", stream)

    def test_gate_uses_one_call_when_stream_fits_and_sequential_slices_when_it_does_not(self):
        short = "brief completed workflow"
        self.assertEqual(skill_pass.split_stream_by_gate(short, 2000), [short])

        long_stream = "\n\n".join(f"record {index} " + ("evidence " * 120) for index in range(12))
        chunks = skill_pass.split_stream_by_gate(long_stream, 2000)

        self.assertGreater(len(chunks), 1)
        self.assertEqual(" ".join("\n\n".join(chunks).split()), " ".join(long_stream.split()))
        self.assertTrue(all(skill_pass.token_count(skill_pass.build_skill_prompt(chunk)) <= 2000 for chunk in chunks))

    def test_gate_balances_two_slices_instead_of_leaving_a_tiny_tail(self):
        stream = "\n\n".join(f"record {index} " + ("evidence " * 120) for index in range(18))
        full_tokens = skill_pass.token_count(skill_pass.build_skill_prompt(stream))
        gate = (full_tokens * 3) // 5

        chunks = skill_pass.split_stream_by_gate(stream, gate)
        chunk_tokens = [skill_pass.token_count(skill_pass.build_skill_prompt(chunk)) for chunk in chunks]

        self.assertEqual(len(chunks), 2)
        self.assertLess(max(chunk_tokens) / min(chunk_tokens), 1.35)
        self.assertTrue(all(value <= gate for value in chunk_tokens))

    def test_skill_discovery_uses_system_gate_and_keeps_experiment_override(self):
        self.assertEqual(skill_pass.skill_discovery_gate_tokens(150000), 150000)
        self.assertEqual(skill_pass.skill_discovery_gate_tokens(80000), 80000)
        self.assertEqual(skill_pass.skill_discovery_gate_tokens(32000), 32000)
        self.assertEqual(skill_pass.skill_discovery_gate_tokens(150000, override=120000), 120000)

    def test_run_writes_independent_report_without_touching_learning_or_infrastructure(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "Actanara")
            filtered = paths.diary_dir / "__diary_daily" / "2026-08-10" / "_filtered" / "codex"
            filtered.mkdir(parents=True)
            filtered.joinpath("unified_daily.jsonl").write_text(
                json.dumps({"time": "10:00", "role": "user", "content": "The first route failed on the difficult task.", "conversationId": "task-a"}) + "\n"
                + json.dumps({"time": "10:10", "role": "assistant", "content": "The verified workflow succeeded.", "conversationId": "task-a"}) + "\n",
                encoding="utf-8",
            )
            calls = []

            def fake_llm(prompt, **kwargs):
                calls.append((prompt, kwargs))
                if kwargs["phase"] == "discovery":
                    return _draft(
                        title="Verified workflow recovery",
                        evidence="thread-0001:000001-000002",
                    )
                if kwargs["phase"] == "authoring":
                    return _lesson(
                        title="Verified workflow recovery",
                        observed="The verified workflow succeeded.",
                    )
                return _candidate().replace(
                    skill_pass.SKILL_MARKER,
                    f"{skill_pass.SKILL_MARKER}\nSource-Workflow: 1",
                    1,
                )

            with patch.object(skill_pass, "resolve_llm_provider", return_value={"pipelineGateTokens": 80000}):
                result = skill_pass.run_skill_pass(
                    "2026-08-10",
                    paths=paths,
                    llm_call=fake_llm,
                )

            report = result.report_path.read_text(encoding="utf-8")

            self.assertEqual(len(calls), 3)
            self.assertEqual(
                [call[1]["phase"] for call in calls],
                ["discovery", "authoring", "upgrade"],
            )
            self.assertEqual(result.slices, 1)
            self.assertEqual(result.discovery_gate_tokens, 80000)
            self.assertEqual(result.llm_calls, 3)
            self.assertEqual(result.discovered_drafts, 1)
            self.assertEqual([item.name for item in result.candidates], ["diagnose-divergent-network-paths"])
            self.assertIn("Skill proposals are a strict subset", report)
            self.assertIn("Total LLM calls: 3", report)
            self.assertIn("Located workflows before crystallization: 1", report)
            self.assertIn(
                "Preliminary located workflow labels (not validated):\n"
                "  - Verified workflow recovery",
                report,
            )
            self.assertIn("Discovery parser rejected: 0", report)
            self.assertIn("Authoring parser rejected: 0", report)
            self.assertIn("Valid lessons: 1", report)
            self.assertIn("Valid Skill proposals: 1", report)
            self.assertIn(skill_pass.LESSON_MARKER, report)
            self.assertIn(skill_pass.SKILL_MARKER, report)
            self.assertEqual(result.report_path.parent, paths.home / "artifacts" / "skills")
            self.assertFalse((paths.diary_dir / "infrastructure.jsonl").exists())
            self.assertFalse(any(paths.diary_dir.rglob("智慧沉淀-*.md")))
            self.assertEqual(result.report_path.stat().st_mode & 0o777, 0o600)

    def test_empty_day_writes_zero_candidate_report_without_llm(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "Actanara")

            def forbidden_llm(*_args, **_kwargs):
                raise AssertionError("LLM must not run for an empty day")

            with patch.object(skill_pass, "resolve_llm_provider", return_value={"pipelineGateTokens": 80000}):
                result = skill_pass.run_skill_pass(
                    "2026-08-10",
                    paths=paths,
                    llm_call=forbidden_llm,
                )

            report = result.report_path.read_text(encoding="utf-8")

        self.assertEqual(result.slices, 0)
        self.assertEqual(result.llm_calls, 0)
        self.assertEqual(result.discovered_drafts, 0)
        self.assertEqual(result.candidates, ())
        self.assertIn("Valid lessons: 0", report)
        self.assertIn("Valid Skill proposals: 0", report)

    def test_malformed_upgrade_gets_one_small_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "Actanara")
            filtered = paths.diary_dir / "__diary_daily" / "2026-08-10" / "_filtered" / "codex"
            filtered.mkdir(parents=True)
            filtered.joinpath("unified_daily.jsonl").write_text(
                json.dumps({
                    "time": "10:00",
                    "role": "assistant",
                    "content": "The route failed and the corrected route tests passed.",
                    "conversationId": "task-a",
                }) + "\n",
                encoding="utf-8",
            )
            phases = []

            def fake_llm(_prompt, **kwargs):
                phases.append(kwargs["phase"])
                if kwargs["phase"] == "discovery":
                    return _draft(title="Corrected route recovery", evidence="thread-0001:000001")
                if kwargs["phase"] == "authoring":
                    return _lesson(
                        title="Corrected route recovery",
                        observed="The route failed and the corrected route tests passed.",
                    )
                if phases.count("upgrade") == 1:
                    return skill_pass.SKILL_MARKER + "\nmalformed"
                return _candidate().replace(
                    skill_pass.SKILL_MARKER,
                    f"{skill_pass.SKILL_MARKER}\nSource-Workflow: 1",
                    1,
                )

            result = skill_pass.run_skill_pass(
                "2026-08-10",
                paths=paths,
                gate_tokens=2000,
                llm_call=fake_llm,
            )

        self.assertEqual(phases, ["discovery", "authoring", "upgrade", "upgrade"])
        self.assertEqual(result.llm_calls, 4)
        self.assertEqual([candidate.name for candidate in result.candidates], [
            "diagnose-divergent-network-paths",
        ])
        self.assertGreaterEqual(result.authoring_rejected_blocks, 1)

    def test_malformed_lesson_attempt_gets_one_bounded_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "Actanara")
            filtered = paths.diary_dir / "__diary_daily" / "2026-08-10" / "_filtered" / "codex"
            filtered.mkdir(parents=True)
            filtered.joinpath("unified_daily.jsonl").write_text(
                json.dumps({
                    "time": "10:00",
                    "role": "assistant",
                    "content": "The route failed and the corrected route verification tests passed.",
                    "conversationId": "task-a",
                }) + "\n",
                encoding="utf-8",
            )
            phases = []

            def fake_llm(_prompt, **kwargs):
                phases.append(kwargs["phase"])
                if kwargs["phase"] == "discovery":
                    return _draft(title="Corrected route recovery", evidence="thread-0001:000001")
                if phases.count("authoring") == 1:
                    return skill_pass.LESSON_MARKER + "\nmalformed"
                if kwargs["phase"] == "authoring":
                    return _lesson(
                        title="Corrected route recovery",
                        observed="The route failed and the corrected route verification tests passed."
                    )
                return ""

            result = skill_pass.run_skill_pass(
                "2026-08-10",
                paths=paths,
                gate_tokens=2000,
                llm_call=fake_llm,
            )

        self.assertEqual(phases, ["discovery", "authoring", "authoring", "upgrade"])
        self.assertEqual(result.llm_calls, 4)
        self.assertEqual(len(result.lessons), 1)
        self.assertEqual(result.candidates, ())
        self.assertGreaterEqual(result.lesson_rejected_blocks, 1)

    def test_multiple_slices_use_one_global_crystallization_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "Actanara")
            filtered = paths.diary_dir / "__diary_daily" / "2026-08-10" / "_filtered" / "codex"
            filtered.mkdir(parents=True)
            filtered.joinpath("unified_daily.jsonl").write_text(
                "".join(
                    json.dumps({
                        "time": f"10:{index:02d}",
                        "role": "assistant",
                        "content": f"The workflow failed before correction. completed record {index}. The record verification tests passed. " + ("evidence " * 100),
                        "conversationId": "task-a",
                    }) + "\n"
                    for index in range(18)
                ),
                encoding="utf-8",
            )
            calls = []

            def fake_llm(prompt, **kwargs):
                calls.append((prompt, kwargs))
                if kwargs["phase"] == "discovery":
                    headers = list(skill_pass._RECORD_HEADER_RE.finditer(kwargs["stream"]))
                    return _draft(
                        title=f"Completed record workflow from slice {kwargs['index']}",
                        evidence=(
                            f"{headers[0].group('thread')}:{headers[0].group('record')}-"
                            f"{headers[-1].group('record')}"
                        ),
                    )
                self.assertIn("Bound-Evidence-Excerpt:", prompt)
                if kwargs["phase"] == "authoring":
                    return _lesson(
                        title="Completed record workflow from slice 1",
                        observed="The record verification tests passed.",
                    )
                return _candidate().replace(
                    skill_pass.SKILL_MARKER,
                    f"{skill_pass.SKILL_MARKER}\nSource-Workflow: 1",
                    1,
                )

            with patch.object(skill_pass, "resolve_llm_provider", return_value={"pipelineGateTokens": 150000}):
                result = skill_pass.run_skill_pass(
                    "2026-08-10",
                    paths=paths,
                    gate_tokens=2000,
                    llm_call=fake_llm,
                )

        self.assertGreater(result.slices, 1)
        self.assertEqual(result.llm_calls, result.slices + 2)
        self.assertEqual(result.discovered_drafts, result.slices)
        self.assertEqual([call[1]["phase"] for call in calls[:-2]], ["discovery"] * result.slices)
        self.assertEqual([call[1]["phase"] for call in calls[-2:]], ["authoring", "upgrade"])

    def test_no_discovered_drafts_skips_crystallization(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "Actanara")
            filtered = paths.diary_dir / "__diary_daily" / "2026-08-10" / "_filtered" / "codex"
            filtered.mkdir(parents=True)
            filtered.joinpath("unified_daily.jsonl").write_text(
                json.dumps({"time": "10:00", "role": "assistant", "content": "Routine work completed."}) + "\n",
                encoding="utf-8",
            )
            phases = []

            def fake_llm(_prompt, **kwargs):
                phases.append(kwargs["phase"])
                return ""

            result = skill_pass.run_skill_pass(
                "2026-08-10",
                paths=paths,
                gate_tokens=2000,
                llm_call=fake_llm,
            )

        self.assertEqual(phases, ["discovery"])
        self.assertEqual(result.llm_calls, 1)
        self.assertEqual(result.discovered_drafts, 0)
        self.assertEqual(result.candidates, ())

    def test_future_only_draft_is_withheld_before_authoring(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "Actanara")
            filtered = paths.diary_dir / "__diary_daily" / "2026-08-10" / "_filtered" / "codex"
            filtered.mkdir(parents=True)
            filtered.joinpath("unified_daily.jsonl").write_text(
                json.dumps({"time": "10:00", "role": "assistant", "content": "The failure was observed.", "conversationId": "task-a"}) + "\n"
                + json.dumps({"time": "10:01", "role": "assistant", "content": "Next I will implement the fix and verify it.", "conversationId": "task-a"}) + "\n",
                encoding="utf-8",
            )
            phases = []

            def fake_llm(_prompt, **kwargs):
                phases.append(kwargs["phase"])
                if kwargs["phase"] == "discovery":
                    return _draft(evidence="thread-0001:000001-000002")
                raise AssertionError("future-only evidence must not reach authoring")

            result = skill_pass.run_skill_pass(
                "2026-08-10",
                paths=paths,
                gate_tokens=2000,
                llm_call=fake_llm,
            )

        self.assertEqual(phases, ["discovery"])
        self.assertEqual(result.unverified_drafts, 1)
        self.assertEqual(result.lessons, ())
        self.assertEqual(result.candidates, ())

    def test_verified_lesson_does_not_need_a_skill_proposal(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "Actanara")
            filtered = paths.diary_dir / "__diary_daily" / "2026-08-10" / "_filtered" / "codex"
            filtered.mkdir(parents=True)
            filtered.joinpath("unified_daily.jsonl").write_text(
                json.dumps({"time": "10:00", "role": "assistant", "content": "The comparison confirmed the boundary.", "conversationId": "task-a"}) + "\n",
                encoding="utf-8",
            )

            def fake_llm(_prompt, **kwargs):
                if kwargs["phase"] == "discovery":
                    return _draft(
                        title="Confirmed comparison boundary",
                        evidence="thread-0001:000001",
                    )
                return _lesson(
                    title="Confirmed comparison boundary",
                    observed="The comparison confirmed the boundary.",
                )

            result = skill_pass.run_skill_pass(
                "2026-08-10",
                paths=paths,
                gate_tokens=2000,
                llm_call=fake_llm,
            )
            report = result.report_path.read_text(encoding="utf-8")

        self.assertEqual(len(result.lessons), 1)
        self.assertEqual(result.candidates, ())
        self.assertIn("## Experience and Lessons", report)
        self.assertIn("## Skill Upgrade Proposals\n\n(none)", report)

    def test_run_reports_the_stage_that_rejected_model_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "Actanara")
            filtered = paths.diary_dir / "__diary_daily" / "2026-08-10" / "_filtered" / "codex"
            filtered.mkdir(parents=True)
            filtered.joinpath("unified_daily.jsonl").write_text(
                json.dumps({"time": "10:00", "role": "user", "content": "A difficult workflow failed.", "conversationId": "task-a"}) + "\n"
                + json.dumps({"time": "10:01", "role": "assistant", "content": "A different method succeeded.", "conversationId": "task-a"}) + "\n",
                encoding="utf-8",
            )

            def fake_llm(_prompt, **kwargs):
                if kwargs["phase"] == "discovery":
                    return _draft(
                        title="Different method recovery",
                        evidence="thread-0001:000001-000002",
                    )
                return "malformed authoring output"

            result = skill_pass.run_skill_pass(
                "2026-08-10",
                paths=paths,
                gate_tokens=2000,
                llm_call=fake_llm,
            )

        self.assertEqual(result.discovery_rejected_blocks, 0)
        self.assertEqual(result.recursive_meta_drafts, 0)
        self.assertEqual(result.recursive_meta_candidates, 0)
        self.assertEqual(result.authoring_rejected_blocks, 1)
        self.assertEqual(result.deduplicated_candidates, 0)
        self.assertEqual(result.rejected_blocks, 1)

    def test_run_filters_memory_meta_that_only_appears_in_final_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "Actanara")
            filtered = paths.diary_dir / "__diary_daily" / "2026-08-10" / "_filtered" / "codex"
            filtered.mkdir(parents=True)
            filtered.joinpath("unified_daily.jsonl").write_text(
                json.dumps({"time": "10:00", "role": "user", "content": "The workflow repeatedly lost results.", "conversationId": "task-a"}) + "\n"
                + json.dumps({"time": "10:01", "role": "assistant", "content": "A separated process preserved them and the regression tests passed.", "conversationId": "task-a"}) + "\n",
                encoding="utf-8",
            )

            def fake_llm(_prompt, **kwargs):
                if kwargs["phase"] == "discovery":
                    return _draft(
                        title="Preserved results with a separated process",
                        evidence="thread-0001:000001-000002",
                    )
                if kwargs["phase"] == "authoring":
                    return _lesson(
                        title="Preserved results with a separated process",
                        observed="A separated process preserved them and the regression tests passed.",
                    )
                return _candidate(
                    "separate-experience-extraction-from-skill-promotion",
                    "When extracting lessons, separate Skill promotion into a governed pass.",
                ).replace(
                    skill_pass.SKILL_MARKER,
                    f"{skill_pass.SKILL_MARKER}\nSource-Workflow: 1",
                    1,
                )

            result = skill_pass.run_skill_pass(
                "2026-08-10",
                paths=paths,
                gate_tokens=2000,
                llm_call=fake_llm,
            )
            report = result.report_path.read_text(encoding="utf-8")

        self.assertEqual(result.candidates, ())
        self.assertEqual(result.recursive_meta_drafts, 0)
        self.assertEqual(result.recursive_meta_candidates, 1)
        self.assertEqual(result.rejected_blocks, 1)
        self.assertIn("Recursive Skill/Memory final candidates filtered: 1", report)

    def test_skill_subprocess_watchdog_lesson_is_recursive_meta(self):
        entry = skill_pass.LearningEntry(
            source_workflow=1,
            title="Skill subprocess 900s watchdog releases the worker",
            situation="The Skill subprocess held the worker after a stage timeout.",
            lesson="Treat the watchdog as a stage timer and close the Skill sidecar.",
            observed_result="The worker was released after the timeout.",
        )

        self.assertTrue(skill_pass._is_recursive_memory_meta_lesson(entry))

    def test_later_unrelated_skill_meta_does_not_reclassify_core_workflow(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "Actanara")
            filtered = paths.diary_dir / "__diary_daily" / "2026-08-10" / "_filtered" / "codex"
            filtered.mkdir(parents=True)
            filtered.joinpath("unified_daily.jsonl").write_text(
                json.dumps({"time": "10:00", "role": "assistant", "content": "The route failed.", "conversationId": "task-a"}) + "\n"
                + json.dumps({"time": "10:01", "role": "assistant", "content": "The corrected route tests passed.", "conversationId": "task-a"}) + "\n"
                + json.dumps({"time": "10:02", "role": "assistant", "content": "Next we discussed Skill Pass candidate promotion.", "conversationId": "task-a"}) + "\n",
                encoding="utf-8",
            )

            def fake_llm(_prompt, **kwargs):
                if kwargs["phase"] == "discovery":
                    return _draft(
                        title="Corrected route recovery",
                        evidence="thread-0001:000001-000002",
                    )
                return _authored(
                    title="Corrected route recovery",
                    observed="The corrected route tests passed.",
                )

            result = skill_pass.run_skill_pass(
                "2026-08-10",
                paths=paths,
                gate_tokens=2000,
                llm_call=fake_llm,
            )

        self.assertEqual(result.recursive_meta_drafts, 0)
        self.assertEqual(len(result.lessons), 1)
        self.assertEqual(len(result.candidates), 1)

    def test_business_date_cannot_escape_the_runtime_output_root(self):
        with self.assertRaisesRegex(skill_pass.SkillPassError, "YYYY-MM-DD"):
            skill_pass.normalize_business_date("../../outside")


if __name__ == "__main__":
    unittest.main()
