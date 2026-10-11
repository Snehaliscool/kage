#!/usr/bin/env python3
import json, sys, urllib.parse, urllib.request
query = " ".join(sys.argv[1:]) or "latest Arch Linux news"
url = "http://127.0.0.1:8888/search?" + urllib.parse.urlencode({"q": query, "format": "json"})
with urllib.request.urlopen(url, timeout=25) as response:
    data = json.load(response)
results = data.get("results", [])
print(f"SearXNG results: {len(results)}")
for i, item in enumerate(results[:8], 1):
    print(f"{i}. {item.get('title', '(no title)')}\n   {item.get('url', '(no URL)')}\n   {item.get('content', '')}\n")
