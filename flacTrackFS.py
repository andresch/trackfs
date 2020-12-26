#!/usr/bin/env python3

# https://github.com/acenko/FLACCue

# Note that you do not want to run this as root as this will
# give anyone read access to any file by just prepending /flaccue/.

from __future__ import print_function, absolute_import, division

import logging
import os

import mutagen
import time
import threading
import math

from errno import EACCES
from os.path import realpath

import sys
sys.path.insert(0, '.')

from fuse import FUSE, FuseOSError, Operations, LoggingMixIn
from typing import NamedTuple
from collections import namedtuple
from tempfile import mkstemp

from mutagen.flac import FLAC
from subprocess import Popen, PIPE, DEVNULL, run

import re


from lark import Lark, Transformer


# Global variables; set during argument parsing
IGNORE_TAGS_REX = None

# cue-sheet grammar according to original spec
# https://web.archive.org/web/20070614044112/http://www.goldenhawk.com/download/cdrwin.pdf

cue_grammar = r"""
   start          : disc_entries tracks
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
   def __init__(self,args):
      entries = args[0]
      self.tracks = args[1].children
      
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
   def __init__(self, args):
      self.num = args[0]
      self.type = args[1]
      self.artists = None
      self.composers = None
      self.title = None
      self.isrc = None
      self.start = None
      self.end = None
      self.duration = None
      self.track_entries = args[2]
      for entry in args[2].children:
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
      return ( f"track #{self.num} {self.type} [{self.isrc}] [{self.start}-{self.end}]\n"
         + (f"  title: '{self.title}'\n" if self.title else "")
         + (f"  artists: {self.artists}\n" if self.artists else "")
         + (f"  composers: {self.composers}\n" if self.composers else "")
         + '\n'
      )
      
class MmSsCc():
   mm = 0
   sscc = 0
   
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
   start = CueSheet
   track = CueTrack
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

cue_parser = Lark(cue_grammar)
    
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
   
   def __init__(self, path, is_track, num, title, start, end):
      self.path = path
      self.is_track = is_track
      self.num = num
      self.title = title[:FilenameInfo.MAX_TITLE_LEN].replace('.','_')
      self.start = start
      self.end = end
   
   def to_filename(self):
      (basename, extension) = os.path.splitext(self.path)
      t_opt = "."+self.title if len(self.title) > 0 else ""
      return f'{basename}{FilenameInfo.TRACK_SEPARATOR}{self.num:03d}{t_opt}.{self.start}-{self.end}{extension}'

   def create_regex(separator, extension, title_length):
      FilenameInfo.MAX_TITLE_LEN = title_length
      FilenameInfo.TRACK_SEPARATOR = separator
      (separator_rex, extension_rex) = [ s.replace('.','\\.') for s in [separator, extension] ]
      flac_cue_rex = (
         '^(?P<basename>.*)'+separator_rex
         + '(?P<num>\\d+)(?P<title>(\\.[^\\.]{,'+str(title_length)
         + '}?)?)\\.(?P<start>\\d{6})-(?P<end>\d{6})'
         + '(?P<extension>'+extension_rex+')$'
      )
      print(flac_cue_rex)
      FilenameInfo.TRACK_FILE_REGEX = re.compile(flac_cue_rex)
      
   def parse(path):
      match = FilenameInfo.TRACK_FILE_REGEX.match(path)
      if match is None: 
         print("no match for "+path);
         return FilenameInfo(path, False, None, "", None, None)
      t = match['title']
      if len(t) > 0: t=t[1:]
      return FilenameInfo(
         match['basename']+match['extension'],
         True,
         int(match['num']), t,
         MmSsCc(match['start']), MmSsCc(match['end'])
      )

class FlacInfo():
   
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
         raw_cue = meta.tags['CUESHEET']
         if len(raw_cue) == 0:
            return None
         #print(raw_cue)
         self._cue = CueTransformer(visit_tokens=True
            ).transform(cue_parser.parse(raw_cue[0])
            ).calc_track_times(meta.info.length)
         #print(self._cue)
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
   
   _IGNORE_TAGS = [ re.compile(rex) for rex in [ 'CUE_TRACK.*', 'COMMENT', 'ALBUM ARTIST' ] ]
   def _ignore_tag(tag):
      for ignorex in FlacInfo._IGNORE_TAGS:
         if ignorex.match(tag): return True
      return False
      
   def _album_tags(self):
      meta = self.meta()
      tags = {}
      for (k,v) in meta.tags:
         k = k.upper()
         # skip multi-line tags
         if len(v.splitlines()) != 1: continue
         # skip _IGNORE_TAGS
         if FlacInfo._ignore_tag(k): continue
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

      #print(tags)
      return ' '.join([ f'--tag="{k}"="{v}"' for k,vs in tags.items() for v in vs ])

class TagModifications():
   def __init__(self,spec):
      self.replacements = self.parse(spec)
   
   def parse(self,spec):
      pass
   
   
class FlacTrackFS(LoggingMixIn, Operations):
   def __init__(self, root):
     self.root = realpath(root)
     self.rwlock = threading.RLock()
     self._open_subtracks = {}
     self._last_positions = {}
     self._last_info = None

   def __call__(self, op, path, *args):
      return super(FlacTrackFS, self).__call__(op, self.root + path, *args)

   def clean_path(self, path):
      # Get a file path for the FLAC file from a FlacTrackFS path.
      # Note that files accessed through FlacTrackFS will
      # still read normally--we just need to trim off the song
      # times.
      if('.flaccuesplit.' in path):
         splits = path.split('.flaccuesplit.')
         times, extension = os.path.splitext(splits[1])
         try:
            # The extension should not parse as an int nor split into ints
            # separated by :. If it does, we have no extension.
            int(extension.split('_')[0])
            extension = ''
         except ValueError:
            pass
         path = splits[0] + extension
      return path
      
   def getattr(self, path, fh=None):
      # If it's one of the FlacTrackFS paths, we need to adjust the file size to be
      # appropriate for the shortened data.
      
      info = FilenameInfo.parse(path)
      if(info.is_track):
         try:
            st = os.lstat(info.path)
            toreturn = dict((key, getattr(st, key)) for key in (
                            'st_atime', 'st_ctime', 'st_gid', 'st_mode', 'st_mtime',
                            'st_nlink', 'st_size', 'st_uid'))
            # Estimate the file size.
            f = self.flac_info(info.path).meta()
            toreturn['st_size'] = int((info.end - info.start).seconds() *
                                      f.info.channels *
                                      (f.info.bits_per_sample/8) *
                                      f.info.sample_rate)
            return toreturn
         except:
            import traceback
            traceback.print_exc()
      # Otherwise, just get the normal info.
      path = self.clean_path(path)
      st = os.lstat(path)
      return dict((key, getattr(st, key)) for key in (
         'st_atime', 'st_ctime', 'st_gid', 'st_mode', 'st_mtime',
         'st_nlink', 'st_size', 'st_uid'))

   getxattr = None

   listxattr = None

   def flac_info(self, path):
      with self.rwlock:
         if self._last_info is None or self._last_info.path != path:
            self._last_info = FlacInfo(path)
         return self._last_info
         
   def is_new_track(self, path):
      return path not in self._open_subtracks
      
   def announce_track(self, path):
      self._open_subtracks[path] = None

   def is_announced_track(self, path):
      return self._open_subtracks[path] is None

   def open_file(self, path, flags, *args, **pargs):
      fh = os.open(path, flags, *args, **pargs)
      print(f"open file [{fh}] {path}")
      self._last_positions[fh] = 0
      return fh
      
   def open_track(self, info, path, flags, *args, **pargs):
     # Process the FLAC file to extract the track in a temp file
      (fh,tempfile) = mkstemp()
      os.close(fh)
      fi = self.flac_info(info.path)
      flac_cmd = (
         f'flac -d --silent --stdout --skip={info.start.flac_time()} --until={info.end.flac_time()} "{info.path}"'
         f' | flac --silent -f --fast {fi.track_tags(info.num)} -o {tempfile} -'
      )
      print(flac_cmd)
      rc = run(flac_cmd, shell=True, stdout=None, stderr=DEVNULL).returncode
      with self.rwlock:
         if rc != 0:
            # failed to create temporary flac file; return original file
            os.remove(tempfile)
            del self._open_subtracks[path]
            return self.open_file(info.path, flags, *args, **pargs)
         else:
            self._open_subtracks[path] = (1, time.time(), tempfile)
      
      def cleanup():
         still_in_use = True
         while still_in_use:
            with self.rwlock:
               (count, last_access, tempfile) = self._open_subtracks[path]
            if count <= 0 and (time.time() - last_access > 60):
               still_in_use = False
            time.sleep(5)
         print("delete track "+path)
         with self.rwlock:
            del self._open_subtracks[path]
         os.remove(tempfile)
      
      print("open track "+path)
      fh = self.open_file(tempfile, flags, *args, **pargs)
      print(f"open track [{fh}] {path}\n  from {tempfile}")
      threading.Thread(target=cleanup).start()
      return fh
   
   def reopen_track(self, path, flags, *args, **pargs):
      with self.rwlock:
         # Update the stored info.
         (count, last_access, tempfile) = self._open_subtracks[path]
         self._open_subtracks[path] = (count+1, time.time(), tempfile)
      # open the file once again
      fh = self.open_file(tempfile, flags, *args, **pargs)
      self._last_positions[fh] = 0
      print(f"reopen track [{fh}] {path}")
      return fh
      
   def release_track(self, path, fh):
      with self.rwlock:
         (count, last_access, tempfile) = self._open_subtracks[path]
         self._open_subtracks[path] = (count-1, time.time(), tempfile)
      print(f"release track [{fh}] {path}")
      return os.close(fh)
      
   def open(self, path, flags, *args, **pargs):
      # We don't want FlacTrackFS messing with actual data.
      # Only allow Read-Only access.
      if((flags | os.O_RDONLY) == 0):
         raise ValueError('Can only open files read-only.')
      # Handle the FlacTrackFS files.
      info = FilenameInfo.parse(path)
      if info.is_track:
         ready_to_process = False
         while not ready_to_process:
            with self.rwlock:
               if self.is_new_track(path):
                  ready_to_process = True
                  self.announce_track(path)
               elif not self.is_announced_track(path):
                  # if neither new nor announced then
                  # we already have cached that track => reopen
                  return self.reopen_track(path, flags, *args, **pargs)
                  
            # give other thread time to finish processing
            time.sleep(0.1)
         
         return self.open_track(info, path, flags, *args, **pargs)         
      else:
         # With any other file, just pass it along normally.
         # This allows FLAC files to be read with a FlacTrackFS path.
         # Note that you do not want to run this as root as this will
         # give anyone read access to any file.
         return self.open_file(path, flags, *args, **pargs)

   def read(self, path, size, offset, fh):
      print(f"read from [{fh}] {offset} until {offset+size}")
      if self._last_positions[fh] != offset:
         os.lseek(fh, offset, 0)
      self._last_positions[fh] = offset+size
      return os.read(fh, size)

   def release(self, path, fh):
      if FilenameInfo.parse(path).is_track:
         return self.release_track(path, fh)
      # Close the OS reference to the file.
      del self._last_positions[fh]
      return os.close(fh)

   def readdir(self, path, fh):
      path = self.clean_path(path)
      entries = []
      for filename in os.listdir(path):
         basename, extension = os.path.splitext(filename)
         if( extension == ".flac" ):
            trx = self.flac_info(os.path.join(path, filename)).tracks()
            if trx:
               for t in trx:
                  entries.append(FilenameInfo(filename, True, t.num, t.title, t.start, t.end).to_filename())
            else:
               entries.append(filename)
         else:
            entries.append(filename)
      return ['.', '..'] + entries

   def readlink(self, path, *args, **pargs):
      path = self.clean_path(path)
      return os.readlink(path, *args, **pargs)

   def statfs(self, path):
      path = self.clean_path(path)
      stv = os.statvfs(path)
      return dict((key, getattr(stv, key)) for key in (
         'f_bavail', 'f_bfree', 'f_blocks', 'f_bsize', 'f_favail',
         'f_ffree', 'f_files', 'f_flag', 'f_frsize', 'f_namemax'))



if __name__ == '__main__':
   import argparse
   parser = argparse.ArgumentParser(
      description='''Maps a directory to a new mount point while replacing all FLAC files with 
embedded cue sheets will be replaced with multiple FLAC files for the individual tracks''')
   parser.add_argument(
      '-s','--separator', nargs='?', dest='separator', default='.#-#.',
      help='The separator used inside the name of the track-files. Must never occur in regular filenames (default: ".#-#.")'
   )
   parser.add_argument(
      '-i','--ignore-tags', nargs='?', dest='ignore', default='CUE_TRACK.*|COMMENT',
      help='A regular expression for tags in the FLAC file that will not be copied to the track FLACs (default: "CUE_TRACK.*|COMMENT")'
   )
   parser.add_argument(
      '-e','--extension', nargs='?', dest='extension', default='.flac',
      help='The file extension of FLAC files (default: ".flac")'
   )
   parser.add_argument(
      '-k', '--keep-flac-cue', dest='keep', action='store_true',
      help='Keep the source FLAC+CUE file in the mapped filesystem'
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

   print(args)
   IGNORE_TAGS_REX = re.compile(args.ignore)
   FilenameInfo.create_regex(args.separator, args.extension, 20)

   #logging.basicConfig(level=logging.DEBUG)
   fuse = FUSE(
      FlacTrackFS(args.root), args.mount, foreground=True, allow_other=True)