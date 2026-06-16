"""
周常任务求解器 — ILP 求解核心

使用 HiGHS 求解器（通过 highspy 包）求解整数线性规划问题：
    min  c'x
    s.t. Ax >= b
         x >= 0, x ∈ Z

移植自 Chaldea:
  chaldea/lib/app/modules/master_mission/solver/solver.dart:16-53
  chaldea/res/js/glpk_solver.js
"""

import logging
from math import ceil

from .matcher import count_mission_target
from .models import Mission, QuestPhase, SolveResult

logger = logging.getLogger("MissionSolver")


def solve(quests: list[QuestPhase], missions: list[Mission]) -> SolveResult:
    """
    求解最优刷本方案。

    Args:
        quests: 候选 Free 副本列表
        missions: 当前周常任务列表

    Returns:
        SolveResult 包含最优方案、总 AP、总次数和明细
    """
    # 分类任务：能通过刷本完成的（战斗可解）纳入 ILP，其余归入 unsolvable。
    #   不可解 = is_valid 为假（非战斗任务，无敌人/副本条件）
    #         或 valid 但没有任何候选副本能产生贡献（未支持的关卡/特性）
    solvable_missions = []   # 纳入求解的任务
    solvable_rows = []       # 对应的贡献行
    unsolvable_missions = []

    for mission in missions:
        if not mission.is_valid:
            unsolvable_missions.append(mission)
            continue
        row = [count_mission_target(mission, quest) for quest in quests]
        if not any(v > 0 for v in row):
            unsolvable_missions.append(mission)
            continue
        solvable_missions.append(mission)
        solvable_rows.append(row)

    if unsolvable_missions:
        logger.info(f"{len(unsolvable_missions)} 条任务无法通过刷本完成（非战斗/未支持），需手动完成:")
        for um in unsolvable_missions:
            logger.info(f"  - {um.description}")

    if not solvable_missions:
        logger.warning("没有可通过刷本完成的任务")
        return SolveResult(plan={}, unsolvable_missions=unsolvable_missions)

    m = len(solvable_missions)
    n = len(quests)

    # 过滤对所有任务贡献为 0 的副本
    useful_cols = [i for i in range(n) if any(solvable_rows[j][i] > 0 for j in range(m))]

    filtered_quests = [quests[i] for i in useful_cols]
    filtered_A = [[solvable_rows[j][i] for i in useful_cols] for j in range(m)]
    fn = len(filtered_quests)

    logger.info(f"问题规模: {m} 个任务, {fn} 个候选副本 (从 {n} 个过滤)")

    # 调用 HiGHS 求解
    try:
        import highspy
    except ImportError:
        logger.error("highspy 未安装，请运行: pip install highspy")
        raise ImportError("请安装 highspy: pip install highspy")

    h = highspy.Highs()
    h.silent()

    inf = highspy.kHighsInf

    # 添加变量: x_i >= 0, 整数, 目标系数 = AP 消耗
    for i in range(fn):
        h.addVariable(0.0, inf, float(filtered_quests[i].consume))

    # 设置变量为整数类型
    for i in range(fn):
        h.changeColIntegrality(i, highspy.HighsVarType.kInteger)

    # 添加约束: Σ(A[j][i] * x_i) >= b[j]
    # 此处每条任务都已保证至少有一个候选副本贡献 >0（不可解任务已提前剔除）。
    for j in range(m):
        indices = [i for i in range(fn) if filtered_A[j][i] > 0]
        values = [float(filtered_A[j][i]) for i in indices]
        h.addRow(float(solvable_missions[j].count), inf, len(indices), indices, values)

    # 求解
    h.run()
    status = h.getModelStatus()

    if status != highspy.HighsModelStatus.kOptimal:
        logger.warning(f"求解未找到最优解，状态: {status}")
        return SolveResult(plan={}, unsolvable_missions=unsolvable_missions)

    # 提取结果
    plan = {}
    total_ap = 0
    total_runs = 0
    details = {}

    sol = h.getSolution()
    col_values = sol.col_value

    for i in range(fn):
        count = int(ceil(col_values[i])) if i < len(col_values) else 0
        if count > 0:
            quest = filtered_quests[i]
            plan[quest.id] = count
            total_runs += count
            total_ap += count * quest.consume

            # 计算明细
            quest_details = {}
            for j, mission in enumerate(solvable_missions):
                contribution = filtered_A[j][i] * count
                if contribution > 0:
                    quest_details[mission.description] = contribution
            if quest_details:
                details[quest.id] = quest_details

    return SolveResult(
        plan=plan,
        total_ap=total_ap,
        total_runs=total_runs,
        details=details,
        unsolvable_missions=unsolvable_missions,
    )
