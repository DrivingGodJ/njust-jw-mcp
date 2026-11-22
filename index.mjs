#!/usr/bin/env node
/**
 * njust-jw — 南京理工大学「教务系统」MCP
 *
 * 数据源：bkjw.njust.edu.cn（强智科技，师生端）
 * 认证：纯 HTTP，复用 ~/.config/njust/jw-cookie.json 里的 JSESSIONID + MOD_AUTH_CAS
 *       失效时才用浏览器刷新一次（浏览器持有 CASTGC，打开即自动 SSO，通常不用输密码）
 *
 * 为什么用教务而不是「南理学工+」：学工接口会漏掉第 10 节（如 06-09 vs 教务的 06-10），
 * 且缺体育、通用英语、国防特色通识教育这类课。教务是准的。
 *
 * 覆盖范围（实测同一份 cookie 全通，不需要门户、不需要验证码）：
 *   课表 xskb_list / 成绩 cjcx_list / 考试 xsksap_list / 学籍 grxx/xsxx / 菜单 main.jsp
 */

import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import readline from 'node:readline';
import { execFile } from 'node:child_process';
import { fileURLToPath } from 'node:url';

// 脚本目录优先级：环境变量 > 仓库自带 scripts/
const HERE = path.dirname(fileURLToPath(import.meta.url));
const SKILL = process.env.NJUST_JW_SCRIPTS || path.join(HERE, 'scripts');

const PYTHON = process.env.NJUST_PYTHON || '/usr/bin/python3';
const FETCH = path.join(SKILL, 'fetch-jw-schedule.py');
const FETCH_DATA = path.join(SKILL, 'fetch-jw-data.py');
const AUTO_LOGIN = path.join(SKILL, 'auto-login.py');
const CAPTURE = path.join(SKILL, 'jw-capture.sh');

const WD = ['一', '二', '三', '四', '五', '六', '日'];

function run(cmd, args, timeout = 60000) {
  return new Promise((resolve) => {
    execFile(cmd, args, { timeout, maxBuffer: 8 * 1024 * 1024 }, (err, stdout, stderr) => {
      resolve({ code: err ? (err.code ?? 1) : 0, stdout: stdout || '', stderr: stderr || '' });
    });
  });
}

async function fetchSchedule({ autoCapture = true } = {}) {
  const out = path.join(os.tmpdir(), `jw-${process.pid}-${Date.now()}.json`);
  const args = [FETCH, '--out', out];
  if (autoCapture) args.push('--auto-capture');
  const r = await run(PYTHON, args, 120000);
  if (r.code !== 0) {
    const msg = (r.stderr || r.stdout || '').trim() || `退出码 ${r.code}`;
    throw new Error(msg);
  }
  const data = JSON.parse(fs.readFileSync(out, 'utf8'));
  try { fs.unlinkSync(out); } catch {}
  return { data, log: r.stderr.trim() };
}

async function fetchData(kind, extraArgs = [], autoCapture = true) {
  const out = path.join(os.tmpdir(), `jw-${kind}-${process.pid}-${Date.now()}.json`);
  const args = [FETCH_DATA, kind, '--out', out, ...extraArgs];
  if (autoCapture) args.push('--auto-capture');
  const r = await run(PYTHON, args, 120000);
  if (r.code !== 0) {
    const msg = (r.stderr || r.stdout || '').trim() || `退出码 ${r.code}`;
    throw new Error(msg);
  }
  const data = JSON.parse(fs.readFileSync(out, 'utf8'));
  try { fs.unlinkSync(out); } catch {}
  return { data, log: r.stderr.trim() };
}

function expandWeeks(courses, weeks) {
  if (!weeks || !weeks.length) return courses;
  const want = new Set(weeks.map(Number));
  return courses.filter((c) => c.classWeek.some((w) => want.has(Number(w))));
}

function formatText(courses, weeks) {
  if (!courses.length) return '（没有匹配的课程）';
  const lines = [];
  const byDay = new Map();
  for (const c of courses) {
    if (!byDay.has(c.weekday)) byDay.set(c.weekday, []);
    byDay.get(c.weekday).push(c);
  }
  for (const d of [...byDay.keys()].sort((a, b) => a - b)) {
    lines.push(`周${WD[d - 1]}：`);
    for (const c of byDay.get(d).sort((a, b) => a.classPeriod.localeCompare(b.classPeriod))) {
      const w = c.classWeek.length > 8
        ? `${c.classWeek[0]}-${c.classWeek[c.classWeek.length - 1]}周等${c.classWeek.length}周`
        : `${c.classWeek.join(',')}周`;
      lines.push(`  ${c.classPeriod}  ${c.courseName}  [${w}]  ${c.teacherName || ''} ${c.classroom || ''}`.replace(/\s+$/, ''));
    }
  }
  if (weeks && weeks.length) lines.unshift(`（已筛选第 ${weeks.join(',')} 周）`);
  return lines.join('\n');
}

const TOOLS = [
  {
    name: 'jw_schedule',
    description:
      '抓取南理工教务系统（bkjw）本学期课表。返回课程名、星期、节次、起止周、教师、教室。' +
      '教务数据比「南理学工+」准（学工会漏第 10 节、缺体育/英语等课）。',
    inputSchema: {
      type: 'object',
      properties: {
        weeks: {
          type: 'array',
          items: { type: 'number' },
          description: '只看指定周次，如 [13,14]。省略则返回全学期。',
        },
        format: {
          type: 'string',
          enum: ['text', 'json'],
          description: 'text 适合人看，json 适合后续处理。默认 text。',
        },
      },
      additionalProperties: false,
    },
  },
  {
    name: 'jw_grades',
    description:
      '查询南理工教务系统的课程成绩。返回开课学期、课程编号、课程名、成绩、学分、课程性质等。' +
      '可按学期或课程名过滤。',
    inputSchema: {
      type: 'object',
      properties: {
        semester: { type: 'string', description: '开课学期，如 "2027-2028-1"。省略则返回全部学期。' },
        course: { type: 'string', description: '课程名称关键词，模糊匹配。' },
      },
      additionalProperties: false,
    },
  },
  {
    name: 'jw_exams',
    description: '查询南理工教务系统的考试安排。返回考试场次、课程、考试时间、考场、座位号。',
    inputSchema: { type: 'object', properties: {}, additionalProperties: false },
  },
  {
    name: 'jw_profile',
    description:
      '读取南理工教务系统里的学籍卡片：院系、专业、班级、学号、姓名、性别、入学日期、政治面貌等。' +
      '身份证号/电话等敏感字段已打码。',
    inputSchema: { type: 'object', properties: {}, additionalProperties: false },
  },
  {
    name: 'jw_modules',
    description:
      '列出教务系统当前账号可用的全部功能菜单及其路径。想接入新功能时先用它看有哪些模块，' +
      '再决定抓哪一页。',
    inputSchema: { type: 'object', properties: {}, additionalProperties: false },
  },
  {
    name: 'jw_plan',
    description:
      '查询培养方案 / 执行计划里每个学期的课程和学分。默认按学籍的入学年份推 4 个学年、逐学期列出课程清单与学分小计。' +
      '只问某一学期就传 semester。注意学校官方的「专业培养方案」文档只编到大二下，而执行计划排到大四，两者会差一两门未排课。',
    inputSchema: {
      type: 'object',
      properties: {
        semester: {
          type: 'string',
          description: '只查这一个学期，如 "2027-2028-1"。省略则查从入学年起 years 个学年。',
        },
        years: { type: 'number', description: '查几个学年，默认 4。' },
        startYear: { type: 'number', description: '起始学年，如 2026。省略则按学籍卡片里的入学日期自动判断。' },
        detail: {
          type: 'boolean',
          description: 'true 返回课程编号/学时/考核方式/开课单位等完整字段；默认只给课程名和学分。',
        },
      },
      additionalProperties: false,
    },
  },
  {
    name: 'jw_classrooms',
    description:
      '查全校性教室课表 / 空闲教室。返回每个教室的占用格子；指定 weekday + slot 时额外返回该时段空闲的教室列表。' +
      'slot 取值：010203（1-3节）/0405/0607/080910/111213。',
    inputSchema: {
      type: 'object',
      properties: {
        week: { type: 'number', description: '周次，如 13。必填。' },
        semester: { type: 'string', description: '学年学期，省略则按当前日期推算。' },
        campus: { type: 'string', enum: ['4y', '01'], description: '4y=江阴校区（默认），01=孝陵卫。' },
        match: { type: 'string', description: '只保留教室名含该关键词的，如「致真」。' },
        weekday: { type: 'number', description: '1-7，查某天是否有空教室。' },
        slot: { type: 'string', description: '010203/0405/0607/080910/111213。' },
      },
      required: ['week'],
      additionalProperties: false,
    },
  },
  {
    name: 'jw_page',
    description:
      '通用入口：抓教务系统任意站内页面（必须 /njlgdx/ 开头），返回标题、表格、页面内链接和正文摘要。' +
      '想接入新功能先用 jw_modules 或本工具看有哪些模块，再用它取数据。',
    inputSchema: {
      type: 'object',
      properties: {
        path: { type: 'string', description: '站内路径，如 /njlgdx/pyfa/zxjh_query_find' },
        method: { type: 'string', enum: ['GET', 'POST'], description: '默认 GET。' },
        data: { type: 'string', description: 'POST 表单，形如 a=1&b=2。' },
      },
      required: ['path'],
      additionalProperties: false,
    },
  },
  {
    name: 'jw_refresh_login',
    description:
      '重建教务登录态。默认走纯 HTTP 自动登录：用 credential.json 里的账号密码登录 CAS，' +
      '拿 CASTGC（30 天），再经 indexsso.jsp 换到教务 session，全程不开浏览器、不需要验证码，约 3-4 秒。' +
      '只有自动登录失败时才回退到打开浏览器抓 cookie。',
    inputSchema: {
      type: 'object',
      properties: {
        space: { type: 'string', description: '仅在回退浏览器时使用的 ego 任务空间名，默认「南理工教务」' },
      },
      additionalProperties: false,
    },
  },
];

async function callTool(name, args) {
  if (name === 'jw_schedule') {
    const { data, log } = await fetchSchedule({ autoCapture: true });
    const courses = expandWeeks(data.courses || [], args.weeks);
    if (args.format === 'json') {
      return {
        count: courses.length,
        total: data.count,
        fetchedAt: data.fetchedAt,
        courses,
        log,
      };
    }
    return {
      count: courses.length,
      total: data.count,
      fetchedAt: data.fetchedAt,
      text: formatText(courses, args.weeks),
      log,
    };
  }

  if (name === 'jw_grades') {
    const extra = [];
    if (args.semester) extra.push('--semester', String(args.semester));
    if (args.course) extra.push('--course', String(args.course));
    const { data, log } = await fetchData('grades', extra);
    return {
      count: data.count,
      columns: data.columns,
      semesters: [...new Set((data.items || []).map((x) => x['开课学期']).filter(Boolean))],
      items: data.items,
      fetchedAt: data.fetchedAt,
      log,
    };
  }

  if (name === 'jw_exams') {
    const { data, log } = await fetchData('exams');
    return { count: data.count, columns: data.columns, items: data.items, fetchedAt: data.fetchedAt, log };
  }

  if (name === 'jw_profile') {
    const { data, log } = await fetchData('profile');
    return { profile: data.profile, fetchedAt: data.fetchedAt, log };
  }

  if (name === 'jw_modules') {
    const { data, log } = await fetchData('modules');
    return { count: data.count, modules: data.modules, fetchedAt: data.fetchedAt, log };
  }

  if (name === 'jw_plan') {
    const extra = [];
    if (args.semester) extra.push('--semester', String(args.semester));
    if (args.years) extra.push('--years', String(args.years));
    if (args.startYear) extra.push('--start-year', String(args.startYear));
    const { data, log } = await fetchData('plan', extra);
    const detail = !!args.detail;
    const terms = (data.terms || []).map((t) => ({
      semester: t.semester,
      count: t.count,
      credits: t.credits,
      courses: (t.courses || []).map((c) =>
        detail
          ? {
              code: c['课程编号'],
              name: c['课程名称'],
              credits: c['学分'],
              hours: c['总学时'],
              exam: c['考核方式'],
              dept: c['开课单位'],
            }
          : { name: c['课程名称'], credits: c['学分'] }),
    }));
    return {
      totalCourses: data.count,
      totalCredits: data.totalCredits,
      terms,
      emptyTerms: data.emptyTerms || [],
      fetchedAt: data.fetchedAt,
      log,
    };
  }

  if (name === 'jw_classrooms') {
    const extra = ['--week', String(args.week)];
    if (args.semester) extra.push('--semester', String(args.semester));
    if (args.campus) extra.push('--campus', String(args.campus));
    if (args.match) extra.push('--match', String(args.match));
    if (args.weekday) extra.push('--weekday', String(args.weekday));
    if (args.slot) extra.push('--slot', String(args.slot));
    const { data, log } = await fetchData('classrooms', extra);
    return {
      week: data.week,
      campus: data.campus,
      count: data.count,
      query: data.query,
      freeCount: data.freeCount,
      freeRooms: data.freeRooms,
      items: data.items,
      log,
    };
  }

  if (name === 'jw_page') {
    const extra = ['--path', String(args.path)];
    if (args.method) extra.push('--method', String(args.method));
    if (args.data) extra.push('--data', String(args.data));
    const { data, log } = await fetchData('page', extra);
    return {
      path: data.path,
      title: data.title,
      tables: data.tables,
      links: data.links,
      text: data.text,
      log,
    };
  }

  if (name === 'jw_refresh_login') {
    // 首选：纯 Python 自动登录（密码 → CAS → CASTGC → indexsso.jsp → 教务 session）
    const a = await run(PYTHON, [AUTO_LOGIN, 'all', '--force'], 120000);
    if (a.code === 0) {
      return { ok: true, method: 'auto-login', message: (a.stderr || a.stdout || '').trim() };
    }
    // 兜底：打开浏览器（只有密码登录也被挡住时才走这条）
    const b = await run(CAPTURE, [args.space || '南理工教务'], 130000);
    if (b.code !== 0) throw new Error((a.stderr || b.stderr || b.stdout || '刷新失败').trim());
    return { ok: true, method: 'browser', message: (b.stdout || '').trim() };
  }

  throw new Error(`未知工具：${name}`);
}

function send(msg) {
  process.stdout.write(JSON.stringify(msg) + '\n');
}

async function handle(msg) {
  const { id, method, params } = msg;

  if (method === 'initialize') {
    send({
      jsonrpc: '2.0',
      id,
      result: {
        protocolVersion: params?.protocolVersion || '2025-06-18',
        capabilities: { tools: {} },
        serverInfo: { name: 'njust-jw', version: '1.3.0' },
        instructions:
          '南京理工大学教务系统（bkjw）：课表、成绩、考试安排、学籍、功能菜单。' +
          '数据比「南理学工+」准确。登录态可用 jw_refresh_login 纯 HTTP 全自动重建，不需要浏览器。' +
          '需要往日历里写时，配合 local-calendar MCP 使用。',
      },
    });
    return;
  }

  if (method === 'notifications/initialized' || method === 'notifications/cancelled') return;
  if (method === 'ping') { send({ jsonrpc: '2.0', id, result: {} }); return; }

  if (method === 'tools/list') {
    send({ jsonrpc: '2.0', id, result: { tools: TOOLS } });
    return;
  }

  if (method === 'tools/call') {
    const name = params?.name;
    const args = params?.arguments || {};
    try {
      const payload = await callTool(name, args);
      send({
        jsonrpc: '2.0',
        id,
        result: { content: [{ type: 'text', text: JSON.stringify(payload, null, 1) }], isError: false },
      });
    } catch (e) {
      send({
        jsonrpc: '2.0',
        id,
        result: { content: [{ type: 'text', text: String(e?.message || e) }], isError: true },
      });
    }
    return;
  }

  if (id !== undefined) {
    send({ jsonrpc: '2.0', id, error: { code: -32601, message: `Method not found: ${method}` } });
  }
}

const rl = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
rl.on('line', (line) => {
  const t = line.trim();
  if (!t) return;
  let msg;
  try { msg = JSON.parse(t); } catch { return; }
  handle(msg).catch((e) => {
    if (msg.id !== undefined) {
      send({ jsonrpc: '2.0', id: msg.id, error: { code: -32603, message: String(e?.message || e) } });
    }
  });
});
