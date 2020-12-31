#!/usr/bin/env python3
# 
# Copyright 2020 by Andreas Schmidt
# All rights reserved.
# This file is part of the trackfs project
# and licensed under the terms of the GNU Lesser General Public License v3.0.
# See https://github.com/andresch/trackfs for details.
#
# This file is derived work of the FLACCue project.
# See https://github.com/acenko/FLACCue for details

from __future__ import print_function, absolute_import, division

import logging
import os

import time
import string
import threading
import math
import unicodedata
import signal

from os.path import realpath

import sys
#sys.path.insert(0, '.')

from fuse import FUSE, FuseOSError, Operations, LoggingMixIn
from tempfile import mkstemp

from mutagen.flac import FLAC
from subprocess import Popen, PIPE, DEVNULL, run

import re
from lark import Lark, Transformer


log = logging.getLogger(__name__)

# Global variables; set during argument parsing
IGNORE_TAGS_REX = None

# cue-sheet grammar according to original spec
# https://web.archive.org/web/20070614044112/http://www.goldenhawk.com/download/cdrwin.pdf

cue_grammar = r"""
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
   INDEX          : DIGIT~1..2
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

class CueSheet():
   """Relevant information from a parsed cue sheet
   
   In the context of FlacTrackFS we're only interested in track
   information from a cue sheet. All additional data elements are
   not used
   """
   def __init__(self,disc_elements,tracks):
      self.tracks = tracks
      
   def __repr__(self):
      return f'cuesheet:\n{self.tracks}'

   def calc_track_times(self, disc_duration):
      for i in range(0,len(self.tracks)-1):
         curr = self.tracks[i]
         curr.end = self.tracks[i+1].start
         curr.duration = curr.end-curr.start
      last = self.tracks[-1];
      last.end = MmSsCc(disc_duration)
      last.duration = last.end-last.start
      return self
         
class CueTrack():
   """All information extracted for a track from a cue sheet
   
   Attributes
   ----------
   num         :  int
                  The track number
   type        :  string
                  The track type
   start       :  MmSsCc
                  The start timestamp of the track
   end         :  MmSsCc
                  The end timestamp of the track
   duration    :  MmSsCc
                  The duration of the track
   artist      :  list[string]
                  The artists performing the track
   composers   :  list[string]
                  The artists who have written the track
                  
   The attributes end and duration don't originate directly from the the track elements
   in the cue sheet. They get set by calling a CueSheet's `calc_track_times` method
   once the whole cue sheet got parsed.
   """
   def __init__(self, num, type, track_entries):
      self.num = num
      self.type = type
      self.artists = None
      self.composers = None
      self.title = None
      self.isrc = None
      self.start = None
      self.end = None
      self.duration = None
      for entry in track_entries:
         if entry.data == 'performer':
            self._extend__list_attr("artists",entry.children[0])
         if entry.data == 'songwriter':
            self._extend__list_attr("composers",entry.children[0])
         elif entry.data == 'title':
            self.title = entry.children[0]
         elif entry.data == 'isrc':
            self.isrc = entry.children[0]
         elif entry.data == 'index':
            idx = entry.children
            # we're only interested in index 1
            if idx[0] == 1:
               self.start = idx[1];
            
   def _extend__list_attr(self,name,value):
      # most rippers use ";" as delimiter for multiple values inside a 
      # single entry rather than having multiple entries
      values = [p.strip() for p in value.split(";")]
      old_value = getattr(self,name)
      if old_value == None:
         setattr(self,name,values)
      else:
         setattr(self,name,old_value+values)
            
   def __repr__(self):
      return ( f"track #{self.num} {self.type} [{self.isrc}] [{self.start}-{self.end}]"
         + (f" title: '{self.title}' " if self.title else "")
         + (f" artists: {self.artists}" if self.artists else "")
         + (f" composers: {self.composers}" if self.composers else "")
      )
      
      
class MmSsCc():
   """ Timestamp / duration information extracted from a cue-sheet
   
   Attributes
   ----------
   mm       :  int
               minutes
   sscc     :  int
               centi-seconds
   
   The constructor supports various initializations:
   - <empty>      :  mm = sscc = 0
   - float        : interpreted as seconds (and fractions of)
   - int-triple   : interpreted as (mm, ss, ff), whith ff being 1/75s
   - 6-charstring : interpreted as "mmsscc"-string
   - two int      : interpreted as mm, sscc
   
   The class supports basic math (+,-)
   The string representation is "mmsscc"; `flac_time' returns the string 
   representation than FLAC expects ("mm:ss.cc")
   """
   def __init__(self,*args):
      if len(args) == 0:
         self.mm = self.sscc = 0
      elif isinstance(args[0],float):
         # value as seconds with faction
         (self.mm, self.sscc) = divmod( int(math.trunc(args[0] * 100)), 6000 )
      elif isinstance(args[0],tuple):
         # value is tuple (minutes, seconds, frames)
         (self.mm, ss, fr) = args[0]
         self.sscc = ss*100 + (fr*100)//75
      elif isinstance(args[0],str):
         # value is string template "mmsscc"
         self.mm = int(args[0][0:2])
         self.sscc = int(args[0][2:6])
      elif isinstance(args[0],int):
         # value is two ints: mm, sscc
         self.mm = args[0]
         self.sscc = args[1]

   def seconds(self):
      return 60.0 * self.mm + self.sscc / 100
   
   def flac_time(self):
      return '%02d:%05.2f' % (self.mm, self.sscc / 100)
      
   def __repr__(self):
      return '%02d%04d' % (self.mm, self.sscc)
   
   def __ne__(self, other):
     return self._time != other._time

   def __eq__(self, other):
      return self.mm == other.mm and self.sscc == other.sscc

   def __add__(self, other):
     c, sscc = divmod( self.sscc+other.sscc, 6000 )
     return MmSsCc(self.mm+other.mm+c, sscc)
       
   def __sub__(self, other):
     sscc = self.sscc - other.sscc
     if sscc<0:
        return MmSsCc(self.mm-other.mm-1, sscc+6000)
     else:
        return MmSsCc(self.mm-other.mm, sscc)
      
class CueTransformer(Transformer):
   """Transforms the Lark-parser-tree in a CueSheet"""
   def cue_sheet(self, subtrees): 
      return CueSheet(subtrees[0].children, subtrees[1].children)
   def track(self, subtrees):
      return CueTrack(subtrees[0], subtrees[1], subtrees[2].children)
   def mmssff(self, elems):
      return MmSsCc(elems[0], elems[1]*100+elems[2]//75)
   def REST_OF_LINE(self, comment_line):
      return comment_line[1:-1] if len(comment_line)>0 else ""
   def STRING(self, string):
      return string[1:-1] if string[0]=='"' else str(string)
   UPC_EAN=str
   ISRC=str
   TRACKTYPE=str
   INT=int
   INDEX=int
   TIME_ELEM=int

cue_parser = Lark(cue_grammar, start='cue_sheet')
    
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
    PERFORMER "Zaz"
    TITLE "Sous le ciel de Paris"
    ISRC FR2PY1403250
    INDEX 01 02:58:68"""
    
#text = '{"key": ["item0", "item1", 3.14, true]}'
#print( CueTransformer(visit_tokens=True).transform(cue_parser.parse(test_cue)) )
#exit()    

class FilenameInfo():
   TRACK_SEPARATOR = None
   TRACK_FILE_REGEX = None
   MAX_TITLE_LEN = None
   
   VALID_FILENAME_CHARS = "-_() " + string.ascii_letters + string.digits
   
   def __init__(self, path, is_track, num, title, start, end):
      self.path = path
      self.is_track = is_track
      self.num = num
      self.title = FilenameInfo.title_for_filename(title)
      self.start = start
      self.end = end
   
   def title_for_filename(title):
      cleanedFilename = unicodedata.normalize('NFKD', title)[:FilenameInfo.MAX_TITLE_LEN]
      return ''.join(c if c in FilenameInfo.VALID_FILENAME_CHARS else "_" for c in cleanedFilename)

   def to_filename(self):
      (basename, extension) = os.path.splitext(self.path)
      t_opt = "."+self.title if len(self.title) > 0 else ""
      return f'{basename}{FilenameInfo.TRACK_SEPARATOR}{self.num:03d}{t_opt}.{self.start}-{self.end}{extension}'

   def init(separator, extension, title_length):
      """Initialize the configuration settings for generating/identifying track files"""
      log.info(f'Track-filename settings: separator: "{separator}", extension: "{extension}", title_length: {title_length}')
      FilenameInfo.MAX_TITLE_LEN = title_length
      FilenameInfo.FLAC_EXTENSION = extension
      FilenameInfo.TRACK_SEPARATOR = separator
      (separator_rex, extension_rex) = [ s.replace('.','\\.') for s in [separator, extension] ]
      flac_cue_rex = (
         '^(?P<basename>.*)'+separator_rex
         + '(?P<num>\\d+)(?P<title>(\\.[^\\.]{,'+str(title_length)
         + '}?)?)\\.(?P<start>\\d{6})-(?P<end>\d{6})'
         + '(?P<extension>'+extension_rex+')$'
      )
      log.debug("filename regex: "+flac_cue_rex)
      FilenameInfo.TRACK_FILE_REGEX = re.compile(flac_cue_rex)
      
   def parse(path):
      """Construct a FilenameInfo instance from a given path"""
      match = FilenameInfo.TRACK_FILE_REGEX.match(path)
      if match is None: 
         log.debug(f'no track file in "{path}"');
         return FilenameInfo(path, False, None, "", None, None)
      log.debug(f'track file in "{path}"');
      t = match['title']
      if len(t) > 0: t=t[1:]
      return FilenameInfo(
         match['basename']+match['extension'],
         True,
         int(match['num']), t,
         MmSsCc(match['start']), MmSsCc(match['end'])
      )

class FlacInfo():
   IGNORE_TAGS_REX = [ re.compile(rex) for rex in [ 'CUE_TRACK.*', 'COMMENT', 'ALBUM ARTIST' ] ]
   
   def __init__(self,path):
      self.path : str = path
      self._meta : FLAC = None
      self._cue : mktoc.parser.ParseData = None
   
   def meta(self) -> FLAC:
      if self._meta is None:
         self._meta = FLAC(self.path)
      return self._meta
      
   def cue(self) -> CueSheet:
      if self._cue is None:
         meta = self.meta()
         raw_cue = meta.tags.get('CUESHEET',"")
         if len(raw_cue) == 0:
            log.debug(f"regular flac file without cue sheet")
            return None
         log.debug(f"raw cue sheet from FLAC file:\n{raw_cue}")
         self._cue = CueTransformer(visit_tokens=True
            ).transform(cue_parser.parse(raw_cue[0])
            ).calc_track_times(meta.info.length)
         log.debug(f"parsed cue sheet from FLAC file:\n{self._cue}")
      return self._cue
   
   def tracks(self):
      cue = self.cue()
      return cue.tracks if cue is not None else None
      
   def track(self, num):
      trx = self.tracks();
      if trx is None: return None
      t = trx[num-1];
      if t.num == num: return t
      for t in trx:
         if t.num == num: return t
      return None
   
   def _album_tags(self):
      meta = self.meta()
      tags = {}
      for (k,v) in meta.tags:
         k = k.upper()
         # skip multi-line tags
         if len(v.splitlines()) != 1: continue
         # skip _IGNORE_TAGS
         if FlacInfo.IGNORE_TAGS_REX.match(k): continue
         if k not in tags: tags[k] = []
         tags[k].append(v)
      
      # make sure ALBUMARTIST and ALBUM are set 
      # in case ARTIST and TITLE have been used instead
      if 'ALBUMARTIST' not in tags and 'ARTIST' in tags:
         tags['ALBUMARTIST'] = tags['ARTIST']
      if 'ALBUM' not in tags and 'TITLE' in tags:
         tags['ALBUM'] = tags['TITLE']
      return tags
   
   def _track_tags(self, num):
      t = self.track(num)
      tags = {}
      if t is not None: 
         if t.artists:     tags['ARTIST']       = t.artists
         if t.composers:   tags['COMPOSER']     = t.composers
         if t.isrc:        tags['ISRC']         = [t.isrc]
         if t.num:         tags['TRACKNUMBER']  = [t.num]
         if t.title:       tags['TITLE']        = [t.title]
         
      return tags
         
   
   def track_tags(self, num):
      tags = self._album_tags()
      for (k,vs) in self._track_tags(num).items():
         tags[k] = vs
         
      if 'TRACKTOTAL' not in tags: 
         tags['TRACKTOTAL'] = [str(len(self.tracks()))]
      if 'ARTIST' in tags:
         tags['COMPOSER'] = tags['ARTIST']

      log.debug(f"tags for current track: {tags}")
      return ' '.join([ f'--tag="{k}"="{v}"' for k,vs in tags.items() for v in vs ])

   def init(ignore):
      log.info(f'Tags to ignore: "{ignore}"')
      FlacInfo.IGNORE_TAGS_REX = re.compile(ignore)

class TrackRegistry():
   """Keeps track of all individual tracks that currently get processed
   
   Each track has a unique key (usually the path of the virtual track file).
   
   The registry distinguishes three states for a track:
   * Unregistered: The track is not (yet) known to the registry
   * Announced: The track is known, but not yet available yet (processing still ongoing)
   * Available: The information about the track is available.
   
   """
   def __init__(self, rwlock):
      self.rwlock = rwlock
      self.registry = {}
      
   def add(self, key, track_file):
      with self.rwlock: 
         self.registry[key] = (1, time.time(), track_file)

   def is_unregistered(self, key):
      """Is the track at the given key not yet registered?"""
      with self.rwlock:
         return key not in self.registry

   def announce(self, key):
      """Announce that a new track will get processed soon"""
      with self.rwlock:
         self.registry[key] = None

   def is_announced(self, key):
      """Is the track registered, but not yet processed?"""
      with self.rwlock:
         # default value "" for get ensures that we don't 
         # treat unknown tracks as announced
         return self.registry.get(key,"") is None

   def is_registered(self, key):
      with self.rwlock:
         return isinstance(self.registry.get(key),tuple)
         
   def register_usage(self, key): return self._change_usage(key, +1)
   def release_usage(self, key): return self._change_usage(key, -1)
   def _change_usage(self, key, incr):
      with self.rwlock:
         (count, last_access, track_file) = self.registry[key]
         result = (count+incr, time.time(), track_file)
         self.registry[key] = result
         return result
         
   def __getitem__(self, key):
      with self.rwlock:
         return self.registry[key]
         
   def get(self, key, default=None):
      with self.rwlock:
         return self.registry.get(key, default)
         
   def __delitem__(self, key):
      with self.rwlock:
         del self.registry[key]
         
class FlacSplitException(Exception):
   pass

class TrackFS(Operations):

   def __init__(self, root, keep_flac=False):
     self.root = realpath(root)
     self.keep_flac = keep_flac
     self.rwlock = threading.RLock()
     self.tracks = TrackRegistry(self.rwlock)
     self._processed_tracks = {}
     self._last_positions = {}
     self._last_info = None

   def __call__(self, op, path, *args):
      return super(TrackFS, self).__call__(op, self.root + path, *args)

   def getattr(self, path, fh=None):
      log.info(f"getattr for ({path}) [{fh}]")
      info = FilenameInfo.parse(path)
      st = os.lstat(info.path)
      result = dict((key, getattr(st, key)) for key in (
         'st_atime', 'st_ctime', 'st_gid', 'st_mode', 'st_mtime',
         'st_nlink', 'st_size', 'st_uid'))

      if(info.is_track):
         # If it's one of the FlacTrackFS track paths, 
         # we need to adjust the file size to be roughly
         # appropriate for the individual track.
         f = self._flac_info(info.path).meta()
         result['st_size'] = int((info.end - info.start).seconds() *
                                   f.info.channels *
                                   (f.info.bits_per_sample/8) *
                                   f.info.sample_rate)
      return result

   getxattr = None

   listxattr = None

   def _flac_info(self, path):
      """Get FlacInfo for given path
      
      Reuse from cache if available
      """
      with self.rwlock:
         if self._last_info is None or self._last_info.path != path:
            self._last_info = FlacInfo(path)
         return self._last_info
         
   def _new_temp_filename(self):
      (fh,tempfile) = mkstemp()
      # we don't want to process the file in python; just want a unique filename
      # that we let flac write the track into
      # => close right away
      os.close(fh)
      return tempfile
      
   def _extract_track(self, path, file_info):
      """creates a real file for a given virtual track file
      
      extracts the track from the underlying FLAC+CUE file into 
      a temporary file and then opens the temporary file"""
      log.info(f'open track "{path}"')
      
      trackfile = self._new_temp_filename()
      
      flac_info = self._flac_info(file_info.path)
      
      # extract picture from flac if available
      picturefile = self._new_temp_filename()
      metaflac_cmd = f'metaflac --export-picture-to="{picturefile}" "{file_info.path}"'
      log.debug(f'extracting picture with command: "{metaflac_cmd}"')
      rc = run(metaflac_cmd, shell=True, stdout=None, stderr=DEVNULL).returncode
      picture_arg = ""
      if rc == 0:
         picture_arg =f' --picture="{picturefile}"'
      
      flac_cmd = (
         f'flac -d --silent --stdout --skip={file_info.start.flac_time()} --until={file_info.end.flac_time()} "{file_info.path}"'
         f' | flac --silent -f --fast {flac_info.track_tags(file_info.num)}{picture_arg} -o {trackfile} -'
      )
      log.debug(f'extracting track with command: "{flac_cmd}"')
      rc = run(flac_cmd, shell=True, stdout=None, stderr=DEVNULL).returncode
      os.remove(picturefile)
      with self.rwlock:
         if rc != 0:
            err_msg = f'failed to extract track #{num} from file "{file_info.path}"'
            log.error(err_msg)
            os.remove(trackfile)
            del self.tracks[path]
            raise FlacSplitException(err_msg)
         else:
            self.tracks.add(path, trackfile)
      
      def cleanup():
         still_in_use = True
         while still_in_use:
            (count, last_access, trackfile) = self.tracks[path]
            if count <= 0 and (time.time() - last_access > 60):
               still_in_use = False
            time.sleep(30)
         log.debug(f'delete track "{path}"')
         del self.tracks[path]
         os.remove(trackfile)
      
      threading.Thread(target=cleanup).start()
      return trackfile
   
   def _file_to_open(self, path):
      info = FilenameInfo.parse(path)
      if info.is_track:
         ready_to_process = False
         while not ready_to_process:
            with self.rwlock:
               if self.tracks.is_unregistered(path):
                  ready_to_process = True
                  self.tracks.announce(path)
               elif self.tracks.is_registered(path):
                  # we already have cached that track => 
                  # register additional usage
                  return self.tracks.register_usage(path)[2] 
                  
            # give other thread time to finish processing
            time.sleep(0.5)
         
         return self._extract_track(path, info)         
      else:
         # With any other file, just pass it along normally.
         return path
         
   def open(self, path, flags, *args, **pargs):
      log.info(f'open file "{path}"')
      # We don't want FlacTrackFS messing with actual data.
      # Only allow Read-Only access.
      if((flags | os.O_RDONLY) == 0):
         raise ValueError('Can only open files read-only.')
      realfile = self._file_to_open(path)
      log.debug(f'file to open = "{realfile}"')
      fh = os.open(realfile, flags, *args, **pargs)
      self._last_positions[fh] = 0
      return fh
      
   def read(self, path, size, offset, fh):
      log.info(f"read from [{fh}] {offset} until {offset+size}")
      if self._last_positions[fh] != offset:
         log.debug(f"out of band read; seek file to offset {offset}")
         os.lseek(fh, offset, 0)
      self._last_positions[fh] = offset+size
      return os.read(fh, size)

   def release(self, path, fh):
      log.info(f'release [{fh}] ({path})')
      del self._last_positions[fh]
      if FilenameInfo.parse(path).is_track:
         self.tracks.release_usage(path)
      return os.close(fh)

   def readdir(self, path, fh):
      log.info(f'readdir [{fh}] ({path})')
      path = FilenameInfo.parse(path).path
      entries = []
      for filename in os.listdir(path):
         basename, extension = os.path.splitext(filename)
         if( extension == FilenameInfo.FLAC_EXTENSION ):
            trx = self._flac_info(os.path.join(path, filename)).tracks()
            if trx:
               if self.keep_flac:
                  entries.append(filename)
               for t in trx:
                  entries.append(
                     FilenameInfo(
                        filename, 
                        True, 
                        t.num, 
                        t.title, 
                        t.start, 
                        t.end
                     ).to_filename())
                
            else:
               entries.append(filename)
         else:
            entries.append(filename)
      return ['.', '..'] + entries

   def readlink(self, path, *args, **pargs):
      log.info(f'readlink ({path})')
      path = FilenameInfo.parse(path).path
      return os.readlink(path, *args, **pargs)

   def statfs(self, path):
      log.info(f'statfs ({path})')
      path = FilenameInfo.parse(path).path
      stv = os.statvfs(path)
      return dict((key, getattr(stv, key)) for key in (
         'f_bavail', 'f_bfree', 'f_blocks', 'f_bsize', 'f_favail',
         'f_ffree', 'f_files', 'f_flag', 'f_frsize', 'f_namemax'))

if __name__ == '__main__':
   import argparse

   parser = argparse.ArgumentParser(
      description='''A FUSE filesystem for extracting individual tracks from FLAC+CUE files.
      
      Maps a directory to a mount point while replacing all FLAC files with 
      embedded cue sheets with multiple FLAC files for the individual tracks''')
   parser.add_argument(
      '-s','--separator', dest='separator', default='.#-#.',
      help='The separator used inside the name of the track-files. Must never occur in regular filenames (default: ".#-#.")'
   )
   parser.add_argument(
      '-i','--ignore-tags', dest='ignore', default='CUE_TRACK.*|COMMENT',
      help='A regular expression for tags in the FLAC file that will not be copied to the track FLACs (default: "CUE_TRACK.*|COMMENT")'
   )
   parser.add_argument(
      '-e','--extension', dest='extension', default='.flac',
      help='The file extension of FLAC files (default: ".flac")'
   )
   parser.add_argument(
      '-k', '--keep-flac-cue', dest='keep', action='store_true',
      help='Keep the source FLAC+CUE file in the mapped filesystem'
   )
   parser.add_argument(
      '-t', '--title-length', dest='title_length', default="20",
      help='Nr. of characters of the track title in filename of track (default: 20)'
   )
   parser.add_argument(
      '--root-allowed', dest='rootok', action='store_true',
      help='Allow running as with root permissions; Neither necessary nor recommended. Use only when you know what you are doing'
   )
   parser.add_argument(
      '-v','--verbose', dest='verbose', action='store_true',
      help='Activate info-level logging'
   )
   parser.add_argument(
      '-d','--debug', dest='debug', action='store_true',
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

   if args.debug: 
      logging.basicConfig(level=logging.DEBUG)
      log.setLevel(logging.DEBUG)
   elif args.verbose:
      logging.basicConfig(level=logging.INFO)
      log.setLevel(logging.INFO)
      
   if os.geteuid() == 0 and not args.rootok:
      print(f'''By default {os.path.basename(__file__)} don't allow to run with root permissions. 
      
If you are absolutely sure that that's what you want, use the option "--root-allowed"''', file=sys.stderr)
      exit(1)
      
   FilenameInfo.init(args.separator, args.extension, int(args.title_length))
   FlacInfo.init(args.ignore)
   trackfs = TrackFS(args.root, args.keep)
   
   fuse = FUSE(trackfs, args.mount, foreground=True, allow_other=True)