**简体中文** | [English](README.en.md)

# 南理工教务 MCP

把南京理工大学教务系统接进任何支持 MCP 的 AI 客户端——课表、成绩、考试、学籍、培养方案、空闲教室，一句中文就能问出来，不用开浏览器、不用每次输密码。

## 装上之后，能这样问

| 你说 | 它做的事 |
| --- | --- |
| 「我这周有哪些课」 | 拉本学期课表，按星期分组列出节次、教室、教师 |
| 「下周一下午的课还在吗」 | 按周次筛选，只回那一门 |
| 「我这学期多少学分」 | 读培养方案执行计划，逐学期给出课程清单与学分小计 |
| 「高数考了多少分」 | 查成绩，支持按学期、课程名过滤 |
| 「下周三下午哪有空教室」 | 查全校教室占用，返回该时段空闲的教室 |
| 「我期末考试在哪考」 | 查考试安排：时间、考场、座位号 |

课表返回的样子（省略部分）：

```text
周三：
  01-05  数据结构  [13周]  A楼302
周一：
  04-05  线性代数  [13,14,15,16周]  B楼108
  06-10  数据结构  [14周]  A楼302
```

培养方案返回的样子：

```json
{
  "totalCourses": 52,
  "totalCredits": 168,
  "terms": [
    { "semester": "2027-2028-1", "count": 12, "credits": 24 },
    { "semester": "2027-2028-2", "count": 11, "credits": 22.5 }
  ],
  "emptyTerms": ["2027-2028-3"]
}
```

## 不需要浏览器，也不用反复输密码

学校的统一身份认证每次登录都要一个动态 salt，密码还得在浏览器里加密。这个项目把那一整套搬到了命令行：

1. 从登录页取出 `pwdEncryptSalt`，用 `AES-128-CBC` 在本地加密「64 位随机串 + 密码」；
2. 换到 CAS 的 `CASTGC`（勾了 30 天免登录）；
3. 再经教务的 `indexsso.jsp` 换成教务会话。

全程纯 HTTP，约 3–4 秒，没有验证码。会话过期时工具会自己重建，你只会看到一次多等两秒。

## 安装

**依赖**：Node.js 18+、`python3`（macOS 自带）、`openssl`（系统自带）。

```bash
git clone https://github.com/DrivingGodJ/njust-jw-mcp.git
cd njust-jw-mcp
```

写一份凭据（只存在你本机，权限 600）：

```bash
mkdir -p ~/.config/njust
cat > ~/.config/njust/credential.json <<'EOF'
{ "studentNo": "你的学号", "password": "统一身份认证密码" }
EOF
chmod 600 ~/.config/njust/credential.json
```

在客户端里注册（以 `~/.workbuddy/mcp.json` 为例，Claude Desktop 用 `claude_desktop_config.json`，字段同为 `mcpServers`）：

```json
{
  "mcpServers": {
    "njust-jw": {
      "command": "node",
      "args": ["/绝对路径/njust-jw-mcp/index.mjs"]
    }
  }
}
```

注册后重开客户端，问一句「我这周有什么课」就能验证。

**可选环境变量**：`NJUST_JW_SCRIPTS`（覆盖脚本目录）、`NJUST_PYTHON`（指定 python 解释器）。

## 工具一览

| 工具 | 作用 |
| --- | --- |
| `jw_schedule` | 本学期课表，可按周次筛选，输出文本或 JSON |
| `jw_grades` | 课程成绩，可按学期、课程名过滤 |
| `jw_exams` | 考试安排：场次、时间、考场、座位号 |
| `jw_profile` | 学籍卡片（身份证、考号按首尾打码） |
| `jw_plan` | 培养方案 / 执行计划，逐学期课程与学分 |
| `jw_classrooms` | 全校教室占用，可查某天某时段空闲教室 |
| `jw_modules` | 当前账号可用的功能菜单，接新功能前先看它 |
| `jw_page` | 通用站内页面抓取，返回标题、表格、链接、正文 |
| `jw_refresh_login` | 重建登录态（自动登录失败才回退浏览器） |

## 一些边界

- **非官方项目**。用的是教务系统面向学生的公开页面与接口，学校升级可能让它失效，届时需要跟着改。
- **只在你本机运行**。数据不经过任何服务器，也不会被上传；MCP 服务器只做「取回并结构化」。
- **凭据是明文的**。`credential.json` 权限 600，但内容没加密。介意的话把它放进钥匙串或磁盘加密卷，再加个开机解锁的包装脚本。
- **只用自己的账号**。别借给别人跑，学校侧看到的是同一个会话。
- 身份证号、准考证号在 `jw_profile` 里已做打码处理，但这只是减少误展示，不改变数据本身在你本机的性质。

## 进一步了解

| 想了解什么 | 去哪里看 |
| --- | --- |
| 踩过的坑：CAS 加密细节、强智表单陷阱、学期下拉不全 | [技术笔记](docs/NOTES.md) |
| 问题反馈 | [Issues](https://github.com/DrivingGodJ/njust-jw-mcp/issues) |

MIT License。校内系统的接口细节随学校版本变化，欢迎提 issue 补充你遇到的差异。
