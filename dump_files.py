#!/usr/bin/env python3

import os
import shutil
import collections
from collections.abc import MutableMapping,Iterable
import util

FileTuple = collections.namedtuple('FileTuple', ['name', 'data'])

BASE_URL = "https://uesc.io"
API_ENDPOINT = "/api/folders"
OUT_DIR = "./uesc.io/"
OUT_TERM = "./uesc-terminal/"
FOLDERS_JSON = "json/uesc-folders.json"
TERMINAL_JSON = "json/uesc-terminal.json"

def dumpFilesystem(out_dir, data):
    # dump the filesystem path, ish. there's two directories where the breadcrumbs don't match the labels.
    print(f"Dumping filesystem to {out_dir}")
    for dir in data:
        path = dir['breadcrumbs'][-1]['url']
        ldir = os.path.join(os.path.normpath(out_dir), os.path.normpath(f"./{path}"))
        try:
            os.makedirs(ldir)
        except:
            pass
        for file in dir['files']:
            fname = os.path.join(ldir, file['fileName'])
            with open(fname, "w") as out_file:
                if file['data']:
                    out_file.write(file['data'])
                elif file['executableType']:
                    out_file.write("<executable>")


def dumpFilesFlat(out_dir, data):
    files = []
    for dir in data:
        path = dir['breadcrumbs'][-1]['url']
        for file in dir['files']:
            fname = f"{path}/{file['fileName']}"
            if file['data']:
                files.append(FileTuple(fname, file['data']))
            elif file['executableType']:
                files.append(FileTuple(fname, "<executable>"))
            else:
                files.append(FileTuple(fname, "<empty>"))

    files = sorted(files, key=lambda file: file.name)

    fname = os.path.join(os.path.normpath(out_dir), "flat.txt")
    print(f"Dumping file contents to {fname}")
    with open(fname, "w") as out_file:
        for file in files:
            out_file.write(f"{file.name}:\n\n{file.data}\n\n")

def dumpExecutableFiles(data):
    files = []
    for dir in data:
        path = dir['breadcrumbs'][-1]['url']
        for file in dir['files']:
            fname = os.path.join(path,file['fileName'])
            if file['executableType']:
                files.append(FileTuple(fname, file['executableType']))

    files = sorted(files, key=lambda file: file.name)

    print(f"Found executables:")
    for file in files:
        print(f"{file.name}: {file.data}")

def dumpBrokenFiles(data):
    files = []
    for dir in data:
        path = dir['breadcrumbs'][-1]['url']
        for file in dir['files']:
            fname = os.path.join(path,file['fileName'])
            if ' ' in fname:
                files.append(FileTuple(fname, None))

    files = sorted(files, key=lambda file: file.name)

    print(f"Found files with whitespace:")
    for file in files:
        print(f"'{file.name}'")

def dumpConfig(out_dir, config):
    odir = os.path.normpath(out_dir)
    try:
        shutil.rmtree(odir)
    except:
        pass
    os.mkdir(odir)

    for key, value in config.items():
        items = []
        if isinstance(value, MutableMapping):
            items.append(value['text'])
        elif isinstance(value, Iterable):
            items.extend([x['text'] for x in value if 'text' in x])
        fname = os.path.join(odir, key)
        with open(fname, "w") as out_file:
            for item in items:
                out_file.write(f"{item}\n")


# clean the existing output
try:
    shutil.rmtree(OUT_DIR)
except:
    pass
os.mkdir(OUT_DIR)

# util.buildJsonMultipage(FOLDERS_JSON, BASE_URL, API_ENDPOINT)
# util.buildJson(TERMINAL_JSON, BASE_URL, "/api/terminal-config")
data = util.flattenJson(FOLDERS_JSON)

dumpFilesystem(OUT_DIR, data)
dumpFilesFlat(OUT_DIR, data)
dumpExecutableFiles(data)
dumpBrokenFiles(data)

config = util.openJson(TERMINAL_JSON)
dumpConfig(OUT_TERM, config)