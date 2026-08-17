"""Versioned semantic-quality rubric registry, independent from judge execution."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Criterion:
    criterion_id: str
    description: str
    pass_score: int = 3


@dataclass(frozen=True)
class Rubric:
    rubric_id: str
    version: str
    criteria: tuple[Criterion, ...]


_RUBRICS = {
    "grounded_answer": Rubric(
        rubric_id="grounded_answer",
        version="grounded_answer@v1",
        criteria=(
            Criterion(
                "semantic_support",
                "candidate 的实质结论是否被 reference 充分支持",
            ),
            Criterion(
                "question_coverage",
                "candidate 是否直接覆盖 question 的主要要求",
            ),
            Criterion(
                "learning_usefulness",
                "candidate 是否清晰、准确且适合作为学习解释",
            ),
        ),
    ),
    "question_quality": Rubric(
        rubric_id="question_quality",
        version="question_quality@v1",
        criteria=(
            Criterion(
                "evidence_support",
                "只判断 source reference 是否包含足以回答 learner_visible 题干的事实；"
                "grader_only 接受的答案是否完整不属于本维度。只要 source 给出完整事实即为 4，"
                "即使 grader rubric 太宽、题干泄露或选项多解也不得重复扣分；source 没有所问"
                "事实才为 1",
            ),
            Criterion(
                "demand_alignment",
                "只判断题目措辞、目标概念与回答形式是否匹配学习需求：精确匹配为 4，"
                "相关但因泄露或歧义弱化为 3；若只问宽泛类别、没有要求 source 中的"
                "定义性不变量，或目标脱离材料学习目标，则为 2。grader_only 的答案声明"
                "不是事实来源；选择题与 source 主题相关但存在多个 source-supported 选项时"
                "本维度为 3，选项多解的严重性只在 response_design 记为 1",
            ),
            Criterion(
                "answer_leakage",
                "只判断题干是否泄露答案：完全无泄露为 4，直接说出正确答案为 1；"
                "不要把泄露缺陷重复计入 evidence_support",
            ),
            Criterion(
                "response_design",
                "只判断作答机制是否可用：唯一可辨的选择题或完整开放题 rubric 为 4，"
                "开放题只接受上位类别而遗漏 source 中的定义性区别时为 2；答案已泄露、"
                "材料不支持或存在多个正确选项时为 1。选择题必须把每个选项逐项对照"
                "source；即使 grader_only 只标一个答案，只要 source 支持多个选项仍为 1",
            ),
            Criterion(
                "learning_usefulness",
                "只判断回答能否产生有意义且可解释的学习证据：精确区分理解为 4，"
                "只证明知道上位类别、没有检验 source 中的关键不变量为 2；泄露、无证据"
                "或多个选项被 source 支持的歧义使结果无诊断价值为 1",
            ),
        ),
    ),
    "reader_fidelity": Rubric(
        rubric_id="reader_fidelity",
        version="reader_fidelity@v1",
        criteria=(
            Criterion(
                "source_fidelity",
                "KnowledgeItem 的每项实质陈述是否由 source 逐项支持；臆造或把练习要求"
                "改写成事实为低分",
            ),
            Criterion(
                "key_concept_coverage",
                "候选是否覆盖该 source slice 的关键概念与定义性不变量，而非只保留次要细节",
            ),
            Criterion(
                "concept_separation",
                "概念是否原子且不与同批候选重复；重复、错误合并或无意义拆分为低分",
            ),
            Criterion(
                "evidence_locality",
                "Evidence 是否来自支持该陈述所需的全部 DocumentNode；跨节点陈述必须保留"
                "每个必要节点",
            ),
            Criterion(
                "learning_usefulness",
                "候选是否适合作为可考核知识点；练习指令、目录、版权和样板文字等伪知识点为低分",
            ),
        ),
    ),
}


def get_rubric(rubric_id: str) -> Rubric | None:
    """Return one closed, code-reviewed rubric definition."""

    return _RUBRICS.get(rubric_id)
