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
        version="grounded_answer@v2",
        criteria=(
            Criterion(
                "semantic_support",
                "只判断 candidate 实际陈述的实质主张是否被 reference 支持，不因遗漏问题的"
                "其他部分扣分：所有已陈述主张受支持即为 4。若 candidate 准确说明 reference"
                "没有提供某项事实，并据此拒绝下结论，这种有依据的材料边界判断也为 4",
            ),
            Criterion(
                "question_coverage",
                "只判断 candidate 是否直接覆盖 question 的主要要求：完整回答所有子问题为 4，"
                "只回答其中一部分为 2。对于 reference 未提供所问事实的问题，明确说明材料"
                "沉默并拒绝猜测就是完整回答，应为 4",
            ),
            Criterion(
                "learning_usefulness",
                "判断 candidate 是否清晰、准确且有诊断价值：完整解释或有依据地拒绝材料外"
                "推断为 4；虽准确但遗漏定义性关系、使学习者只得到部分概念时为 2；不要因"
                "回答简短而降低一个已经完整说明证据边界的合理拒答",
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
        version="reader_fidelity@v3",
        criteria=(
            Criterion(
                "source_fidelity",
                "只判断 KnowledgeItem 的每项实质陈述是否由 source 逐项支持：全部陈述受"
                "支持即为 4，即使候选遗漏关键概念、与同批候选重复或学习价值较低也不得"
                "在本维度重复扣分；必须保持原句模态，祈使句、思考题、要求或建议不能支持"
                "“系统已经采用/使用该方案”的事实断言，例如把“请设计 X”改写成“系统使用 X”"
                "属于臆造并记为 1",
            ),
            Criterion(
                "key_concept_coverage",
                "只判断候选是否覆盖 source slice 的关键概念与定义性不变量：完整覆盖为 4，"
                "遗漏定义性关系但保留主体定义为 2，把练习要求抽成事实且未保留任何事实"
                "不变量为 1；重复和 Evidence 定位问题不得在本维度重复扣分",
            ),
            Criterion(
                "concept_separation",
                "只判断概念是否原子且不与同批候选重复：原子且唯一为 4，即使内容遗漏或"
                "Evidence 有问题也不得连带扣分；同一概念重复抽取为 1；练习指令等并非有效"
                "知识概念但形状仍原子时为 2",
            ),
            Criterion(
                "evidence_locality",
                "只判断 Evidence 是否精确来自候选所依据的全部 DocumentNode，不判断该"
                "Evidence 是否在语义上蕴含候选陈述：单节点引用正确为 4，练习节点被错误"
                "解释成事实但仍精确引用该练习节点也为 4；跨节点陈述缺任一必要节点才扣分",
            ),
            Criterion(
                "learning_usefulness",
                "综合判断候选能否形成有诊断价值的可考核单元：完整、唯一且有效为 4；遗漏"
                "定义性不变量或同批重复使诊断价值降为 2；练习指令、目录、版权和样板文字"
                "等伪知识点为 1",
            ),
        ),
    ),
}


def get_rubric(rubric_id: str) -> Rubric | None:
    """Return one closed, code-reviewed rubric definition."""

    return _RUBRICS.get(rubric_id)
