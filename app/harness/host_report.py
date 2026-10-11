"""Keep server observations and retrieved material separate from model hypotheses."""

import json
import re


def format_host_report(run, generated, repository):
    payload = run["input"]
    history = payload.get("history") or {}
    metrics, logs = history.get("metrics", []), history.get("logs", [])
    facts = [
        f"- 主机：{payload['host']}",
        f"- 最后心跳（服务端 UTC）：{payload['last_heartbeat_at']}",
        f"- 云端观察：超过失联阈值未收到心跳；事件轮次 {payload['outage_seq']}。",
        f"- 故障前留存 {len(metrics)} 条指标、{len(logs)} 条关键日志。",
        "- 实时 MCP / search_log：unavailable，本次未连接失联主机。",
    ]
    for row in metrics[-3:] + logs[-5:]:
        facts.append(
            f"- 历史 evidence_id={row['evidence_id']}，接收于 {row['received_at']}："
            + json.dumps(row["payload"], ensure_ascii=False)[:1024]
        )
    retrieved = []
    for call in repository.list_tool_calls(run["id"]):
        if call["tool_name"] == "retrieve_knowledge" and call["status"] == "succeeded":
            retrieved.append(
                f"tool_call_id={call['id']}："
                + json.dumps(call["result"], ensure_ascii=False)[:2048]
            )
    for event in repository.list_events(run["id"], limit=1000):
        if event["type"] == "planner_retrieval":
            retrieved.append(
                f"event_id={event['id']}：" + event["payload"].get("content", "")[:2048]
            )
    # A heartbeat cannot certify a physical power state; fail closed if the model
    # asserts one. Hypotheses about network/collector/host remain unverified.
    if re.search(
        r"(?:已经|已|确认|确定|证实|必然|一定).{0,8}(?:物理宕机|宕机|断电|关机)", generated
    ):
        generated = (
            "模型生成了无法由现有证据确认的主机状态结论，已拒绝该分析。需恢复连接或补充独立证据。"
        )
    return (
        "# 主机失联降级诊断\n\n## 已观测事实\n"
        + "\n".join(facts)
        + "\n\n## 检索证据\n"
        + ("\n\n".join(retrieved) or "无成功检索记录。")
        + "\n检索内容为参考案例，不能证明本次事故根因。"
        + "\n\n## 分析推断\n原因未确定。心跳失联不足以确定主机物理状态。"
        + "\n以下模型分析尚未验证，需结合网络、采集器和恢复后的主机日志核查：\n\n"
        + generated
    )
