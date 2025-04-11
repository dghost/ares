#!/usr/bin/env python3

import urllib.request

import os
import shutil


def scrapRadio(out_dir):
    odir = os.path.normpath(out_dir)
    FM_START = 75
    FM_END = 133
    for i in range(FM_START, FM_END+1):
        url = f"https://goliath-radio-server-700331821540.us-east4.run.app/{i}/manifest"
        path = os.path.join(odir, os.path.normpath(f"{i}/manifest.m3u"))
        try:
            os.makedirs(os.path.dirname(path))
        except:
            pass
        try:
            urllib.request.urlretrieve(url, filename=path)
        except:
            try:
                shutil.rmtree(os.path.dirname(path))
            except:
                pass

    # https://goliath-radio-server-700331821540.us-east4.run.app/130/manifest



scrapRadio("./radio/")