"""Comprehensive API + performance test for the AI News Aggregator Bot.

Tests every endpoint, measures response times, and reports results.
Run with: python scripts/api_test.py
"""

import time
import json
import httpx
import sys

BASE = "http://localhost:8080"

# Track results
results = []
passed = 0
failed = 0
total_time = 0


def test(method, path, name, expected_status=200, json_body=None, check=None):
    """Run a single API test."""
    global passed, failed, total_time
    url = f"{BASE}{path}"
    start = time.perf_counter()

    try:
        if method == "GET":
            r = httpx.get(url, timeout=30)
        elif method == "POST":
            r = httpx.post(url, json=json_body, timeout=30)
        elif method == "PUT":
            r = httpx.put(url, json=json_body, timeout=30)
        elif method == "DELETE":
            r = httpx.delete(url, timeout=30)
        else:
            raise ValueError(f"Unknown method: {method}")

        elapsed = (time.perf_counter() - start) * 1000
        total_time += elapsed

        status_ok = r.status_code == expected_status

        check_ok = True
        check_msg = ""
        if check and status_ok:
            try:
                data = r.json()
                check_ok, check_msg = check(data)
            except Exception as e:
                check_ok = False
                check_msg = f"JSON parse error: {e}"

        ok = status_ok and check_ok

        if ok:
            passed += 1
            icon = "PASS"
        else:
            failed += 1
            icon = "FAIL"

        status_info = f"HTTP {r.status_code}" if not status_ok else ""
        extra = check_msg if check_msg else status_info

        results.append({
            "name": name,
            "method": method,
            "path": path,
            "status": r.status_code,
            "time_ms": round(elapsed, 1),
            "ok": ok,
            "extra": extra,
        })

        print(f"  [{icon}] {name:55s} {elapsed:7.1f}ms  {r.status_code}  {extra}")

    except Exception as e:
        elapsed = (time.perf_counter() - start) * 1000
        total_time += elapsed
        failed += 1
        results.append({
            "name": name,
            "method": method,
            "path": path,
            "status": 0,
            "time_ms": round(elapsed, 1),
            "ok": False,
            "extra": str(e),
        })
        print(f"  [FAIL] {name:55s} {elapsed:7.1f}ms  ERR  {e}")


def main():
    global passed, failed

    print("=" * 80)
    print("AI NEWS AGGREGATOR BOT — Full API & Performance Test")
    print(f"Target: {BASE}")
    print("=" * 80)

    # =========================================================================
    # 1. HEALTH & INFRASTRUCTURE
    # =========================================================================
    print("\n--- 1. Health & Infrastructure ---")

    test("GET", "/health", "Health check",
         check=lambda d: (d.get("status") in ("healthy", "degraded"), f"status={d.get('status')}"))

    test("GET", "/health", "Database check",
         check=lambda d: (d["checks"]["database"]["status"] == "ok",
                          f"db={d['checks']['database']['status']}"))

    test("GET", "/health", "Redis check",
         check=lambda d: (d["checks"]["redis"]["status"] == "ok",
                          f"redis={d['checks']['redis']['status']}"))

    # =========================================================================
    # 2. DASHBOARD PAGES (HTML)
    # =========================================================================
    print("\n--- 2. Dashboard Pages ---")

    test("GET", "/admin/", "Overview page")
    test("GET", "/admin/sources", "Sources page")
    test("GET", "/admin/articles", "Articles page")
    test("GET", "/admin/categories", "Categories page")
    test("GET", "/admin/logs", "Logs page")
    test("GET", "/admin/settings", "Settings page")
    test("GET", "/admin/commands", "Commands page")
    test("GET", "/admin/articles/1", "Article detail page (id=1)")
    test("GET", "/admin/articles/9999", "Article detail (not found)", expected_status=200)

    # =========================================================================
    # 3. DASHBOARD API — Stats
    # =========================================================================
    print("\n--- 3. Stats API ---")

    test("GET", "/admin/api/stats", "Get dashboard stats",
         check=lambda d: (
             "total_articles_today" in d and "articles_by_category" in d,
             f"articles_today={d.get('total_articles_today')}, sources={d.get('total_sources')}"
         ))

    # =========================================================================
    # 4. DASHBOARD API — Sources CRUD
    # =========================================================================
    print("\n--- 4. Sources API ---")

    test("GET", "/admin/api/sources", "List all sources",
         check=lambda d: (isinstance(d, list) and len(d) > 0,
                          f"count={len(d) if isinstance(d, list) else 'N/A'}"))

    # Create a test source
    test_source = {
        "name": "Test Source (API Test)",
        "url": "https://test.example.com/feed",
        "scraper_type": "rss",
        "schedule_cron": "*/60 * * * *",
        "priority": 1,
    }
    new_source_id = None

    def capture_source_id(d):
        nonlocal new_source_id
        new_source_id = d.get("id")
        return (new_source_id is not None, f"id={new_source_id}")

    test("POST", "/admin/api/sources", "Create source", json_body=test_source,
         check=capture_source_id)

    if new_source_id:
        test("PUT", f"/admin/api/sources/{new_source_id}", "Update source",
             json_body={"name": "Updated Test Source", "priority": 2},
             check=lambda d: (d.get("name") == "Updated Test Source", f"name={d.get('name')}"))

        test("POST", f"/admin/api/sources/{new_source_id}/toggle", "Toggle source",
             check=lambda d: ("enabled" in d, f"enabled={d.get('enabled')}"))

        test("DELETE", f"/admin/api/sources/{new_source_id}", "Delete source")

    # =========================================================================
    # 5. DASHBOARD API — Articles
    # =========================================================================
    print("\n--- 5. Articles API ---")

    test("GET", "/admin/api/articles", "List articles (no filter)",
         check=lambda d: ("items" in d and len(d["items"]) > 0,
                          f"count={len(d.get('items', []))}"))

    test("GET", "/admin/api/articles?search=AI", "Search articles (query=AI)",
         check=lambda d: ("items" in d, f"count={len(d.get('items', []))}"))

    test("GET", "/admin/api/articles?score_min=3", "Filter articles (score>=3)",
         check=lambda d: ("items" in d, f"count={len(d.get('items', []))}"))

    test("GET", "/admin/api/articles?status=routed", "Filter articles (status=routed)",
         check=lambda d: ("items" in d, f"count={len(d.get('items', []))}"))

    test("GET", "/admin/api/articles/1", "Get article detail (id=1)",
         check=lambda d: ("title" in d, f"title={d.get('title', '')[:40]}"))

    test("GET", "/admin/api/articles/9999", "Get article (not found)", expected_status=404)

    # =========================================================================
    # 6. DASHBOARD API — Categories
    # =========================================================================
    print("\n--- 6. Categories API ---")

    test("GET", "/admin/api/categories", "List categories",
         check=lambda d: (isinstance(d, list) and len(d) >= 11,
                          f"count={len(d) if isinstance(d, list) else 'N/A'}"))

    # =========================================================================
    # 7. DASHBOARD API — Logs
    # =========================================================================
    print("\n--- 7. Logs API ---")

    test("GET", "/admin/api/logs", "List post logs",
         check=lambda d: ("items" in d, f"count={len(d.get('items', []))}"))

    # =========================================================================
    # 8. BOT COMMANDS (via command tester)
    # =========================================================================
    print("\n--- 8. Bot Commands ---")

    test("POST", "/admin/api/commands/execute", "Command: /help",
         json_body={"command": "/help"},
         check=lambda d: ("card" in d and d["card"] is not None, "has card"))

    test("POST", "/admin/api/commands/execute", "Command: /latest",
         json_body={"command": "/latest"},
         check=lambda d: ("cards" in d and len(d.get("cards", [])) > 0,
                          f"cards={len(d.get('cards', []))}"))

    test("POST", "/admin/api/commands/execute", "Command: /latest AI Products",
         json_body={"command": "/latest AI Products"},
         check=lambda d: ("cards" in d, f"cards={len(d.get('cards', []))}"))

    test("POST", "/admin/api/commands/execute", "Command: /search Meta",
         json_body={"command": "/search Meta"},
         check=lambda d: ("text" in d, d.get("text", "")[:60]))

    test("POST", "/admin/api/commands/execute", "Command: /search transformer",
         json_body={"command": "/search transformer"},
         check=lambda d: ("text" in d, d.get("text", "")[:60]))

    test("POST", "/admin/api/commands/execute", "Command: /digest now",
         json_body={"command": "/digest now"},
         check=lambda d: (d.get("card") is not None or "article" in d.get("text", "").lower(),
                          d.get("text", "")[:60]))

    test("POST", "/admin/api/commands/execute", "Command: /subscribe AI Security & Risks",
         json_body={"command": "/subscribe AI Security & Risks"},
         check=lambda d: ("subscribed" in d.get("text", "").lower() or
                          "already" in d.get("text", "").lower(),
                          d.get("text", "")[:60]))

    test("POST", "/admin/api/commands/execute", "Command: /unsubscribe AI Security & Risks",
         json_body={"command": "/unsubscribe AI Security & Risks"},
         check=lambda d: ("unsubscribed" in d.get("text", "").lower() or
                          "not subscribed" in d.get("text", "").lower(),
                          d.get("text", "")[:60]))

    test("POST", "/admin/api/commands/execute", "Command: /summarize (cached URL)",
         json_body={"command": "/summarize https://techcrunch.com/2026/04/23/meta-will-now-allow-parents-to-see-the-topics-their-child-discussed-with-meta-ai/"},
         check=lambda d: ("text" in d, d.get("text", "")[:60]))

    test("POST", "/admin/api/commands/execute", "Command: /settings",
         json_body={"command": "/settings"},
         check=lambda d: ("text" in d, d.get("text", "")[:60]))

    test("POST", "/admin/api/commands/execute", "Command: unknown /foo",
         json_body={"command": "/foo"},
         check=lambda d: ("text" in d, d.get("text", "")[:60]))

    test("POST", "/admin/api/commands/execute", "Command: empty",
         json_body={"command": ""},
         check=lambda d: ("text" in d, d.get("text", "")[:60]))

    # =========================================================================
    # 9. PERFORMANCE — Rapid-fire requests
    # =========================================================================
    print("\n--- 9. Performance (rapid-fire) ---")

    # 10 rapid requests to stats endpoint
    times = []
    for i in range(10):
        start = time.perf_counter()
        r = httpx.get(f"{BASE}/admin/api/stats", timeout=10)
        elapsed = (time.perf_counter() - start) * 1000
        times.append(elapsed)

    avg = sum(times) / len(times)
    p95 = sorted(times)[int(len(times) * 0.95)]
    max_t = max(times)
    min_t = min(times)

    perf_ok = avg < 500  # under 500ms average
    if perf_ok:
        passed += 1
    else:
        failed += 1

    print(f"  [{'PASS' if perf_ok else 'FAIL'}] Stats endpoint x10                "
          f"         avg={avg:.1f}ms  min={min_t:.1f}ms  max={max_t:.1f}ms  p95={p95:.1f}ms")

    results.append({"name": "Stats x10 perf", "time_ms": round(avg, 1), "ok": perf_ok,
                     "extra": f"avg={avg:.1f}ms p95={p95:.1f}ms"})

    # 10 rapid requests to articles endpoint
    times = []
    for i in range(10):
        start = time.perf_counter()
        r = httpx.get(f"{BASE}/admin/api/articles", timeout=10)
        elapsed = (time.perf_counter() - start) * 1000
        times.append(elapsed)

    avg = sum(times) / len(times)
    p95 = sorted(times)[int(len(times) * 0.95)]
    max_t = max(times)

    perf_ok = avg < 500
    if perf_ok:
        passed += 1
    else:
        failed += 1

    print(f"  [{'PASS' if perf_ok else 'FAIL'}] Articles endpoint x10              "
          f"         avg={avg:.1f}ms  min={min(times):.1f}ms  max={max_t:.1f}ms  p95={p95:.1f}ms")

    results.append({"name": "Articles x10 perf", "time_ms": round(avg, 1), "ok": perf_ok,
                     "extra": f"avg={avg:.1f}ms p95={p95:.1f}ms"})

    # 10 rapid command executions
    times = []
    for i in range(10):
        start = time.perf_counter()
        r = httpx.post(f"{BASE}/admin/api/commands/execute",
                       json={"command": "/latest"}, timeout=10)
        elapsed = (time.perf_counter() - start) * 1000
        times.append(elapsed)

    avg = sum(times) / len(times)
    p95 = sorted(times)[int(len(times) * 0.95)]

    perf_ok = avg < 1000  # commands can be slower
    if perf_ok:
        passed += 1
    else:
        failed += 1

    print(f"  [{'PASS' if perf_ok else 'FAIL'}] /latest command x10                "
          f"         avg={avg:.1f}ms  min={min(times):.1f}ms  max={max(times):.1f}ms  p95={p95:.1f}ms")

    results.append({"name": "Command x10 perf", "time_ms": round(avg, 1), "ok": perf_ok,
                     "extra": f"avg={avg:.1f}ms p95={p95:.1f}ms"})

    # =========================================================================
    # SUMMARY
    # =========================================================================
    print("\n" + "=" * 80)
    print("RESULTS SUMMARY")
    print("=" * 80)

    total = passed + failed
    print(f"\n  Total tests:  {total}")
    print(f"  Passed:       {passed}")
    print(f"  Failed:       {failed}")
    print(f"  Pass rate:    {passed/total*100:.1f}%")
    print(f"  Total time:   {total_time:.0f}ms")

    # Performance summary
    api_times = [r["time_ms"] for r in results if r.get("time_ms")]
    if api_times:
        print(f"\n  Response times:")
        print(f"    Average:    {sum(api_times)/len(api_times):.1f}ms")
        print(f"    Fastest:    {min(api_times):.1f}ms")
        print(f"    Slowest:    {max(api_times):.1f}ms")

    # Failed tests detail
    failures = [r for r in results if not r.get("ok")]
    if failures:
        print(f"\n  Failed tests:")
        for f in failures:
            print(f"    - {f['name']}: {f.get('extra', '')}")

    print("\n" + "=" * 80)

    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
