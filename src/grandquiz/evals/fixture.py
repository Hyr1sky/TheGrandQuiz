"""Eval solver 与规则 grader 共享的确定性 fixture 数据。"""

import json

MC_CORRECT = "正确选项"
MC_WRONG = "干扰项"

INGEST_RAW_CONTENT = "React hooks 深读材料：q1、q2、q3"
READER_JSON = json.dumps(
    {
        "topic": "JavaScript 核心机制",
        "candidates": [
            {
                "concept": "闭包",
                "summary": "s1",
                "evidence": [
                    {
                        "node_key": "n000001",
                        "start_offset": INGEST_RAW_CONTENT.index("q1"),
                        "end_offset": INGEST_RAW_CONTENT.index("q1") + 2,
                        "quote": "q1",
                    }
                ],
                "confidence": 0.9,
            },
            {
                "concept": "变量提升",
                "summary": "s2",
                "evidence": [
                    {
                        "node_key": "n000001",
                        "start_offset": INGEST_RAW_CONTENT.index("q2"),
                        "end_offset": INGEST_RAW_CONTENT.index("q2") + 2,
                        "quote": "q2",
                    }
                ],
                "confidence": 0.8,
            },
            {
                "concept": "事件循环",
                "summary": "s3",
                "evidence": [
                    {
                        "node_key": "n000001",
                        "start_offset": INGEST_RAW_CONTENT.index("q3"),
                        "end_offset": INGEST_RAW_CONTENT.index("q3") + 2,
                        "quote": "q3",
                    }
                ],
                "confidence": 0.7,
            },
        ],
    },
    ensure_ascii=False,
)
INGEST_APPROVED_CONCEPTS = ["闭包", "事件循环"]
INGEST_CANDIDATE_COUNT = 3
