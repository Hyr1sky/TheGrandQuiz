"""grandquiz CLI 入口——argparse 子命令路由 + 分发。

各子命令的编排 / handler / console 打印按命令分模块（``commands/``），可复用的对象图装配（store /
memory / TraceStore / EventEmitter / Runner / ContextBuilder 接线）剥到 ``composition.py``（CLI
与未来 Web/API 共用同一套装配、不复制粘贴）。本模块只留 ``_build_parser`` + ``main`` 分发 + 向后
兼容 re-export：``run_ingest`` / ``run_quiz`` / ``run_react`` / ``export_trace_html`` /
``_file_source`` 仍可从 ``grandquiz.interfaces.cli.app`` 导入。

CLI 是事件脊柱的消费者：``quiz`` / ``react`` 把 ``QuizEventPrinter`` 订阅到考核事件流做 Rich 呈现、
不另起渲染逻辑（呼应架构卖点）。子命令都用真 ``OpenAICompatProvider.from_env()`` + 持久化 SQLite
（``--db`` 默认 ``~/.grandquiz/learning.db``，自动建父目录；store / memory 同一 db 文件，薄弱点跨
会话留存）。真机交互试跑（``grandquiz quiz`` / ``react`` 的 tty 逐题）留给 human。无子命令 → 帮助。
"""

import argparse
import asyncio
import contextlib
from collections.abc import Sequence
from pathlib import Path

from dotenv import load_dotenv

# 各命令的公开编排（re-export，见 __all__）+ 私有 CLI handler（main 分发调用）。
from grandquiz.interfaces.cli.commands.audit import run_document_dogfood_audit_cli
from grandquiz.interfaces.cli.commands.ingest import _run_ingest_cli, run_ingest
from grandquiz.interfaces.cli.commands.quiz import _run_quiz_cli, run_quiz
from grandquiz.interfaces.cli.commands.react import _run_react_cli, run_react
from grandquiz.interfaces.cli.commands.search import _run_search_cli, run_search
from grandquiz.interfaces.cli.commands.trace import (
    _run_report_cli,
    _run_trace_cli,
    export_trace_html,
)

# 向后兼容 re-export：``_file_source`` 现居 composition，历史上从本模块导入（测试依赖）。
from grandquiz.interfaces.cli.composition import _DEFAULT_DB, _DEFAULT_ROUNDS, _file_source

# 保持 ``grandquiz.interfaces.cli.app`` 的历史导入契约稳定（re-export 公开编排 + _file_source）。
__all__ = [
    "_file_source",
    "build_parser",
    "export_trace_html",
    "main",
    "run_ingest",
    "run_quiz",
    "run_react",
    "run_search",
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="grandquiz", description="考核驱动的个人学习工具")
    sub = parser.add_subparsers(dest="command")

    p_ingest = sub.add_parser("ingest", help="读本地材料 → 深读 → 入库")
    p_ingest.add_argument("material_file", type=Path, help="本地材料文件路径")
    p_ingest.add_argument(
        "--task", required=True, help="本次入库横幅名（仅打印；全局 KB 单池、不分库）"
    )
    p_ingest.add_argument("--db", type=Path, default=_DEFAULT_DB, help="SQLite 库路径")

    p_quiz = sub.add_parser("quiz", help="对全局知识库逐题交互考核")
    p_quiz.add_argument("title", nargs="?", default=None, help="可选横幅（仅打印，不进选题范围）")
    p_quiz.add_argument("--rounds", type=int, default=_DEFAULT_ROUNDS, help="考核轮数")
    p_quiz.add_argument("--db", type=Path, default=_DEFAULT_DB, help="SQLite 库路径")
    p_quiz.add_argument(
        "--prefer-lang",
        default=None,
        help="显式设出题语言偏好（如 英文 / en），跨会话留存并覆盖任务默认语言",
    )

    p_react = sub.add_parser("react", help="真机 ReAct 对话——学材料 / 出题 / 判卷全经工具")
    p_react.add_argument("title", nargs="?", default=None, help="可选横幅（仅打印，不进考核范围）")
    p_react.add_argument("--db", type=Path, default=_DEFAULT_DB, help="SQLite 库路径")
    p_react.add_argument(
        "--materials-dir",
        type=Path,
        default=Path.cwd(),
        help="本地材料目录（ingest 的 file://local/<文件名> 相对此目录解析，默认当前目录）",
    )

    p_search = sub.add_parser(
        "search",
        help="直接搜索学习材料候选（不调用 LLM、不抓取或入库）",
        description="直接搜索学习材料候选（不调用 LLM、不抓取、不执行 Reader 或入库）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""配置：
  TAVILY_API_KEY=tvly-... grandquiz search "MySQL 面试高频考点"
  SEARXNG_URL=http://127.0.0.1:8080 grandquiz search "MySQL 面试高频考点"

同时配置两者时，用 WEB_SEARCH_PROVIDER=tavily|searxng 显式选择。
""",
    )
    p_search.add_argument("query", help="搜索词")
    p_search.add_argument("--limit", type=int, default=5, choices=range(1, 11), help="候选数 1..10")
    p_search.add_argument(
        "--domain",
        dest="domains",
        action="append",
        default=[],
        help="只保留指定域名，可重复（如 --domain github.com）",
    )
    p_search.add_argument(
        "--trace-db",
        type=Path,
        default=None,
        help="独立 trace 库路径（默认 ~/.grandquiz/trace.db）",
    )

    p_report = sub.add_parser(
        "report",
        help="跑 eval harness → 导出自包含 HTML 报告",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""示例：
  grandquiz report
  open ~/.grandquiz/eval-report/index.html
  grandquiz report --out ./eval-report && open ./eval-report/index.html

默认只使用 Replay cassette，零网络、不会从 .env 隐式调用真实 judge。
默认首页：~/.grandquiz/eval-report/index.html
""",
    )
    p_report.add_argument(
        "--out", type=Path, default=None, help="报告输出目录（默认 ~/.grandquiz/eval-report）"
    )

    p_trace = sub.add_parser(
        "trace",
        help="按 trace_id 从 trace 库导出自包含 HTML",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""示例：
  grandquiz trace <trace_id> --db ~/.grandquiz/learning.db
  grandquiz trace <trace_id> --trace-db ~/.grandquiz/trace.db
  open ~/.grandquiz/trace-<trace_id>.html

默认 trace DB：learning.db 同目录的 trace.db
默认输出：trace DB 同目录的 trace-<trace_id>.html
""",
    )
    p_trace.add_argument("trace_id", help="要导出的会话 trace_id（会话结束时打印过）")
    p_trace.add_argument(
        "--db", type=Path, default=_DEFAULT_DB, help="learning 库路径（派生默认 trace 库位置）"
    )
    p_trace.add_argument(
        "--trace-db", type=Path, default=None, help="独立 trace 库路径（默认同目录 trace.db）"
    )
    p_trace.add_argument(
        "--out", type=Path, default=None, help="输出 HTML 文件（默认 trace-<trace_id>.html）"
    )

    p_audit = sub.add_parser("audit-doc", help="只读核验文档结构 dogfood 的 trace/DB 证据")
    p_audit.add_argument("--ingest-trace", required=True, help="真实 HITL ingest 的 trace_id")
    p_audit.add_argument("--search-trace", required=True, help="开放搜索/citation 的 trace_id")
    p_audit.add_argument("--db", type=Path, default=_DEFAULT_DB, help="SQLite learning 库路径")
    p_audit.add_argument(
        "--trace-db", type=Path, default=None, help="独立 trace 库路径（默认同目录 trace.db）"
    )
    p_audit.add_argument(
        "--max-read-fraction",
        type=float,
        default=0.25,
        help="允许读取的 revision 正文比例上限（默认 0.25）",
    )

    return parser


def main(argv: Sequence[str] | None = None) -> None:
    """子命令路由入口（``[project.scripts] grandquiz``）。无子命令 → 打印帮助。

    启动即 ``load_dotenv()``（从 cwd 向上找 ``.env``）：让 ``grandquiz`` 在仓库里开箱可用、
    无需每次 ``--env-file``；已在环境里的变量不覆盖（``uv run --env-file .env`` 仍兼容）。
    """
    load_dotenv()
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "ingest":
        with contextlib.suppress(KeyboardInterrupt):
            asyncio.run(
                _run_ingest_cli(title=args.task, material_path=args.material_file, db_path=args.db)
            )
    elif args.command == "quiz":
        with contextlib.suppress(KeyboardInterrupt):
            asyncio.run(
                _run_quiz_cli(
                    title=args.title,
                    rounds=args.rounds,
                    db_path=args.db,
                    prefer_lang=args.prefer_lang,
                )
            )
    elif args.command == "react":
        with contextlib.suppress(KeyboardInterrupt):
            asyncio.run(
                _run_react_cli(title=args.title, db_path=args.db, materials_dir=args.materials_dir)
            )
    elif args.command == "search":
        _run_search_cli(
            query=args.query,
            limit=args.limit,
            domains=tuple(args.domains),
            trace_db_path=args.trace_db,
        )
    elif args.command == "report":
        _run_report_cli(out=args.out)
    elif args.command == "trace":
        _run_trace_cli(
            trace_id=args.trace_id,
            db_path=args.db,
            trace_db_path=args.trace_db,
            out_path=args.out,
        )
    elif args.command == "audit-doc":
        run_document_dogfood_audit_cli(
            db_path=args.db,
            trace_db_path=args.trace_db,
            ingest_trace_id=args.ingest_trace,
            search_trace_id=args.search_trace,
            max_read_fraction=args.max_read_fraction,
        )
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
