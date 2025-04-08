#!/usr/bin/env python3

import json
import urllib.request
import os
import shutil
import util

BASE_URL = "https://goliath-assets-700331821540.us-east4.run.app"
API_ENDPOINT = "/api/media"
OUT_DIR = "./traxus.global/"
MEDIA_JSON = "json/media.json"

def scrapeAssets(blob, out_dir):
    for asset in blob:
        path = os.path.relpath(f"{out_dir}.{asset['url'].removeprefix(API_ENDPOINT)}") # Dumb path computation, will break on windows
        try:
            os.makedirs(os.path.dirname(path))
        except:
            pass
        print(f"Fetching {asset['url']}...")
        url = f"{BASE_URL}{asset['url']}"
        urllib.request.urlretrieve(url, filename=path)

try:
    shutil.rmtree(OUT_DIR)
except:
    pass
os.mkdir(OUT_DIR)

util.buildJsonMultipage(MEDIA_JSON, BASE_URL, API_ENDPOINT)
flattened = util.flattenJson(MEDIA_JSON)
scrapeAssets(flattened, OUT_DIR)