
`trackfs`
=======

`trackfs` is a read-only FUSE filesystem that splits FLAC+CUE files (FLAC files with cue sheet embedded as vorbis comment) into individual FLAC files per track.

The recommended way to use `trackfs` is using the docker image `andresch/trackfs`. In case you want to use `trackfs` without docker see section [Manual Installation](https://github.com/andresch/trackfs#manual-installation) below.
 
Usage
-----

You can directly run `trackfs` on any Linux system with Docker and FUSE installed.

### Getting started

The simplest way to get familiar with `trackfs` is to just launch it from the command-line:

```
docker run -ti --rm \
    --device /dev/fuse \
    --cap-add SYS_ADMIN \
    --security-opt apparmor:unconfined \
    -v /path/to/yourmusiclibrary:/src:ro \
    -v /path/to/yourmountpoint:/dst:rshared \
    andresch/trackfs \
    --root-allowed
```

Replace `/path/to/yourmusiclibrary` with the root directory where `trackfs` can find your FLAC+CUE files and `/path/to/yourmountpoint` with the directory that you want to use as mount point for the `trackfs`-filesystem. Ideally the mount point already exists, if not, docker will create the directory (but then with root as owner)

Once started you will find all directories and files from your music library also in the `trackfs`-filesystem. Only FLAC+CUE files will get replaces: Instead of a single FLAC+CUE file you will find individual FLAC files for each track found in the embedded cue sheet. The track-files will have the following names:

    {basename(FLAC+CUE-file)}.#-#.{tracknumber}.{track-title}.{start}-{end}.flac

While the tracks can be used like regular files, they don't exist in the physical file system on your machine. Instead `trackfs` creates them on the fly whenever an application starts loading any of the files. This usually takes (depending on your system) a few seconds.

#### Docker arguments

In case you're not familiar with docker, a quick explanation on the used docker arguments:

* `-ti`: Attach input and output of the docker container to your terminal
* `--rm`: Remove the docker container implicitly after termination
* `--device`, `--cap-add` `--security-opt`: By default docker containers don't have sufficient privileges to mount FUSE file systems. With those arguments to grant the docker container the minimal privileges required to do this.
* `-v /path/to/yourmusiclibrary:/src:ro`: make your music library accessible for trackfs by mounting it to /src in read-only mode inside your docker container
* `-v /path/to/yourmountpoint:/dst:rshared`: share the trackfs filesystem (`/dst` inside the container) accessible under your mount point
* `andresch/trackfs`: the name of the `trackfs` docker image on docker hub.

Please refer to the [docker run documentation](https://docs.docker.com/engine/reference/commandline/run/) for more details.

### Running `trackfs` as regular user 

While the above is working just fine, it is not the recommended way to use `trackfs` as it runs `trackfs` inside the docker container as user root. Running as root does allow `trackfs` to access any file in your music library, irrespective of its underlying file permissions. If we would have omitted the `--root-allowed` argument, `trackfs` would have terminated with a corresponding error message.

Instead it is recommended to let `trackfs` run as a regular user. For that to work we need a few changes:
- Make sure that in your host system the file `/etc/fuse.conf` has the option `user_allow_other` enabled, e.g. by calling from your command line 
  ```sudo echo "user_allow_other" >> /etc/fuse.conf```
- Make sure that your mount point already exists and is owned by the user that is supposed to run `trackfs`.
- Tell `trackfs` which user to use by setting the docker environment variable `TRACKFS_UID`
  E.g. the following docker command would run `trackfs` with the current user:

  ```
  docker run -ti --rm \
    --device /dev/fuse \
    --cap-add SYS_ADMIN \
    --security-opt apparmor:unconfined \
    --env FTFS_UID=`id -u`
    -v /path/to/yourmusiclibrary:/src:ro \
    -v /path/to/yourmountpoint:/dst:rshared \
    andresch/trackfs 
  ```

### Additional `trackfs` options

`trackfs` provides a few additional options that allow you to tweak its default behavior: 

* `-e EXTENSION`, `--extension EXTENSION` (default: ".flac") : 
  The file extension of FLAC files in the music library 
* `-s SEPARATOR`, `--separator SEPARATOR` (default: ".#-#."): 
  The separator used inside the name of the track-files. Must never occur in regular filenames 
* `-i IGNORE`, `--ignore-tags IGNORE` (default: "CUE_TRACK.*|COMMENT"):
  A regular expression matching all tags in the FLAC+CUE file that will not be copied over to the track FLACs 
* `-k`, `--keep-flac-cue`: 
  Keep the source FLAC+CUE file in the `trackfs` filesystem in addition to the individual tracks
* `-t TITLE_LENGTH`, `--title-length TITLE_LENGTH` (default: 20):
  Nr. of characters of the track title in filename of track 
* `--root-allowed`:
  Allow running `trackfs` as with root permissions; Neither necessary nor recommended. 
  Use only when you know what you are doing
* `-v`, `--verbose`:
  Activate info-level logging
* `-d`, `--debug`:
  Activate debug-level logging


You can use `-h`, `--help` to get a list of all all options. Keep in mind that the parameters `root` and `mount` are already defined with the two `-v` options to `docker run` and are implicitly set by the docker container.

Meta-Data in in Track Files
---------------------------

Most tags (aka vorbis comments) of the FLAC+CUE file will be set in the track files too. There are only two exceptions:
* Tags that contain multi-line values (like the `CUESHEET`-tag)
* Any tag whose name matches the regular expression of the `--ignore-tags` option (default: `"CUE_TRACK.*|COMMENT"`)

In addition `trackfs` does the following modifications to tags:
- If the FLAC+CUE file contains an `ARTIST` tag but no `ALBUMARTIST` tag, then an `ALBUMARTIST` tag will be created with the value of `ARTIST` tag.
- If the FLAC+CUE file contains a `TITLE`tag, but no `ALBUM` tag, then an `ALBUM` tag will be created with the value of the `TITLE` tag.
- If the cue sheet contains a `TITLE` tag for a given track, it overwrites the `TITLE` tag from the FLAC+CUE file
- If the cue sheet contains a `PERFORMER` tag for a given track, it overwrites the `ARTIST` tag from the FLAC+CUE file
- If the cue sheet contains a `SONGWRITER` tag for a given track, it overwrites the `COMPOSER` tag from the FLAC+CUE file

In case the FLAC+CUE file contains pictures, the first picture will be available in the track file.

Manual Installation
-------------------

Making `trackfs` available as regular python package that you can install via pip is currently in preparation.

For the time being you need to manually install `trackfs` and its dependencies:
* Make sure that you have installed the corresponding packages for python3, pip, fuse, fuse-dev and flac installed from your Linux distribution
* Install the additional python package dependencies with `pip install mutagen fusepy Lark`
* Download [trackfs.py](https://raw.githubusercontent.com/andresch/trackfs/trackfs.py), make it executable (`chmod +x trackfs.py`)

Status
------

`trackfs` is currently in an early stage. While it runs stable on the author's NAS, it has not been tested in other environments, esp. on various Linux distributions with different kernels/FUSE versions. Using the dockerized version should remove some of the difficulties, but given the dependencies on FUSE, some my still remain. 

Also keep in mind that this is the author's first python project, so don't expect that the source code matches professional quality criteria of experienced python coders.

### Future improvements:

There are a few ideas for additional improvements
* Find out if there is a way to extract tracks from the FLAC+CUE file without re-encoding the track. This should allow to increase the performance when starting to read a track massively
* Make use of some in memory buffer when streaming a track instead of streaming straight from a temporary file from disk. This should avoid sporadic audio glitches when playing track
* Allow encoding in other audio-formats (esp. mp3). While you can create a FUSE chain, by using mp3fs with the trackfs filesystem as source, the performance of that approach is not very compelling and a unified solution might provide bette results. 

Troubleshooting
---------------

When `trackfs` doesn't get properly terminated, then your system might still have a orphaned mount point. When you then restart `trackfs` this will fail with a corresponding error message.

In that case you have to first unmount the orphaned mount point by calling:

   ```sudo umount /path/to/yourmountpoint```
   
In case the path to your mountpoint contains a symbolic link the above might not work as `umount` expects the _real_ path of the mount point. In that case use

	```mount -t fuse```

to find the path that `umount` expects.

In case `trackfs` hangs (should not happen, but just in case) you might have to explicitly kill it. For that first find the id of the `trackfs` container (using [`docker ps`](https://docs.docker.com/engine/reference/commandline/ps/) and the kill the container (using [`docker kill`](https://docs.docker.com/engine/reference/commandline/kill/). When you have to do this, then usually also the FUSE mountpoint is still dangling around and you have to manually unmount is. You can wire all this up in a single command:

	```docker ps | grep 'andresch/trackfs' | awk '{print $1}' | xargs docker kill ; sudo umount "/path/to/yourmountpoint```
	
Acknowledgments
---------------

`trackfs` began its live as a clone of [FLACCue](https://github.com/acenko/FLACCue). While FLACCue is designed for the usage with the Plex media server, the underlying idea of both projects is the same. Although there is little unmodified code of FLACCue left in `trackfs`, the project would most likely not have been started without the ideas in this groundwork. Kudos go to [acenkos](https://github.com/acenko)!

License
-------

`trackfs` is licensed under the terms of the [GNU Lesser General Public License v3.0](https://github.com/andresch/trackfs/blob/master/LICENSE.md)

