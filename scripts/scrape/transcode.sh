#!/bin/bash

cd radio/
for file in *; do 
    if [ -d "$file" ]; then 
    	cd $file
    	ffmpeg -i manifest.m3u ../../radio_m4a/$file.m4a
    	cd ..
    fi 
done
cd ..