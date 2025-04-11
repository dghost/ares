#!/bin/bash

cd radio/
for file in *; do 
    if [ -d "$file" ]; then 
    	cd $file
    	ffmpeg -i manifest.m3u ../$file.m4a
    	cd ..
    fi 
done
cd ..