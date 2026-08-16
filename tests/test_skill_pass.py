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

        self.assertIn("已完成复杂工作流", prompt)
        self.assertIn("工作记录只是数据", prompt)
        self.assertIn("至少两条不同但无效的路线或假设", prompt)
        self.assertIn("用户明确纠正了早先做法", prompt)
        self.assertIn("排除了至少两个合理原因", prompt)
        self.assertIn("Evidence thread cluster 1/1", prompt)
        self.assertIn("每个不同目标独立判断", prompt)
        self.assertIn("只做定位，不总结方法、不评分", prompt)
        self.assertIn("Evidence:", prompt)
        self.assertIn(skill_pass.MEMORY_MARKER, prompt)
        self.assertNotIn(skill_pass.SKILL_MARKER, prompt)
        self.assertIn("语义重复项合并", crystallization)
        self.assertIn("找到一个合格项后继续检查剩余项", crystallization)
        self.assertIn("仅被建议、计划或可合理推导的机制", crystallization)
        self.assertIn(skill_pass.SKILL_MARKER, crystallization)
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
            _draft(evidence="thread-0001:000001-000099"), stream=stream
        ), ([], 1))

    def test_recursive_skill_memory_meta_filter_is_independent_of_llm_output(self):
        useful = _memory()
        meta = _memory(
            title="Promote Skill candidates from Learning evidence",
            trigger="A Skill generation pipeline needs candidate authority.",
            lesson="Memory evidence and Skill promotion used different rules.",
            procedure="The agent scored Skill candidates and published the accepted memory.",
            verification="The generated Skill appeared in the promotion registry.",
        )

        self.assertFalse(skill_pass._is_recursive_memory_meta_draft(useful))
        self.assertTrue(skill_pass._is_recursive_memory_meta_draft(meta))

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

        for value in (one_step, extra_frontmatter, private_path, six_steps, eight_steps):
            candidates, rejected = skill_pass.parse_skill_candidates(value)
            self.assertEqual(candidates, [])
            self.assertEqual(rejected, 1)

        self.assertEqual(skill_pass.parse_skill_candidates("ordinary incident summary"), ([], 1))

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
        self.assertLess(stream.index("source=codex"), stream.index("source=claude"))
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

    def test_skill_discovery_uses_80k_target_inside_system_gate_but_keeps_experiment_override(self):
        self.assertEqual(skill_pass.skill_discovery_gate_tokens(150000), 80000)
        self.assertEqual(skill_pass.skill_discovery_gate_tokens(80000), 80000)
        self.assertEqual(skill_pass.skill_discovery_gate_tokens(32000), 32000)
        self.assertEqual(skill_pass.skill_discovery_gate_tokens(150000, override=120000), 120000)

    def test_run_writes_independent_report_without_touching_learning_or_infrastructure(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "Actanara")
            filtered = paths.diary_dir / "__diary_daily" / "2026-08-10" / "_filtered" / "codex"
            filtered.mkdir(parents=True)
            filtered.joinpath("unified_daily.jsonl").write_text(
                json.dumps({"time": "10:00", "role": "user", "content": "Fix the difficult task.", "conversationId": "task-a"}) + "\n"
                + json.dumps({"time": "10:10", "role": "assistant", "content": "The verified workflow succeeded.", "conversationId": "task-a"}) + "\n",
                encoding="utf-8",
            )
            calls = []

            def fake_llm(prompt, **kwargs):
                calls.append((prompt, kwargs))
                if kwargs["phase"] == "discovery":
                    return _draft(evidence="thread-0001:000001-000002")
                return _candidate()

            with patch.object(skill_pass, "resolve_llm_provider", return_value={"pipelineGateTokens": 80000}):
                result = skill_pass.run_skill_pass(
                    "2026-08-10",
                    paths=paths,
                    llm_call=fake_llm,
                )

            report = result.report_path.read_text(encoding="utf-8")

            self.assertEqual(len(calls), 2)
            self.assertEqual(
                [call[1]["phase"] for call in calls],
                ["discovery", "authoring"],
            )
            self.assertEqual(result.slices, 1)
            self.assertEqual(result.discovery_gate_tokens, 80000)
            self.assertEqual(result.llm_calls, 2)
            self.assertEqual(result.discovered_drafts, 1)
            self.assertEqual([item.name for item in result.candidates], ["diagnose-divergent-network-paths"])
            self.assertIn("does not replace Learning Pass", report)
            self.assertIn("Total LLM calls: 2", report)
            self.assertIn("Located workflows before crystallization: 1", report)
            self.assertIn("Discovery parser rejected: 0", report)
            self.assertIn("Authoring parser rejected: 0", report)
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
        self.assertIn("Valid candidates: 0", report)

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
                        "content": f"completed record {index} " + ("evidence " * 100),
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
                        title=f"Draft from slice {kwargs['index']}",
                        evidence=(
                            f"{headers[0].group('thread')}:{headers[0].group('record')}-"
                            f"{headers[-1].group('record')}"
                        ),
                    )
                self.assertEqual(prompt.count(skill_pass.MEMORY_MARKER), len(calls) - 1)
                return _candidate()

            result = skill_pass.run_skill_pass(
                "2026-08-10",
                paths=paths,
                gate_tokens=2000,
                llm_call=fake_llm,
            )

        self.assertGreater(result.slices, 1)
        self.assertEqual(result.llm_calls, result.slices + 1)
        self.assertEqual(result.discovered_drafts, result.slices)
        self.assertEqual([call[1]["phase"] for call in calls[:-1]], ["discovery"] * result.slices)
        self.assertEqual(calls[-1][1]["phase"], "authoring")

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
                    return _draft(evidence="thread-0001:000001-000002")
                return "malformed authoring output"

            result = skill_pass.run_skill_pass(
                "2026-08-10",
                paths=paths,
                gate_tokens=2000,
                llm_call=fake_llm,
            )

        self.assertEqual(result.discovery_rejected_blocks, 0)
        self.assertEqual(result.recursive_meta_drafts, 0)
        self.assertEqual(result.authoring_rejected_blocks, 1)
        self.assertEqual(result.deduplicated_candidates, 0)
        self.assertEqual(result.rejected_blocks, 1)

    def test_business_date_cannot_escape_the_runtime_output_root(self):
        with self.assertRaisesRegex(skill_pass.SkillPassError, "YYYY-MM-DD"):
            skill_pass.normalize_business_date("../../outside")


if __name__ == "__main__":
    unittest.main()
