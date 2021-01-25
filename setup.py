# 
# Copyright 2020-2021 by Andreas Schmidt
# All rights reserved.
# This file is part of the trackfs project
# and licensed under the terms of the GNU Lesser General Public License v3.0.
# See https://github.com/andresch/trackfs for details.
#

import setuptools

def slurp(fn):
    with open(fn, "r", encoding="utf-8") as fh:
        return fh.read()


setuptools.setup(
    name="trackfs",
    version=slurp("VERSION"),
    author="Andreas Schmidt",
    author_email="author@example.com",
    description="A read-only FUSE filesystem that splits FLAC+CUE files into individual FLAC files per track",
    long_description=slurp("README.py.md"),
    long_description_content_type="text/markdown",
    url="https://github.com/andresch/trackfs",
    packages=setuptools.find_packages(),
    install_requires=[
       "mutagen", "fusepy", "Lark"
    ],
    classifiers=[
        "Programming Language :: Python :: 3",
        "Development Status :: 4 - Beta",
        "License :: OSI Approved :: GNU Lesser General Public License v3 or later (LGPLv3+)",
        "Operating System :: POSIX",
    ],
    python_requires='>=3.8',
    entry_points={
      'console_scripts': [
         'trackfs=trackfs.__init__:main'
      ],
    }
)
