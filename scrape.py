#!/usr/bin/env python3

import urllib.request
import os
import shutil
import util
import glob

BASE_URL = "https://traxus.global"
ASSET_URL = "https://goliath-assets-700331821540.us-east4.run.app"
API_ENDPOINT = "/api/media/file/"

TRAXUS_DIR = "./traxus.global/"
NAV_JSON = "json/traxus-nav.json"
CONFIG_JSON = "json/traxus-config.json"

GOLIATH_JSON = "json/goliath-media.json"
GOLIATH_OUT = "./goliath/"


def scrapeTraxusAssets(navData, configData, out_dir):
    odir = os.path.normpath(out_dir)
    try:
        shutil.rmtree(odir)
    except:
        pass
    os.mkdir(odir)

    drills = []
    if configData['drillUserManual']:
        drills.append(configData['drillUserManual'])
    if configData['hiddenDrillUserManual']:
        drills.append(configData['hiddenDrillUserManual'])

    for drill in drills:
        url = f"{ASSET_URL}{drill['url']}"
        filename = drill['filename']
        path = os.path.join(os.path.normpath(odir), filename)
        try:
            os.makedirs(os.path.dirname(path))
        except:
            pass
        print(f"- Fetching {filename}...")
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

            path = os.path.join(os.path.normpath(odir), filename)
            try:
                os.makedirs(os.path.dirname(path))
            except:
                pass
            print(f"- Fetching {filename}...")
            urllib.request.urlretrieve(url, filename=path)

def scrapeAssets(blob, out_dir):
    odir = os.path.normpath(out_dir)
    try:
        shutil.rmtree(odir)
    except:
        pass
    os.mkdir(odir)

    existing_files = glob.glob(f"{TRAXUS_DIR}**", recursive=True)
    existing_files = [os.path.basename(x) for x in existing_files if os.path.isfile(x)]
    for asset in blob:
        if asset['filename'] not in existing_files:
            path = os.path.join(odir, os.path.normpath(asset['url'].removeprefix(API_ENDPOINT)))
            try:
                os.makedirs(os.path.dirname(path))
            except:
                pass
            print(f"- Fetching {asset['filename']}...")
            url = f"{ASSET_URL}{asset['url']}"
            urllib.request.urlretrieve(url, filename=path)



util.buildJson(NAV_JSON, BASE_URL, "/api/nav")
util.buildJson(CONFIG_JSON, BASE_URL, "/api/config")
util.buildJsonMultipage(GOLIATH_JSON, ASSET_URL, '/api/media')

nav = util.openJson(NAV_JSON)
config = util.openJson(CONFIG_JSON)
flattened = util.flattenJson(GOLIATH_JSON)

print(f"Fetching assets referenced from {BASE_URL}...")
scrapeTraxusAssets(nav, config, TRAXUS_DIR)
print("Fetching assets scraped from goliath CDN...")
scrapeAssets(flattened, GOLIATH_OUT)
