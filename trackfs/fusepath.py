#!/usr/bin/env python3
# 
# Copyright 2020-2021 by Andreas Schmidt
# All rights reserved.
# This file is part of the trackfs project
# and licensed under the terms of the GNU Lesser General Public License v3.0.
# See https://github.com/andresch/trackfs for details.
#

#
# This module provides mapping of names between the virtual trackfs 
# filesystem and the underlying root filesystem, esp. the naming of
# the individual track-files
#

import os
import re
import string
import unicodedata

from dataclasses import dataclass, field
from functools import cached_property

from . import cuesheet
from . import flacinfo

import logging
log = logging.getLogger(__name__)

DEFAULT_TRACK_SEPARATOR     : str   = '.#-#.'
DEFAULT_MAX_TITLE_LEN       : int   = 20
DEFAULT_FLAC_EXTENSION      : str   = '.flac'
DEFAULT_VALID_CHARS         : str   = "-_() " + string.ascii_letters + string.digits
DEFAULT_KEEP_PATH           : bool  = False

@dataclass(frozen=True)
class Factory():
    '''manages the configuration options for the virtual fuse paths'''
    
    track_separator         : str   = DEFAULT_TRACK_SEPARATOR
    max_title_len           : int   = DEFAULT_MAX_TITLE_LEN
    flac_extension          : str   = DEFAULT_FLAC_EXTENSION
    valid_filename_chars    : str   = DEFAULT_VALID_CHARS
    keep_flac               : bool  = DEFAULT_KEEP_PATH
    
    @cached_property
    def track_file_regex(self):
        (separator_rex, extension_rex) = [ s.replace('.','\\.') for s in [self.track_separator, self.flac_extension] ]
        flac_cue_rex = (
            '^(?P<basename>.*)'+separator_rex
            + '(?P<num>\\d+)(?P<title>(\\.[^\\.]{,'+str(self.max_title_len)
            + '}?)?)\\.(?P<start>\\d{6})-(?P<end>\d{6})'
            + '(?P<extension>'+extension_rex+')$'
        )
        log.debug("Factory.track_file_regex: "+flac_cue_rex)
        return re.compile(flac_cue_rex)

    def from_vpath(self, path):
        """Construct a FusePath instance from a given virtual path"""
        match = self.track_file_regex.match(path)
        if match is None: 
            log.debug(f'no track file in "{path}"');
            (root, ext) = os.path.splitext(path)
            return FusePath(root, ext, _factory=self)
        log.debug(f'track file in "{path}"');
        title = match['title'].lstrip()
        return FusePath(
            match['basename'], match['extension'], True,
            int(match['num']), title,
            cuesheet.Time.create(match['start']), cuesheet.Time.create(match['end']),
            self
        )
        
    def from_track(self, source_root, extension, track):
        return FusePath(
            source_root, extension, True,
            track.num, track.title, track.start, track.end,
            self
        )      
        
_DEFAULT_FACTORY = Factory()

@dataclass(frozen=True)
class FusePath():
    ''' represents an entry in the virtual trackfs filesystem'''
    source_root             : str
    extension               : str
    is_track                : bool          = False
    num                     : int           = None
    title                   : str           = None
    start                   : cuesheet.Time = None    
    end                     : cuesheet.Time = None
    _factory                : Factory       = _DEFAULT_FACTORY
        
    @property
    def track_separator(self): return self._factory.track_separator
    @property
    def max_title_len(self): return self._factory.max_title_len
    @property
    def flac_extension(self): return self._factory.flac_extension
    @property
    def valid_filename_chars(self): return self._factory.valid_filename_chars
    @property
    def track_file_regex(self): return self._factory.track_file_regex
    @property
    def keep_flac(self): return self._factory.keep_flac

    @cached_property
    def source(self):
        return self.source_root + self.extension
    
    @property
    def title_fragment(self):
        '''the fragment of a track's title that goes into a vpath'''
        if self.title is None or len(self.title) == 0: 
            return ""
        else:
            clean_title = unicodedata.normalize('NFKD', self.title)[:self.max_title_len]
            return "."+''.join(c if c in self.valid_filename_chars else "_" for c in clean_title)
        
    @property
    def vpath(self):
        if(self.is_track): 
            return (
                f'{self.source_root}{self.track_separator}{self.num:03d}'
                f'{self.title_fragment}.{self.start}-{self.end}{self.extension}'
            )
        else:  
            return self.source
            
    def dirname(self):
        return os.path.dirname(self.source_root)
    
    def readdir(self):
        entries = ['.', '..']
        for filename in os.listdir(self.source):
            (basename, extension) = os.path.splitext(filename)
            if( extension == self.flac_extension ):
                trx = flacinfo.get(os.path.join(self.source, filename)).tracks()
                if trx:
                    if self.keep_flac:
                        entries.append(filename)
                    for t in trx:
                        entries.append( 
                            self._factory.from_track(basename, extension, t).vpath
                        )
                else:
                   entries.append(filename)
            else:
                entries.append(filename)
        log.debug(f'vdir entries:{entries}')
        return entries

    def for_other_track(self, num: int, title: str, start: cuesheet.Time, end: cuesheet.Time):
        '''Construct fusepath entry for another track of the same FLAC+CUE file'''
        return FusePath(
            self.source_root, self.extension, True,
            num, title, start, end,
            self._factory
        )