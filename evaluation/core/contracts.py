from __future__ import annotations

from .models import ContractInfo
from ..report_models import EvaluationSpec, TaskContract, TestCase


def resolve_contract(spec: EvaluationSpec, case: TestCase) -> TaskContract:
    if case.task_contract is not None:
        return case.task_contract
    if spec.task_contract_by_tag:
        for tag in case.tags:
            if tag in spec.task_contract_by_tag:
                return spec.task_contract_by_tag[tag]
    return spec.task_contract_default


def to_contract_info(contract: TaskContract) -> ContractInfo:
    return ContractInfo(
        output_format=contract.output_format,
        language=contract.language,
        truth_source=contract.truth_source,
        json_schema=contract.json_schema,
        require_clarifying_question_if_ambiguous=contract.require_clarifying_question_if_ambiguous,
        must_include=list(contract.must_include),
        must_not_include=list(contract.must_not_include),
    )
