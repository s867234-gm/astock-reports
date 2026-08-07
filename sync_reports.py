# -*- coding: utf-8 -*-
"""
每日复盘 / 选股报告 同步脚本
--------------------------------
将 D:/选股报告存档/<类别>/ 下的 HTML 报告 增量镜像 到
D:/报告同步/astock-reports/<类别>/ ，重建 index.html 落地页，
并推送到 GitHub(github) 与 Gitee(origin) 两个 remote。

=== 整站密码保护（方案 A：AES-256-GCM 客户端解密）===
- 仓库里的 HTML 在提交前被加密为「解密门页」；浏览器端输入密码后用
  原生 Web Crypto(AES-GCM + PBKDF2) 本地解密，密码从不上传。
- 主密码绝不写进会被 GitHub 公开的仓库文件：优先读环境变量 ASTOCK_SITE_PASS，
  否则读本机文件 D:/报告同步/site_pass.txt（位于仓库父目录，不会被提交）。
- 提示文字(SITE_HINT)解密页公开可见，仅作本人提醒，勿写泄露密码的内容。
- 本地存档 D:/选股报告存档/ 始终保持明文（双击可看）；仅仓库副本加密。

规则：
- 仅做增量拷贝（不删除仓库中已有的其他报告，如历史模拟），保证历史不丢。
- 仅当确有内容变化（新增/修改报告、新增脚本等，排除 index.html 时间戳自动刷新）才提交。
- 双平台分别推送；某平台网络失败不影响另一个。

用法：python sync_reports.py
"""
import os, shutil, subprocess, datetime, sys, socket, re, time, urllib.request, urllib.error
import base64, json, hashlib

# ---- 整站密码加密依赖 ----
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

SRC = r"D:\选股报告存档"
DST = r"D:\报告同步\astock-reports"
REPO = DST

# ===== 整站密码保护配置 =====
SITE_HINT = "江生日"                      # 解密页提示（公开可见，仅本人可懂）
SITE_PASS_ENV = "ASTOCK_SITE_PASS"        # 优先：环境变量
SITE_PASS_FILE = r"D:\报告同步\site_pass.txt"  # 兜底：本机文件（仓库外，不提交）
ENC_CACHE = os.path.join(REPO, ".enc_cache.json")  # 本地缓存(gitignore)：跳过未变更文件，避免无意义提交
PBKDF2_ITERS = 200000

# 固定使用「系统 Git for Windows」(C:\Program Files\Git)，其 system 级
# credential.helper 已设为 manager，可避免 WorkBuddy 自带 PortableGit 的
# helper-selector 每次弹出凭据助手选择框。若系统版不存在则退回 PATH 中的 git。
_CAND = [r"C:\Program Files\Git\cmd\git.exe",
         r"C:\Program Files (x86)\Git\cmd\git.exe"]
GIT = next((c for c in _CAND if os.path.isfile(c)), "git")


# index.html 落地页统一由 gitee-sync-tools/gen_index.py 生成（深色紧凑、与每月复盘风格一致）。
# 单点维护，避免和 gitee-sync-tools/gen_index.py 分叉导致两套样式互覆盖。
# build_index() 直接委托给它（见下方 build_index）。


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
    """重建 index.html：委托给 gitee-sync-tools/gen_index.py（深色紧凑、与每月复盘一致），单点维护。"""
    tools = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "gitee-sync-tools"))
    if tools not in sys.path:
        sys.path.insert(0, tools)
    import gen_index as _gi
    _gi.build_index(DST)


# ===== 整站密码加密：解密门页模板 + 加解密函数 =====
GATE_TEMPLATE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>私密报告 · 需要密码</title>
<style>
  :root{--bg:#181f2c;--card:#222b3c;--ink:#e8e2d4;--muted:#9aa3b2;--accent:#3a2e1a;--line:#3b4a66;}
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--ink);font-family:-apple-system,"Microsoft YaHei",sans-serif;display:flex;min-height:100vh;align-items:center;justify-content:center;padding:20px}
  .gate{background:var(--card);border:1px solid var(--line);border-radius:14px;max-width:420px;width:100%;padding:28px 24px;text-align:center}
  h1{margin:0 0 6px;font-size:22px}
  .hint{color:var(--muted);font-size:14px;margin:0 0 18px}
  input[type=password]{width:100%;padding:12px 14px;font-size:16px;border-radius:10px;border:1px solid var(--line);background:#1a2230;color:var(--ink);outline:none}
  button{margin-top:14px;width:100%;padding:12px;border:none;border-radius:10px;background:var(--accent);color:#f3ead6;font-size:16px;cursor:pointer}
  button:disabled{opacity:.5;cursor:not-allowed}
  .row{display:flex;align-items:center;gap:8px;margin-top:14px;color:var(--muted);font-size:13px;justify-content:center}
  .err{color:#e07a6f;font-size:13px;min-height:18px;margin:10px 0 0;white-space:pre-line}
  .loading{color:var(--muted);font-size:14px;margin-top:12px}
</style>
</head>
<body>
<div class="gate">
  <h1>私密报告</h1>
  <p class="hint">提示：__HINT__</p>
  <input id="pw" type="password" placeholder="请输入密码" autocomplete="off" autofocus>
  <button id="go">解锁</button>
  <div class="row"><input type="checkbox" id="rm"> 记住密码（仅本机浏览器）</div>
  <div id="err" class="err"></div>
  <div id="loading" class="loading" style="display:none">解密中…</div>
</div>
<script>
const PAYLOAD = __PAYLOAD__;
function b64dec(s){return Uint8Array.from(atob(s),function(c){return c.charCodeAt(0);});}
function navigate(html){document.open();document.write(html);document.close();}
async function deriveKey(pw, salt){
  var km = await crypto.subtle.importKey('raw', new TextEncoder().encode(pw), 'PBKDF2', false, ['deriveKey']);
  return crypto.subtle.deriveKey({name:'PBKDF2', salt:salt, iterations:__ITERS__, hash:'SHA-256'}, km, {name:'AES-GCM', length:256}, false, ['decrypt']);
}
async function doDecrypt(pw){
  var salt=b64dec(PAYLOAD.s), nonce=b64dec(PAYLOAD.n), ct=b64dec(PAYLOAD.c);
  var key=await deriveKey(pw, salt);
  var plain=await crypto.subtle.decrypt({name:'AES-GCM', iv:nonce}, key, ct);
  return new TextDecoder().decode(plain);
}
function setSession(pw){
  sessionStorage.setItem('astock_pw', pw);
  if(document.getElementById('rm').checked) localStorage.setItem('astock_pw', pw);
}
function showErr(m){document.getElementById('err').textContent=m;document.getElementById('loading').style.display='none';}
function unlock(pw){
  document.getElementById('loading').style.display='block';
  document.getElementById('err').textContent='';
  doDecrypt(pw).then(function(html){ setSession(pw); navigate(html); }).catch(function(){ showErr('密码错误，请重试'); });
}
window.addEventListener('DOMContentLoaded', function(){
  if(!window.crypto || !crypto.subtle){
    document.getElementById('err').textContent='请在 GitHub / Gitee Pages 的 HTTPS 网址打开；本地双击文件（file://）无法解密。';
    document.getElementById('loading').style.display='none';
    document.getElementById('go').disabled=true;
    return;
  }
  var pwInput=document.getElementById('pw'), go=document.getElementById('go');
  function bindManual(){
    go.addEventListener('click', function(){ unlock(pwInput.value); });
    pwInput.addEventListener('keydown', function(e){ if(e.key==='Enter') unlock(pwInput.value); });
  }
  var cached = sessionStorage.getItem('astock_pw') || localStorage.getItem('astock_pw');
  if(cached){
    pwInput.value=cached;
    document.getElementById('rm').checked = !!localStorage.getItem('astock_pw');
    document.getElementById('loading').style.display='block';
    doDecrypt(cached).then(function(html){ setSession(cached); navigate(html); }).catch(function(){
      sessionStorage.removeItem('astock_pw'); localStorage.removeItem('astock_pw');
      document.getElementById('loading').style.display='none';
      document.getElementById('err').textContent='已保存的密码无效，请重新输入';
      pwInput.value='';
      bindManual();
    });
    return;
  }
  bindManual();
});
</script>
</body>
</html>"""


def load_site_password():
    env = os.environ.get(SITE_PASS_ENV)
    if env:
        return env
    if os.path.isfile(SITE_PASS_FILE):
        with open(SITE_PASS_FILE, encoding="utf-8") as f:
            pw = f.read().strip()
        if pw:
            return pw
    raise SystemExit("[sync] 未找到站点密码：请设置环境变量 %s，或在 %s 写入主密码。" % (SITE_PASS_ENV, SITE_PASS_FILE))


def encrypt_html(plain: bytes, password: str) -> str:
    """AES-256-GCM 加密整页 HTML，产出带密码门的解密页（与浏览器端 Web Crypto 参数一致）。"""
    salt = os.urandom(16)
    nonce = os.urandom(12)
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=PBKDF2_ITERS)
    key = kdf.derive(password.encode("utf-8"))
    ct = AESGCM(key).encrypt(nonce, plain, None)
    payload = json.dumps({
        "s": base64.b64encode(salt).decode(),
        "n": base64.b64encode(nonce).decode(),
        "c": base64.b64encode(ct).decode(),
    }, separators=(",", ":"))
    return (GATE_TEMPLATE
            .replace("__PAYLOAD__", payload)
            .replace("__HINT__", SITE_HINT)
            .replace("__ITERS__", str(PBKDF2_ITERS)))


def load_cache():
    if os.path.isfile(ENC_CACHE):
        try:
            with open(ENC_CACHE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_cache(c):
    with open(ENC_CACHE, "w", encoding="utf-8") as f:
        json.dump(c, f)


def ensure_encrypted(rel_path, plaintext, password, cache):
    """明文未变化则跳过（保留既有密文，避免无意义提交）；否则加密写盘。返回是否本次写盘。"""
    h = hashlib.sha256(plaintext).hexdigest()
    key = rel_path.replace("\\", "/")
    if cache.get(key) == h:
        return False
    out = encrypt_html(plaintext, password)
    dst = os.path.join(DST, rel_path)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with open(dst, "w", encoding="utf-8") as f:
        f.write(out)
    cache[key] = h
    return True


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


def decrypt_payload(html, password):
    """从解密门页提取并解密 PAYLOAD，返回明文 HTML 字符串。"""
    m = re.search(r"const PAYLOAD = (\{.*?\});", html, re.DOTALL)
    if not m:
        raise ValueError("PAYLOAD not found")
    payload = json.loads(m.group(1))
    salt = base64.b64decode(payload["s"])
    nonce = base64.b64decode(payload["n"])
    ct = base64.b64decode(payload["c"])
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=PBKDF2_ITERS)
    key = kdf.derive(password.encode("utf-8"))
    return AESGCM(key).decrypt(nonce, ct, None).decode("utf-8")


def extract_generated(text):
    """从 index 明文提取「生成于」时间戳，用于比对 Pages 是否已刷新到本次版本。"""
    if isinstance(text, bytes):
        text = text.decode("utf-8", "replace")
    m = re.search(r"生成于\s*([\d\-:\s]+)", text)
    return m.group(1).strip() if m else None


def wait_for_pages(local_gen, password, timeout=180, poll=15):
    """推送后轮询 GitHub Pages，直到解密后的落地页「生成于」>= 本地本次生成时间，
    证明后台 build 已部署完成；超时或无法访问则提示手动刷新，不阻塞主流程。

    说明：GitHub Pages 在 push 后会异步触发一次站点构建，通常几十秒~几分钟才生效，
    期间访问到的仍是旧版。此函数让脚本自己等到「用户看到的就是最新」，无需手动刷新等待。
    """
    url = "https://s867234-gm.github.io/astock-reports/"
    deadline = time.time() + timeout
    first = True
    while time.time() < deadline:
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "Mozilla/5.0",
                              "Cache-Control": "no-cache", "Pragma": "no-cache"})
            with urllib.request.urlopen(req, timeout=20) as resp:
                html = resp.read().decode("utf-8")
        except urllib.error.URLError as e:
            if first:
                print(f"[sync] 无法访问 GitHub Pages（{e}），跳过部署等待；本地与仓库已更新，稍后手动访问即可")
                return False
            time.sleep(poll); first = False; continue
        except Exception:
            if first:
                print("[sync] 无法访问 GitHub Pages，跳过部署等待；本地与仓库已更新，稍后手动访问即可")
                return False
            time.sleep(poll); first = False; continue
        try:
            plain = decrypt_payload(html, password)
        except Exception:
            time.sleep(poll); first = False; continue
        remote_gen = extract_generated(plain)
        if local_gen and remote_gen and remote_gen >= local_gen:
            print(f"[sync] GitHub Pages 已刷新（生成于 {remote_gen}）✅")
            return True
        first = False
        time.sleep(poll)
    print(f"[sync] 警告：{timeout}s 内 GitHub Pages 仍未刷新到最新（可能后台仍在构建）；仓库与本地均已更新，请稍后刷新页面")
    return False


def main():
    apply_proxy()
    password = load_site_password()
    cache = load_cache()
    changed = 0
    local_gen = None  # 本次 index.html 明文「生成于」时间，用于等待 Pages 部署

    # 1) 报告：从本地明文存档读取并加密写入仓库（仅变更者重加密）
    if os.path.isdir(SRC):
        for name in sorted(os.listdir(SRC)):
            sp = os.path.join(SRC, name)
            if not os.path.isdir(sp):
                continue
            for root, _dirs, files in os.walk(sp):
                for f in files:
                    if not f.lower().endswith(".html"):
                        continue
                    s = os.path.join(root, f)
                    rel = os.path.relpath(s, sp)
                    rel_full = os.path.join(name, rel)
                    with open(s, "rb") as fh:
                        pt = fh.read()
                    if ensure_encrypted(rel_full, pt, password, cache):
                        changed += 1

    # 2) 落地页 index.html：先由 gen_index 生成明文，再加密
    build_index()
    idx = os.path.join(DST, "index.html")
    if os.path.isfile(idx):
        with open(idx, "rb") as fh:
            pt = fh.read()
        local_gen = extract_generated(pt)
        if ensure_encrypted("index.html", pt, password, cache):
            changed += 1

    # 3) 兜底：加密仓库内任何仍为明文的 .html（历史遗留），确保无明文泄露
    for root, _dirs, files in os.walk(DST):
        for f in files:
            if not f.lower().endswith(".html"):
                continue
            p = os.path.join(root, f)
            rel = os.path.relpath(p, DST)
            with open(p, "rb") as fh:
                data = fh.read()
            if b"PAYLOAD" not in data:   # 尚未加密
                if ensure_encrypted(rel, data, password, cache):
                    changed += 1

    save_cache(cache)
    print(f"[sync] 加密更新 HTML：{changed} 个")

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

    # 4) 等待 GitHub Pages 部署完成（消除异步 build 延迟导致的页面陈旧）
    if local_gen:
        wait_for_pages(local_gen, password)


if __name__ == "__main__":
    main()
    sys.exit(0)
