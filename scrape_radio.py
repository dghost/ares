#!/usr/bin/env python3

import urllib.request

import os
import shutil
import m3u8
import subprocess
import glob

def scrapeRadio(out_dir):
    odir = os.path.normpath(out_dir)
    try:
        shutil.rmtree(odir)
    except:
        pass
    os.mkdir(odir)
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
            with urllib.request.urlopen(url) as response:
                raw = [x.decode('utf-8').strip("\n") for x in response.readlines()]
                try:
                    idx = raw.index('#EXT-X-DISCONTINUITY')
                    headers = raw[:3]
                    startidx = 3
                    if raw[3].startswith("#EXT-X-MEDIA-SEQUENCE"):
                        startidx += 1
                    front = raw[startidx:idx]
                    back = raw[idx+2:]
                    raw =  headers + back + front
                except:
                    pass
                raw.append('#EXT-X-ENDLIST')
                try:
                    manifest = m3u8.loads("\n".join(raw))
                    with open(path, 'w') as file_name:
                        file_name.write(manifest.dumps())

                except Exception as e:
                    print(e)
                # print(raw)
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


def parseManifests(out_dir):
    odir = os.path.normpath(out_dir)
    existing_files = glob.glob(f"{odir}/**/manifest.m3u", recursive=True)
    existing_files.sort()
    print(existing_files)
    for path in existing_files:
        i = int(path.split('/')[1])
        raw = []
        with open(path) as read_file:
          raw = [x.strip('\n') for x in read_file.readlines()]
        try:
            idx = raw.index('#EXT-X-DISCONTINUITY')
            headers = raw[:3]
            startidx = 3
            if raw[3].startswith("#EXT-X-MEDIA-SEQUENCE"):
                startidx += 1
            front = raw[startidx:idx]
            back = raw[idx+2:]
            raw =  headers + back + front
        except:
            pass
        raw.append('#EXT-X-ENDLIST')
        try:
            manifest = m3u8.loads("\n".join(raw))
            with open(path, 'w') as file_out:
                file_out.write(manifest.dumps())
        except Exception as e:
            print(e)

        with open(path) as file_name:
            manifest = m3u8.loads(file_name.read())
            print(f"Fetching {len(manifest.files)} files")
            for file in manifest.files:
                furl = f"https://goliath-radio-server-700331821540.us-east4.run.app/{i}/{file}"
                fout = os.path.join(os.path.dirname(path), file)
                urllib.request.urlretrieve(furl, filename=fout)
                print('.', end="")
            print('')
        # cmd = ['ffmpeg', '-i', './manifest.m3u', str(f"./{i}.m4a")]
        # print(' '.join(cmd))
        # subprocess.run(cmd, cwd=os.path.dirname(path), shell=True, text=True)


scrapeRadio("./radio/")

# parseManifests("./radio")