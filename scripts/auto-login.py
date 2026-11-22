#!/usr/bin/env python3
"""南理工统一认证「全自动」登录 —— 纯 HTTP，不需要浏览器、不需要验证码

链路：
    密码 --AES-128-CBC--> ids.njust.edu.cn CAS --> CASTGC（30 天，靠 rememberMe）
    CASTGC --http://bkjw.njust.edu.cn/njlgdx/indexsso.jsp--> 教务的 MOD_AUTH_CAS + JSESSIONID
    CASTGC --https://xg.njust.edu.cn/sso------------------> 学工会话（仅建立，token 另抓）

为什么密码可以离线加密（关键，别当成 hack）：
    CAS 登录页把 `pwdEncryptSalt` 明文写在 DOM 里，前端用
        AES-CBC/PKCS7, key = salt, iv = 16 位随机, 明文 = 64 位随机前缀 + 密码
    加密后提交。IV 随机但我们不需要知道它 —— CBC 下 IV 只影响第 1 个 block，
    而前 64 字节（4 个 block）全是随机垃圾，服务端解密后直接丢掉这 64 字节，
    拿到的就是密码。所以只要 key 对、填充对，明文前缀随便填都能过。

为什么教务不需要验证码：
    强智登录页（/njlgdx/framework/main.jsp 的 USERNAME/PASSWORD/RANDOMCODE）
    是**另一条**路，且已被学校转发到概念版 bkjwxt（空库，登录必失败）。
    真正可用的入口是门户收藏应用里那个 `pcAccessUrl`：
        /njlgdx/indexsso.jsp
    它主动跳 CAS，回跳时由 mod_auth_cas 种下 MOD_AUTH_CAS，全程无验证码。

用法:
    auto-login.py all      # 密码 → CASTGC → 教务 cookie，全部落盘（默认）
    auto-login.py cas      # 只重建 CASTGC
    auto-login.py bkjw     # 只做教务 SSO（复用现有 CASTGC）
    auto-login.py check    # 只检查现有凭据是否还有效
退出码: 0 成功 / 2 需要人工介入（密码错、CAS 改版） / 3 其他错误
"""

import argparse
import hashlib
import http.cookiejar
import json
import os
import random
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

CFG = os.path.expanduser("~/.config/njust")
CRED = os.path.join(CFG, "credential.json")
JAR = os.path.join(CFG, "cas-jar.txt")
CAPTURE = os.path.join(CFG, "jw-cookie.json")

CAS = "https://ids.njust.edu.cn/authserver/login"
PORTAL_SVC = "https://ehall2.njust.edu.cn/login"
PORTAL = "https://ehall2.njust.edu.cn"
BKJW_SSO = "http://bkjw.njust.edu.cn/njlgdx/indexsso.jsp"
XG_SSO = "https://xg.njust.edu.cn/sso"
KB_URL = "https://bkjw.njust.edu.cn/njlgdx/xskb/xskb_list.do?Ves632DSdyV=NEW_XSD_PYGL"

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36")

# 金智前端 randomString 用的字符集，照抄
CHARS = "ABCDEFGHJKMNPQRSTWXYZabcdefhijkmnprstwxyz2345678"
_rnd = random.SystemRandom()


def log(msg):
    sys.stderr.write(msg + "\n")


def load_cred():
    if not os.path.exists(CRED):
        raise SystemExit("没有 %s（需要 studentNo / password）" % CRED)
    d = json.load(open(CRED, encoding="utf-8"))
    if not d.get("studentNo") or not d.get("password"):
        raise SystemExit("credential.json 缺 studentNo 或 password")
    return d["studentNo"], d["password"]


def encrypt_password(pwd, salt):
    """复现 CryptoJS: AES-128-CBC/PKCS7(key=salt, iv=随机16, 明文=随机64+密码) → base64"""
    plain = "".join(_rnd.choice(CHARS) for _ in range(64)) + pwd
    iv = "".join(_rnd.choice(CHARS) for _ in range(16))
    r = subprocess.run(
        ["openssl", "enc", "-aes-128-cbc",
         "-K", salt.strip().encode("utf-8").hex(),
         "-iv", iv.encode("utf-8").hex(), "-a", "-A"],
        input=plain.encode("utf-8"), capture_output=True,
    )
    if r.returncode != 0:
        raise RuntimeError("openssl 加密失败: " + r.stderr.decode("utf-8", "replace")[:200])
    return r.stdout.decode("utf-8").strip()


def new_jar():
    jar = http.cookiejar.MozillaCookieJar(JAR)
    if os.path.exists(JAR):
        try:
            jar.load(ignore_discard=True, ignore_expires=True)
        except Exception:
            pass
    return jar


def opener(jar):
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    op.addheaders = [("User-Agent", UA), ("Accept-Language", "zh-CN,zh;q=0.9")]
    return op


def save_jar(jar):
    os.makedirs(CFG, mode=0o700, exist_ok=True)
    jar.save(ignore_discard=True, ignore_expires=True)
    os.chmod(JAR, 0o600)


def cas_login(user, pwd, jar):
    """密码登录 CAS，把新拿到的 cookie 合并进 jar，返回 CASTGC 剩余天数

    注意：必须用**空 jar** 发起登录。带着旧 CASTGC 请求登录页会被 CAS 直接 302
    到 service（不再返回表单），就抓不到 pwdEncryptSalt 了。
    """
    fresh = http.cookiejar.MozillaCookieJar()
    op = opener(fresh)
    url = CAS + "?service=" + urllib.parse.quote(PORTAL_SVC, safe="")
    page = op.open(url, timeout=25).read().decode("utf-8", "replace")

    salt = re.search(r'id="pwdEncryptSalt"\s+value="([^"]+)"', page)
    execution = re.search(r'id="execution"[^>]*value="([^"]+)"', page)
    if not salt or not execution:
        raise RuntimeError("CAS 登录页结构变了：找不到 pwdEncryptSalt / execution")

    body = urllib.parse.urlencode({
        "username": user,
        "password": encrypt_password(pwd, salt.group(1)),
        "_eventId": "submit", "dllt": "generalLogin", "cllt": "userNameLogin",
        "lt": "", "execution": execution.group(1), "rmShown": "1",
        "captcha": "", "rememberMe": "true",
    }).encode()

    try:
        op.open(urllib.request.Request(url, data=body, headers={
            "User-Agent": UA, "Content-Type": "application/x-www-form-urlencoded",
            "Referer": url, "Origin": "https://ids.njust.edu.cn",
        }), timeout=25)
    except urllib.error.HTTPError:
        pass  # 302 才是正常

    castgc = next((c for c in fresh if c.name == "CASTGC"), None)
    if not castgc:
        raise RuntimeError("CAS 未返回 CASTGC（密码错误或账号被锁）")

    for c in fresh:
        jar.set_cookie(c)

    days = round((castgc.expires - time.time()) / 86400) if castgc.expires else 0
    return days


def has_castgc(jar):
    c = next((x for x in jar if x.name == "CASTGC"), None)
    if not c:
        return False
    return not c.expires or c.expires > time.time() + 3600


def sso_visit(jar, url, timeout=40):
    op = opener(jar)
    try:
        r = op.open(url, timeout=timeout)
        return r.status, r.geturl(), r.read()[:400]
    except urllib.error.HTTPError as e:
        return e.code, url, b""


def refresh_bkjw(jar):
    """用 CASTGC 走教务的 SSO 入口，拿 MOD_AUTH_CAS + JSESSIONID"""
    status, final, _ = sso_visit(jar, BKJW_SSO)
    bkjw = [c for c in jar if "bkjw.njust.edu.cn" in (c.domain or "")]
    if not bkjw:
        raise RuntimeError("SSO 后没有 bkjw cookie（CASTGC 可能已失效）")
    names = {c.name for c in bkjw}
    if "MOD_AUTH_CAS" not in names and "JSESSIONID" not in names:
        raise RuntimeError("SSO 后 cookie 不含 MOD_AUTH_CAS / JSESSIONID: %s" % names)
    return status, final, bkjw


def verify_bkjw(jar):
    """用新 cookie 拉一次课表，确认真的认证上了"""
    ck = "; ".join("%s=%s" % (c.name, c.value)
                   for c in jar if "bkjw.njust.edu.cn" in (c.domain or ""))
    if not ck:
        return False, 0
    req = urllib.request.Request(KB_URL, headers={
        "User-Agent": UA, "Cookie": ck,
        "Referer": "https://bkjw.njust.edu.cn/njlgdx/framework/main.jsp",
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
    text = raw.decode("utf-8", "replace")
    return ("kbcontent" in text and 'type="password"' not in text), len(raw)


def write_capture(jar, href, title, path=CAPTURE):
    """按 jw-cookie.json 的既有格式落盘（fetch-*.py 直接读这个）"""
    cookies = []
    for c in jar:
        dom = c.domain or ""
        if "njust.edu.cn" not in dom:
            continue
        cookies.append({
            "name": c.name, "value": c.value, "domain": dom,
            "path": c.path or "/", "secure": bool(c.secure),
            "httpOnly": bool(c._rest.get("HttpOnly")),
            "expires": c.expires if c.expires else -1,
        })
    if not cookies:
        raise RuntimeError("没有可落盘的 njust cookie")
    os.makedirs(os.path.dirname(path) or ".", mode=0o700, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({
            "capturedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "href": href, "title": title, "ua": UA, "cookies": cookies,
            "source": "auto-login",
        }, f, ensure_ascii=False, indent=2)
    os.chmod(path, 0o600)
    return len(cookies)


def do_check():
    jar = new_jar()
    ok_cas = has_castgc(jar)
    log("CASTGC: %s" % ("有效" if ok_cas else "缺失/过期"))
    try:
        ok, size = verify_bkjw(jar)
    except Exception as e:
        ok, size = False, 0
        log("教务检查失败: %s" % e)
    log("教务: %s%s" % ("有效" if ok else "失效", ("（%d B）" % size) if size else ""))
    return 0 if (ok_cas and ok) else 2


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("action", nargs="?", default="all",
                    choices=["all", "cas", "bkjw", "check"])
    ap.add_argument("--force", action="store_true", help="即使 CASTGC 还有效也重新登录")
    ap.add_argument("--capture", default=CAPTURE, help="cookie 落盘路径，默认 jw-cookie.json")
    args = ap.parse_args()

    if args.action == "check":
        return do_check()

    user, pwd = load_cred()
    jar = new_jar()

    if args.action in ("all", "cas"):
        if has_castgc(jar) and not args.force:
            log("CASTGC 仍在有效期内，跳过密码登录")
        else:
            days = cas_login(user, pwd, jar)
            save_jar(jar)
            log("CASTGC 已重建，%d 天后到期" % days)

    if args.action in ("all", "bkjw"):
        if not has_castgc(jar):
            log("CASTGC 不可用，先登录")
            days = cas_login(user, pwd, jar)
            save_jar(jar)
            log("CASTGC 已重建，%d 天后到期" % days)
        status, final, bkjw = refresh_bkjw(jar)
        save_jar(jar)
        ok, size = verify_bkjw(jar)
        n = write_capture(jar, final, "学期理论课表", args.capture)
        log("教务 SSO: %s → %s" % (status, final[:70]))
        log("cookie: %s" % ", ".join("%s(%d)" % (c.name, len(c.value)) for c in bkjw))
        log("落盘 %d 个 cookie → %s" % (n, CAPTURE))
        if not ok:
            log("⚠️ 课表验证未通过（%d B），可能需要人工介入" % size)
            return 2
        log("✅ 课表验证通过（%d B）" % size)

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as e:
        log("%s: %s" % (type(e).__name__, e))
        sys.exit(3)
