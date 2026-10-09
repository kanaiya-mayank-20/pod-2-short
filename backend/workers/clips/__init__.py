"""Turning chosen titles into rendered, subtitled clips.

The title stage of the hierarchy already decided which moments are worth a clip. This
package turns one of those recommendations into a video:

* :mod:`workers.clips.catalog` reads the stored hierarchy and lists every title with a
  stable id, so the API can show them and the client can pick some;
* :mod:`workers.clips.subtitles` writes the burned-in word-by-word captions;
* :mod:`workers.clips.render` cuts the segments out of the source video with ffmpeg and
  hands back one file per title.

Subtitle styling is fixed in :mod:`workers.clips.style` and is deliberately not a
request parameter.
"""
