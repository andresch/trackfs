# 
# Copyright 2020-2021 by Andreas Schmidt
# All rights reserved.
# This file is part of the trackfs project
# and licensed under the terms of the GNU Lesser General Public License v3.0.
# See https://github.com/andresch/trackfs for details.
#

#
# A few useful commands within the dev-docker container of trackfs
# the dev-launcher will make this file ~/.shrc
#

thelp () {
    echo "
A few shortcuts for trackfs development:

tfs         run trackfs on the std mount points with --root-enabled
pidtfs      return the pid of the current running trackfs instance
killtfs     kill the running trackfs instance and unmount /dst
tfshelp     prints this info
pypackage   build the distribution files for the trackfs python package
pypubtest   published the distribution files on testpypi
pypubprod   publishes the distribution files on pypi
lt          reloads the the trackfs dev tools 
st          prints the trackfs dev tools code
"
}

realias () {
    if alias "${1}" >/dev/null 2>/dev/null; then unalias "${1}"; fi
    alias "${1}"="${2}"
}

realias tfs "/usr/local/bin/trackfs --root-allowed /src /dst"
realias st "cat /work/tools.sh"
realias lt "source /work/tools.sh"

# find pid of trackfs
pidtfs () { 
    ps -o comm,pid | awk '/trackfs/{print $2}' 
}

# kill trackfs and unmount /dst
killtfs () {
    kill -KILL "$(pidtfs)"
    fusermount -u /dst
}

pypackage () {
    local curr="${PWD}"
    cd ~/dev
    rm -f dist/*
    python3 setup.py sdist bdist_wheel
    # now run any arbitrary command that has been passed in
    if [[ ${#} -gt 0 ]]; then
        $@
    fi
    cd "${curr}"
}

realias pypubtest 'pypackage twine upload -r testpypi dist/*'
realias pypubprod 'pypackage twine upload dist/*'
