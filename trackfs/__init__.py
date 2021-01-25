#!/usr/bin/env python3
# 
# Copyright 2020-2021 by Andreas Schmidt
# All rights reserved.
# This file is part of the trackfs project
# and licensed under the terms of the GNU Lesser General Public License v3.0.
# See https://github.com/andresch/trackfs for details.
#

from __future__ import print_function, absolute_import, division

from .fuseops import TrackFSOps


def main(foreground=True, allow_other=True):
    import os
    import sys
    import argparse

    from fuse import FUSE
    from . import fusepath

    import logging
    log = logging.getLogger(__name__)

    parser = argparse.ArgumentParser(
        description='''A FUSE filesystem for extracting individual tracks from FLAC+CUE files.

        Maps a directory to a mount point while replacing all FLAC+CUE files (with 
        embedded cue sheets) with multiple FLAC files for the individual tracks''')
    parser.add_argument(
        '-s', '--separator', dest='separator', default=fusepath.DEFAULT_TRACK_SEPARATOR,
        help=(
            f'The separator used inside the name of the track-files. '
            f'Must never occur in regular filenames (default: "{fusepath.DEFAULT_TRACK_SEPARATOR}")'
        )
    )
    parser.add_argument(
        '-i', '--ignore-tags', dest='ignore', default='CUE_TRACK.*|COMMENT',
        help=(
            'A regular expression for tags in the FLAC file that will not be '
            'copied to the track FLACs (default: "CUE_TRACK.*|COMMENT")'
        )
    )
    parser.add_argument(
        '-e', '--extension', dest='extension', default=fusepath.DEFAULT_FLAC_EXTENSION,
        help=f'The file extension of FLAC files (default: "{fusepath.DEFAULT_FLAC_EXTENSION}")'
    )
    parser.add_argument(
        '-k', '--keep-flac-cue', dest='keep', action='store_true',
        help='Keep the source FLAC+CUE file in the mapped filesystem'
    )
    parser.add_argument(
        '-t', '--title-length', dest='title_length', default=fusepath.DEFAULT_MAX_TITLE_LEN,
        help=f'Nr. of characters of the track title in filename of track (default: {fusepath.DEFAULT_MAX_TITLE_LEN})'
    )
    parser.add_argument(
        '--root-allowed', dest='rootok', action='store_true',
        help=(
            'Allow running as with root permissions; Neither necessary nor recommended. '
            'Use only when you know what you are doing'
        )
    )
    parser.add_argument(
        '-v', '--verbose', dest='verbose', action='store_true',
        help='Activate info-level logging'
    )
    parser.add_argument(
        '-d', '--debug', dest='debug', action='store_true',
        help='Activate debug-level logging'
    )
    parser.add_argument(
        'root',
        help='The root of the directory tree to be mapped'
    )
    parser.add_argument(
        'mount',
        help='The mount point for the mapped directory tree'
    )
    args = parser.parse_args()

    log_fmt = '{levelname:6s}[{threadName:15s}]({module:10}) {message}'
    if args.debug:
        logging.basicConfig(format=log_fmt, style='{', level=logging.DEBUG )
        log.setLevel(logging.DEBUG)
    elif args.verbose:
        logging.basicConfig(format=log_fmt, style='{', level=logging.INFO )
        log.setLevel(logging.INFO)

    if os.geteuid() == 0 and not args.rootok:
        print(
            f'''By default {os.path.basename(sys.argv[0])} don't allow to run with root permissions. 
     
If you are absolutely sure that that's what you want, use the option "--root-allowed"''',
            file=sys.stderr
        )
        exit(1)

    trackfs = TrackFSOps(
        args.root,
        keep_flac=args.keep, separator=args.separator, flac_extension=args.extension,
        title_length=int(args.title_length), tags_ignored=args.ignore
    )

    fuse = FUSE(trackfs, args.mount, foreground=foreground, allow_other=allow_other)


if __name__ == '__main__':
    main()
