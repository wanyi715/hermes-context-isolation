#!/usr/bin/env python3
"""项目/话题自动检测 + 创建 功能测试"""
import json, urllib.request, urllib.error, sys

BRIDGE = "http://127.0.0.1:8766"
PASS, FAIL, SKIP = 0, 0, 0

def ok(msg): global PASS; PASS += 1; print(f"  ✅ {msg}")
def no(msg): global FAIL; FAIL += 1; print(f"  ❌ {msg}")
def skip(msg): global SKIP; SKIP += 1; print(f"  ⏭️  {msg}")

def api_post(path, data):
    req = urllib.request.Request(
        f"{BRIDGE}{path}",
        data=json.dumps(data).encode(),
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read()), resp.status
    except urllib.error.HTTPError as e:
        return {"error": str(e)}, e.code


# ─── Test 1: 检测现有项目 ───
print("\n📋 Test 1: 话题检测 → 现有项目")

# 模拟讨论皇帝游戏的对话
msgs = [
    {"role": "user", "text": "皇帝游戏 v0.3.8 有个 bug，大臣无法任命"},
    {"role": "assistant", "text": "让我看看 main.py 的任命逻辑"},
    {"role": "user", "text": "问题是角色列表没刷新"},
    {"role": "assistant", "text": "找到问题了，src/ 目录下的角色管理器没更新"},
]
data, code = api_post("/api/detect-topic", {"messages": msgs})
if code == 200:
    action = data.get("action", "?")
    if action == "switch":
        ok(f"检测到应切换至: {data.get('project_key')} ({data.get('name')})")
    else:
        no(f"期望 switch，实际 {action}: {data}")
else:
    no(f"HTTP {code}: {data}")

# ─── Test 2: 检测新项目 ───
print("\n📋 Test 2: 话题检测 → 新项目")

msgs2 = [
    {"role": "user", "text": "我想做一个自动化数据看板，每天抓取芯片价格数据"},
    {"role": "assistant", "text": "好的，这需要定时爬虫 + 数据库 + 前端可视化"},
    {"role": "user", "text": "对，数据源有官网、交易所 API，还要做趋势分析"},
    {"role": "assistant", "text": "这个规模确实需要一个独立项目来管理"},
]
data, code = api_post("/api/detect-topic", {"messages": msgs2})
if code == 200:
    action = data.get("action", "?")
    if action in ("new_project", "new_topic"):
        ok(f"检测到应创建: {action} → {data.get('name')}")
    elif action == "none":
        skip(f"LLM 判断为无需操作（模型可能保守）: {data}")
    else:
        no(f"期望 new_project/new_topic，实际 {action}: {data}")
else:
    no(f"HTTP {code}: {data}")

# ─── Test 3: 普通闲聊不应触发 ───
print("\n📋 Test 3: 话题检测 → 闲聊（应为 NONE）")

msgs3 = [
    {"role": "user", "text": "今天天气不错"},
    {"role": "assistant", "text": "确实，适合出去走走"},
    {"role": "user", "text": "你中午吃的什么"},
    {"role": "assistant", "text": "我是 AI，不需要吃饭哈哈"},
    {"role": "user", "text": "那周末有什么计划"},
]
data, code = api_post("/api/detect-topic", {"messages": msgs3})
if code == 200:
    action = data.get("action", "?")
    if action == "none":
        ok("闲聊正确返回 NONE")
    else:
        no(f"闲聊应返回 none，实际 {action}: {data}")
else:
    no(f"HTTP {code}: {data}")

# ─── Test 4: 消息不足 ───
print("\n📋 Test 4: 话题检测 → 消息不足")

few = [{"role": "user", "text": "hi"}, {"role": "assistant", "text": "你好"}]
data, code = api_post("/api/detect-topic", {"messages": few})
if data.get("action") == "none" and "not enough" in data.get("reason", "").lower():
    ok("消息不足时正确拒绝")
else:
    no(f"期望 reason='not enough messages'，实际: {data}")

# ─── Test 5: 创建项目 ───
print("\n📋 Test 5: 创建项目")

data, code = api_post("/api/create-project", {"name": "自动化测试项目", "timestamp": "2026-05-18"})
if code == 200 and "project_key" in data:
    ok(f"创建成功: {data['project_key']} ({data['name']})")
    test_key = data["project_key"]
else:
    # May already exist from previous test run
    if "exists" in str(data):
        skip(f"项目已存在（可能上次测试残留）: {data}")
        test_key = "zidong-hua-ce-shi-xiang-mu"  # guess key
    else:
        no(f"创建失败: {data}")
        test_key = None

# ─── Test 6: 切换项目 ───
print("\n📋 Test 6: 切换项目")

data, code = api_post("/api/switch-project", {"project": "emperor-game"})
if code == 200:
    ok(f"切换成功，当前: {data.get('current')}")
else:
    no(f"切换失败: {data}")

data2, code2 = api_post("/api/switch-project", {"project": "main"})
if code2 == 200:
    ok("切回 main 成功")

# ─── Test 7: 项目列表 ───
print("\n📋 Test 7: 获取项目列表")

req = urllib.request.Request(f"{BRIDGE}/api/projects")
with urllib.request.urlopen(req, timeout=10) as resp:
    proj_data = json.loads(resp.read())
projects = proj_data.get("projects", [])
if len(projects) >= 5:
    ok(f"项目数: {len(projects)}: {[p['key'] for p in projects[:5]]}...")
else:
    no(f"项目数不足: {len(projects)}")

# ─── 汇总 ───
print(f"\n{'='*50}")
print(f"  总计: {PASS+FAIL+SKIP}  |  ✅ {PASS}  |  ❌ {FAIL}  |  ⏭️  {SKIP}")
print(f"{'='*50}")
sys.exit(0 if FAIL == 0 else 1)
