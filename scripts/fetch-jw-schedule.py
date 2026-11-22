#!/usr/bin/env python3
"""抓取南理工教务系统（bkjw，强智科技）学期理论课表 —— 纯 HTTP，不用浏览器

认证链路：
    浏览器已登录过 → 缓存 cookies（bkjw 的 JSESSIONID + MOD_AUTH_CAS）
    → 直接 GET 课表页（实测可行）

前提：`~/.config/njust/jw-cookie.json` 存在。失效时用 `jw-capture.sh` 刷新
（那一步需要浏览器，但浏览器已持有 CASTGC，打开即自动 SSO，无需输密码）。

页面结构（踩过的坑，别改）：
    3 张表，第 2 张是网格：6 个数据行（六大节）× 7 列（周一~周日），**行不带宽高标签，靠顺序定位**
    每格两个 div：kbcontent1 可见版（只有课名+周次）、kbcontent 隐藏版（含老师/周次/教室）
        → 必须读 kbcontent
    多门课用 5 个以上连字符分隔，切开后残留 <br> 必须清掉，否则第二门课名变空
    同一课程跨相邻大节会占多行，合并成 01-05 这种连续区段

用法:
    fetch-jw-schedule.py [--out PATH] [--cache PATH] [--auto-capture]
退出码: 0 成功 / 2 需要重新捕获 / 3 其他错误
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
import html as htmllib

DEFAULT_CACHE = os.path.expanduser("~/.config/njust/jw-cookie.json")
KB_URL = "https://bkjw.njust.edu.cn/njlgdx/xskb/xskb_list.do?Ves632DSdyV=NEW_XSD_PYGL"
MAIN_URL = "https://bkjw.njust.edu.cn/njlgdx/framework/main.jsp"

BIG = ["01-03", "04-05", "06-07", "08-10", "11-13", "14-14"]
BIG_NUM = [(int(a), int(b)) for a, b in (x.split("-") for x in BIG)]


def strip_tags(s):
    return htmllib.unescape(re.sub(r"<[^>]+>", "", s)).strip()


def parse_weeks(text):
    """'6(周)' → [6]；'4-6(周)' → [4,5,6]；支持 '1-8,10-16(周)'"""
    out = []
    for chunk in re.findall(r"([\d,\-\s]+)\s*\(\s*周\s*\)", text):
        for part in chunk.split(","):
            part = part.strip()
            if "-" in part:
                a, b = part.split("-", 1)
                if a.strip().isdigit() and b.strip().isdigit():
                    out.extend(range(int(a), int(b) + 1))
            elif part.isdigit():
                out.append(int(part))
    return sorted(set(out))


def parse_cell(inner):
    courses = []
    for m in re.finditer(r'class="kbcontent"\s*>(.*?)</div>', inner, re.S):
        block = m.group(1)
        for piece in re.split(r"-{5,}", block):
            piece = re.sub(r"^\s*(<br\s*/?>\s*)+", "", piece, flags=re.I).strip()
            if not piece:
                continue
            teachers = re.findall(r'title=[\'"]老师[\'"]\s*>([^<]*)<', piece)
            weeks = re.findall(r'title=[\'"]周次\(节次\)[\'"]\s*>([^<]*)<', piece)
            rooms = re.findall(r'title=[\'"]教室[\'"]\s*>([^<]*)<', piece)
            course = strip_tags(re.split(r"<br\s*/?>", piece, maxsplit=1)[0])
            if not course:
                continue
            courses.append({
                "courseName": course,
                "teacherName": ",".join(x for x in (strip_tags(t) for t in teachers) if x),
                "classWeek": parse_weeks(strip_tags(weeks[0])) if weeks else [],
                "classroom": strip_tags(rooms[0]) if rooms else "",
            })
    return courses


def parse_html(text):
    tables = re.findall(r"<table[^>]*>(.*?)</table>", text, re.S)
    grid = next((t for t in tables if "星期一" in t and "kbcontent" in t), None)
    if grid is None:
        return None

    data_rows = []
    for r in re.findall(r"<tr[^>]*>(.*?)</tr>", grid, re.S):
        ths = re.findall(r"<th[^>]*>(.*?)</th>", r, re.S)
        if ths and "星期" in strip_tags(" ".join(ths)):
            continue
        tds = re.findall(r"<td[^>]*>(.*?)</td>", r, re.S)
        if len(tds) >= 7:
            data_rows.append(tds[:7])

    raw = []
    for row_idx, cells in enumerate(data_rows):
        if row_idx >= len(BIG):
            break
        for i, cell in enumerate(cells):
            for c in parse_cell(cell):
                c["weekday"] = i + 1
                c["periods"] = BIG[row_idx]
                raw.append(c)

    # 合并同课程/星期/教室/周次/老师的相邻大节
    groups = defaultdict(list)
    for c in raw:
        key = (c["courseName"], c["weekday"], c["classroom"], tuple(c["classWeek"]), c["teacherName"])
        groups[key].append(c["periods"])

    out = []
    for (course, weekday, room, weeks, teacher), periods in groups.items():
        idxs = sorted({BIG.index(p) for p in periods if p in BIG})
        if not idxs:
            continue
        runs, cur = [], [idxs[0]]
        for a, b in zip(idxs, idxs[1:]):
            if b == a + 1:
                cur.append(b)
            else:
                runs.append(cur)
                cur = [b]
        runs.append(cur)
        for run in runs:
            p1 = BIG_NUM[run[0]][0]
            p2 = BIG_NUM[run[-1]][1]
            out.append({
                "courseName": course,
                "weekday": weekday,
                "classPeriod": "%02d-%02d" % (p1, p2),
                "classWeek": list(weeks),
                "classroom": room,
                "teacherName": teacher,
                "source": "jw",
            })
    out.sort(key=lambda x: (x["weekday"], x["classPeriod"], x["courseName"]))
    return out


def fetch(cache):
    jars = [c for c in cache.get("cookies") or [] if "bkjw.njust.edu.cn" in (c.get("domain") or "")]
    if not jars:
        raise PermissionError("缓存里没有 bkjw 的 cookie")
    ck = "; ".join("%s=%s" % (c["name"], c["value"]) for c in jars)
    req = urllib.request.Request(KB_URL, headers={
        "Cookie": ck,
        "User-Agent": cache.get("ua") or "Mozilla/5.0",
        "Referer": MAIN_URL,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
    text = raw.decode("utf-8", "replace")
    if 'type="password"' in text or "请先登录系统" in text:
        raise PermissionError("cookie 已失效，跳回登录页")
    return text, len(raw)


def do_capture(path):
    here = os.path.dirname(os.path.abspath(__file__))
    # 首选：纯 HTTP 自动登录（密码在 credential.json，走 CAS + indexsso.jsp，无验证码）
    auto = os.path.join(here, "auto-login.py")
    if os.path.exists(auto):
        sys.stderr.write("尝试自动登录（纯 HTTP，不开浏览器）…\n")
        r = subprocess.run([sys.executable, auto, "bkjw", "--capture", path],
                           capture_output=True, text=True)
        if r.returncode == 0 and os.path.exists(path):
            sys.stderr.write("自动登录成功\n")
            return True
        sys.stderr.write("自动登录未成功：" + (r.stderr or r.stdout or "")[-300:] + "\n")
    # 兜底：浏览器抓 cookie（只有密码登录也被挡住时才用得上）
    script = os.path.join(here, "jw-capture.sh")
    if not os.path.exists(script):
        return False
    sys.stderr.write("回退到浏览器刷新 cookie…\n")
    r = subprocess.run([script, path], capture_output=True, text=True)
    if r.returncode != 0:
        sys.stderr.write((r.stderr or r.stdout or "")[-400:] + "\n")
    return os.path.exists(path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="")
    ap.add_argument("--cache", default=DEFAULT_CACHE)
    ap.add_argument("--auto-capture", action="store_true")
    args = ap.parse_args()

    def attempt():
        if not os.path.exists(args.cache):
            raise PermissionError("没有 cookie 缓存：%s" % args.cache)
        age = (time.time() - os.path.getmtime(args.cache)) / 3600.0
        sys.stderr.write("cookie 年龄 %.1f 小时\n" % age)
        cache = json.load(open(args.cache, encoding="utf-8"))
        return fetch(cache)

    try:
        text, size = attempt()
    except PermissionError as e:
        sys.stderr.write("%s\n" % e)
        if not args.auto_capture:
            sys.stderr.write("请运行 scripts/jw-capture.sh（或加 --auto-capture）\n")
            return 2
        if not do_capture(args.cache):
            sys.stderr.write("自动刷新失败\n")
            return 2
        try:
            text, size = attempt()
        except PermissionError as e2:
            sys.stderr.write("刷新后仍失败：%s\n" % e2)
            return 2
    except Exception as e:
        sys.stderr.write("请求失败：%s: %s\n" % (type(e).__name__, e))
        return 3

    courses = parse_html(text)
    if courses is None:
        sys.stderr.write("页面结构变了：找不到课表网格\n")
        return 3

    print("HTML %d 字节 → 解析 %d 条课程" % (size, len(courses)), file=sys.stderr)

    payload = {
        "source": "jw",
        "fetchedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "count": len(courses),
        "courses": courses,
    }
    if args.out:
        json.dump(payload, open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("已写入: %s" % args.out, file=sys.stderr)
    else:
        json.dump(payload, sys.stdout, ensure_ascii=False, indent=1)
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
