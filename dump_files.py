#!/usr/bin/env python3

import os
import shutil
import collections
import util

FileTuple = collections.namedtuple('FileTuple', ['name', 'data'])

BASE_URL = "https://uesc.io"
API_ENDPOINT = "/api/folders"
OUT_DIR = "./uesc.io/"
FOLDERS_JSON = "json/folders.json"

def dumpFilesystem(out_dir, data):
    # dump the filesystem path, ish. there's two directories where the breadcrumbs don't match the labels.
    print(f"Dumping filesystem to {out_dir}")
    for dir in data:
        path = dir['breadcrumbs'][-1]['url']
        ldir = os.path.relpath(f"{out_dir}.{path}") # Dumb path computation, will break on windows
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

    fname = os.path.join(out_dir, "flat.txt")
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

def dumpHiddenFiles(data):
    files = []
    for dir in data:
        path = dir['breadcrumbs'][-1]['url']
        for file in dir['files']:
            fname = os.path.join(path,file['fileName'])
            if '.' in fname:
                files.append(FileTuple(fname, None))

    files = sorted(files, key=lambda file: file.name)
    print(f"Found hidden files:")
    for file in files:
        print(f"{file.name}")

# clean the existing output
try:
    shutil.rmtree(OUT_DIR)
except:
    pass
os.mkdir(OUT_DIR)

# util.buildJsonMultipage(FOLDERS_JSON, BASE_URL, API_ENDPOINT)
data = util.flattenJson(FOLDERS_JSON)

dumpFilesystem(OUT_DIR, data)
dumpFilesFlat(OUT_DIR, data)
dumpExecutableFiles(data)
dumpBrokenFiles(data)
# dumpHiddenFiles(data)