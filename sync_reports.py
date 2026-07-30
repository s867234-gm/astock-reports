# -*- coding: utf-8 -*-
"""
每日复盘 / 选股报告 同步脚本
--------------------------------
将 D:/选股报告存档/<类别>/ 下的 HTML 报告 增量镜像 到
D:/报告同步/astock-reports/<类别>/ ，重建 index.html 落地页，
并推送到 GitHub(github) 与 Gitee(origin) 两个 remote。

规则：
- 仅做增量拷贝（不删除仓库中已有的其他报告，如历史模拟），保证历史不丢。
- 仅当确有内容变化（新增/修改报告、新增脚本等，排除 index.html 时间戳自动刷新）才提交。
- 双平台分别推送；某平台网络失败不影响另一个。

用法：python sync_reports.py
"""
import os, shutil, subprocess, datetime, sys, socket

SRC = r"D:\选股报告存档"
DST = r"D:\报告同步\astock-reports"
REPO = DST

# 固定使用「系统 Git for Windows」(C:\Program Files\Git)，其 system 级
# credential.helper 已设为 manager，可避免 WorkBuddy 自带 PortableGit 的
# helper-selector 每次弹出凭据助手选择框。若系统版不存在则退回 PATH 中的 git。
_CAND = [r"C:\Program Files\Git\cmd\git.exe",
         r"C:\Program Files (x86)\Git\cmd\git.exe"]
GIT = next((c for c in _CAND if os.path.isfile(c)), "git")


def copy_category(src_cat, dst_cat):
    """递归拷贝 src_cat 下所有 .html 到 dst_cat，返回新增/覆盖数量。"""
    if not os.path.isdir(src_cat):
        return 0
    n = 0
    for root, _dirs, files in os.walk(src_cat):
        for f in files:
            if f.lower().endswith(".html"):
                s = os.path.join(root, f)
                rel = os.path.relpath(s, src_cat)
                d = os.path.join(dst_cat, rel)
                os.makedirs(os.path.dirname(d), exist_ok=True)
                shutil.copy2(s, d)
                n += 1
    return n


def build_index():
    """扫描 DST 下各「类别子目录」，重建 index.html。"""
    cats = []
    for name in sorted(os.listdir(DST)):
        p = os.path.join(DST, name)
        if not os.path.isdir(p) or name in (".git",):
            continue
        items = []
        for root, _dirs, files in os.walk(p):
            for f in files:
                if not f.lower().endswith(".html"):
                    continue
                full = os.path.join(root, f)
                if os.path.abspath(full) == os.path.abspath(os.path.join(DST, "index.html")):
                    continue
                rel = os.path.relpath(full, DST).replace("\\", "/")
                disp = os.path.relpath(full, DST).replace("/", "\\")
                items.append((os.path.getmtime(full), rel, disp))
        if items:
            items.sort(reverse=True)
            cats.append((name, items))

    total = sum(len(it) for _, it in cats)
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    style = (
        "body{font-family:-apple-system,Segoe UI,Roboto,sans-serif;margin:0;background:#f5f6f8;color:#222;}"
        ".wrap{max-width:860px;margin:0 auto;padding:16px;} h1{font-size:20px;} h2{font-size:16px;margin-top:24px;color:#185fa5;}"
        ".card{background:#fff;border-radius:10px;padding:10px 14px;margin:8px 0;box-shadow:0 1px 3px rgba(0,0,0,.08);}"
        "a{color:#185fa5;text-decoration:none;font-size:15px;} .t{color:#888;font-size:12px;margin-left:8px;} .empty{color:#999;}"
        "@media(max-width:600px){.wrap{padding:10px;} a{font-size:14px;}}"
    )
    html = ["<!doctype html>",
            "<html lang='zh-CN'><head><meta charset='utf-8'>",
            "<meta name='viewport' content='width=device-width,initial-scale=1'>",
            "<title>股票分析报告索引</title>",
            f"<style>{style}</style>",
            "</head><body><div class='wrap'>",
            "<h1>股票分析报告索引</h1>",
            f"<p class='t'>生成于 {now} ｜ 共 {total} 个报告</p>"]
    for name, items in cats:
        html.append(f"<h2>{name}</h2>")
        for mt, rel, disp in items:
            ts = datetime.datetime.fromtimestamp(mt).strftime("%Y-%m-%d %H:%M")
            html.append(f"<div class='card'><a href='{rel}'>{disp}</a><span class='t'>{ts}</span></div>")
    html.append("</div></body></html>")
    with open(os.path.join(DST, "index.html"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(html))


def clear_repo_proxy():
    """清除仓库级 http.proxy 与代理环境变量，确保 git 直连（避免死代理拖垮推送）。"""
    subprocess.run([GIT, "-C", REPO, "config", "--unset", "http.proxy"],
                   capture_output=True, text=True)
    for v in ("HTTP_PROXY", "HTTPS_PROXY", "GIT_HTTP_PROXY", "GIT_HTTPS_PROXY",
              "http_proxy", "https_proxy"):
        os.environ.pop(v, None)


def apply_proxy():
    """智能代理：仅当系统代理启用且端口确实可达时才走代理；
    否则清除仓库级残留代理，让 git 直连。git 默认不读系统代理，且仓库级
    http.proxy 会强制覆盖直连，故需显式管理，防止死代理把所有平台都拖垮。"""
    if sys.platform != "win32":
        clear_repo_proxy()
        return
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                             r"Software\Microsoft\Windows\CurrentVersion\Internet Settings")
        enabled, _ = winreg.QueryValueEx(key, "ProxyEnable")
        server, _ = winreg.QueryValueEx(key, "ProxyServer")
    except Exception:
        clear_repo_proxy()
        return
    if not enabled or not server:
        clear_repo_proxy()
        return
    s = str(server).strip()
    if "=" in s:  # 形如 http=host:port;https=host:port
        parts = dict(kv.split("=", 1) for kv in s.split(";") if "=" in kv)
        addr = parts.get("https=") or parts.get("http=") or next(iter(parts.values()), "")
    else:  # 形如 host:port
        addr = s
    addr = addr.strip()
    if not addr:
        clear_repo_proxy()
        return
    # 探测端口是否真正可达，避免把直连请求打到已死的代理
    host, _, port = addr.partition(":")
    port = int(port) if port.isdigit() else 80
    sock = socket.socket(); sock.settimeout(2)
    try:
        sock.connect((host, port)); reachable = True
    except Exception:
        reachable = False
    finally:
        sock.close()
    if not reachable:
        print(f"[sync] 系统代理 {addr} 不可达，改用直连（已清除仓库级代理）")
        clear_repo_proxy()
        return
    proxy = addr if "://" in addr else f"http://{addr}"
    for v in ("HTTP_PROXY", "HTTPS_PROXY", "GIT_HTTP_PROXY", "GIT_HTTPS_PROXY"):
        os.environ[v] = proxy
    print(f"[sync] 启用系统代理推送: {proxy}")


def git(*args):
    r = subprocess.run([GIT, "-C", REPO, *args], capture_output=True, text=True)
    return r.returncode, (r.stdout or "").strip(), (r.stderr or "").strip()


def push_all():
    """无条件推送到两个 remote（幂等）。即使本次无新提交，也能把
    本地领先远端、之前因网络失败而积压的提交补推上去。"""
    rc, branch, _ = git("rev-parse", "--abbrev-ref", "HEAD")
    branch = branch.strip() or "master"
    for remote in ("github", "origin"):
        rc, out, err = git("push", remote, branch)
        head = (out or err or "").replace("\n", " ")[:200]
        print(f"[sync] push {remote} rc={rc} {head}")


def main():
    apply_proxy()
    copied = 0
    if os.path.isdir(SRC):
        for name in sorted(os.listdir(SRC)):
            sp = os.path.join(SRC, name)
            if os.path.isdir(sp):
                copied += copy_category(sp, os.path.join(DST, name))
    build_index()
    print(f"[sync] 镜像新增/覆盖 HTML：{copied} 个")

    rc, out, _ = git("status", "--porcelain")
    lines = [ln for ln in out.splitlines() if "index.html" not in ln]
    if not lines:
        # 仅 index.html 时间戳刷新，无实质内容变化 -> 丢弃，保持工作树干净
        git("checkout", "--", "index.html")
        print("[sync] 无内容变化，跳过提交")
    else:
        git("add", "-A")
        ts = datetime.datetime.now().strftime("%Y%m%d %H%M")
        rc, out, err = git("commit", "-m", f"auto sync reports {ts}")
        print(f"[sync] commit rc={rc} {out} {err}")

    # 无论是否有新提交，都尝试推送（补推之前因网络失败积压的提交）
    push_all()


if __name__ == "__main__":
    main()
