# Asset credits

`static/riitube-logo.svg` combines newly typeset "Rii" lettering with the red
badge and Tube paths from YouTube's 2006–2011 wordmark. Editable source:
`riitube-logo-source.svg`. Reference: `youtube-2006-reference.svg`, from
[Wikimedia Commons](https://commons.wikimedia.org/wiki/File:Logo_of_YouTube_(2006-2011).svg).
YouTube is the author; Commons marks the simple text/shape logo as PD-textlogo.
YouTube's marks remain their owners' marks.

The Rii lettering uses Oswald Medium from
[Google Fonts](https://github.com/google/fonts/tree/main/ofl/oswald), converted
to paths with Inkscape. The font and its SIL Open Font License are retained in
`static/fonts/`; that license is separate from the application's AGPLv3 license.

`youtube-favicon-2005-2009.png` preserves YouTube's original favicon, sourced
from [Wikimedia Commons](https://commons.wikimedia.org/wiki/File:YouTube_favicon_(2005-2009).png)
(YouTube; Commons PD-textlogo). The shipped RiiTube icons adapt its stacked layout
using the existing wordmark paths; the reference PNG is not the site's icon.

Rebuild icons with `.venv/bin/python assets/build_favicons.py`, using Pillow and
Inkscape. It renders the SVG, PNG, ICO, Safari mask, Windows tile, and web manifest assets.
