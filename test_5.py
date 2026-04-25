import asyncio, aiohttp, time, json, os

URL = os.environ.get("LLM_URL", "http://gx10-f2c9:8080/v1/chat/completions")
HEADERS = {"Content-Type": "application/json"}

async def call_agent(name, messages, max_tokens=512, dump=False):
    payload = {
        "model": "gemma",
        "messages": messages,
        "max_tokens": max_tokens,
        "stream": False,
        "temperature": 0.2,
    }
    start = time.perf_counter()
    async with aiohttp.ClientSession() as session:
        async with session.post(URL, headers=HEADERS, json=payload) as resp:
            data = await resp.json()
    dt = time.perf_counter() - start

    usage = data.get("usage", {})
    timings = data.get("timings", {})
    out_tokens = usage.get("completion_tokens", 0)
    tps = timings.get("predicted_per_second", 0)
    choice = data["choices"][0]
    msg = choice["message"]
    finish = choice.get("finish_reason")

    print(f"\n=== {name} ===")
    print(f"wall_time: {dt:.2f}s  tokens: {out_tokens}  tps: {tps:.2f}  finish: {finish}")
    print("content len:", len(msg.get("content") or ""))
    print("reasoning_content len:", len(msg.get("reasoning_content") or ""))
    print("content preview:", (msg.get("content") or "")[:240].replace("\n", " "))
    if dump:
        print("FULL message keys:", list(msg.keys()))
        print("FULL message:", json.dumps(msg, indent=2)[:1500])

async def main():
    orch = [
        {"role": "system", "content": "You are an orchestrator. Respond ONLY with the final plan, no internal thoughts."},
        {"role": "user", "content": "Break 'build a tiny CLI todo app in Python' into 3 parallel subtasks, one line each."},
    ]
    w1 = [
        {"role": "system", "content": "You are worker 1. Output Python code only, no commentary."},
        {"role": "user", "content": "Implement a Todo class with add/list/remove."},
    ]
    w2 = [
        {"role": "system", "content": "You are worker 2. Output pytest code only."},
        {"role": "user", "content": "Write pytest tests for add/list/remove on a Todo class."},
    ]
    w3 = [
        {"role": "system", "content": "You are worker 3. Output an argparse CLI wrapper only."},
        {"role": "user", "content": "Wrap a Todo class with an argparse CLI for add/list/remove."},
    ]

    # First call dumps full structure so we know where the text lives.
    await call_agent("orchestrator", orch, 400, dump=True)
    await asyncio.gather(
        call_agent("worker_1", w1, 700),
        call_agent("worker_2", w2, 700),
        call_agent("worker_3", w3, 700),
    )

asyncio.run(main())
