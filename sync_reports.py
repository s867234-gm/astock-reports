# -*- coding: utf-8 -*-
"""
每日复盘 / 选股报告 同步脚本
--------------------------------
将 D:\选股报告存档\<类别>\ 下的 HTML 报告 增量镜像 到
D:\报告同步\astock-reports\<类别>\ ，重建 index.html 落地页，
并推送到 GitHub(github) 与 Gitee(origin) 两个 remote。

规则：
- 仅做增量拷贝（不删除仓库中已有的其他报告，如历史模拟），保证历史不丢。
- 仅当确有内容变化（新增/修改报告、新增脚本等，排除 index.html 时间戳自动刷新）才提交。
- 双平台分别推送；某平台网络失败不影响另一个。

用法：python sync_reports.py
"""
import os, shutil, subprocess, datetime

SRC = r"D:\选股报告存档"
DST = r"D:\报告同步\astock-reports"
REPO = DST


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


def git(*args):
    r = subprocess.run(["git", "-C", REPO, *args], capture_output=True, text=True)
    return r.returncode, (r.stdout or "").strip(), (r.stderr or "").strip()


def main():
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
        return

    git("add", "-A")
    ts = datetime.datetime.now().strftime("%Y%m%d %H%M")
    rc, out, err = git("commit", "-m", f"auto sync reports {ts}")
    print(f"[sync] commit rc={rc} {out} {err}")

    rc, branch, _ = git("rev-parse", "--abbrev-ref", "HEAD")
    branch = branch.strip() or "master"
    for remote in ("github", "origin"):
        rc, out, err = git("push", remote, branch)
        head = (out or err or "").replace("\n", " ")[:200]
        print(f"[sync] push {remote} rc={rc} {head}")


if __name__ == "__main__":
    main()
