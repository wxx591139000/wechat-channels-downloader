"""截图：抖音窗口「恢复上次清单」后的样子（v3.1.5）。

验证点：
  · 重开工具后清单不是空的（客户核心抱怨）
  · 已下载的显示「✅ 已完成」且不勾选；失败的标出来
  · 长标题单行显示不裁切、列宽合理

★ 清单写到临时文件（patch douyin_store.STORE_FILE），
  绝不碰开发机上真实的 douyin_records.json。
"""
import ctypes
import queue
import sys
import tempfile
import time
import tkinter as tk
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import sphdl.douyin_store as STORE                 # noqa: E402
import sphdl.douyin_ui as DYUI                     # noqa: E402
from sphdl.douyin import DouyinVideo               # noqa: E402
from tests.shot_window import capture              # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="shot_dy_"))
STORE.STORE_FILE = TMP / "douyin_records.json"

OUT_DIR = TMP / "抖音"
OUT_DIR.mkdir(parents=True, exist_ok=True)
done_file = OUT_DIR / "沉浸式打乒乓球！第一视角看对错_7600000000000000001.mp4"
done_file.write_bytes(b"\0" * 4096)

TITLES = [
    ("33天城市生存攒钱挑战今天是第13天", "城市生存", 1896653, True),
    ("沉浸式打乒乓球！第一视角看对错？用 Osmo Nano 记录打球日常，超轻巧机身", "乒乓日常", 44080000, True),
    ("羞答答的玫瑰轰隆隆地开🤣#杭州宋城 #宋城千古情 #千古情奇妙夜", "宋城千古情", 12000000, False),
    ("凌晨四点的早餐摊，一天只卖 200 碗", "深夜食堂", 8300000, False),
    ("他舅说这个生日是不是太寒酸了，他说太寒酸了", "家庭日常", 5200000, False),
    ("这条上次下载失败了（链接过期）", "其它", 3000000, False),
]

rows = []
for i, (title, author, size, done) in enumerate(TITLES):
    v = DouyinVideo(aweme_id=f"76000000000000000{i:02d}", title=title,
                    author=author, duration_ms=(i + 1) * 45000,
                    play_url=f"https://v26-webf.douyinvod.com/x/{i}.mp4",
                    size=size)
    rows.append(STORE.video_to_entry(
        v, status="done" if done else ("failed" if i == 5 else "pending"),
        file=str(done_file) if done else ""))
STORE.save(rows)


class FakeApp:
    def __init__(self, root):
        self.root = root
        self.dir_var = tk.StringVar(master=root, value=str(TMP))
        self.ui_q = queue.Queue()

    def push_log(self, *a):
        pass


root = tk.Tk()
root.withdraw()
w = DYUI.DouyinWindow(FakeApp(root))
w.win.deiconify()
w.win.update()
time.sleep(1.2)
w.win.update()

u = ctypes.windll.user32
top = u.GetAncestor(w.win.winfo_id(), 2) or w.win.winfo_id()
out = ROOT / "docs/screenshots/v315-douyin-restore.png"
size = capture(top, out)
print("✅ 抖音窗口（恢复清单后）", size, "→", out)
w.win.destroy()
root.destroy()
