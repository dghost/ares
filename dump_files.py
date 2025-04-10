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

def printFilename(path, node):
    print(f"{path}/{node['fileName']}")

def traverseFilesystem(data, callback=printFilename, currentName = "", currentParent = None, currentIndent = ''):
    nodes = []
    for x in data:
        if currentParent is None:
            if x['parent'] == currentParent:
                nodes.append(x)
        else:
            if x['parent'] is not None:
                if x['parent']['id'] == currentParent:
                    nodes.append(x)
    nodes = sorted(nodes, key=lambda file: file['name'])
    for node in nodes:
        newName = f"{currentName}/{node['name']}"
        # print(newName)
        for file in node['files']:
            callback(newName, file)
        traverseFilesystem(data, callback, newName, node['id'], currentIndent + '-')

def dumpFilesystem(out_dir, data):
    # dump the filesystem path, ish. there's two directories where the breadcrumbs don't match the labels.
    print(f"Dumping filesystem to {out_dir}")
    def writeFile(path, file):
        ldir = os.path.join(os.path.normpath(out_dir), os.path.normpath(f"./{path}"))
        try:
            os.makedirs(ldir)
        except:
            pass
        fname = os.path.join(ldir, file['fileName'])
        with open(fname, "w") as out_file:
            if file['data']:
                out_file.write(file['data'])
            elif file['executableType']:
                out_file.write("<executable>")

    traverseFilesystem(data, writeFile)

def dumpFilesFlat(out_dir, data):
    files = []
    def recordFile(path, file):
        fname = f"{path}/{file['fileName']}"
        if file['data']:
            files.append(FileTuple(fname, file['data']))
        elif file['executableType']:
            files.append(FileTuple(fname, "<executable>"))
        else:
            files.append(FileTuple(fname, "<empty>"))

    traverseFilesystem(data, recordFile)

    files = sorted(files, key=lambda file: file.name.lower())

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

def findSusFiles(out_dir, data):
    files = []
    def recordFile(path, file):
        fname = f"{path}/{file['fileName']}"
        if file['data']:
            files.append(FileTuple(fname, file['data']))
        elif file['executableType']:
            files.append(FileTuple(fname, "<executable>"))
        else:
            files.append(FileTuple(fname, "<empty>"))

    traverseFilesystem(data, recordFile)
    files = sorted(files, key=lambda file: file.name)
    whitespace = [x for x in files if ' ' in x.name]
    duplicates = []
    filenames = [x.name for x in files]        
    duplicates = [x for x in files if filenames.count(x.name) > 1]
    if len(whitespace) > 0:
        fname = os.path.join(os.path.normpath(out_dir), "whitespace.txt")
        print(f"Found files with whitespace:")
        with open(fname, "w") as out_file:
            for file in whitespace:
                print(f"'{file.name}'")
                out_file.write(f"{file.name}:\n\n{file.data}\n\n")

    if len(duplicates) > 0:
        fname = os.path.join(os.path.normpath(out_dir), "collisions.txt")
        print(f"Found filename collisions:")
        with open(fname, "w") as out_file:
            for file in duplicates:
                print(f"'{file.name}'")
                out_file.write(f"{file.name}:\n\n{file.data}\n\n")


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
findSusFiles(OUT_DIR, data)
dumpExecutableFiles(data)

# traverseFilesystem(data)

config = util.openJson(TERMINAL_JSON)
dumpConfig(OUT_TERM, config)