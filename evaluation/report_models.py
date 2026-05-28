from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


MetricName = str


class MetricSpec(BaseModel):
    name: MetricName
    threshold: float = Field(..., ge=0.0, le=1.0)
    enabled: bool = True
    weight: float = Field(1.0, ge=0.0)
    config: Dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "forbid"}


class LLMConfig(BaseModel):
    retries: int = 0
    timeout_s: int = 30
    average_runs: int = 1
    fail_on_variance: bool = False

    model_config = {"extra": "forbid"}


class InstructionPolicy(BaseModel):
    language: str = "es"
    do_not_invent: bool = True
    no_markdown: bool = True
    brand_tone: str = "profesional y cordial"
    refusal_message_hint: str = "Lo siento, no tengo acceso a esa información ahora."
    additional_rules: List[str] = Field(default_factory=list)

    model_config = {"extra": "forbid"}


class TaskContract(BaseModel):
    must_include: List[str] = Field(default_factory=list)
    must_not_include: List[str] = Field(default_factory=list)
    require_next_step: bool = False
    require_clarifying_question_if_ambiguous: bool = False
    output_format: Literal["free_text", "json"] = "free_text"
    json_schema: Optional[Dict[str, Any]] = None
    language: str = "es"
    truth_source: Literal["context_only", "context_plus_tools", "open_world_allowed"] = "context_only"

    model_config = {"extra": "forbid"}


class EvaluationSpec(BaseModel):
    run_id: str
    mode: Literal["deterministic", "generated", "adversarial"] = "deterministic"
    num_tests: int = 25
    seed: int = 7
    agent_kind: Literal["callable", "http"] = "callable"
    agent_module: str = "agent"
    agent_function: str = "run_agent"
    agent_timeout_s: float = 10.0
    prompt_base: Optional[str] = None
    metrics: List[MetricSpec] = Field(default_factory=list)
    grading_mode: Literal["llm", "rubric"] = "llm"
    gating_metrics: Optional[List[MetricName]] = None
    diagnostic_metrics: Optional[List[MetricName]] = None
    llm_metrics: Optional[List[MetricName]] = None
    rag_metrics: Optional[List[MetricName]] = None
    safety_metrics: Optional[List[MetricName]] = None
    extra_metrics: Optional[List[MetricName]] = None
    metrics_by_agent_kind: Dict[str, List[MetricName]] = Field(default_factory=dict)
    instruction_policy: InstructionPolicy = Field(default_factory=InstructionPolicy)
    task_contract_default: TaskContract = Field(default_factory=TaskContract)
    task_contract_by_tag: Dict[str, TaskContract] = Field(default_factory=dict)
    global_context: List[str] = Field(default_factory=list)
    allow_empty_retrieval: bool = True
    enable_metamorphic: bool = True
    metamorphic_variants_per_case: int = 3
    latency_budget_ms: Optional[int] = 5000
    fail_fast: bool = False
    llm_preflight: bool = True
    llm_metric_timeout_s: int = 30
    llm_fail_fast_on_infra: bool = False
    strict_infra: bool = False
    llm_config: LLMConfig = Field(default_factory=LLMConfig)
    cases_file: Optional[str] = None
    agent_type: Optional[Literal["rag_agent", "tool_agent", "chat_agent", "classification_agent"]] = None
    scorecard_weights: Dict[str, float] = Field(default_factory=dict)
    scorecard_gates: Dict[str, Any] = Field(default_factory=dict)
    anti_gaming_config: Dict[str, Any] = Field(default_factory=dict)
    audit_mode: Literal["balanced", "enterprise"] = "balanced"
    scorecard_min_pass_rate: float = 0.80
    reliability_min: float = 0.90
    max_concurrency: int = 1
    # Id del vocabulario de dominio que las heurísticas del engine deben usar
    # (ambigüedad, pregunta aclaratoria, categorías). None / desconocido =>
    # vocabulario vacío; las métricas dependientes se omiten en lugar de
    # devolver datos sesgados. Valores registrados hoy: "supermercado",
    # "inmuebles". Ver evaluation/core/domain_vocabulary.py.
    domain_vocabulary_id: Optional[str] = None

    model_config = {"extra": "forbid"}


class RagConfig(BaseModel):
    enabled: bool = False
    context_text: Optional[str] = None
    allow_empty_retrieval: bool = True
    must_ground_in_context: bool = False

    model_config = {"extra": "forbid"}


class OutputContract(BaseModel):
    output_format: Literal["free_text", "json"] = "free_text"
    json_schema: Optional[Dict[str, Any]] = None
    no_markdown: bool = True
    must_include: List[str] = Field(default_factory=list)
    must_not_include: List[str] = Field(default_factory=list)
    require_clarifying_question_if_ambiguous: bool = False
    language: str = "es"
    truth_source: Literal["context_only", "context_plus_tools", "open_world_allowed"] = "context_only"

    model_config = {"extra": "forbid"}


class JudgeProfile(BaseModel):
    judge_model: str = "gpt-4o-mini"
    llm_preflight: bool = True
    llm_metric_timeout_s: int = 30
    llm_fail_fast_on_infra: bool = True
    metrics: List[MetricSpec] = Field(default_factory=list)

    model_config = {"extra": "forbid"}


class BudgetControls(BaseModel):
    max_llm_calls: Optional[int] = None
    max_cost_usd: Optional[float] = None

    model_config = {"extra": "forbid"}


class ContraAgentSpec(BaseModel):
    run_id: str
    seed: int = 7
    language: Literal["es"] = "es"
    prompt_base: str
    num_cases: int = 5
    intensity: Literal["superficial", "rapida", "normal", "exhaustiva"] = "rapida"
    domain: Optional[str] = None
    rag: RagConfig = Field(default_factory=RagConfig)
    output_contract: OutputContract = Field(default_factory=OutputContract)
    judge_profile: JudgeProfile = Field(default_factory=JudgeProfile)
    budget_controls: BudgetControls = Field(default_factory=BudgetControls)
    latency_budget_ms: Optional[int] = 5000
    grading_mode: Literal["llm", "rubric"] = "llm"
    gating_metrics: Optional[List[MetricName]] = None
    diagnostic_metrics: Optional[List[MetricName]] = None
    llm_config: LLMConfig = Field(default_factory=LLMConfig)
    agent_type: Optional[Literal["rag_agent", "tool_agent", "chat_agent", "classification_agent"]] = None
    scorecard_weights: Dict[str, float] = Field(default_factory=dict)
    scorecard_gates: Dict[str, Any] = Field(default_factory=dict)
    anti_gaming_config: Dict[str, Any] = Field(default_factory=dict)
    audit_mode: Literal["balanced", "enterprise"] = "balanced"
    scorecard_min_pass_rate: float = 0.80
    reliability_min: float = 0.90

    model_config = {"extra": "forbid"}


class AgentEvalInput(BaseModel):
    system_prompt: str
    user_input: str
    retrieval_context: List[str] = Field(default_factory=list)
    language: str = "es"
    no_markdown: bool = True
    output_format: str = "free_text"
    json_schema: Optional[Dict[str, Any]] = None

    model_config = {"extra": "forbid"}


class TestCase(BaseModel):
    __test__ = False
    case_id: str
    input: str
    tags: List[str] = Field(default_factory=list)
    severity: str = "media"
    task_contract: Optional[TaskContract] = None
    expected_behavior: str = ""
    expected_output: Optional[str] = None
    context: List[str] = Field(default_factory=list)
    retrieval_context: List[str] = Field(default_factory=list)
    turns: Optional[List[str]] = None

    model_config = {"extra": "forbid"}


class MetricResult(BaseModel):
    name: MetricName
    score: Optional[float] = None
    threshold: Optional[float] = None
    success: Optional[bool] = None
    reason: Optional[str] = None
    reason_es: Optional[str] = None
    error: Optional[str] = None
    duration_ms: Optional[float] = None
    model: Optional[str] = None
    diagnostic_only: bool = False
    skipped: bool = False
    skip_reason: Optional[str] = None
    infra_skipped: bool = False
    retries_used: int = 0
    infra_error: bool = False
    model_error: bool = False
    std_dev: Optional[float] = None
    samples: Optional[List[float]] = None
    raw: Dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "forbid"}


class ClaimItem(BaseModel):
    text: str
    verdict: Literal["supported", "contradicted", "unverifiable"]
    evidence_snippets: List[str] = Field(default_factory=list)

    model_config = {"extra": "forbid"}


class ClaimAnalysis(BaseModel):
    supported_ratio: float
    unverifiable_ratio: float
    contradicted_ratio: float
    claims: List[ClaimItem] = Field(default_factory=list)

    model_config = {"extra": "forbid"}


class PromptEdit(BaseModel):
    priority: Literal["P0", "P1", "P2"]
    problem: str
    edit_type: Literal[
        "add_rule",
        "rewrite_rule",
        "add_example",
        "add_format_spec",
        "add_refusal_policy",
    ]
    proposed_text: str
    evidence: Dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "forbid"}


class PromptPatch(BaseModel):
    mode: Literal["append", "replace_section"] = "append"
    text: str = ""

    model_config = {"extra": "forbid"}


class PromptImprovement(BaseModel):
    summary: List[str] = Field(default_factory=list)
    suggested_edits: List[PromptEdit] = Field(default_factory=list)
    prompt_patch: PromptPatch = Field(default_factory=PromptPatch)

    model_config = {"extra": "forbid"}


class OverallFeedback(BaseModel):
    primary_fail_reasons: List[str] = Field(default_factory=list)
    notes: List[str] = Field(default_factory=list)

    model_config = {"extra": "forbid"}


class QuestionFeedback(BaseModel):
    question: str
    answered: bool
    answer_snippet: str = ""
    expected_from_context: str = ""
    verdict: Literal["correcto", "parcial", "incorrecto", "no_respondido", "incorrecto_por_unidad"]
    suggestion: str = ""

    model_config = {"extra": "forbid"}


class RagIssue(BaseModel):
    type: Literal[
        "missing_answer",
        "unit_mismatch",
        "extra_item",
        "unsupported_number",
    ]
    question_ref: str
    entity: Optional[str] = None
    expected_unit: Optional[str] = None
    found_unit: Optional[str] = None
    output_snippet: str = ""
    context_snippet: Optional[str] = None

    model_config = {"extra": "forbid"}


class RagAudit(BaseModel):
    issues: List[RagIssue] = Field(default_factory=list)
    summary: List[str] = Field(default_factory=list)
    contradictions: List[str] = Field(default_factory=list)

    model_config = {"extra": "forbid"}


class CaseFeedback(BaseModel):
    overall: OverallFeedback = Field(default_factory=OverallFeedback)
    prompt_improvement: PromptImprovement = Field(default_factory=PromptImprovement)
    question_by_question: List[QuestionFeedback] = Field(default_factory=list)
    rag_audit: Optional[RagAudit] = None

    model_config = {"extra": "forbid"}


class TurnReport(BaseModel):
    turn_index: int
    user_input: str
    agent_output: str
    metrics: List[MetricResult] = Field(default_factory=list)
    claim_analysis: Optional[ClaimAnalysis] = None

    model_config = {"extra": "forbid"}


class CaseReport(BaseModel):
    case_id: str
    tags: List[str] = Field(default_factory=list)
    severity: str = "media"
    passed: bool = False
    metrics: List[MetricResult] = Field(default_factory=list)
    claim_analysis: Optional[ClaimAnalysis] = None
    turns: Optional[List[TurnReport]] = None
    latency_ms: Optional[float] = None
    feedback: Optional[CaseFeedback] = None
    normalized_run: Optional[Dict[str, Any]] = None
    agent_type: Optional[str] = None
    agent_type_policy: Optional[Dict[str, Any]] = None
    dimensions: Optional[Dict[str, Any]] = None
    scorecard: Optional[Dict[str, Any]] = None
    anti_gaming: Optional[Dict[str, Any]] = None
    gating_metrics_resultado: Optional[List[Dict[str, Any]]] = None
    input_text: Optional[str] = None
    output_text: Optional[str] = None
    expected_behavior: Optional[str] = None
    evaluation_mode: Optional[str] = None  # "adversarial" | "standard"

    model_config = {"extra": "forbid"}


class RunSummary(BaseModel):
    run_id: str
    total_cases: int
    passed_cases: int
    failed_cases: int
    pass_rate: float
    passed_cases_rubric: Optional[int] = None
    passed_cases_llm: Optional[int] = None
    by_metric_failures: Dict[str, int] = Field(default_factory=dict)
    by_metric_failures_gating: Dict[str, int] = Field(default_factory=dict)
    by_metric_failures_diagnostic: Dict[str, int] = Field(default_factory=dict)
    by_tag_failures: Dict[str, int] = Field(default_factory=dict)
    by_tag_counts: Dict[str, int] = Field(default_factory=dict)
    by_tag_pass_rate: Dict[str, float] = Field(default_factory=dict)
    skipped_by_metric: Dict[str, int] = Field(default_factory=dict)
    infra_skips_summary: Dict[str, int] = Field(default_factory=dict)
    reliability_score: Optional[float] = None
    completeness_score: Optional[float] = None
    recommendations: List[str] = Field(default_factory=list)
    executive_summary: Optional[Dict[str, Any]] = None
    autogen_summary: Optional[Dict[str, Any]] = None

    model_config = {"extra": "forbid"}

    def to_dict(self) -> Dict[str, Any]:
        data = self.model_dump(mode="json")
        if self.executive_summary is not None:
            data["executive_summary"] = self.executive_summary
        if self.autogen_summary is not None:
            data["autogen_summary"] = self.autogen_summary
        return data


class RunReport(BaseModel):
    summary: RunSummary
    cases: List[CaseReport]
    spec: EvaluationSpec

    model_config = {"extra": "forbid"}
