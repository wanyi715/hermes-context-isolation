#!/usr/bin/env python3
"""SSE 聊天功能自动化测试 — 测试环境 (bridge:8766)"""
import json, time, urllib.request, sys

BRIDGE = "http://127.0.0.1:8766"
PASS, FAIL, SKIP = 0, 0, 0

def ok(msg): global PASS; PASS += 1; print(f"  ✅ {msg}")
def no(msg): global FAIL; FAIL += 1; print(f"  ❌ {msg}")
def skip(msg): global SKIP; SKIP += 1; print(f"  ⏭️  {msg}")

def sse_chat(message, project="hermes-agent", timeout=120):
    """发 SSE 流式请求，返回 (events, response_text, error)"""
    req = urllib.request.Request(
        f"{BRIDGE}/api/chat",
        data=json.dumps({"message": message, "stream": True, "project": project}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    events = []
    response_text = ""
    error = None
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            buf = b""
            event_type = ""
            while True:
                chunk = resp.read(4096)
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    line = line.decode("utf-8", errors="replace")
                    if line.startswith("event: "):
                        event_type = line[7:].strip()
                    elif line.startswith("data: "):
                        try:
                            data = json.loads(line[6:])
                        except:
                            data = {"raw": line[6:]}
                        events.append((event_type, data))
                        if event_type == "done":
                            response_text = data.get("response", "")
                        elif event_type == "error":
                            error = data.get("message", "unknown")
                        event_type = ""
    except Exception as e:
        error = str(e)
    return events, response_text, error


# ─── Test 1: 基础 SSE 流 ───
print("\n📋 Test 1: 基础 SSE 流式对话")
events, text, err = sse_chat("回复一个词：测试成功", timeout=60)
if err:
    no(f"请求失败: {err}")
else:
    types = [e[0] for e in events]
    if "thinking" in types: ok("收到 thinking 事件")
    else: no("缺少 thinking 事件")
    if "done" in types: ok("收到 done 事件")
    else: no("缺少 done 事件")
    if "close" in types: ok("收到 close 事件（连接正常关闭）")
    else: no("缺少 close 事件（连接未正常关闭）")
    if text: ok(f"回复内容: {text[:60]}")
    else: no("回复为空")

# ─── Test 2: 心跳机制 ───
print("\n📋 Test 2: 心跳保活（模拟慢 API）")
events, text, err = sse_chat(
    "列出 Python 标准库中 itertools 模块的所有函数，每个加一行说明。",
    timeout=60
)
hb_events = [e for e in events if e[0] == "heartbeat"]
if len(hb_events) >= 1:
    ok(f"收到 {len(hb_events)} 次心跳 (evt count={hb_events[-1][1].get('count','?')})")
else:
    no("未收到心跳事件（可能 API 太快）")
if text and "itertools" in text.lower():
    ok(f"长任务回复正常 ({len(text)} chars)")
else:
    no(f"长任务回复异常: {text[:40]}")

# ─── Test 3: 项目隔离 ───
print("\n📋 Test 3: 跨项目隔离")
# 先在 emperor-game 发一条
events1, t1, e1 = sse_chat("记住：测试项目是emperor-game", project="emperor-game", timeout=60)
# 再在 hermes-agent 问
events2, t2, e2 = sse_chat("我刚才说测试项目是什么？如果不知道就说不知道", project="hermes-agent", timeout=60)
if "emperor" in t2.lower() or "皇帝" in t2:
    no(f"跨项目泄露！hermes-agent 回复提到了 emperor-game: {t2[:60]}")
else:
    ok(f"项目隔离正常（hermes-agent 不知道 emperor-game 的内容）: {t2[:60]}")

# ─── Test 4: 并发请求 ───
print("\n📋 Test 4: 并发请求（线程安全）")
import concurrent.futures
def concurrent_chat(idx):
    events, text, err = sse_chat(f"回复数字{idx}", timeout=60)
    return idx, text, err
with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
    futures = [ex.submit(concurrent_chat, i) for i in range(3)]
    results = [f.result() for f in concurrent.futures.as_completed(futures)]
all_ok = all(r[1] and not r[2] for r in results)
if all_ok:
    ok(f"3 个并发请求全部成功: {[r[1][:20] for r in results]}")
else:
    no(f"并发请求有问题: {results}")

# ─── Test 5: 空消息 / 异常输入 ───
print("\n📋 Test 5: 边界输入")
import urllib.error
try:
    req = urllib.request.Request(
        f"{BRIDGE}/api/chat",
        data=json.dumps({"message": "", "stream": True, "project": "main"}).encode(),
        headers={"Content-Type": "application/json"}, method="POST"
    )
    urllib.request.urlopen(req, timeout=5)
    no("空消息应返回 400")
except urllib.error.HTTPError as e:
    if e.code == 400:
        ok("空消息正确返回 400")
    else:
        no(f"空消息返回 {e.code} 而非 400")

# ─── 汇总 ───
print(f"\n{'='*50}")
print(f"  总计: {PASS+FAIL+SKIP}  |  ✅ {PASS}  |  ❌ {FAIL}  |  ⏭️  {SKIP}")
print(f"{'='*50}")
sys.exit(0 if FAIL == 0 else 1)
