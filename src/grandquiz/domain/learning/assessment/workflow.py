"""Stable descriptor owned by the deterministic single-question assessment workflow."""

from typing import Literal

from pydantic import BaseModel, ConfigDict

WorkflowNodeId = Literal[
    "select_target",
    "generate_question",
    "validate_evidence",
    "judge_distractors",
    "await_answer",
    "grade_answer",
    "commit_learning",
]
AssessmentWorkflowVariant = Literal["open", "multiple_choice"]
WorkflowEdgeKind = Literal["next", "optional"]

SELECT_TARGET: WorkflowNodeId = "select_target"
GENERATE_QUESTION: WorkflowNodeId = "generate_question"
VALIDATE_EVIDENCE: WorkflowNodeId = "validate_evidence"
JUDGE_DISTRACTORS: WorkflowNodeId = "judge_distractors"
AWAIT_ANSWER: WorkflowNodeId = "await_answer"
GRADE_ANSWER: WorkflowNodeId = "grade_answer"
COMMIT_LEARNING: WorkflowNodeId = "commit_learning"


class AssessmentWorkflowNode(BaseModel):
    model_config = ConfigDict(frozen=True)

    node_id: WorkflowNodeId
    label: str
    optional: bool = False


class AssessmentWorkflowEdge(BaseModel):
    model_config = ConfigDict(frozen=True)

    source: WorkflowNodeId
    target: WorkflowNodeId
    kind: WorkflowEdgeKind = "next"


class AssessmentWorkflowDescriptor(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: Literal["assessment_workflow.v1"] = "assessment_workflow.v1"
    workflow_kind: Literal["assessment"] = "assessment"
    variant: AssessmentWorkflowVariant
    nodes: tuple[AssessmentWorkflowNode, ...]
    edges: tuple[AssessmentWorkflowEdge, ...]


_COMMON_NODES = (
    AssessmentWorkflowNode(node_id=SELECT_TARGET, label="选择知识点"),
    AssessmentWorkflowNode(node_id=GENERATE_QUESTION, label="生成题目"),
    AssessmentWorkflowNode(node_id=VALIDATE_EVIDENCE, label="校验证据"),
)
_TAIL_NODES = (
    AssessmentWorkflowNode(node_id=AWAIT_ANSWER, label="等待作答"),
    AssessmentWorkflowNode(node_id=GRADE_ANSWER, label="判卷"),
    AssessmentWorkflowNode(node_id=COMMIT_LEARNING, label="提交学习事实"),
)


def describe_assessment_workflow(
    variant: AssessmentWorkflowVariant,
) -> AssessmentWorkflowDescriptor:
    """Describe only paths that the current atomic assessment engine can execute."""
    if variant == "open":
        nodes = (*_COMMON_NODES, *_TAIL_NODES)
        edges = (
            AssessmentWorkflowEdge(source=SELECT_TARGET, target=GENERATE_QUESTION),
            AssessmentWorkflowEdge(source=GENERATE_QUESTION, target=VALIDATE_EVIDENCE),
            AssessmentWorkflowEdge(source=VALIDATE_EVIDENCE, target=AWAIT_ANSWER),
            AssessmentWorkflowEdge(source=AWAIT_ANSWER, target=GRADE_ANSWER),
            AssessmentWorkflowEdge(source=GRADE_ANSWER, target=COMMIT_LEARNING),
        )
    else:
        judge = AssessmentWorkflowNode(
            node_id=JUDGE_DISTRACTORS,
            label="评审干扰项",
            optional=True,
        )
        nodes = (*_COMMON_NODES, judge, *_TAIL_NODES)
        edges = (
            AssessmentWorkflowEdge(source=SELECT_TARGET, target=GENERATE_QUESTION),
            AssessmentWorkflowEdge(source=GENERATE_QUESTION, target=VALIDATE_EVIDENCE),
            AssessmentWorkflowEdge(
                source=VALIDATE_EVIDENCE,
                target=JUDGE_DISTRACTORS,
                kind="optional",
            ),
            AssessmentWorkflowEdge(source=VALIDATE_EVIDENCE, target=AWAIT_ANSWER),
            AssessmentWorkflowEdge(source=JUDGE_DISTRACTORS, target=AWAIT_ANSWER),
            AssessmentWorkflowEdge(source=AWAIT_ANSWER, target=GRADE_ANSWER),
            AssessmentWorkflowEdge(source=GRADE_ANSWER, target=COMMIT_LEARNING),
        )
    return AssessmentWorkflowDescriptor(variant=variant, nodes=nodes, edges=edges)
