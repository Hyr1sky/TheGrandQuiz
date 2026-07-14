"""考核引擎：选题（selection）→ 题型路由（routing）→ 出题（question）→ 判卷（grading）→
记账，由 ``engine.py`` 的 ``assess_once`` 编排成单题确定性 workflow（ADR-0004）。

不在此处 re-export：``memory.py``（top-level）需要 ``grading.py`` 的 ``VerdictLabel``，若本
``__init__`` 在包导入时就拉起 ``engine.py``（进而 import ``memory.py``），两者会互相等对方
先初始化完成，构成循环 import。故本包保持空壳，调用方一律走精确子模块路径，例如
``from grandquiz.domain.learning.assessment.engine import assess_once, AssessmentResult``。
"""
