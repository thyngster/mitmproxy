import io
import typing

from kaitaistruct import KaitaiStream

from mitmproxy.contrib.kaitaistruct import png
from mitmproxy.contrib.kaitaistruct import gif

Metadata = typing.List[typing.Tuple[str, str]]


def parse_png(data: bytes) -> Metadata:
    img = png.Png(KaitaiStream(io.BytesIO(data)))
    parts = [
        ('Format', 'Portable network graphics')
    ]
    parts.append(('Size', "{0} x {1} px".format(img.ihdr.width, img.ihdr.height)))
    for chunk in img.chunks:
        if chunk.type == 'gAMA':
            parts.append(('gamma', str(chunk.body.gamma_int / 100000)))
        elif chunk.type == 'pHYs':
            aspectx = chunk.body.pixels_per_unit_x
            aspecty = chunk.body.pixels_per_unit_y
            parts.append(('aspect', "{0} x {1}".format(aspectx, aspecty)))
        elif chunk.type == 'tEXt':
            parts.append((chunk.body.keyword, chunk.body.text))
        elif chunk.type == 'iTXt':
            parts.append((chunk.body.keyword, chunk.body.text))
        elif chunk.type == 'zTXt':
            parts.append((chunk.body.keyword, chunk.body.text_datastream.decode('iso8859-1')))
    return parts


def parse_gif(data: bytes) -> Metadata:
    img = gif.Gif(KaitaiStream(io.BytesIO(data)))
    parts = [
        ('Format', 'Compuserve GIF')
    ]
    parts.append(('version', "GIF{0}".format(img.header.version.decode('ASCII'))))
    descriptor = img.logical_screen_descriptor
    parts.append(('Size', "{0} x {1} px".format(descriptor.screen_width, descriptor.screen_height)))
    parts.append(('background', str(descriptor.bg_color_index)))
    ext_blocks = []
    for block in img.blocks:
        if block.block_type.name == 'extension':
            ext_blocks.append(block)
    comment_blocks = []
    for block in ext_blocks:
        if block.body.label._name_ == 'comment':
            comment_blocks.append(block)
    for block in comment_blocks:
        entries = block.body.body.entries
        for entry in entries:
            comment = entry.bytes
            if comment is not b'':
                parts.append(('comment', str(comment)))
    return parts
