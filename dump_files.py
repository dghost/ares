#!/usr/bin/env python3

import json
import os
import shutil
import collections

FileTuple = collections.namedtuple('FileTuple', ['name', 'data'])

def dumpFilesystem(out_dir, data):
    # dump the filesystem path, ish. there's two directories where the breadcrumbs don't match the labels.
    print(f"Dumping filesystem to {out_dir}")
    for dir in data:
        path = dir['breadcrumbs'][-1]['url']
        ldir = out_dir + path + "/"
        try:
            os.makedirs(ldir)
        except:
            pass
        for file in dir['files']:
            fname = ldir + file['fileName']
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

    print(f"Dumping file contents to {out_dir + "flat.txt"}")
    with open(out_dir + "flat.txt", "w") as out_file:
        for file in files:
            out_file.write(f"{file.name}:\n\n{file.data}\n\n")

def dumpExecutableFiles(data):
    files = []
    for dir in data:
        path = dir['breadcrumbs'][-1]['url']
        for file in dir['files']:
            fname = f"{path}/{file['fileName']}"
            if file['executableType']:
                files.append(FileTuple(fname, file['executableType']))

    files = sorted(files, key=lambda file: file.name)

    print(f"Found executables:")
    for file in files:
        print(f"{file.name}: {file.data}")


# clean the existing output
out_dir = "./uesc.io/"
try:
    shutil.rmtree(out_dir)
except:
    pass
os.mkdir(out_dir)

# load the raw json
data = []
with open('raw.json') as json_file:
    data = json.load(json_file)

dumpFilesystem(out_dir, data)
dumpFilesFlat(out_dir, data)
dumpExecutableFiles(data)
