#!/usr/bin/env python3

import json
import os
import shutil
import util
import urllib.request

BASE_URL = "https://traxus.global"
ASSET_URL = "https://goliath-assets-700331821540.us-east4.run.app"
API_ENDPOINT = "/api/media/file/"
OUT_DIR = "./traxus.global/"
NAV_JSON = "json/traxus-nav.json"
CONFIG_JSON = "json/traxus-config.json"


def scrapeAssets(navData, configData, out_dir):
    try:
        shutil.rmtree(out_dir)
    except:
        pass
    os.mkdir(out_dir)

    drills = []
    if configData['drillUserManual']:
        drills.append(configData['drillUserManual'])
    if configData['hiddenDrillUserManual']:
        drills.append(configData['hiddenDrillUserManual'])

    for drill in drills:
        url = f"{ASSET_URL}{drill['url']}"
        filename = drill['filename']
        path = os.path.join(out_dir,filename)
        try:
            os.makedirs(os.path.dirname(path))
        except:
            pass
        print(f"Fetching {filename}...")
        urllib.request.urlretrieve(url, filename=path)

    for item in navData:
        if 'popup' in item and item['popup']:
            popup = item['popup']
            url = None
            filename = None
            if popup['image']:
                url = f"{ASSET_URL}{popup['image']['url']}"
                filename = popup['image']['filename']
            elif popup['videoURL']:
                url = popup['videoURL']
                filename = url.removeprefix("https://goliath.b-cdn.net/ads/")

            path = os.path.join(out_dir,filename)
            try:
                os.makedirs(os.path.dirname(path))
            except:
                pass
            print(f"Fetching {filename}...")
            urllib.request.urlretrieve(url, filename=path)


    # print(navData)
    # print(configData)

# util.buildJson(NAV_JSON, BASE_URL, "/api/nav")
# util.buildJson(CONFIG_JSON, BASE_URL, "/api/config")
nav = util.openJson(NAV_JSON)
config = util.openJson(CONFIG_JSON)

# scrapeAssets(nav, config, OUT_DIR)