import pytest

from mitmproxy.contentviews.image import image_parser
from mitmproxy.test import tutils


@pytest.mark.parametrize("filename, metadata", {
    # no textual data
    "mitmproxy/data/image_parser/ct0n0g04.png": [
        ('Format', 'Portable network graphics'),
        ('Size', '32 x 32 px'),
        ('gamma', '1.0')
    ],
    # with textual data
    "mitmproxy/data/image_parser/ct1n0g04.png": [
        ('Format', 'Portable network graphics'),
        ('Size', '32 x 32 px'),
        ('gamma', '1.0'),
        ('Title', 'PngSuite'),
        ('Author', 'Willem A.J. van Schaik\n(willem@schaik.com)'),
        ('Copyright', 'Copyright Willem van Schaik, Singapore 1995-96'),
        ('Description', 'A compilation of a set of images created to test the\n'
         'various color-types of the PNG format. Included are\nblack&white, color,'
         ' paletted, with alpha channel, with\ntransparency formats. All bit-depths'
         ' allowed according\nto the spec are present.'),
        ('Software', 'Created on a NeXTstation color using "pnmtopng".'),
        ('Disclaimer', 'Freeware.')
    ],
    # with compressed textual data
    "mitmproxy/data/image_parser/ctzn0g04.png": [
        ('Format', 'Portable network graphics'),
        ('Size', '32 x 32 px'),
        ('gamma', '1.0'),
        ('Title', 'PngSuite'),
        ('Author', 'Willem A.J. van Schaik\n(willem@schaik.com)'),
        ('Copyright', 'Copyright Willem van Schaik, Singapore 1995-96'),
        ('Description', 'A compilation of a set of images created to test the\n'
         'various color-types of the PNG format. Included are\nblack&white, color,'
         ' paletted, with alpha channel, with\ntransparency formats. All bit-depths'
         ' allowed according\nto the spec are present.'),
        ('Software', 'Created on a NeXTstation color using "pnmtopng".'),
        ('Disclaimer', 'Freeware.')
    ],
    # UTF-8 international text - english
    "mitmproxy/data/image_parser/cten0g04.png": [
        ('Format', 'Portable network graphics'),
        ('Size', '32 x 32 px'),
        ('gamma', '1.0'),
        ('Title', 'PngSuite'),
        ('Author', 'Willem van Schaik (willem@schaik.com)'),
        ('Copyright', 'Copyright Willem van Schaik, Canada 2011'),
        ('Description', 'A compilation of a set of images created to test the '
         'various color-types of the PNG format. Included are black&white, color,'
         ' paletted, with alpha channel, with transparency formats. All bit-depths'
         ' allowed according to the spec are present.'),
        ('Software', 'Created on a NeXTstation color using "pnmtopng".'),
        ('Disclaimer', 'Freeware.')
    ],
    # check gamma value
    "mitmproxy/data/image_parser/g07n0g16.png": [
        ('Format', 'Portable network graphics'),
        ('Size', '32 x 32 px'),
        ('gamma', '0.7')
    ],
    # check aspect value
    "mitmproxy/data/image_parser/aspect.png": [
        ('Format', 'Portable network graphics'),
        ('Size', '1280 x 798 px'),
        ('aspect', '72 x 72'),
        ('date:create', '2012-07-11T14:04:52-07:00'),
        ('date:modify', '2012-07-11T14:04:52-07:00')
    ],
}.items())
def test_parse_png(filename, metadata):
    with open(tutils.test_data.path(filename), "rb") as f:
        assert metadata == image_parser.parse_png(f.read())


@pytest.mark.parametrize("filename, metadata", {
    # check comment
    "mitmproxy/data/image_parser/hopper.gif": [
        ('Format', 'Compuserve GIF'),
        ('version', 'GIF89a'),
        ('Size', '128 x 128 px'),
        ('background', '0'),
        ('comment', "b'File written by Adobe Photoshop\\xa8 4.0'")
    ],
    # check background
    "mitmproxy/data/image_parser/chi.gif": [
        ('Format', 'Compuserve GIF'),
        ('version', 'GIF89a'),
        ('Size', '320 x 240 px'),
        ('background', '248'),
        ('comment', "b'Created with GIMP'")
    ],
    # check working with color table
    "mitmproxy/data/image_parser/iss634.gif": [
        ('Format', 'Compuserve GIF'),
        ('version', 'GIF89a'),
        ('Size', '245 x 245 px'),
        ('background', '0')
    ],
}.items())
def test_parse_gif(filename, metadata):
    with open(tutils.test_data.path(filename), 'rb') as f:
        assert metadata == image_parser.parse_gif(f.read())
