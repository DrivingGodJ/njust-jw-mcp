#!/usr/bin/env python3
"""抓取南理工教务系统（bkjw，强智科技）的各类数据 —— 纯 HTTP，不用浏览器

与 fetch-jw-schedule.py 共用同一份 cookie 缓存（bkjw 的 JSESSIONID + MOD_AUTH_CAS）。
实测：整套「教务」菜单只要这两个 cookie 就全通，不需要门户、不需要验证码。

支持的 kind:
    schedule   学期理论课表（同 fetch-jw-schedule.py，这里不重复实现，直接调用）
    grades     课程成绩（POST /njlgdx/kscj/cjcx_list）
    exams      考试安排（GET  /njlgdx/xsks/xsksap_list）
    profile    学籍卡片（GET  /njlgdx/grxx/xsxx）
    modules    教务菜单目录（GET  /njlgdx/framework/main.jsp，列出所有模块入口）
    classrooms 教室课表 / 空闲教室（POST /njlgdx/kbcx/kbxx_classroom_ifr）
    page       任意站内页面的通用抓取
    plan       培养方案 / 执行计划（POST /njlgdx/pyfa/pyfa_query，按学期查课程与学分）

用法:
    fetch-jw-data.py <kind> [--out PATH] [--cache PATH] [--auto-capture]
                     [--semester YYYY-YYYY-1] [--course 关键词]
                     [--years 4] [--start-year 2026]
退出码: 0 成功 / 2 需要重新登录 / 3 其他错误
"""

import argparse
import datetime
import html as htmllib
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_CACHE = os.path.expanduser("~/.config/njust/jw-cookie.json")
BASE = "https://bkjw.njust.edu.cn"
MAIN = "/njlgdx/framework/main.jsp"

# kind -> (method, path, referer)
ROUTES = {
    "grades": ("POST", "/njlgdx/kscj/cjcx_list", "/njlgdx/kscj/cjcx_query?Ves632DSdyV=NEW_XSD_XJCJ"),
    "exams": ("GET", "/njlgdx/xsks/xsksap_list", "/njlgdx/xsks/xsksap_query?Ves632DSdyV=NEW_XSD_KSBM"),
    "profile": ("GET", "/njlgdx/grxx/xsxx?Ves632DSdyV=NEW_XSD_XJCJ", MAIN),
    "modules": ("GET", MAIN, MAIN),
}


def current_semester():
    """按当前日期推算学年学期：8 月起算新学年，1 月仍属上一学年第一学期。"""
    now = datetime.datetime.now()
    if now.month >= 8:
        return "%d-%d-1" % (now.year, now.year + 1)
    if now.month == 1:
        return "%d-%d-1" % (now.year - 1, now.year)
    return "%d-%d-2" % (now.year - 1, now.year)


def strip_tags(s):
    s = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", s)
    return htmllib.unescape(re.sub(r"<[^>]+>", "", s)).replace("\xa0", " ").strip()


def clean_cell(s):
    return re.sub(r"\s+", " ", strip_tags(s)).strip()


def cookies_from(cache):
    jars = [c for c in (cache.get("cookies") or []) if "bkjw.njust.edu.cn" in (c.get("domain") or "")]
    if not jars:
        raise PermissionError("缓存里没有 bkjw 的 cookie")
    return "; ".join("%s=%s" % (c["name"], c["value"]) for c in jars)


def request(cache, kind, data=None, path_override=None, method_override=None):
    if path_override:
        method = (method_override or "GET").upper()
        path = path_override
        ref = MAIN
    else:
        method, path, ref = ROUTES[kind]
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    headers = {
        "Cookie": cookies_from(cache),
        "User-Agent": cache.get("ua") or "Mozilla/5.0",
        "Referer": BASE + ref,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }
    if body is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(BASE + path, data=body, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
    text = raw.decode("utf-8", "replace")
    if 'type="password"' in text or "请先登录系统" in text:
        raise PermissionError("cookie 已失效，跳回登录页")
    return text, len(raw)


# ---------------- 解析 ----------------

def parse_table(html_text, table_id="dataList"):
    """解析强智的列表页：第一行 th 作表头，其余 tr 作数据行。"""
    m = re.search(r'<table[^>]*id="%s"[^>]*>(.*?)</table>' % table_id, html_text, re.S)
    if not m:
        return None, []
    inner = m.group(1)
    head = []
    rows = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", inner, re.S):
        ths = re.findall(r"<th[^>]*>(.*?)</th>", tr, re.S)
        if ths:
            head = [clean_cell(x) for x in ths]
            continue
        tds = re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)
        if not tds:
            continue
        cells = [clean_cell(x) for x in tds]
        if not any(cells):
            continue
        rows.append(cells)
    items = []
    for cells in rows:
        rec = {}
        for i, v in enumerate(cells):
            key = head[i] if i < len(head) else "col%d" % i
            rec[key] = v
        items.append(rec)
    return head, items


def parse_profile(html_text):
    """学籍卡片：值单元格以 &nbsp; 开头，标签单元格不带；按 tr 内顺序配对。"""
    m = re.search(r'<table[^>]*id="xjkpTable"[^>]*>(.*?)</table>', html_text, re.S)
    if not m:
        return {}
    out = {}
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", m.group(1), re.S):
        cells = re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)
        if len(cells) < 2:
            continue
        pending = None
        for raw in cells:
            is_value = raw.lstrip().startswith("&nbsp;") or raw.lstrip().startswith("\xa0")
            txt = clean_cell(raw)
            if is_value:
                if pending and txt:
                    out[pending] = txt
                pending = None
            else:
                # 「院系：设计科学与艺术学院」这种内嵌冒号的直接拆
                if re.match(r"^[^：:]{1,8}[：:]", txt):
                    k, v = re.split(r"[：:]", txt, 1)
                    if v.strip():
                        out[k.strip()] = v.strip()
                    else:
                        pending = k.strip()
                elif txt and len(txt) <= 8:
                    pending = txt
                else:
                    pending = None
    return out


def visible_text(html_text, limit=3500):
    t = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", html_text)
    t = re.sub(r"<br\s*/?>|</tr>|</p>|</div>", "\n", t, flags=re.I)
    t = htmllib.unescape(re.sub(r"<[^>]+>", " ", t)).replace("\xa0", " ")
    t = re.sub(r"[ \t]+", " ", t)
    lines = [ln.strip() for ln in t.split("\n")]
    lines = [ln for ln in lines if ln and not ln.startswith("Copyright")]
    t = "\n".join(lines)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t[:limit] + ("\n…（截断）" if len(t) > limit else "")


def parse_all_tables(html_text):
    out = []
    for m in re.finditer(r'<table([^>]*)>(.*?)</table>', html_text, re.S):
        attrs, inner = m.group(1), m.group(2)
        tid = re.search(r'id="([^"]+)"', attrs)
        head, rows = [], []
        for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", inner, re.S):
            ths = re.findall(r"<th[^>]*>(.*?)</th>", tr, re.S)
            if ths:
                head = [clean_cell(x) for x in ths]
                continue
            tds = re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)
            cells = [clean_cell(x) for x in tds]
            if cells and any(cells):
                rows.append(cells)
        if not rows:
            continue
        out.append({
            "id": tid.group(1) if tid else "",
            "columns": head,
            "rows": rows[:80],
            "rowCount": len(rows),
        })
    return out


MASK_KEYS = ("身份证", "证件号", "考生号", "入学考号", "电话", "手机", "邮箱", "家庭地址", "住址", "银行卡")


def mask_sensitive(prof):
    """身份证/电话这类字段只留首尾，避免整串进模型上下文。"""
    out = {}
    for k, v in prof.items():
        if any(m in k for m in MASK_KEYS) and len(v) > 6:
            out[k] = v[:4] + "*" * (len(v) - 8) + v[-4:] if len(v) > 8 else v[:2] + "*" * (len(v) - 2)
        else:
            out[k] = v
    return out


SLOTS = ["010203", "0405", "0607", "080910", "111213"]
WD = ["一", "二", "三", "四", "五", "六", "日"]
CAMPUS = {"4y": "江阴校区", "01": "孝陵卫"}


def parse_classroom_grid(html_text):
    """全校性教室课表：每行 = 1 个教室名 + 35 格（7 天 × 5 大节）。空格 = 空闲。"""
    trs = re.findall(r"<tr[^>]*>(.*?)</tr>", html_text, re.S)
    rooms = []
    for tr in trs:
        cells = re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)
        if len(cells) != 36:
            continue
        name = clean_cell(cells[0])
        if not name or name.startswith("教室"):
            continue
        busy = []
        for i, cell in enumerate(cells[1:]):
            txt = re.sub(r"\s+", " ", clean_cell(cell)).strip()
            if not txt:
                continue
            day, slot = i // 5, i % 5
            busy.append({"day": day + 1, "slot": SLOTS[slot], "course": txt})
        rooms.append({"classroom": name, "busy": busy, "freeSlots": 35 - len(busy)})
    return rooms


def parse_modules(html_text):
    out = []
    seen = set()
    for href, label in re.findall(r'<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', html_text, re.S):
        if not href.startswith("/njlgdx/"):
            continue
        name = clean_cell(label)
        if not name or "main.jsp" in href or href in seen:
            continue
        seen.add(href)
        out.append({"name": name, "path": href})
    return out


def parse_semesters(html_text):
    """从成绩查询页的 kksj 下拉里取可选学期。"""
    m = re.search(r'<select[^>]*id="kksj"[^>]*>(.*?)</select>', html_text, re.S)
    if not m:
        return []
    return [clean_cell(v) for v in re.findall(r"<option[^>]*>(.*?)</option>", m.group(1), re.S) if clean_cell(v)]


# ---------------- 刷新 cookie ----------------

# 执行计划查询：表单页 /njlgdx/pyfa/zxjh_query_find 的 action 是空的，
# 真实提交地址由内联 JS querypyfa() 动态指向这里。
PLAN_QUERY_URL = "/njlgdx/pyfa/pyfa_query"


def enroll_year(cache):
    """从学籍卡片「入学日期」拿入学年份（如 2026-09 → 2026）。拿不到返回 None。"""
    try:
        text, _ = request(cache, "profile", None)
        v = parse_profile(text).get("入学日期") or ""
        m = re.match(r"(\d{4})", v)
        if m:
            return int(m.group(1))
    except Exception:
        pass
    return None


def pick_semesters(start_year=None, years=4):
    """学生本人的学年学期序列：起始学年起连续 years 个学年 × 3 个学期。

    注意：学校下拉框只列到当前已排课的最远学期（2027-2028），
    但 POST 任意学期都能查，所以必须自己生成，不能拿下拉当全集。
    """
    if start_year is None:
        today = datetime.date.today()
        start_year = today.year if today.month >= 8 else today.year - 1
    out = []
    for y in range(start_year, start_year + years):
        for t in ("1", "2", "3"):
            out.append("%d-%d-%s" % (y, y + 1, t))
    return out


def fetch_plan_terms(cache, semesters):
    """逐个学期 POST 执行计划，返回 (terms, 累计字节)。"""
    terms = []
    total = 0
    for s in semesters:
        form = {"xnxq": s, "kctxs": "", "kcsx": "", "kctx": ""}
        text, size = request(cache, "plan", form,
                             path_override=PLAN_QUERY_URL, method_override="POST")
        total += size
        _, items = parse_table(text)
        credits = 0.0
        for it in items:
            try:
                credits += float(it.get("学分") or 0)
            except ValueError:
                pass
        terms.append({
            "semester": s,
            "count": len(items),
            "credits": round(credits, 1),
            "courses": items,
        })
    return terms, total


def do_capture(path):
    here = os.path.dirname(os.path.abspath(__file__))
    # 首选：纯 HTTP 自动登录（CAS + indexsso.jsp，无验证码、不开浏览器）
    auto = os.path.join(here, "auto-login.py")
    if os.path.exists(auto):
        sys.stderr.write("尝试自动登录（纯 HTTP，不开浏览器）…\n")
        r = subprocess.run([sys.executable, auto, "bkjw", "--capture", path],
                           capture_output=True, text=True)
        if r.returncode == 0 and os.path.exists(path):
            sys.stderr.write("自动登录成功\n")
            return True
        sys.stderr.write("自动登录未成功：" + (r.stderr or r.stdout or "")[-300:] + "\n")
    # 兜底：浏览器抓 cookie
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
    ap.add_argument("kind", choices=list(ROUTES) + ["schedule", "page", "classrooms", "plan"])
    ap.add_argument("--out", default="")
    ap.add_argument("--cache", default=DEFAULT_CACHE)
    ap.add_argument("--auto-capture", action="store_true")
    ap.add_argument("--semester", default="", help="开课学期，如 2027-2028-1；空=全部")
    ap.add_argument("--course", default="", help="课程名称关键词")
    ap.add_argument("--path", default="", help="kind=page 时的站内路径，必须 /njlgdx/ 开头")
    ap.add_argument("--method", default="GET", help="kind=page 时的请求方法 GET/POST")
    ap.add_argument("--data", default="", help="kind=page 时的 POST 表单，形如 a=1&b=2")
    ap.add_argument("--week", type=int, default=0, help="kind=classrooms 时的周次")
    ap.add_argument("--campus", default="4y", help="4y=江阴校区 / 01=孝陵卫")
    ap.add_argument("--match", default="", help="只保留教室名含该关键词的（如 致真）")
    ap.add_argument("--weekday", type=int, default=0, help="1-7，查某天")
    ap.add_argument("--slot", default="", help="010203/0405/0607/080910/111213")
    ap.add_argument("--years", type=int, default=4, help="kind=plan 时查几个学年，默认 4")
    ap.add_argument("--start-year", type=int, default=0,
                    help="kind=plan 时起始学年（如 2026）；0=按当前日期推算")
    args = ap.parse_args()

    if args.kind == "schedule":
        sib = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fetch-jw-schedule.py")
        cmd = [sys.executable, sib, "--cache", args.cache]
        if args.out:
            cmd += ["--out", args.out]
        if args.auto_capture:
            cmd.append("--auto-capture")
        return subprocess.run(cmd).returncode

    if args.kind == "classrooms":
        wk = args.week or 1
        form = {
            "xnxqh": args.semester or current_semester(),
            "skyx": "",
            "xqid": args.campus,
            "jzwid": "",
            "zc1": str(wk),
            "zc2": str(wk),
            "xq": "1",
            "xq2": "7",
            "jc1": "1",
            "jc2": "13",
        }

        def attempt():
            if not os.path.exists(args.cache):
                raise PermissionError("没有 cookie 缓存：%s" % args.cache)
            cache = json.load(open(args.cache, encoding="utf-8"))
            return request(cache, "classrooms", form,
                           path_override="/njlgdx/kbcx/kbxx_classroom_ifr", method_override="POST")
    elif args.kind == "page":
        p = args.path.strip()
        if not p.startswith("/njlgdx/"):
            sys.stderr.write("--path 必须是站内路径且以 /njlgdx/ 开头（拒绝外站地址）\n")
            return 3
        form = None
        if args.data:
            form = dict(urllib.parse.parse_qsl(args.data, keep_blank_values=True))

        def attempt():
            if not os.path.exists(args.cache):
                raise PermissionError("没有 cookie 缓存：%s" % args.cache)
            cache = json.load(open(args.cache, encoding="utf-8"))
            return request(cache, "page", form, path_override=p, method_override=args.method)
    elif args.kind == "plan":

        def attempt():
            if not os.path.exists(args.cache):
                raise PermissionError("没有 cookie 缓存：%s" % args.cache)
            cache = json.load(open(args.cache, encoding="utf-8"))
            if args.semester:
                sems = [args.semester]
            else:
                start = args.start_year or enroll_year(cache)
                sems = pick_semesters(start, args.years)
            if not sems:
                raise RuntimeError("没有匹配的学期：起始学年 %s / %d 个学年"
                                   % (args.start_year or "自动", args.years))
            sys.stderr.write("查询 %d 个学期：%s\n" % (len(sems), " ".join(sems)))
            return fetch_plan_terms(cache, sems)
    else:

        def attempt():
            if not os.path.exists(args.cache):
                raise PermissionError("没有 cookie 缓存：%s" % args.cache)
            age = (time.time() - os.path.getmtime(args.cache)) / 3600.0
            sys.stderr.write("cookie 年龄 %.1f 小时\n" % age)
            cache = json.load(open(args.cache, encoding="utf-8"))
            data = None
            if args.kind == "grades":
                data = {"kksj": args.semester, "kcxz": "", "kcmc": args.course, "xsfs": "all"}
            return request(cache, args.kind, data)

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

    payload = {
        "source": "jw",
        "kind": args.kind,
        "fetchedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }

    if args.kind == "plan":
        terms = text  # plan 的 attempt 返回的是 (terms, bytes) 而非 HTML
        payload.update({
            "count": sum(t["count"] for t in terms),
            "totalCredits": round(sum(t["credits"] for t in terms), 1),
            "terms": [t for t in terms if t["count"]],
            "emptyTerms": [t["semester"] for t in terms if not t["count"]],
        })
    elif args.kind in ("grades", "exams"):
        head, items = parse_table(text)
        if head is None:
            sys.stderr.write("页面结构变了：找不到 #dataList\n")
            return 3
        payload.update({"columns": head, "count": len(items), "items": items})
    elif args.kind == "profile":
        prof = parse_profile(text)
        if not prof:
            sys.stderr.write("页面结构变了：找不到 #xjkpTable\n")
            return 3
        payload.update({"count": len(prof), "profile": mask_sensitive(prof)})
    elif args.kind == "modules":
        mods = parse_modules(text)
        payload.update({"count": len(mods), "modules": mods})
    elif args.kind == "classrooms":
        rooms = parse_classroom_grid(text)
        if not rooms:
            sys.stderr.write("页面结构变了：解析不到教室网格\n")
            return 3
        if args.match:
            rooms = [r for r in rooms if args.match in r["classroom"]]
        payload.update({
            "week": wk,
            "campus": CAMPUS.get(args.campus, args.campus),
            "count": len(rooms),
            "items": [
                {
                    "classroom": r["classroom"],
                    "freeSlots": r["freeSlots"],
                    "busy": ["周%s/%s" % (WD[b["day"] - 1], b["slot"]) for b in r["busy"]],
                }
                for r in rooms
            ],
        })
        if args.weekday and args.slot:
            free = [
                r["classroom"] for r in rooms
                if not any(b["day"] == args.weekday and b["slot"] == args.slot for b in r["busy"])
            ]
            payload["query"] = "第%d周 周%s %s" % (wk, WD[args.weekday - 1], args.slot)
            payload["freeRooms"] = free
            payload["freeCount"] = len(free)
    elif args.kind == "page":
        title = re.search(r"<title>(.*?)</title>", text, re.S)
        payload.update({
            "path": args.path,
            "title": clean_cell(title.group(1)) if title else "",
            "tables": parse_all_tables(text),
            "links": parse_modules(text)[:80],
            "text": visible_text(text),
        })
    else:
        payload.update({"raw": text})

    print("HTML %d 字节 → %s 解析 %d 条" % (size, args.kind, payload.get("count", 0)), file=sys.stderr)

    if args.out:
        json.dump(payload, open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("已写入: %s" % args.out, file=sys.stderr)
    else:
        json.dump(payload, sys.stdout, ensure_ascii=False, indent=1)
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
