#!/usr/bin/env python3
"""rvn_thumb.py - a thumbnail that is a rendering of the numbers, not an asset.

Card-only channels in this network already solved this: openrappter-training
ships its thumb as an inline `data:image/svg+xml;base64,...` URI. No file on
disk, nothing to 404, nothing to go stale against the entry it belongs to.

That matters more here than it does there. These channels are written by twins
on a schedule, and a producer that also had to author a JPEG would be a
producer that either fabricates an image or skips the thumb. Rendering the
measured numbers as SVG keeps the rule intact: everything published was
measured, including the picture.
"""

import base64


def _esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def card_svg(kicker, line1, line2, stat, accent="#5ee0a0", footer=""):
    """1280x720 title card. Deliberately plain: it is a label, not a poster."""
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" '
        'viewBox="0 0 1280 720">',
        '<rect width="1280" height="720" fill="#080c13"/>',
        '<rect x="0" y="0" width="1280" height="10" fill="%s"/>' % accent,
        '<text x="76" y="120" font-family="ui-monospace,SFMono-Regular,Menlo,monospace" '
        'font-size="30" fill="%s">%s</text>' % (accent, _esc(kicker)),
        '<text x="76" y="270" font-family="ui-monospace,SFMono-Regular,Menlo,monospace" '
        'font-size="62" font-weight="700" fill="#e9eef5">%s</text>' % _esc(line1),
    ]
    if line2:
        parts.append(
            '<text x="76" y="350" font-family="ui-monospace,SFMono-Regular,Menlo,monospace" '
            'font-size="62" font-weight="700" fill="#e9eef5">%s</text>' % _esc(line2))
    parts += [
        '<rect x="76" y="430" width="1128" height="120" rx="12" fill="#121924"/>',
        '<text x="106" y="505" font-family="ui-monospace,SFMono-Regular,Menlo,monospace" '
        'font-size="38" fill="%s">%s</text>' % (accent, _esc(stat)),
        '<text x="76" y="655" font-family="ui-monospace,SFMono-Regular,Menlo,monospace" '
        'font-size="26" fill="#6b7684">%s</text>' % _esc(footer),
        '</svg>',
    ]
    return "".join(parts)


def data_uri(svg):
    b64 = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return "data:image/svg+xml;base64," + b64
