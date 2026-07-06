"""``python -m grandquiz.evals``——跑全部规则用例、打印报告、以退出码反映全绿与否。"""

import sys

from grandquiz.evals.harness import main

if __name__ == "__main__":
    sys.exit(main())
