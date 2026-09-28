"""Pydantic 请求模型。"""
from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, Field, field_validator

# 非空字符串（节点名 / 资源名等）：沿用 min_length=1 的批次内既有约定
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
    parallelism: int = Field(
        ..., ge=1,
        description="总并发上限（正整数）：任一时刻运行中的正工期节点数不超过该值",
    )
    resources: dict[NonEmptyStr, NonEmptyStr] | None = Field(
        None,
        description="可选节点->资源名映射；键 / 值均为非空字符串。"
                    "未映射的节点不占资源；映射到同一资源的节点互斥（不可同时运行）",
    )

    @field_validator("parallelism", mode="before")
    @classmethod
    def _reject_bool(cls, v: Any) -> Any:
        # bool 是 int 子类，pydantic 默认会把 true 接受为 1；正整数参数需显式拒绝
        if isinstance(v, bool):
            raise ValueError("parallelism 必须为正整数，不能是布尔值")
        return v
