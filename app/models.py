"""Pydantic 请求模型。"""
from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, Field, field_validator

# 非空字符串：节点名 / 资源名等不允许空串
NonEmptyStr = Annotated[str, Field(min_length=1)]


class NodeInput(BaseModel):
    name: str = Field(..., min_length=1, description="节点名，批次内唯一")
    node_type: str = Field("service", description="节点类型，如 service / config / job")
    estimated_seconds: int | None = Field(
        None, ge=1, description="预估工期（正整数，秒）；不传视为 0，用于关键路径分析"
    )
    max_attempts: int = Field(
        1, ge=1, le=10, description="最大尝试次数（含首次，1～10）；1 表示不自动重试，旧批次兼容为 1"
    )
    retry_delay_seconds: int = Field(
        0, ge=0, le=3600, description="失败后自动重试的延迟（0～3600 秒），到期前节点不进入 next_nodes"
    )
    metadata: dict[str, Any] = Field(default_factory=dict, description="附加元数据")


class EdgeInput(BaseModel):
    # to_node 依赖 from_node：from_node 必须先执行
    from_node: str = Field(..., min_length=1, description="前置节点")
    to_node: str = Field(..., min_length=1, description="后置节点（依赖前置）")


class CreateReleaseRequest(BaseModel):
    version: str = Field(..., min_length=1, description="发布版本号，如 2026.09.20-001")
    nodes: list[NodeInput] = Field(..., min_length=1, description="服务节点列表")
    dependencies: list[EdgeInput] = Field(default_factory=list, description="依赖边 from->to")
    targets: list[Annotated[str, Field(min_length=1)]] | None = Field(
        None,
        description="可选显式发布目标（节点名）。传入非空且不重复的列表后，"
                    "仅持久化并执行每个目标及其全部传递前置依赖；不传表示全量发布",
    )


class ReportRequest(BaseModel):
    node: str = Field(..., min_length=1, description="回报的节点名")
    status: str = Field(..., pattern="^(success|failed)$", description="成功或失败")
    version: str = Field(..., min_length=1, description="回报时携带的版本号，必须与批次版本一致")
    error_code: str | None = Field(None, description="失败错误码（失败时可选）")
    error_message: str | None = Field(None, description="失败原因（失败时可选）")


class CancelReleaseRequest(BaseModel):
    reason: str = Field(..., min_length=1, description="取消原因，会持久化并在状态查询中返回")
    expected_revision: int = Field(
        ..., ge=1,
        description="乐观锁：调用方最后一次看到的批次修订号；与当前 revision 不一致时返回 409 state_conflict 且状态不变",
    )


class SimulationRequest(BaseModel):
    """只读排程推演请求：并行度 + 可选节点到资源名的映射。"""

    parallelism: int = Field(
        ..., ge=1,
        description="同时运行的最大节点数（正整数）；运行不可中断，未映射资源的节点只占并行度空位",
    )
    resources: dict[NonEmptyStr, NonEmptyStr] | None = Field(
        None,
        description="可选节点名 -> 非空资源名映射；同一资源上的节点互斥（同一时刻至多一个在运行），"
                    "未映射的节点不占任何资源",
    )

    @field_validator("parallelism", mode="before")
    @classmethod
    def _strict_positive_int(cls, v: Any) -> Any:
        # pydantic 默认会把 bool / 数字字符串 / 整数浮点强转为 int；
        # 此处明确只接受真正的 int（正整数范围由 Field(ge=1) 兜底）
        if isinstance(v, bool) or not isinstance(v, int):
            raise ValueError("parallelism 必须为正整数")
        return v

    @field_validator("resources", mode="before")
    @classmethod
    def _non_empty_resources(cls, v: Any) -> Any:
        # dict 的强转（如 [["a","r"]]）也一并拒绝，只接受真正的 JSON 对象 / 键值序列；
        # 键 / 值必须为非空字符串：键的 min_length 由 Annotated 校验，空串值在此提前拒绝
        if v is not None:
            if not isinstance(v, dict):
                raise ValueError("resources 必须为对象（节点名到资源名的映射）")
            for name, resource in v.items():
                if not isinstance(name, str) or not name:
                    raise ValueError("resources 的键必须为非空节点名")
                if not isinstance(resource, str) or not resource:
                    raise ValueError(f"节点 {name!r} 的资源名必须为非空字符串")
        return v
