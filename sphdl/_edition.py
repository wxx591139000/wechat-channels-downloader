"""构建期档位配置（默认：不锁档、默认档 500）。

`release.py` 在构建前按需改写本文件，构建结束自动还原——仓库里永远是下面
这两行的默认值，实际档位只存在于对应构建产物里。

语义（v2.0.17 定稿：4 档全部为确定数值，**没有「不限量」**）：
  LOCKED_MAX_RECORDS = None   → 不锁档：档位由客户输入的**授权码**决定
  LOCKED_MAX_RECORDS = 500    → 档位写死 500（轻量版；界面不显示授权入口）
  LOCKED_MAX_RECORDS = 1000   → 档位写死 1000（标准版）
  LOCKED_MAX_RECORDS = 2000   → 档位写死 2000（专业版）
  LOCKED_MAX_RECORDS = 20000  → 档位写死 20000（旗舰版，实测容量上限）
  DEFAULT_MAX_RECORDS_OVERRIDE = None → 未授权默认档取 config.DEFAULT_MAX_RECORDS(500)
  DEFAULT_MAX_RECORDS_OVERRIDE = 100  → 未授权默认档 100（如试用版）
"""

LOCKED_MAX_RECORDS = None
DEFAULT_MAX_RECORDS_OVERRIDE = None
