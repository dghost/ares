#!/usr/bin/env python3

import json
import urllib.request
import os.path
import shutil

BASE_URL = "https://goliath-assets-700331821540.us-east4.run.app"
API_ENDPOINT = "/api/media"
OUT_DIR = "./traxus.global/"
MEDIA_JSON = "media.json"

def buildJson(filename):
    print(f"Fetching API root from: {BASE_URL}{API_ENDPOINT}")
    with urllib.request.urlopen(f"{BASE_URL}{API_ENDPOINT}") as response:
        html = response.read()
        root = json.loads(html) 

    numDocs = root['totalDocs']
    totalPages = root['totalPages']
    print(f"{numDocs} docs on {totalPages} pages")

    pages = []
    for i in range (1, totalPages + 1):
        print(f"Fetching page {i} of {totalPages}")
        with urllib.request.urlopen(f"{BASE_URL}{API_ENDPOINT}?page={i}") as response:
            html = response.read()
            page = json.loads(html)
            pages.append(page)

    with open(filename, "w") as out_file:
        out_file.write(json.dumps(pages))

def scrapeAssets(filename, out_dir):
    docs = []
    with open(filename) as in_file:
        pages = json.load(in_file)
        for page in pages:
            docs += page['docs']
    for doc in docs:
        path = os.path.relpath(f"{out_dir}.{doc['url'].removeprefix(API_ENDPOINT)}") # Dumb path computation, will break on windows
        try:
            os.makedirs(os.path.dirname(path))
        except:
            pass
        print(f"Fetching {doc['url']}...")
        url = f"{BASE_URL}{doc['url']}"
        urllib.request.urlretrieve(url, filename=path)

try:
    shutil.rmtree(OUT_DIR)
except:
    pass
os.mkdir(OUT_DIR)

# buildJson(MEDIA_JSON)
scrapeAssets(MEDIA_JSON, OUT_DIR)