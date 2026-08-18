#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Experimental three-stage Lesson and Skill distillation path.

Discovery maximizes causal recall, adjudication compares and scores candidate
families, and crystallization writes only the accepted assets. Local code
validates structure, evidence locators, privacy, and the model-authored
decision contract; it does not infer semantic value from transcript text.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Iterable

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))

from data_foundation.llm_execution import execute_llm_message
from data_foundation.paths import RuntimePaths, load_paths
from data_foundation.settings import resolve_llm_provider
from data_foundation.time import business_today
from diary_generator import skill_pass_single_call as single
from diary_generator import skill_pass_two_call as two


PROMPT_VERSION = "v22"
REVIEW_MARKER = "<!-- actanara-review -->"
DEFAULT_MAX_OUTPUT_TOKENS = 16384
THINKING_MODE = os.getenv("SKILL_LLM_THINKING_MODE", "medium").strip().lower()
_REVIEW_MARKER_RE = re.compile(rf"(?m)^{re.escape(REVIEW_MARKER)}[ \t]*$")
_REVIEW_CARD_START_RE = re.compile(
    r"(?m)^# [^\n]+[ \t]*\nCandidate-ID[ \t]*:[ \t]*candidate-\d{3}[ \t]*$"
)
_DECISION_RE = re.compile(r"(?mi)^Decision[ \t]*:[ \t]*(skill|lesson|drop)[ \t]*$")
_REASON_RE = re.compile(r"(?mi)^Reason[ \t]*:[ \t]*(?P<reason>[^\n]+?)[ \t]*$")
_REVIEW_ID_RE = re.compile(r"(?mi)^Review-ID[ \t]*:[ \t]*(?P<value>review-\d{3})[ \t]*$")
_CANDIDATE_ID_RE = re.compile(
    r"(?mi)^Candidate-ID[ \t]*:[ \t]*(?P<value>candidate-\d{3})[ \t]*$"
)
_PROCEDURE_READINESS_RE = re.compile(
    r"(?mi)^Procedure Readiness[ \t]*:[ \t]*(?P<value>[0-5])/5[ \t]*$"
)
_EVIDENCE_RECORDS_RE = re.compile(
    r"(?mi)^Evidence-Records[ \t]*:[ \t]*(?P<value>[^\n]+?)[ \t]*$"
)
_ACTION_RECORDS_RE = re.compile(
    r"(?mi)^Action-Records[ \t]*:[ \t]*(?P<value>[^\n]+?)[ \t]*$"
)
_VERIFICATION_RECORDS_RE = re.compile(
    r"(?mi)^Verification-Records[ \t]*:[ \t]*(?P<value>[^\n]+?)[ \t]*$"
)
_SCOPE_RE = re.compile(
    r"(?mi)^Scope[ \t]*:[ \t]*(?P<value>domain-work|distillation-meta)[ \t]*$"
)
_ACTION_STATE_RE = re.compile(
    r"(?mi)^Action-State[ \t]*:[ \t]*(?P<value>executed|diagnostic|planned|none)[ \t]*$"
)
_VERIFICATION_STATE_RE = re.compile(
    r"(?mi)^Verification-State[ \t]*:[ \t]*(?P<value>user-confirmed|behavior-observed|agent-reported|none)[ \t]*$"
)
_NOVELTY_RE = re.compile(
    r"(?mi)^Novelty[ \t]*:[ \t]*(?P<value>nontrivial|routine)[ \t]*$"
)
REVIEW_SYSTEM = """你是 Agent 程序性资产的独立裁决者。你不写最终 Skill，也不补充候选。你只根据候选引用的原始记录，按全局时序比较同一目标下的竞争解释，给每个语义家族作 Skill、Lesson 或 Drop 决策并评分。

候选标题和 Why 都不是事实。后来的真实结果、用户纠正和行为验证优先于早期猜测。不得把被后续证据推翻的临时 workaround 保留为独立资产。

最高优先级禁令：Skill、Memory、Learning、知识蒸馏、候选评分、证据绑定、Promotion、发布或 Agent 自我进化机制自身只能 Decision=drop，不能判 Lesson 或 Skill。即使候选涉及 SQLite、加密、并发、LLM、回执或大量测试，只要其真实目的在改造上述蒸馏/晋升机制，Scope 仍必须是 distillation-meta。反之，数据库、Runtime、Dashboard、后台任务、安装升级、性能、安全或网络工程不因属于 Actanara 或外部产品而成为 distillation-meta。"""


REVIEW_PROMPT = """下面是高召回发现阶段提出的候选 dossier。每个 dossier 已把一张候选线索与它唯一允许使用的原始记录放在一起。候选可能重复、错误、过窄或已被后续证据推翻；原始记录才是事实。

<candidate_dossiers>
{candidates}
</candidate_dossiers>

本轮共有 {candidate_count} 张候选卡，必须逐一裁决且只裁决这些 ID：{candidate_ids}。输出前先自行核对每个 ID 恰好出现一次；不能因为重复、证据很多、结论是 Drop 或输出较长而省略。

## 裁决流程

1. 先按真实用户目标与故障机制理解候选，不按产品名或关键词判断价值。每个 Candidate-ID 必须单独输出一张裁决卡；若它与更完整候选重复，就 Decision=drop 并在 Reason 指明重复关系，不得合并后省略。若一张候选把两个可独立触发、独立验证的机制混在一起，它不能判 Skill：被其它精确候选覆盖就 Drop，否则最多 Lesson。
2. 在每个家族中按 record 的全局顺序重建：目标、困难、尝试、纠正、决定性转折、最终观察结果。后来的验证结论覆盖早期假设；早期错误路线只可成为最终 Skill 的 Pitfall，不能与最终根因并列成 Skill。
3. 只依据已发生证据判断：计划中的修复、建议、未执行设计和 Agent 自述不能变成 Procedure。已执行的诊断流程若形成稳定可操作结论，可以是 Skill。
4. 比较同族候选后只保留最小且最终的因果单元。若两个候选具有不同 Trigger、不同机制和独立 Verification，才可分别保留。
5. 消费产品真伪、配对、固件和普通功能咨询，普通发布操作，单字段/单路径查错，一次性配置，以及没有非平凡转折与行为验证的通用架构原则均 Drop。
6. Skill 必须同时具备：可识别 Trigger、证据中实际执行的非平凡有序动作、真实失败路线或关键边界、直接行为 Verification。Lesson 只保存已验证但不足以形成完整程序的行动原则。其余 Drop。

## 评分与决策

三个评分均为 0–5：

- Evidence Closure：目标、行动、结果是否同一因果链并有行为证据。
- Learning Value：相对一个能够查文档、读错误和编写常规代码的通用 Agent，是否新增了难以现场推导、并会显著改变行动路线的知识。代码量、测试数量、发布成功、实现跨度和讨论篇幅都不能提高该分数。单一路径迁移、正则同步、固定容量值和普通契约更新通常不超过 3 分。
- Transferability：移除事故身份后，能否在同类触发再次出现时被另一个 Agent 识别并执行。它不等于适用面宽；平台特定、协议特定或工具特定的程序，只要面对的是稳定问题类别而非单次对象，也可以是 4–5 分。

另单独给出 Procedure Readiness 0–5：是否已有清晰 Trigger、证据中实际执行的有序动作、真实 Pitfall 和直接行为 Verification。宽泛原则、一次性改动、只有建议或只有结果通常不超过 3 分。

Decision=skill 要求三个评分与 Procedure Readiness 全部至少 4/5。Decision=lesson 要求 Evidence Closure 与 Learning Value 至少 4/5，但 Transferability 或 Procedure Readiness 至少一项不足 4/5。Learning Value 不足 4/5 必须 Drop。没有数量目标。

## AI 资产门槛校准

“问题真实发生、代码已经修改、测试已经通过”只提高 Evidence Closure，不自动提高 Learning Value、Transferability 或 Procedure Readiness。逐卡执行以下资产测试：

1. **标题删除测试**：若未来 Agent 看到候选标题后，剩余做法只是照抄一个字段、路径、开关、版本事实、标准 API、普通原子写、普通端口转发、普通分支拆分或 CI 步骤排序，这不是 Skill；有长期提醒价值时是 Lesson，否则 Drop。
2. **程序而非结论**：Skill 必须保留一个可再次执行的决策程序，包含如何识别 Trigger、如何通过观察或对照选择路径、如何避开真实错误路线、如何用行为结果结束。单句“X 不支持 Y”“A 要先于 B”“把 C 换成 D”即使正确，也通常没有 Procedure Readiness。
3. **非平凡信息增益**：Learning Value 4–5 要求记录证明了通用 Agent 难以从报错、官方文档或常规工程习惯立即推出的机制、时序、交互边界或诊断顺序。事故影响大、代码跨度大、测试很多、讨论很久都不是信息增益。
4. **补丁形状测试**：若资产只适用于重做这一次代码补丁，而不是处理再次出现的一类可观察问题，Transferability 不得达到 4。稳定的平台或协议诊断可以迁移；某仓库的分支政策、产物权威偏好、迁移编号和固定路径通常不能。
5. **完整程序测试**：Procedure Readiness 4–5 不要求固定步骤数量，但必须存在不止“实施最终改动”这一件事；至少还要有证据支持的定位、选择、保护不变量或验证逻辑。只有静态认识时降为 Lesson，不要把它扩写成虚假 SOP。
6. **包含关系测试**：若候选只是另一完整程序中的一次测量、前置条件、保护步骤或验证步骤，不得再晋升为第二个 Skill。只有它有独立 Trigger、不同决策机制和独立完成标准时才可拆分；否则保留信息更完整的主程序，支持候选 Drop。

用下列形状校准，而不是机械匹配主题：

- 可判 Skill：通过单流/并发和端到端对照区分高 RTT 单连接上限与线路总容量；在位图局部修改中保护未授权像素并以最终落盘文件验证；用文件身份、游标与同事务提交构造可崩溃恢复的增量摄取；把事务 owner 身份绑定到候选服务健康响应以区分正常持锁与恢复冲突。
- 通常只能 Lesson/Drop：某操作系统版本不支持一个功能；设置改存某个默认目录；用标准 SSH 本地转发访问回环端口；把一个混杂分支按职责拆开；Release 与标签调整先后；指定本地或云端产物谁覆盖谁；某模型上下文数字或单个配置字段应写多少。
- 消费产品真伪、保修、配对、购买建议和普通功能判断不是本系统要沉淀的 Agent 程序性资产；观察固件、序列号、商家话术或重置行为仍属于消费判断，不得判 Skill。只有实际开发或调试软件、协议、固件、硬件的工程程序才进入本任务。
- “某次前向兼容迁移只能 CREATE INDEX、不能 ALTER TABLE”这类特定旧版本限制最多是 Lesson；不得泛化成所有增量迁移的绝对规则。只有包含事务身份、切换条件、回滚/修复路径和行为验证的完整升级协议才可能是 Skill。

不要为了保留发现阶段的劳动而给 4 分。0–3 分是普通已完成工程的正常结果；也不要为了显得严格而压低真正满足完整程序的候选。没有数量目标，判断每张卡的资产形状。

判断 Skill 时检查程序闭环，而不是主题大小：诊断流程、事务边界、幂等恢复、交互回归或数据一致性程序都可以是 Skill。相反，宽泛架构愿景、固定容量值、某次发布脚本和单路径配置即使讨论很长也不是 Skill。

严禁按“同一项目、同一治理体系、同一后台系统”合并候选。一张 Skill 卡只能有一个可观察 Trigger、一个决定性机制和一个直接 Verification。按需卸载、进程优先级、队列容量、孤儿账本收敛、业务幂等分别是不同机制；若各自证据不足就分别 Lesson/Drop，不得拼成“后台治理大全”。至少执行一次的队列不丢，与业务副作用不会重复，不是同一个保证。

在评分前执行“通用 Agent 基线测试”：如果未来 Agent 看到报错或官方文档后，十分钟内就能自然得到同一动作（例如查找二进制路径、同步两个版本正则、给队列设置某个容量），该项 Drop；不要把它泛化成“先检查配置/契约”来制造虚假的可迁移性。若删掉项目专名后只剩常识性口号，也必须 Drop。

基线测试要评估完整程序，而不是候选的一句话口号。一个原则看起来熟悉，不代表证据中形成的执行程序没有价值：若记录包含反复失败或用户纠正、必须同时维持的多个不变量、非直觉的有序动作，以及针对最终产物而非输入参数的行为验证，应按完整程序评分。尤其是“只改局部且保持其余内容不变”这类任务，只有在证据确实给出保护不变量、避免生成式重绘、从干净基线重建和验证最终可见结果的方法时，才可能成为 Skill；一次性坐标和视觉偏好仍应删除。

诊断程序不因“没有直接修复上游系统”而降级。若它通过受控对照改变一个变量，并在请求与响应两端同时观察，依次排除了协议、端口、本机配置等早期解释，最后得到可重复验证且会改变后续行动的故障方向，那么它可以是 Skill。不得把这种证据链缩写成“看抓包就会知道”的常识，也不得把某个服务商、地址或客服流程写进可迁移边界。

反过来，固定容量、超时、文件大小或保留槽位的具体数字本身不是程序性资产。只有证据形成了可迁移的准入控制机制——区分工作类别、定义溢出行为、保护关键工作并验证不会饥饿——才可按完整程序评分；仅给某个队列补几个上限，即使测试通过，也只能 Lesson 或 Drop。

Evidence Closure 只表示这次事情确实闭合，不代表它值得沉淀。一个一次性发布修复可以是 5/5 Closure、但只能是 1–3/5 Learning Value。Reason 中不得用“测试很多、改动很大、最终发布成功”证明 Learning Value 或 Transferability。

若一个局部 workaround 暂时跑通，但后续对照或数据包证据表明真正机制是更上层的路径、状态或时序差异，不得把 workaround 写成最终资产；应保留证据真正支持的诊断程序，或者 Drop。业务动作在崩溃重放时重复、而队列本身没有丢失，属于可复用的业务幂等问题，不能仅因实现横跨多个组件而视为普通架构描述。

每个 Candidate-ID 恰好输出一张紧凑裁决卡；Drop 也要输出，不能省略。三组 Records 只逐字复制当前 dossier 的 allowed_work_records 方括号里的六位全局 record 编号，逐个列出、不得写 thread 或范围：

- Evidence-Records：闭合本次决策所需的最小记录集合，必须非空，且每个编号都必须逐字取自当前 dossier 的 Allowed-Records；即使其它 dossier 中存在该编号，也不得借用。当前 dossier 的 `<allowed_work_records>` 是本卡唯一事实边界。
- Action-Records：明确陈述动作已经执行的记录；没有就写 `none`。
- Verification-Records：明确陈述执行后的行为结果、稳定诊断结果或用户确认的记录；没有就写 `none`。

Decision=skill 时 Action-Records 与 Verification-Records 都不得为 `none`。计划、建议、准备执行、正在验证、测试尚未返回，不属于已执行动作或 Verification。Reason 严格只写一句简洁裁决理由，不写 Procedure、事故复述或测试清单；这样必须为所有候选留下输出空间。输出中不得复述真实 IP、主机名、用户目录、密钥、token、内部路径或凭证值，统一写成角色化描述。

在 Decision 前先填写三个独立判断，不得用分数代替：

- Scope=`distillation-meta`：候选描述 Skill/Memory/Learning/证据提取/候选评分/Promotion/发布/Agent 进化机制自身；此时 Decision 必须是 drop。其它实际工程为 `domain-work`。
- Action-State=`executed`：修复或工作流确已执行；`diagnostic`：受控诊断确已执行并形成稳定边界；`planned`：只有计划/建议/准备；`none`：没有行动。只有 executed/diagnostic 才能有 Action-Records。
- Verification-State=`user-confirmed`、`behavior-observed` 或 `agent-reported`：分别表示用户确认、直接行为观察、Agent 对已完成结果的报告；计划中的测试或“接下来验证”一律是 none。Verification-State 非 none 才能有 Verification-Records。
- Novelty=`nontrivial`：证据至少包含一种会改变未来行动路线的非显然来源，例如反复失败后的转折、用户纠正、受控对照推翻直觉、隐藏的运行时语义、必须共同保护的多个不变量。`routine`：查文档、套公式、同步配置、普通测试或一次性发布即可自然得到。实现跨度、测试数量和事故篇幅不能把 routine 提升为 nontrivial。

Decision=skill 还要求 Scope=domain-work、Action-State 为 executed/diagnostic、Verification-State 非 none、Novelty=nontrivial，且三个评分和 Procedure Readiness 都至少 4/5。任何一项不满足都只能 Lesson 或 Drop；Novelty=routine 时 Learning Value 必须低于 4 且 Decision=drop。

特别校准：修改 Skill Pass 的切片、P/S/E、grounding、证据门禁或 fallback 属 distillation-meta，不因实现复杂而变成 domain-work。为某个 token gate 写 `min/max` 公式及边界测试属于 routine，不因公式有三个边界就成为 Skill。相反，热循环中的隐藏 N+1、同进程文件描述符破坏数据库锁、受控网络对照锁定非显然拦截层，才是 nontrivial 的典型形状。

时态校准：`将修复`、`修复会`、`准备`、`建议`、`下一步`、`应当` 都不证明修复已经执行。若同一证据已经完成根因复现或受控定位，但代码修复仍是将来时，Action-State 应为 diagnostic，Action-Records 只引用已完成的诊断，最终 Procedure 也只能写诊断程序，不能写计划中的修复。

严格格式：

<!-- actanara-review -->
# <必须替换为具体的单一因果身份>
Candidate-ID: candidate-001
Scope: domain-work
Action-State: executed
Verification-State: behavior-observed
Novelty: nontrivial
Decision: skill
Evidence-Records: 000001, 000004, 000009
Action-Records: 000004
Verification-Records: 000009
Scores: Evidence Closure 5/5 · Learning Value 5/5 · Transferability 4/5
Procedure Readiness: 5/5
Reason: <必须替换为一句实际裁决理由，不得保留尖括号占位内容>
"""


CRYSTALLIZATION_SYSTEM = """你是资深的 Agent 程序性记忆架构师。独立裁决阶段已经完成候选比较、时序冲突处理和评分；你只把 Decision=skill 的裁决卡及其原始证据写成简洁、可跨 Agent 使用的最终 Skill。Lesson 留在裁决账本，本阶段不得输出 Lesson。

最高优先级事实边界：Procedure 只能使用 dossier 的 action_records；Verification 只能使用 verification_records 中明确已经完成的结果。将来时、准备、建议和“现在去验收”都不是完成证据。Action-State=diagnostic 时只能写诊断程序，不能把建议修法写成步骤。同一记录中与当前卡机制独立的其它修复、权限问题或顺手改动必须删除。不得新增候选、改变决策或补写证据中没有执行过的步骤。"""


CRYSTALLIZATION_PROMPT = """下面是已经通过独立裁决的资产 dossier。每个 dossier 只包含一张获批卡及其唯一允许使用的原始记录。

<reviewed_asset_dossiers>
{reviews}
</reviewed_asset_dossiers>

每张 Skill 裁决卡必须恰好生成一个最终 Skill。候选摘要不是事实，Trigger/Pitfalls 可参考 context_records，Procedure 只可参考 action_records，Verification 只可参考 verification_records；不得跨分区偷换角色，也不得使用其它 dossier 的术语、步骤或验证。本轮共有 {asset_count} 张获批 Skill 卡，必须输出且只输出这些 ID：{asset_ids}。

每个最终产物必须逐字复制对应裁决卡的 `Review-ID`、`Evidence`、`Scope`、`Action-State`、`Verification-State`、`Novelty`、三组 Records 和 `Scores`。Procedure 只能取自 Action-Records，Verification 只能取自 Verification-Records；其它 Evidence-Records 只提供目标、障碍和边界上下文。不得遗漏裁决卡、重复 ID、改变 Skill/Lesson 类型或调整分数。

写作时执行以下压缩：

- `description` 同时说明做什么和何时触发；名称使用 2–6 个词的简短动词短语，优先描述稳定方法而非具体事故组件。禁止相邻或同义词重复，例如 `loop-loop`、`session-session`。
- YAML `name` 必须是 64 字符以内的 ASCII 小写 kebab-case，只能匹配 `[a-z0-9]+(?:-[a-z0-9]+)*`；标题和正文可以使用中文。
- 选择最高且仍可执行的稳定抽象，删除项目名、产品名、日期、地址、内部编号、临时路径、具体服务商清单和事故数值。
- Procedure 只保留证据中实际执行且改变结果的最小动作；已执行的诊断不得扩写成未执行的修复。
- 若裁决卡 `Action-State=diagnostic`，最终 name、description、标题与 Procedure 必须描述“如何诊断/隔离/验证边界”，而不是“如何修复”；即使证据中出现了建议修法，也不能把它改写成命令式步骤。只有 `Action-State=executed` 才能把实际执行的修复写入 Procedure。
- Pitfalls 只保留真实发生、未来容易重犯且会改变决策的错误路线。
- Verification 只保留直接证明目标或诊断边界的行为检查。
- 严格区分“已执行并观察到”“建议下一步执行”和“由现象推断”。记录中写成下一步、可选方案或尚未执行的检查，不能改写成已经完成的 Verification。工具证据也不得偷换类型：traceroute 不是客户端抓包，文件哈希一致不等于视觉不变量成立，测试通过不等于用户接受最终效果。
- `现在做真实验收`、`接下来验证`、`建议增加回归`、`修复会` 等将来时不是已完成 Verification；只保留同一句或其它 Verification-Records 中明确已经发生的结果。若只有单元测试完成，就只写单元测试覆盖的行为，不得升级为真实浏览器、重启或生产验收。
- 保持测量对象精确：某个被抬出循环的稳定辅助函数由“每行一次”降为“每请求一次”，不等于整个循环或所有函数调用不再随记录数增长。不得把局部复杂度改善扩大成全流程复杂度结论。
- 按全局时序确认最终采用的路线。被用户放弃、替换或否定的做法只能进入 Pitfalls，不能因为它曾有局部测试就写入 Procedure。若裁决卡的概括与后来的原始记录冲突，以原始记录的最终结果为准，并只写仍被证据支持的最小程序。
- 对每一句做删除测试：删掉后若不影响执行、避错或完成判断，就删除。发布、安装、签名、配置同步、测试数量、凭证处理通常不属于核心程序。
- 数值仅在它是协议、格式、资源安全边界或已证明可迁移的阈值时保留。
- 不得把同一资产再拆出附属 Lesson；不得改变裁决分数。
- 默认把 Skill 控制在 600 个中文字符左右；Procedure 通常 3–6 个动作。超过时先删除事故清单、测试数量、发布细节和实现枚举，再考虑是否需要更长。不要为了达到长度而省略决定性机制。
- Procedure 使用编号列表时必须严格按 `1, 2, 3...` 连续递增，不得重复或倒序。
- 对 provider、协议或操作系统的字段语义，只能写成“先核对该来源的公开字段契约，再按契约归一化”，不得把某次观察到的字段关系冒充所有来源的普适公式。

Skill 格式：

<!-- actanara-skill -->
Review-ID: review-001
Evidence: thread-0001:000001-000010
Scope: domain-work
Action-State: executed
Verification-State: behavior-observed
Novelty: nontrivial
Evidence-Records: 000001, 000004, 000009
Action-Records: 000004
Verification-Records: 000009
Scores: Evidence Closure 5/5 · Learning Value 5/5 · Transferability 4/5

---
name: concise-verb-led-name
description: 说明 Skill 做什么，以及什么可观察场景下应使用。
---

# 可迁移的问题与方法标题

## Trigger
泛化后的触发状态或症状。

## Procedure
已验证机制与最小执行步骤。

## Pitfalls
真实错误路线或适用边界。

## Verification
直接行为验证。
"""


@dataclass(frozen=True)
class ReviewDecision:
    title: str
    candidate_id: str
    decision: str
    evidence: tuple[str, ...]
    scores: single.ScoreCard
    procedure_readiness: int
    reason: str
    scope: str = "domain-work"
    action_state: str = "none"
    verification_state: str = "none"
    novelty: str = "routine"
    evidence_records: tuple[int, ...] = ()
    action_records: tuple[int, ...] = ()
    verification_records: tuple[int, ...] = ()
    asset_id: str = ""

    def markdown(self) -> str:
        return "\n".join(
            [
                REVIEW_MARKER,
                f"# {self.title}",
                f"Candidate-ID: {self.candidate_id}",
                *([f"Review-ID: {self.asset_id}"] if self.asset_id else []),
                f"Scope: {self.scope}",
                f"Action-State: {self.action_state}",
                f"Verification-State: {self.verification_state}",
                f"Novelty: {self.novelty}",
                f"Decision: {self.decision}",
                f"Evidence: {', '.join(self.evidence)}",
                "Evidence-Records: "
                + ", ".join(f"{record_id:06d}" for record_id in self.evidence_records),
                "Action-Records: "
                + (
                    ", ".join(f"{record_id:06d}" for record_id in self.action_records)
                    or "none"
                ),
                "Verification-Records: "
                + (
                    ", ".join(f"{record_id:06d}" for record_id in self.verification_records)
                    or "none"
                ),
                self.scores.markdown(),
                f"Procedure Readiness: {self.procedure_readiness}/5",
                f"Reason: {self.reason}",
            ]
        ).strip()


@dataclass(frozen=True)
class ThreeCallResult:
    business_date: str
    input_entries: int
    full_input_tokens: int
    discovery_evidence_tokens: int
    final_evidence_tokens: int
    gate_tokens: int
    llm_calls: int
    discoveries: tuple[two.DiscoveryCandidate, ...]
    reviews: tuple[ReviewDecision, ...]
    lessons: tuple[single.LearningEntry, ...]
    candidates: tuple[single.SkillCandidate, ...]
    excluded_recursive_meta: int
    rejected_discoveries: int
    rejected_reviews: int
    rejected_final_blocks: int
    report_path: Path
    discovery_raw_path: Path | None
    review_raw_path: Path | None
    crystallization_raw_path: Path | None


def build_review_prompt(
    candidates: Iterable[two.DiscoveryCandidate],
    *,
    evidence_stream: str,
) -> str:
    candidate_rows = list(candidates)
    candidate_text = "\n\n".join(
        f'<candidate_dossier id="candidate-{index:03d}">\n'
        f"Candidate-ID: candidate-{index:03d}\n"
        + candidate.markdown().replace("Records:", "Allowed-Records:", 1)
        + "\n<allowed_work_records>\n"
        + two.select_cited_records(evidence_stream, [candidate])
        + "\n</allowed_work_records>\n"
        + "</candidate_dossier>"
        for index, candidate in enumerate(candidate_rows, start=1)
    )
    candidate_ids = [
        f"candidate-{index:03d}" for index in range(1, len(candidate_rows) + 1)
    ]
    return (
        REVIEW_PROMPT.replace("{candidates}", candidate_text)
        .replace("{candidate_count}", str(len(candidate_rows)))
        .replace("{candidate_ids}", ", ".join(candidate_ids))
    )


def _parse_review_record_ids(
    value: str,
    *,
    stream: str,
    allow_none: bool,
) -> tuple[int, ...] | None:
    raw = str(value or "").strip()
    if allow_none and raw.casefold() == "none":
        return ()
    # Providers occasionally drop leading zeroes from an otherwise exact
    # global record id.  Recover only comma-separated decimal tokens; the
    # canonical parser below still verifies that every padded id exists in the
    # supplied evidence stream.
    parts = [part.strip() for part in re.split(r"\s*[,，;；]\s*", raw) if part.strip()]
    if parts and all(re.fullmatch(r"\d{1,6}", part) for part in parts):
        raw = ", ".join(f"{int(part):06d}" for part in parts)
    parsed = two._parse_global_record_ids(raw, stream=stream)
    return parsed[1] if parsed is not None else None


def _parse_review_block(block: str, *, stream: str) -> ReviewDecision | None:
    raw = str(block or "").strip()
    if not raw.startswith(REVIEW_MARKER) or single._contains_private_runtime_value(raw):
        return None
    title_match = re.search(r"(?m)^# (?P<title>[^\n]+)[ \t]*$", raw)
    candidate_ids = list(_CANDIDATE_ID_RE.finditer(raw))
    decisions = list(_DECISION_RE.finditer(raw))
    reasons = [
        match
        for match in _REASON_RE.finditer(raw)
        if match.group("reason").strip()
        not in {
            "一句简洁裁决理由。",
            "<必须替换为一句实际裁决理由，不得保留尖括号占位内容>",
        }
    ]
    evidence_rows = list(_EVIDENCE_RECORDS_RE.finditer(raw))
    action_rows = list(_ACTION_RECORDS_RE.finditer(raw))
    verification_rows = list(_VERIFICATION_RECORDS_RE.finditer(raw))
    scope_rows = list(_SCOPE_RE.finditer(raw))
    action_state_rows = list(_ACTION_STATE_RE.finditer(raw))
    verification_state_rows = list(_VERIFICATION_STATE_RE.finditer(raw))
    novelty_rows = list(_NOVELTY_RE.finditer(raw))
    score_rows = list(single._SCORES_LINE_RE.finditer(raw))
    procedure_rows = list(_PROCEDURE_READINESS_RE.finditer(raw))
    if (
        title_match is None
        or len(candidate_ids) != 1
        or len(decisions) != 1
        or len(reasons) != 1
        or len(evidence_rows) != 1
        or len(action_rows) != 1
        or len(verification_rows) != 1
        or len(scope_rows) != 1
        or len(action_state_rows) != 1
        or len(verification_state_rows) != 1
        or len(novelty_rows) != 1
        or len(score_rows) != 1
        or len(procedure_rows) != 1
    ):
        return None
    title = title_match.group("title").strip()
    reason = reasons[0].group("reason").strip()
    evidence_records = _parse_review_record_ids(
        evidence_rows[0].group("value"), stream=stream, allow_none=False
    )
    action_records = _parse_review_record_ids(
        action_rows[0].group("value"), stream=stream, allow_none=True
    )
    verification_records = _parse_review_record_ids(
        verification_rows[0].group("value"), stream=stream, allow_none=True
    )
    if (
        not title
        or not reason
        or evidence_records is None
        or action_records is None
        or verification_records is None
        or not set(action_records).issubset(evidence_records)
        or not set(verification_records).issubset(evidence_records)
    ):
        return None
    evidence = two._references_for_record_ids(
        evidence_records,
        records=single._stream_records(stream),
    )
    score = score_rows[0]
    scores = single.ScoreCard(
        evidence_closure=int(score.group("closure")),
        learning_value=int(score.group("learning")),
        transferability=int(score.group("transfer")),
    )
    procedure_readiness = int(procedure_rows[0].group("value"))
    decision = decisions[0].group(1).casefold()
    scope = scope_rows[0].group("value").casefold()
    action_state = action_state_rows[0].group("value").casefold()
    verification_state = verification_state_rows[0].group("value").casefold()
    novelty = novelty_rows[0].group("value").casefold()
    # A non-Skill card carries no promotion authority.  If the provider labels
    # it executed/diagnostic but cites no action records, safely lower that
    # auxiliary state instead of losing the Lesson/Drop audit entry.  Never
    # apply this repair to a Skill decision.
    if decision != "skill" and not action_records:
        action_state = "none"
    if decision != "skill" and not verification_records:
        verification_state = "none"
    has_executed_action = action_state in {"executed", "diagnostic"}
    has_verification = verification_state != "none"
    if (
        bool(action_records) != has_executed_action
        or bool(verification_records) != has_verification
        or (scope == "distillation-meta" and decision != "drop")
        or (
            novelty == "routine"
            and (decision != "drop" or scores.learning_value >= 4)
        )
        or (
            decision == "skill"
            and (
                scope != "domain-work"
                or not has_executed_action
                or not has_verification
                or novelty != "nontrivial"
                or scores.evidence_closure < 4
                or scores.learning_value < 4
                or scores.transferability < 4
                or procedure_readiness < 4
            )
        )
        or (
            decision == "lesson"
            and (
                scores.evidence_closure < 4
                or scores.learning_value < 4
                or (
                    scores.transferability >= 4
                    and procedure_readiness >= 4
                    and scope == "domain-work"
                    and has_executed_action
                    and has_verification
                )
            )
        )
    ):
        return None
    return ReviewDecision(
        title=title,
        candidate_id=candidate_ids[0].group("value").casefold(),
        decision=decision,
        evidence=evidence,
        scores=scores,
        procedure_readiness=procedure_readiness,
        reason=reason,
        scope=scope,
        action_state=action_state,
        verification_state=verification_state,
        novelty=novelty,
        evidence_records=evidence_records,
        action_records=action_records,
        verification_records=verification_records,
    )


def parse_review_output(
    raw_output: str,
    *,
    stream: str,
    candidates: Iterable[two.DiscoveryCandidate] | None = None,
) -> tuple[list[ReviewDecision], int]:
    expected_rows = list(candidates) if candidates is not None else None
    expected = (
        {
            f"candidate-{index:03d}": candidate
            for index, candidate in enumerate(expected_rows, start=1)
        }
        if expected_rows is not None
        else None
    )
    prepared = single._unwrap_outer_fence(single._THINKING_RE.sub("", str(raw_output or ""))).strip()
    if not prepared:
        return [], len(expected or {})
    markers = list(_REVIEW_MARKER_RE.finditer(prepared))
    card_starts = list(_REVIEW_CARD_START_RE.finditer(prepared))
    if not markers or not card_starts:
        return [], 1
    if len(markers) == 1 and len(card_starts) > 1:
        blocks = []
        for index, start in enumerate(card_starts):
            end = card_starts[index + 1].start() if index + 1 < len(card_starts) else len(prepared)
            body = prepared[start.start() : end].strip()
            blocks.append(f"{REVIEW_MARKER}\n{body}")
    else:
        blocks = [
            prepared[
                marker.start() : (
                    markers[index + 1].start()
                    if index + 1 < len(markers)
                    else len(prepared)
                )
            ]
            for index, marker in enumerate(markers)
        ]
    decisions: list[ReviewDecision] = []
    seen_candidates: set[str] = set()
    rejected = 0
    for block in blocks:
        item = _parse_review_block(block, stream=stream)
        if item is None:
            rejected += 1
            continue
        if expected is not None:
            source = expected.get(item.candidate_id)
            if (
                source is None
                or item.candidate_id in seen_candidates
                or not set(item.evidence_records).issubset(
                    source.record_ids
                    or two._record_ids_for_references(source.evidence, stream=stream)
                )
            ):
                rejected += 1
                continue
            asset_id = item.candidate_id.replace("candidate-", "review-", 1)
            seen_candidates.add(item.candidate_id)
        else:
            asset_id = f"review-{len(decisions) + 1:03d}"
        decisions.append(replace(item, asset_id=asset_id))
    if expected is not None:
        decisions.sort(key=lambda item: item.candidate_id)
        rejected = len(expected) - len(decisions)
    return decisions, rejected


def _evidence_is_subset(child: Iterable[str], parent: Iterable[str]) -> bool:
    def expand(references: Iterable[str]) -> set[tuple[str, int]]:
        records: set[tuple[str, int]] = set()
        for reference in references:
            match = single._EVIDENCE_REFERENCE_RE.fullmatch(reference)
            if match is None:
                return set()
            start = int(match.group("start"))
            end = int(match.group("end") or match.group("start"))
            records.update((match.group("thread"), index) for index in range(start, end + 1))
        return records

    child_records = expand(child)
    parent_records = expand(parent)
    return bool(child_records) and child_records.issubset(parent_records)


def _as_discovery(decision: ReviewDecision) -> two.DiscoveryCandidate:
    return two.DiscoveryCandidate(
        title=decision.title,
        goal="",
        obstacle="",
        effective_turn="",
        observed_result="",
        reuse_hypothesis="",
        evidence=decision.evidence,
        record_ids=decision.evidence_records,
        summary=decision.reason,
    )


def select_reviewed_records(stream: str, decisions: Iterable[ReviewDecision]) -> str:
    return two.select_cited_records(stream, (_as_discovery(item) for item in decisions))


def _records_for_role(
    evidence_stream: str,
    decision: ReviewDecision,
    record_ids: Iterable[int],
) -> str:
    selected = tuple(dict.fromkeys(int(item) for item in record_ids))
    if not selected:
        return "(none)"
    candidate = replace(_as_discovery(decision), evidence=(), record_ids=selected)
    return two.select_cited_records(evidence_stream, [candidate]) or "(none)"


def build_crystallization_prompt(
    decisions: Iterable[ReviewDecision],
    *,
    evidence_stream: str,
) -> str:
    accepted = [item for item in decisions if item.decision == "skill"]
    reviews = "\n\n".join(
        f'<reviewed_asset_dossier id="{item.asset_id}">\n'
        + item.markdown()
        + "\n<context_records>\n"
        + _records_for_role(
            evidence_stream,
            item,
            (
                record_id
                for record_id in item.evidence_records
                if record_id not in set(item.action_records)
                and record_id not in set(item.verification_records)
            ),
        )
        + "\n</context_records>\n"
        + "<action_records>\n"
        + _records_for_role(evidence_stream, item, item.action_records)
        + "\n</action_records>\n"
        + "<verification_records>\n"
        + _records_for_role(evidence_stream, item, item.verification_records)
        + "\n</verification_records>\n"
        + "</reviewed_asset_dossier>"
        for item in accepted
    )
    return (
        CRYSTALLIZATION_PROMPT.replace("{reviews}", reviews)
        .replace("{asset_count}", str(len(accepted)))
        .replace("{asset_ids}", ", ".join(item.asset_id for item in accepted))
    )


def parse_crystallization_output(
    raw_output: str,
    *,
    stream: str,
    decisions: Iterable[ReviewDecision],
) -> tuple[list[single.LearningEntry], list[single.SkillCandidate], int]:
    """Accept exactly one final Skill for each approved adjudication card."""
    approved = {item.asset_id: item for item in decisions if item.decision == "skill"}
    prepared = single._unwrap_outer_fence(
        single._THINKING_RE.sub("", str(raw_output or ""))
    ).strip()
    if not approved:
        return [], [], 0 if not prepared else 1
    if not prepared:
        return [], [], len(approved)
    markers = list(single._MARKER_RE.finditer(prepared))
    if not markers:
        return [], [], len(approved)

    lessons: list[single.LearningEntry] = []
    candidates: list[single.SkillCandidate] = []
    seen: set[str] = set()
    rejected = 0
    for index, marker in enumerate(markers):
        end = markers[index + 1].start() if index + 1 < len(markers) else len(prepared)
        block = prepared[marker.start() : end].strip()
        identifiers = list(_REVIEW_ID_RE.finditer(block))
        if len(identifiers) != 1:
            rejected += 1
            continue
        asset_id = identifiers[0].group("value").casefold()
        review = approved.get(asset_id)
        if review is None or asset_id in seen:
            rejected += 1
            continue
        seen.add(asset_id)

        item = single.parse_skill_candidate(block, stream=stream)
        evidence_record_rows = list(_EVIDENCE_RECORDS_RE.finditer(block))
        action_record_rows = list(_ACTION_RECORDS_RE.finditer(block))
        verification_record_rows = list(_VERIFICATION_RECORDS_RE.finditer(block))
        scope_rows = list(_SCOPE_RE.finditer(block))
        action_state_rows = list(_ACTION_STATE_RE.finditer(block))
        verification_state_rows = list(_VERIFICATION_STATE_RE.finditer(block))
        novelty_rows = list(_NOVELTY_RE.finditer(block))
        final_evidence_records = (
            _parse_review_record_ids(
                evidence_record_rows[0].group("value"), stream=stream, allow_none=False
            )
            if len(evidence_record_rows) == 1
            else None
        )
        final_action_records = (
            _parse_review_record_ids(
                action_record_rows[0].group("value"), stream=stream, allow_none=False
            )
            if len(action_record_rows) == 1
            else None
        )
        final_verification_records = (
            _parse_review_record_ids(
                verification_record_rows[0].group("value"), stream=stream, allow_none=False
            )
            if len(verification_record_rows) == 1
            else None
        )
        final_scope = (
            scope_rows[0].group("value").casefold() if len(scope_rows) == 1 else None
        )
        final_action_state = (
            action_state_rows[0].group("value").casefold()
            if len(action_state_rows) == 1
            else None
        )
        final_verification_state = (
            verification_state_rows[0].group("value").casefold()
            if len(verification_state_rows) == 1
            else None
        )
        final_novelty = (
            novelty_rows[0].group("value").casefold()
            if len(novelty_rows) == 1
            else None
        )
        if (
            item is None
            or item.evidence != review.evidence
            or item.scores != review.scores
            or final_scope != review.scope
            or final_action_state != review.action_state
            or final_verification_state != review.verification_state
            or final_novelty != review.novelty
            or final_evidence_records != review.evidence_records
            or final_action_records != review.action_records
            or final_verification_records != review.verification_records
        ):
            rejected += 1
            continue
        candidates.append(item)

    rejected += len(set(approved) - seen)
    return lessons, candidates, rejected


def _chunk_id(stage: str, source: str) -> str:
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()[:12]
    return f"{stage}-0001-{digest}"


def call_three_stage_llm(
    prompt: str,
    *,
    system: str,
    stage: str,
    source: str,
    paths: RuntimePaths,
) -> str:
    started = time.time()
    print(
        f"   [SKILL-LLM-START] three-call/{stage}: prompt≈{single.token_count(prompt):,} tokens",
        flush=True,
    )
    result = execute_llm_message(
        system=system,
        prompt=prompt,
        temperature=0.1,
        max_tokens=DEFAULT_MAX_OUTPUT_TOKENS,
        thinking_mode="high" if stage in {"adjudication", "crystallization"} else THINKING_MODE,
        paths=paths,
        pass_id="skill-three-call",
        label=f"skill three-call {stage}",
        chunk_id=_chunk_id(stage, source),
    ).text
    cleaned = single._THINKING_RE.sub("", result or "").strip()
    print(
        f"   [SKILL-LLM-END] three-call/{stage}: {time.time() - started:.1f}s, chars={len(cleaned):,}",
        flush=True,
    )
    return cleaned


def report_path(paths: RuntimePaths, business_date: str) -> Path:
    return paths.home / "artifacts" / "skills" / f"skill-three-call-{PROMPT_VERSION}-{business_date}.md"


def raw_output_path(paths: RuntimePaths, business_date: str, stage: str) -> Path:
    return (
        paths.home
        / "artifacts"
        / "skills"
        / f"skill-three-call-{PROMPT_VERSION}-raw-{stage}-{business_date}.md"
    )


def render_report(result: ThreeCallResult) -> str:
    review_counts = {
        decision: sum(1 for item in result.reviews if item.decision == decision)
        for decision in ("skill", "lesson", "drop")
    }
    lines = [
        f"# {result.business_date} Three-call Learning and Skill Proposals ({PROMPT_VERSION})",
        "",
        "> Discovery finds compact causal clues; adjudication compares and scores families; crystallization writes accepted assets.",
        "> This experiment does not install or publish skills.",
        "",
        f"- Input entries: {result.input_entries}",
        f"- Full-stream estimated tokens: {result.full_input_tokens}",
        f"- Discovery evidence packet tokens: {result.discovery_evidence_tokens}",
        f"- Final evidence packet tokens: {result.final_evidence_tokens}",
        f"- Gate tokens: {result.gate_tokens}",
        f"- Total logical LLM calls: {result.llm_calls}",
        f"- Valid discoveries: {len(result.discoveries)}",
        f"- Discoveries excluded by local semantic filters: {result.excluded_recursive_meta} (disabled)",
        f"- Structurally rejected discoveries: {result.rejected_discoveries}",
        f"- Review decisions: skill={review_counts['skill']}, lesson={review_counts['lesson']}, drop={review_counts['drop']}",
        f"- Structurally rejected reviews: {result.rejected_reviews}",
        f"- Adjudicated Lessons retained in audit: {review_counts['lesson']}",
        f"- Crystallized Lessons: {len(result.lessons)} (not produced by this stage)",
        f"- Valid Skill proposals: {len(result.candidates)}",
        f"- Structurally rejected final blocks: {result.rejected_final_blocks}",
        "",
        "## Adjudication Audit",
    ]
    if result.rejected_reviews:
        lines.insert(
            lines.index("## Adjudication Audit"),
            "> PARTIAL: at least one known candidate lacked a valid adjudication card; each invalid card was withheld while independently closed cards remained eligible for crystallization.",
        )
    elif result.rejected_final_blocks:
        lines.insert(
            lines.index("## Adjudication Audit"),
            "> PARTIAL: malformed final blocks were withheld; independently closed Skill siblings were retained.",
        )
    if not result.reviews:
        lines.extend(["", "(none)"])
    for review in result.reviews:
        lines.extend(["", review.markdown()])
    lines.extend([
        "",
        "## Experience and Lessons",
    ])
    if not result.lessons:
        lines.extend(["", "(none)"])
    for lesson in result.lessons:
        lines.extend(["", lesson.markdown()])
    lines.extend(["", "## Skill Upgrade Proposals"])
    if not result.candidates:
        lines.extend(["", "(none)"])
    for candidate in result.candidates:
        review = next(
            (
                item
                for item in result.reviews
                if item.decision == "skill"
                and item.evidence == candidate.evidence
                and item.scores == candidate.scores
            ),
            None,
        )
        if review is not None:
            lines.extend(["", f"### {review.asset_id}"])
        lines.extend(["", candidate.markdown()])
    return "\n".join(lines).rstrip() + "\n"


def run_skill_pass(
    business_date: str,
    *,
    paths: RuntimePaths | None = None,
    gate_tokens: int | None = None,
    llm_call: Callable[..., str] = call_three_stage_llm,
    discovery_raw_override: str | None = None,
    review_raw_override: str | None = None,
    crystallization_raw_override: str | None = None,
) -> ThreeCallResult:
    selected = paths or load_paths()
    business_date = single.normalize_business_date(business_date)
    entries = single.load_filtered_stream(selected, business_date)
    stream = single.render_filtered_stream(entries)
    provider = resolve_llm_provider(selected, redact_secrets=True)
    configured_gate = int(provider.get("pipelineGateTokens") or 30000)
    gate = single.single_call_gate_tokens(configured_gate, override=gate_tokens)

    discoveries: list[two.DiscoveryCandidate] = []
    reviews: list[ReviewDecision] = []
    lessons: list[single.LearningEntry] = []
    candidates: list[single.SkillCandidate] = []
    rejected_discoveries = 0
    excluded_recursive_meta = 0
    rejected_reviews = 0
    rejected_final = 0
    calls = 0
    discovery_raw_path: Path | None = None
    review_raw_path: Path | None = None
    crystallization_raw_path: Path | None = None
    discovery_evidence = ""
    final_evidence = ""

    if stream:
        raw_discovery = discovery_raw_override
        if raw_discovery is None:
            prompt = two.build_discovery_prompt(stream)
            if single.token_count(prompt) > gate:
                raise single.SkillPassError("complete filtered stream exceeds the discovery gate")
            raw_discovery = llm_call(
                prompt,
                system=two.DISCOVERY_SYSTEM,
                stage="discovery",
                source=stream,
                paths=selected,
            )
        if discovery_raw_override is None:
            calls += 1
        safe_discovery = two.redact_discovery_output(raw_discovery)
        discovery_raw_path = two.raw_output_path(selected, business_date, "discovery")
        two._persist_raw(discovery_raw_path, safe_discovery)
        discoveries, rejected_discoveries = two.parse_discovery_output(
            safe_discovery,
            stream=stream,
        )
        if discoveries:
            discovery_evidence = two.select_cited_records(stream, discoveries)
            raw_review = review_raw_override
            if raw_review is None:
                review_prompt = build_review_prompt(
                    discoveries,
                    evidence_stream=discovery_evidence,
                )
                if single.token_count(review_prompt) > gate:
                    raise single.SkillPassError("discovery evidence exceeds the adjudication gate")
                raw_review = llm_call(
                    review_prompt,
                    system=REVIEW_SYSTEM,
                    stage="adjudication",
                    source=discovery_evidence,
                    paths=selected,
                )
                calls += 1
            review_raw_path = raw_output_path(selected, business_date, "adjudication")
            safe_review = two.redact_discovery_output(raw_review)
            two._persist_raw(review_raw_path, safe_review)
            reviews, rejected_reviews = parse_review_output(
                safe_review,
                stream=discovery_evidence,
                candidates=discoveries,
            )

        # Invalid cards never gain authority, but they also must not suppress
        # unrelated cards whose evidence and decision contract are complete.
        # The report preserves the measurable coverage loss as PARTIAL.
        accepted_reviews = [item for item in reviews if item.decision == "skill"]
        if accepted_reviews:
            final_evidence = select_reviewed_records(stream, accepted_reviews)
            final_prompt = build_crystallization_prompt(
                accepted_reviews,
                evidence_stream=final_evidence,
            )
            if single.token_count(final_prompt) > gate:
                raise single.SkillPassError("reviewed evidence exceeds the crystallization gate")
            raw_final = crystallization_raw_override
            if raw_final is None:
                raw_final = llm_call(
                    final_prompt,
                    system=CRYSTALLIZATION_SYSTEM,
                    stage="crystallization",
                    source=final_evidence,
                    paths=selected,
                )
                calls += 1
            crystallization_raw_path = raw_output_path(selected, business_date, "crystallization")
            safe_final = two.redact_discovery_output(raw_final)
            two._persist_raw(crystallization_raw_path, safe_final)
            lessons, candidates, rejected_final = parse_crystallization_output(
                safe_final,
                stream=final_evidence,
                decisions=accepted_reviews,
            )

    destination = report_path(selected, business_date)
    result = ThreeCallResult(
        business_date=business_date,
        input_entries=len(entries),
        full_input_tokens=single.token_count(stream) if stream else 0,
        discovery_evidence_tokens=(single.token_count(discovery_evidence) if discovery_evidence else 0),
        final_evidence_tokens=single.token_count(final_evidence) if final_evidence else 0,
        gate_tokens=gate,
        llm_calls=calls,
        discoveries=tuple(discoveries),
        reviews=tuple(reviews),
        lessons=tuple(lessons),
        candidates=tuple(candidates),
        excluded_recursive_meta=excluded_recursive_meta,
        rejected_discoveries=rejected_discoveries,
        rejected_reviews=rejected_reviews,
        rejected_final_blocks=rejected_final,
        report_path=destination,
        discovery_raw_path=discovery_raw_path,
        review_raw_path=review_raw_path,
        crystallization_raw_path=crystallization_raw_path,
    )
    single._write_text_atomic(destination, render_report(result))
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Test three-stage Lesson and Skill distillation.")
    parser.add_argument("business_date", nargs="?", default=business_today())
    parser.add_argument("--gate-tokens", type=int)
    parser.add_argument(
        "--resume-discovery",
        action="store_true",
        help="reuse the owner-only sanitized discovery artifact and run adjudication + crystallization",
    )
    args = parser.parse_args(argv)
    try:
        override = None
        if args.resume_discovery:
            selected = load_paths()
            path = two.raw_output_path(
                selected,
                single.normalize_business_date(args.business_date),
                "discovery",
            )
            override = path.read_text(encoding="utf-8")
        result = run_skill_pass(
            args.business_date,
            gate_tokens=args.gate_tokens,
            discovery_raw_override=override,
        )
    except (OSError, single.SkillPassError, ValueError) as exc:
        print(f"[X] Three-call Skill Pass could not finish: {exc}", file=sys.stderr)
        return 1
    print(
        f"[OK] Three-call Skill Pass {result.business_date}: calls={result.llm_calls}, "
        f"discoveries={len(result.discoveries)}, reviews={len(result.reviews)}, "
        f"lessons={len(result.lessons)}, skills={len(result.candidates)}, report={result.report_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
