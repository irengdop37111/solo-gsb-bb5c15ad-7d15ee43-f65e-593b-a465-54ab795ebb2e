"""只读排程推演（what-if simulation）。

读取批次**已保存**的节点、依赖与 estimated_seconds，忽略一切实际回报状态，
从 0 时刻起按确定性贪心规则模拟执行：

- 总并发上限 ``parallelism``：任一时刻运行中的正工期节点数不超过该值；
- 资源互斥：映射到同一非空资源名的节点在时间轴上互斥（不能同时运行）；
- 未在 ``resources`` 中映射的节点不占任何资源，仅受并行度约束；
- 每个时刻先释放全部在该时刻完成的节点，再按节点名**字典序**扫描
  “所有前置均已完成”的待执行节点：资源被占用者跳过，其余尽量填满空位；
- 运行一旦开始不可中断；填满后（或无可启动节点时）直接跳到最近的完成时刻；
- 零工期节点在同一时刻立即完成并继续参与后续调度（释放新的依赖 / 资源 / 空位），
  缺失工期（NULL）按 0 处理。

推演为纯只读：不开写事务、不触碰节点回报状态、不写事件。
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any


def simulate_schedule(
    nodes: list[dict[str, Any]],
    edges: list[tuple[str, str]],
    parallelism: int,
    resources: dict[str, str] | None = None,
) -> dict[str, Any]:
    """对已持久化的批次图执行离散事件排程推演。

    ``nodes`` 需含 name / estimated_seconds；``edges`` 为 (from, to) 依赖边
    （to 依赖 from）。``resources`` 为节点名 -> 非空资源名的映射。
    返回每个节点的 start_seconds / end_seconds 及总工期。
    """
    resource_of = resources or {}
    duration = {n["name"]: int(n.get("estimated_seconds") or 0) for n in nodes}
    names = sorted(duration)  # 节点名字典序：扫描与输出统一使用该顺序

    prereqs: dict[str, set[str]] = defaultdict(set)
    for frm, to in edges:
        prereqs[to].add(frm)

    started: dict[str, int] = {}
    finished: dict[str, int] = {}
    # 运行中的正工期节点：name -> 完成时刻；同一完成时刻的节点在下个时刻统一释放
    running: dict[str, int] = {}
    occupied: set[str] = set()  # 当前被运行中节点占用的资源名

    t = 0
    while len(finished) < len(names):
        # 1) 释放全部在 t 时刻完成的节点（同一时刻的完成者先全部离场，再统一调度）
        completed = sorted(n for n, end in running.items() if end == t)
        for name in completed:
            del running[name]
            res = resource_of.get(name)
            if res is not None:
                occupied.discard(res)
            finished[name] = t

        # 2) 按字典序反复扫描可启动节点，直到本时刻再也无法启动任何节点。
        #    零工期节点在本时刻立即完成（不进入 running、不长期占用资源 / 并行空位），
        #    其下游与同资源节点可能在同一时刻继续被调度，因此需要多轮扫描。
        progressed = True
        while progressed:
            progressed = False
            for name in names:
                if name in started:
                    continue
                if not prereqs[name] <= set(finished):
                    continue  # 仍有前置未完成
                res = resource_of.get(name)
                if res is not None and res in occupied:
                    continue  # 同资源节点互斥：跳过，不消耗本次扫描的后续机会以外的东西
                if len(running) >= parallelism:
                    break  # 并行空位已满：字典序靠后的节点本轮不可能再启动

                started[name] = t
                d = duration[name]
                if d == 0:
                    # 零工期：即时完成，不占并行空位；映射了资源也只是瞬时互斥
                    finished[name] = t
                    progressed = True  # 释放出的新就绪节点需要重新从头扫描
                else:
                    running[name] = t + d
                    if res is not None:
                        occupied.add(res)
                    if len(running) >= parallelism:
                        break  # 空位填满：结束本轮扫描，随后跳到最近完成时刻

        if len(finished) == len(names):
            break

        # 3) 运行不可中断：无可启动节点（或空位已填满）时跳到最近的完成时刻
        if not running:  # 防御：持久化图已在创建时保证无环，正常不会触发
            raise RuntimeError("排程推演停滞：存在永远无法调度的节点（图中可能含环）")
        t = min(running.values())

    total_duration = max(finished.values(), default=0)
    return {
        "parallelism": parallelism,
        "resources": resource_of,
        "total_duration_seconds": total_duration,
        "nodes": [
            {
                "node": name,
                "resource": resource_of.get(name),
                "estimated_seconds": duration[name],
                "start_seconds": started[name],
                "end_seconds": finished[name],
            }
            for name in names
        ],
    }
