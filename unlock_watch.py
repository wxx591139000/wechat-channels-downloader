"""解锁侦测器：前台进程不再是 LockApp 即视为已解锁，打印 UNLOCKED 后退出。
用于在锁屏解除后自动继续 GUI 点击流程。上限 10 分钟。"""
import ctypes
import subprocess
import time

u32 = ctypes.windll.user32


def fg_proc_name():
    h = u32.GetForegroundWindow()
    pid = ctypes.c_ulong()
    u32.GetWindowThreadProcessId(h, ctypes.byref(pid))
    out = subprocess.check_output(
        ["tasklist", "/FI", "PID eq %d" % pid.value, "/FO", "CSV", "/NH"],
        text=True, timeout=10).strip()
    if not out:
        return ""
    return out.split(",")[0].strip('"')


def main():
    start = time.time()
    log = open("C:/Users/Dancing/wechat-channels-downloader/sphdl/unlock_watch.log",
               "a", encoding="utf-8")

    def emit(m):
        line = "[%s] %s" % (time.strftime("%H:%M:%S"), m)
        print(line, flush=True)
        log.write(line + "\n")
        log.flush()

    emit("unlock watch started")
    unlocked = False
    while time.time() - start < 600:
        try:
            name = fg_proc_name()
        except Exception as e:
            name = "err:%s" % e
        if name and "lockapp" not in name.lower():
            emit("UNLOCKED  foreground=%s" % name)
            unlocked = True
            break
        time.sleep(2)
    if not unlocked:
        emit("timeout 600s still locked")
    emit("unlock watch ended")
    log.close()


if __name__ == "__main__":
    main()
