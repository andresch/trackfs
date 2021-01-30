#!/bin/sh
# 
# Copyright 2020 by Andreas Schmidt
# All rights reserved.
# This file is part of the trackfs project
# and licensed under the terms of the GNU Lesser General Public License v3.0.
# See https://github.com/andresch/trackfs for details.
#

# tell fusepy where to find fuse library
export FUSE_LIBRARY_PATH=/usr/lib/libfuse.so
# make sure that python doesn't write into the mounted working directory
export PYTHONPYCACHEPREFIX=/tmp/__pycache__

# prepare the dev tools for trackfs 
ln -s /work/.pypirc ~/.pypirc

# create an isolated environment in ~/dev for the python tooling
mkdir -p ~/dev
cd ~/dev
for file in $(ls /work); do ln -s "/work/${file}" "${file}"; done
ln -s "/work/.github" ".github"
pip install -e .

# launch sub-shell
export ENV=/work/tools.sh
/bin/sh

# cleanup
pip uninstall --yes trackfs


