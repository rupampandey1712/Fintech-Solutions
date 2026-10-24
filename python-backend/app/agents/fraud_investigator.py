"""Fraud case investigator: a LangGraph agent that gathers evidence and drafts a recommendation.

The graph is a loop between the model and its tools:

    START -> agent --(tool calls)--> tools --(no report yet)--> agent
                                       \\--(report submitted, or step budget spent)--> END

The agent's tools are read-only and bound to the one case being investigated
(app.services.investigation_facts), so it can neither change anything nor look beyond that case.
It finishes by calling ``submit_report``; the analyst then makes the decision with the existing
review endpoint. Nothing here moves money or changes a case.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any, Literal, TypedDict

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field

from app.services.investigation_facts import CaseFacts

# The instructions live in a resource file shared word for word with the .NET agent
# (tests/test_contract.py keeps the two copies identical).
SYSTEM_PROMPT = (Path(__file__).resolve().parent.parent / "resources"
                 / "fraud_investigator_prompt.txt").read_text(encoding="utf-8").strip()

Recommendation = Literal["false_positive", "closed", "escalated"]


class InvestigationReport(BaseModel):
    recommendation: Recommendation = Field(
        description="false_positive releases the payment, closed confirms fraud, escalated "
                    "hands it to a senior reviewer.")
    confidence: Literal["low", "medium", "high"]
    summary: str = Field(description="Two or three sentences for the analyst.", max_length=800)
    key_findings: list[str] = Field(description="Short sentences, each naming its evidence.",
                                    max_length=8)
    suggested_note: str = Field(description="A review note the analyst can reuse, at most 200 "
                                            "characters.", max_length=200)


class _NoArgs(BaseModel):
    pass


class _HistoryArgs(BaseModel):
    days: int = Field(default=90, ge=1, le=365, description="Look-back window in days.")


@dataclass
class InvestigationStep:
    tool: str
    label: str


@dataclass
class InvestigationResult:
    report: InvestigationReport | None
    steps: list[InvestigationStep] = field(default_factory=list)
    model_turns: int = 0


class InvestigationState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    report: dict[str, Any] | None
    turns: int


TOOL_LABELS = {
    "get_case_overview": "Read the flagged payment and triggered rules",
    "get_payment_history": "Compared with the customer's payment history",
    "get_beneficiary_profile": "Checked the beneficiary and destination account",
    "get_compliance_status": "Checked KYC status and AML alerts",
    "get_account_activity": "Reviewed account age, sign-ins and other cases",
}


def build_tools(facts: CaseFacts) -> list[StructuredTool]:
    """The read-only tools, each a closure over one case's facts."""

    async def get_case_overview() -> dict[str, Any]:
        return await facts.overview()

    async def get_payment_history(days: int = 90) -> dict[str, Any]:
        return await facts.payment_history(days)

    async def get_beneficiary_profile() -> dict[str, Any]:
        return await facts.beneficiary_profile()

    async def get_compliance_status() -> dict[str, Any]:
        return await facts.compliance()

    async def get_account_activity() -> dict[str, Any]:
        return await facts.account_activity()

    return [
        StructuredTool.from_function(
            coroutine=get_case_overview, name="get_case_overview", args_schema=_NoArgs,
            description="The flagged payment, its beneficiary, the risk score and which fraud "
                        "rules triggered."),
        StructuredTool.from_function(
            coroutine=get_payment_history, name="get_payment_history", args_schema=_HistoryArgs,
            description="The customer's other payments in a window: count by status, typical "
                        "and largest amounts, and how many were at least as large as this one."),
        StructuredTool.from_function(
            coroutine=get_beneficiary_profile, name="get_beneficiary_profile",
            args_schema=_NoArgs,
            description="Prior payments to this beneficiary and their outcomes, the destination "
                        "account's age, and how many customers pay that account."),
        StructuredTool.from_function(
            coroutine=get_compliance_status, name="get_compliance_status", args_schema=_NoArgs,
            description="The customer's KYC status and recent AML alerts."),
        StructuredTool.from_function(
            coroutine=get_account_activity, name="get_account_activity", args_schema=_NoArgs,
            description="Account ages, successful and failed sign-ins in the last 7 days, and "
                        "the customer's other fraud cases."),
    ]


def submit_report_tool() -> StructuredTool:
    """Bound so the model sees its schema; the graph intercepts the call rather than running it."""

    def submit_report(**_: Any) -> str:
        return "submitted"

    return StructuredTool.from_function(
        func=submit_report, name="submit_report", args_schema=InvestigationReport,
        description="Submit the final recommendation. Call exactly once, when the evidence is in.")


def build_graph(model: BaseChatModel, tools: list[StructuredTool], max_turns: int):
    by_name = {tool.name: tool for tool in tools}
    model_with_tools = model.bind_tools([*tools, submit_report_tool()])

    async def agent(state: InvestigationState) -> dict[str, Any]:
        response = await model_with_tools.ainvoke(
            [SystemMessage(SYSTEM_PROMPT), *state["messages"]])
        return {"messages": [response], "turns": state["turns"] + 1}

    async def run_tools(state: InvestigationState) -> dict[str, Any]:
        last = state["messages"][-1]
        assert isinstance(last, AIMessage)
        results: list[ToolMessage] = []
        report: dict[str, Any] | None = None
        for call in last.tool_calls:
            if call["name"] == "submit_report":
                try:
                    report = InvestigationReport.model_validate(call["args"]).model_dump()
                    content = "Report received."
                except ValueError as exc:
                    content = f"The report was invalid, fix it and submit again: {exc}"
                results.append(ToolMessage(content, tool_call_id=call["id"]))
                continue
            tool = by_name.get(call["name"])
            if tool is None:
                results.append(ToolMessage(f"Unknown tool {call['name']}.",
                                           tool_call_id=call["id"], status="error"))
                continue
            try:
                output = await tool.ainvoke(call["args"])
                content = json.dumps(output, default=str)
            except Exception as exc:  # a failing tool is reported to the model, not raised
                content = f"Tool failed: {type(exc).__name__}"
            results.append(ToolMessage(content, tool_call_id=call["id"], name=call["name"]))
        return {"messages": results, "report": report}

    def after_agent(state: InvestigationState) -> str:
        last = state["messages"][-1]
        return "tools" if isinstance(last, AIMessage) and last.tool_calls else END

    def after_tools(state: InvestigationState) -> str:
        if state["report"] is not None or state["turns"] >= max_turns:
            return END
        return "agent"

    graph = StateGraph(InvestigationState)
    graph.add_node("agent", agent)
    graph.add_node("tools", run_tools)
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", after_agent, ["tools", END])
    graph.add_conditional_edges("tools", after_tools, ["agent", END])
    return graph.compile()


async def investigate(model: BaseChatModel, facts: CaseFacts,
                      max_turns: int) -> InvestigationResult:
    graph = build_graph(model, build_tools(facts), max_turns)
    state = await graph.ainvoke(
        {"messages": [HumanMessage("Investigate this fraud case and submit your report.")],
         "report": None, "turns": 0},
        # Each turn is two graph steps (agent, tools); leave headroom for the final agent step.
        config={"recursion_limit": max_turns * 2 + 2},
    )
    steps: list[InvestigationStep] = []
    seen: set[str] = set()
    for message in state["messages"]:
        if isinstance(message, AIMessage):
            for call in message.tool_calls:
                name = call["name"]
                if name in TOOL_LABELS and name not in seen:
                    seen.add(name)
                    steps.append(InvestigationStep(tool=name, label=TOOL_LABELS[name]))
    report = state.get("report")
    return InvestigationResult(
        report=InvestigationReport.model_validate(report) if report else None,
        steps=steps, model_turns=state["turns"],
    )


ModelFactory = Callable[[], BaseChatModel]


def gemini_model(api_key: str, model: str, timeout_seconds: float) -> BaseChatModel:
    from langchain_google_genai import ChatGoogleGenerativeAI  # noqa: PLC0415 (optional import)

    return ChatGoogleGenerativeAI(model=model, api_key=api_key, temperature=0, retries=2,
                                  request_timeout=timeout_seconds)
