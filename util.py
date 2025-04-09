#!/usr/bin/env python3

import json
import urllib.request

def buildJsonMultipage(filename, base_url, api_endpoint):
    print(f"Fetching API root from: {base_url}{api_endpoint}")
    with urllib.request.urlopen(f"{base_url}{api_endpoint}") as response:
        html = response.read()
        root = json.loads(html) 

    numDocs = root['totalDocs']
    totalPages = root['totalPages']
    print(f"- {numDocs} docs on {totalPages} pages")

    pages = {}
    for i in range (1, totalPages + 1):
        print('.', end='')
        with urllib.request.urlopen(f"{base_url}{api_endpoint}?page={i}") as response:
            html = response.read()
            page = json.loads(html)
            pages[i] = page
    print('')
    with open(filename, "w") as out_file:
        out_file.write(json.dumps(pages, indent=4))

def buildJson(filename, base_url, api_endpoint):
    print(f"Fetching API root from: {base_url}{api_endpoint}")
    with urllib.request.urlopen(f"{base_url}{api_endpoint}") as response:
        html = response.read()
        root = json.loads(html)         
        with open(filename, "w") as out_file:
            out_file.write(json.dumps(root, indent=4))

def flattenJson(filename):
    docs = []
    with open(filename) as in_file:
        pages = json.load(in_file)
        for page in pages.values():
            docs += page['docs']
    return docs

def openJson(filename):
    with open(filename) as in_file:
        return json.load(in_file)
