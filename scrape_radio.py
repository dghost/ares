#!/usr/bin/env python3

import urllib.request

import os
import shutil
import m3u8


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
            with open(path) as file_name:
                manifest = m3u8.loads(file_name.read())
                print(f"Fetching {len(manifest.files)} files")
                for file in manifest.files:
                    furl = f"https://goliath-radio-server-700331821540.us-east4.run.app/{i}/{file}"
                    fout = os.path.join(odir, os.path.normpath(f"{i}/{file}"))
                    urllib.request.urlretrieve(furl, filename=fout)
                    print('.', end="")
                print('')
        except:
            try:
                shutil.rmtree(os.path.dirname(path))
            except:
                pass

    # https://goliath-radio-server-700331821540.us-east4.run.app/130/manifest



scrapRadio("./radio/")