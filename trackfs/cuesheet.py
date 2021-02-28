#!/usr/bin/env python3
# 
# Copyright 2020-2021 by Andreas Schmidt
# All rights reserved.
# This file is part of the trackfs project
# and licensed under the terms of the GNU Lesser General Public License v3.0.
# See https://github.com/andresch/trackfs for details.
#

#
# This module provides the cuesheet parsing functionality for trackfs.
# While it specifies a fully fledged parser for cuesheets, only those
# elements needed in the context for trackfs (which is the track information) 
# get extracted and exposed.
# 
# This implementation uses original spec of the cuesheet format as found on archive.org
# https://web.archive.org/web/20070614044112/http://www.goldenhawk.com/download/cdrwin.pdf
#

import math
from dataclasses import dataclass, field
from functools import cached_property

from lark import Lark, Transformer

import logging

from typing import List, Any, Dict

log = logging.getLogger(__name__)

# Lark cue-sheet grammar according to original spec
_CUE_LARK_GRAMMAR = r"""
   cue_sheet      : disc_entries tracks
   disc_entries   : disc_entry* 
   tracks         : track*
    
   ?disc_entry : catalog 
      | comment
      | performer
      | songwriter
      | title
      | file

   track          : "TRACK" INT TRACKTYPE track_entries
   track_entries  : ( track_entry )*
   ?track_entry   : isrc
      | flags
      | pregap
      | index
      | postgap
      | comment
      | performer
      | songwriter
      | title
      | file
      
   catalog        : "CATALOG" UPC_EAN
   comment        : "REM" REST_OF_LINE
   performer      : "PERFORMER" STRING
   songwriter     : "SONGWRITER" STRING
   title          : "TITLE" STRING
   file           : "FILE" STRING FILETYPE
   isrc           : "ISRC" ISRC
   flags          : "FLAGS" FLAG+
   index          : "INDEX" INDEX mmssff
   pregap         : "PREGAP" mmssff
   postgap        : "POSTGAP" mmssff
   mmssff         : TIME_ELEM ":" TIME_ELEM ":" TIME_ELEM
   
   TIME_ELEM      : DIGIT ~ 2
   INDEX          : DIGIT ~ 1..2
   UPC_EAN        : DIGIT ~ 12..13
   REST_OF_LINE   : /[^\n]*/ NEWLINE
   ISRC           : LETTER LETTER (DIGIT|LETTER) ~ 10
   
   STRING : ("\"" /.*?/ "\"") | /[^ \n]+/
   FILETYPE : "BINARY"  // Intel binary file (LSBF). Use for data files.
      | "MOTOROLA"      // Motorola binary file (MSBF). Use for data files.
      | "AIFF"          // Audio AIFF file (44.1KHz 16-bit stereo)
      | "WAVE"          // Audio WAVE file (44.1KHz 16-bit stereo)
      | "MP3"           // Audio MP3 file (44.1KHz 16-bit stereo)

   TRACKTYPE: "AUDIO"   // Audio/Music (2352)
      | "CDG"           // Karaoke CD+G (2448)
      | "MODE1/2048"    // CD-ROM Mode1 Data (cooked)
      | "MODE1/2352"    // CD-ROM Mode1 Data (raw)
      | "MODE2/2336"    // CD-ROM XA Mode2 Data
      | "MODE2/2352"    // CD-ROM XA Mode2 Data
      | "CDI/2336"      // CD-I Mode2 Data
      | "CDI/2352"      // CD-I Mode2 Data

   FLAG: "DCP"       // Digital copy permitted
      | "4CH"        // Four channel audio
      | "PRE"        // Pre-emphasis enabled (audio tracks only)
      | "SCMS"       // Serial Copy Management System
      | "DATA"       // set for data files

   %import common.NEWLINE
   %import common.INT
   %import common.LETTER
   %import common.DIGIT
   %import common.ESCAPED_STRING
   %import common.LETTER
   %import common.WS
   %ignore WS"""

_CUE_LARK_PARSER = Lark(_CUE_LARK_GRAMMAR, start='cue_sheet')


class TagTools:
    @staticmethod
    def object_tags(o: object, names: Dict[str, str] or List[str] = None) -> Dict[str, List[str]]:
        tags = {}
        if names is None:
            names = dir(o)
        if isinstance(names, List):
            names = zip(names, names)
        else:
            names = names.items()
        for (attr, tag) in names:
            value = getattr(o, attr, None)
            if isinstance(value, List):
                # hacky way to get rid of plural s
                tags[tag[0: -1].upper()] = value
            elif value is not None:
                tags[tag.upper()] = [value]
        return tags


@dataclass
class CueSheet:
    """Meta data from a parsed cue sheet
   
    """
    tracks: List['Track'] = field(default_factory=list)
    albumartists: List[str] = None
    album: str = None
    composers: List[str] = None
    catalog: str = None
    discid: str = None
    year: str = None
    discnumber: str = None
    totaldiscs: str = None

    def calc_track_times(self, disc_duration):
        for i in range(0, len(self.tracks) - 1):
            curr = self.tracks[i]
            curr.end = self.tracks[i + 1].start
        last = self.tracks[-1];
        last.end = Time.create(disc_duration)
        return self

    def tags(self):
        return TagTools.object_tags(self, [
            t for t in vars(self).keys() if t != 'tracks'])


@dataclass
class Track:
    """All information extracted for a track from a cue sheet

    Attributes
    ----------
    num         :  int
                   The track number
    type        :  string
                   The track type
    start       :  Time
                   The start timestamp of the track
    end         :  Time
                   The end timestamp of the track
    artists     :  list[string]
                   The artists performing the track
    composers   :  list[string]
                   The artists who have written the track

    The attributes end and duration don't originate directly from the the track elements
    in the cue sheet. They get set by calling a CueSheet's `calc_track_times` method
    once the whole cue sheet got parsed.
    """

    num: int
    type: str
    artists: List[str] = None
    composers: List[str] = None
    title: List[str] = None
    isrc: str = None
    start: 'Time' = None
    end: 'Time' = None

    @cached_property
    def duration(self) -> 'Time':
        return self.end - self.start

    def tags(self):
        return TagTools.object_tags(self,
                                    {'artists': 'artists', 'composers': 'composers',
                                     'title': 'title', 'num': 'tracknumber'})


@dataclass(frozen=True)
class Time:
    """ Timestamp / duration information with CD frame accuracy

    Attributes
    ----------
    mm      :   int
                minutes
    ss      :   int
                seconds
    ff      :   int
                sub-second CD frames (75 frames per second)


    The constructor supports various initializations:
    - <empty>      :  mm = ss == ff = 0
    - float        : interpreted as seconds (and fractions of)
    - int-triple   : interpreted as (mm, ss, ff
    - 6-charstring : interpreted as "mmssff"-string
    - three int    : interpreted as mm, ss, ff

    The class supports basic math (+,-)
    The string representation is "mmsscc"; `flac_time' returns the string 
    representation than FLAC expects ("mm:ss.cc")
    """

    mm: int
    ss: int
    ff: int

    @classmethod
    def create(cls, *args):
        if len(args) == 0:
            return cls(0, 0, 0)
        elif isinstance(args[0], float):
            # value as seconds with faction
            (sec, frac) = divmod(args[0], 1)
            (mm, ss) = divmod(int(sec), 60)
            return cls(mm, ss, int(frac * 75))
        elif isinstance(args[0], tuple):
            # value is tuple (minutes, seconds, frames)
            (mm, ss, ff) = args[0]
            return cls(mm, ss, ff)
        elif isinstance(args[0], str):
            # value is string template "Time"
            ts = args[0]
            assert len(ts) == 6
            return cls(int(ts[0:2]), int(ts[2:4]), int(ts[4:6]))
        elif isinstance(args[0], int):
            # value is three ints: mm, ss, ff
            assert len(args) == 3
            return cls(*args)

    def seconds(self):
        return (60.0 * self.mm) + self.ss + (self.ff / 100.0)

    def flac_time(self):
        return f'{self.mm:02d}:{self.ss:02d}.{int(100.0 / 75.0 * self.ff):02d}'

    def __repr__(self):
        return '%02d%02d%02d' % (self.mm, self.ss, self.ff)

    def __add__(self, other):
        (cs, ff) = divmod(self.ff + other.ff, 75)
        (cm, ss) = divmod(self.ss + other.ss + cs, 60)
        return Time(self.mm + other.mm + cm, ss, ff)

    def __sub__(self, other):
        ff = self.ff - other.ff
        ss = self.ss - other.ss - (0 if ff > 0 else 1)
        return Time(
            self.mm - other.mm - (0 if ss > 0 else 1),
            ss if ss >= 0 else ss + 60,
            ff if ff >= 0 else ff + 75
        )


class _CueTransformer(Transformer):
    """Transforms the Lark-parser-tree in a CueSheet"""

    @staticmethod
    def _extend__list_arg(args, name, value):
        # most rippers use ";" as delimiter for multiple values inside a
        # single entry rather than having multiple entries
        values = [p.strip() for p in value.split(";")]
        old_value = args.get(name, None)
        if old_value is None:
            args[name] = values
        else:
            args[name] = old_value + values

    @staticmethod
    def _map_to_arg(args, mappings, name, value) -> bool:
        mapping = mappings.get(name, None)
        if mapping is None:
            return False
        (mapped_name, is_list) = mapping
        if is_list:
            _CueTransformer._extend__list_arg(args, mapped_name, value)
        else:
            args[mapped_name] = value
        return True

    ALBUM_MAPPINGS = {
        'performer': ("albumartists", True),
        'songwriter': ("composers", True),
        'title': ("album", False),
        'catalog': ("catalog", False)
    }

    COMMENT_MAPPINGS = {
        'discid': ("discid", False),
        'date': ("year", False),
        'discnumber': ("discnumber", False),
        'totaldiscs': ("totaldiscs", False)
    }

    def cue_sheet(self, subtrees):
        args = {'tracks': subtrees[1].children}
        for entry in subtrees[0].children:
            (name, value) = (entry.data.lower(), entry.children[0])
            if _CueTransformer._map_to_arg(args, self.ALBUM_MAPPINGS, name, value):
                pass
            elif name == 'comment':
                # extract known key-value comments
                splits = value.split(' ', 1)
                if len(splits) > 1:
                    _CueTransformer._map_to_arg(args, self.COMMENT_MAPPINGS, splits[0].lower(), splits[1])
        return CueSheet(**args)

    TRACK_MAPPINGS = {
        'performer': ("artists", True),
        'songwriter': ("composers", True),
        'title': ("title", False),
        'isrc': ("isrc", False)
    }

    def track(self, subtrees):
        args = {'num': subtrees[0], 'type': subtrees[1]}
        for entry in subtrees[2].children:
            (name, value) = (entry.data.lower(), entry.children[0])
            if _CueTransformer._map_to_arg(args, self.TRACK_MAPPINGS, name, value):
                pass
            elif name == 'index':
                idx = entry.children
                # we're only interested in index 1
                if idx[0] == 1:
                    args['start'] = idx[1]
        return Track(**args)

    def mmssff(self, elems):
        return Time(elems[0], elems[1], elems[2])

    def REST_OF_LINE(self, comment_line):
        return comment_line[1:-1] if len(comment_line) > 0 else ""

    def STRING(self, string):
        return string[1:-1] if string[0] == '"' else str(string)

    UPC_EAN = str
    ISRC = str
    TRACKTYPE = str
    INT = int
    INDEX = int
    TIME_ELEM = int


def parse(cuesheet, disc_total_length):
    return _CueTransformer(visit_tokens=True
                           ).transform(_CUE_LARK_PARSER.parse(cuesheet)
                                       ).calc_track_times(disc_total_length)


if __name__ == '__main__':
    test_cue = r"""REM DISCID A10A2E0D
    PERFORMER "Zaz"
    TITLE "Paris"
    CATALOG 5054196339524
    REM DATE 2014
    REM DISCNUMBER 1
    REM TOTALDISCS 1
    REM COMMENT "CUERipper v2.1.4 Copyright (C) 2008-12 Grigory Chudov"
    FILE "Zaz - Paris.flac" WAVE
      TRACK 01 AUDIO
        PERFORMER Zaz
        PERFORMER Zazo
        TITLE "Paris sera toujours Paris"
        ISRC FR2PY1403200
        INDEX 01 00:00:00
      TRACK 02 AUDIO
        PERFORMER "Zaz; Zazo"
        TITLE "Sous le ciel de Paris"
        ISRC FR2PY1403250
        INDEX 01 02:58:68"""

    print(
        _CueTransformer(
            visit_tokens=True
        ).transform(
            _CUE_LARK_PARSER.parse(test_cue)
        )
    )
