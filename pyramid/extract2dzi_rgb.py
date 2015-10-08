#!/usr/bin/env python

import sys
import os
import re
import numpy
from PIL import Image
import StringIO

# you need to install this library yourself
# recent versions handle bigtiff too...
import tifffile

"""
Extract pyramidal TIFF files with JPEG tiled storage into a tree of
separate JPEG files into DZI compliant directory that is usable 
by openseadragon. Multiple channels info are combined into single
jpeg.

usage: extract2dzi_rgb.py pyramid-file-dir dest-dir
         
The pyramid-file must be a multi-page TIFF with each page having an
image scaled by 1/2 from the previous page.  All pages must be tiled
with the same tile size, and tiles must be stored using the new-style
JPEG compression format, i.e. TIFF compression == 7.

The lowest resolution page must have 4 or fewer tiles.  If it has
more than 1, this script will leave space for the user to decide whether
final lowest zoom tile 0/0_0.jpg that is 1/2 scaled version of the image 
represented by that last page should be generated or not.

File directory generated
   
    dest-dir
      ImageProperties.xml
      pyramid.dzi
      0 
        0_0.jpg
      1
        0_0.jpg
        1_0.jpg
      ...

Since the tiled tiff kept padded tiles and openseadragon expected its
jpeg files to be cropped but not padded, the border tiles are cropped
and the width and height of image uses the actual image dimension
"""

try:
    srcloc = sys.argv[1]
    outloc = sys.argv[2]
    skip0 = False

    if not os.path.exists(srcloc) or not os.path.isdir(srcloc):
        sys.stderr.write('Pyramid directory must be given and exist')
        sys.stderr.write('\nusage: extract2dzi_rgb.py pyramid-file-directory dest-dir\n\n')
        sys.exit(1)

    if not os.path.exists(outloc):
      os.makedirs(outloc)
except:
    sys.stderr.write('\nusage: extract2dzi_rgb.py pyramid-file-directory dest-dir\n\n')
    raise


## 20140403-R26-Tdt-JJG-0-38-000-DAPI-Z3.tif
## 20140403-R26-Tdt-JJG-0-38-000-FITC-Z3.tif
## 20140403-R26-Tdt-JJG-0-38-000-Rhodamine-Z3.tif
## iterate through the files,
## if valid tiff file, then change the outdir to 
##     outdir/DAPI/.xml,.dzi,0,1..
## essentialy like calling extract2dzi.py filename outdir/color

infile=None
txsize=0
tysize=0
pxsize=0
pysize=0
zoomno=0
outinfo=[]
total_tiles=0
topdir_template = '%(outdir)s'
dir_template = topdir_template +'/%(zoomno)d'
tile_template = dir_template + '/%(tcolno)d_%(trowno)d.jpg'
dzi_template = '%(outdir)s/%(dzi_name)s'
image_template = '%(outdir)s/ImageProperties.xml'

################# helper functions ###################
# http://www.w3.org/Graphics/JPEG/jfif3.pdf
def jpeg_assemble(jpeg_tables_bytes, jpeg_bytes):
    return jpeg_bytes[0:2] + jpeg_tables_bytes + jpeg_bytes[2:]

def load_tile(infile, tile_offset, tile_length):
    infile.seek(tile_offset)
    return infile.read(tile_length)

def getTile(page, infile, jpeg_tables_bytes, tileno):
    jpeg = jpeg_assemble(jpeg_tables_bytes, load_tile(infile, page.tags.tile_offsets.value[tileno], page.tags.tile_byte_counts.value[tileno]))
    outfile = StringIO.StringIO()
    outfile.write( jpeg )
    outfile.seek(0)
    image = Image.open(outfile)
    ret = numpy.asarray(image)
    outfile.close()
    return ret

def maxTile(page, infile):
    pxsize, pysize, txsize, tysize, tcols, trows, jpeg_tables_bytes = get_page_info(page)
    maxval = 0
    for tileno in range(0, len(page.tags.tile_offsets.value)):
        tile = getTile(page, infile, jpeg_tables_bytes, tileno)
        maxval = max(maxval, tile.max())
    return maxval


def write_tile(tileno, trow, trows, tcol, tcols, rgb_image):
    """Output one tile.  Note this manages global state for tile grouping in subdirs."""
    global zoomno
    global total_tiles

    cropIt = False
    if (trow+1 == trows) or (tcol+1 == tcols) : 
        #this is a border tile, crop it if need to
        if tcol+1 == tcols :
           cpxsize= (pxsize-(txsize * tcol))
        else:
           cpxsize=txsize
        if trow+1 == trows :
           cpysize= (pysize-(tysize * trow))
        else:
           cpysize=tysize
        cropIt = True
    
    total_tiles += 1

    topdir = topdir_template % dict(
        outdir = outloc
    )
    if not os.path.exists(topdir):
        os.makedirs(topdir, mode=0755)

    dirname = dir_template % dict(
        outdir = outloc,
        zoomno = zoomno
        )

    if not os.path.exists(dirname):
        # create tile group dir on demand
        os.makedirs(dirname, mode=0755)

    outname = tile_template % dict(
        outdir = outloc,
        zoomno = zoomno,
        tcolno = tcol,
        trowno = trow
        )
    
    if cropIt :
        rgb_image = rgb_image.crop((0,0, cpxsize, cpysize))
    rgb_image.save(outname, 'JPEG')
    return outname

def get_page_info(page):
    pxsize = page.tags.image_width.value
    pysize = page.tags.image_length.value

    # get common JPEG tables to insert into all tiles
    # ffd8 ffdb .... ffd9
    if hasattr(page.tags, 'jpeg_tables'):
        # trim off start-image/end-image byte markers at prefix and suffix
        jpeg_tables_bytes = bytes(bytearray(page.tags.jpeg_tables.value))[2:-2]
    else:
        # no common tables to insert?
        jpeg_tables_bytes = bytes(bytearray([]))

    # this page has multiple JPEG tiles
    txsize = page.tags.tile_width.value
    tysize = page.tags.tile_length.value

    tcols = pxsize / txsize + (pxsize % txsize > 0)
    trows = pysize / tysize + (pysize % tysize > 0)

    return pxsize, pysize, txsize, tysize, tcols, trows, jpeg_tables_bytes

def processTiff() :
  global skip0
  global infile
  global txsize
  global tysize
  global pxsize
  global pysize
  global zoomno
  global total_tiles

  fname=tiff_files[0];
  chop=re.search('(?:[-][^-]*[-]Z[0-9]).tif', fname);
  t=chop.group(0)
  dzi_name=fname.replace(t,'.dzi');

  for file in range(0, len(tiff_files)):
      tiff = tifffile.TiffFile(srcloc+'/'+tiff_files[file])
      tiff_tifffile.append(tiff)
      pages = list(tiff)
      pages.reverse()
      outpages = [ page for page in pages if hasattr(page.tags, 'tile_offsets') ]
      if type(outpages[0].tags.tile_offsets.value) is int:
          outpages[0].tags.tile_offsets.value=[outpages[0].tags.tile_offsets.value]
          outpages[0].tags.tile_byte_counts.value=[outpages[0].tags.tile_byte_counts.value]
      tiff_outpages.append(outpages)
      infile = open(srcloc+'/'+tiff_files[file], 'rb')
      tiff_infile.append(infile)

# skip pages that aren't tiled... thumbnails?!
#  outpages = tiff_outpages[0]  

  if hasattr(outpages[0].tags, 'tile_offsets') and len(outpages[0].tags.tile_offsets.value) > 1:
      # first input zoom level is multi-tile
  #    assert len(outpages[0].tags.tile_offsets.value) <= 4
      if (len(outpages[0].tags.tile_offsets.value) > 4) :
        skip0 = True;
  
      zoomno = 1
      total_tiles = 1
      need_to_build_0 = True
      if (skip0):
        lowest_level = 1;
      else:
        lowest_level = 0;
  
  else:
      # input includes first zoom level already
      zoomno = 0
      lowest_level = 0
      total_tiles = 0
      need_to_build_0 = False
  
  # remember values for debugging sanity checks
  prev_page = None
  tile_width = None
  tile_length = None


###############CODE############
  for channelno in range(0, len(tiff_outpages)):
      tiff_maxval.append([])
      for pageno in range(0, len(tiff_outpages[0])):
          tiff_maxval[channelno].append(max(0, maxTile(tiff_outpages[channelno][pageno], tiff_infile[channelno])))

  for pageno in range(0, len(tiff_outpages[0])):
      page = tiff_outpages[0][pageno]
      # panic if these change from reverse-engineered samples
      assert page.tags.fill_order.value == 1
      assert page.tags.orientation.value == 1
      assert page.tags.compression.value == 7 # new-style JPEG

      if prev_page is not None:
          assert prev_page.tags.image_width.value == (page.tags.image_width.value / 2)
          assert prev_page.tags.image_length.value == (page.tags.image_length.value / 2)

      tiff_page_info = []
      for channelno in range(0, len(tiff_outpages)):
          tiff_page_info.append(tiff_outpages[channelno][pageno])
          
      
      for tileno in range(0, len(page.tags.tile_offsets.value)):
          tile_array = []
          for channelno in range(0, len(tiff_outpages)):
              tiffPage = tiff_outpages[channelno][pageno]
              pxsize, pysize, txsize, tysize, tcols, trows, jpeg_tables_bytes = get_page_info(tiffPage)
              # figure position of tile within tile array
              trow = tileno / tcols
              tcol = tileno % tcols
      
              assert trow >= 0 and trow < trows
              assert tcol >= 0 and tcol < tcols
              if tile_width is not None:
                  assert tile_width == txsize
                  assert tile_length == tysize
              else:
                  tile_width = txsize
                  tile_length = tysize
              tile = getTile(tiffPage, tiff_infile[channelno], jpeg_tables_bytes, tileno)
              tile_norm = (255 * (tile.astype('float') / tiff_maxval[channelno][pageno])).astype('uint8')
              tile_array.append(tile_norm)
          rgb_array = numpy.dstack( tuple(tile_array) )
          rgb_image = Image.fromarray(rgb_array)
          write_tile(tileno, trow, trows, tcol, tcols, rgb_image)
      
      outinfo.append(
          dict(
              tile_width= txsize,
              tile_length= tysize,
              image_width_orig= pxsize,
              image_length_orig= pysize,
              image_width_padded= tcols * txsize,
              image_length_padded= trows * tysize,
              image_level = zoomno,
              total_tile_count= total_tiles
              )
          )

      # each page is next higher zoom level
      zoomno += 1
      prev_page = page
  
  for infile in tiff_infile:
     infile.close()
  
  if need_to_build_0 :
  # add only if user wants to
      if (skip0 == False):
        # tier 0 was missing from input image, so built it from tier 1 data
        page = outpages[0]
  
        pxsize, pysize, txsize, tysize, tcols, trows, jpeg_tables_bytes = get_page_info(page)
  
        tier1 = None
  
        for tileno in range(0, len(page.tags.tile_offsets.value)):
            trow = tileno / tcols
            tcol = tileno % tcols
  
            image = Image.open(tile_template % dict(zoomno=1, tcolno=tcol, trowno=trow, outdir=outloc))
    
            if tier1 is None:
              # lazily create with proper pixel data type
                tier1 = Image.new(image.mode, (tcols * txsize, trows * tysize))
    
            # accumulate tile into tier1 image
            tier1.paste(image, (tcol * txsize, trow * tysize))
    
        # generate reduced resolution tier and crop to real page size
        tier0 = tier1.resize( (txsize * tcols / 2, tysize * trows / 2), Image.ANTIALIAS ).crop((0, 0, pxsize / 2, pysize / 2))
  
## can remove this restriction for openseadragon's viewer
##        assert tier0.size[0] <= txsize
##        assert tier0.size[1] <= tysize
  
        dirname = dir_template % dict(
            outdir = outloc,
            zoomno = 0 
            )
    
        if not os.path.exists(dirname):
            # create tile group dir on demand
            os.makedirs(dirname, mode=0755)
    
        # write final tile
        tier0.save(tile_template % dict(zoomno=0, tcolno=0, trowno=0, outdir=outloc), 'JPEG')
  else:
      # tier 0 must be cropped down to the page size...
      page = outpages[0]
      pxsize, pysize, txsize, tysize, tcols, trows, jpeg_tables_bytes = get_page_info(page)
      image = Image.open(tile_template % dict(zoomno=0, tcolno=0, trowno=0, outdir=outloc))
      image = image.crop((0,0, pxsize,pysize))
      image.save(tile_template % dict(zoomno=0, tcolno=0, trowno=0, outdir=outloc), 'JPEG')
  
  imageinfo=outinfo[-1]
  
  dzi_descriptor = """\
<?xml version="1.0" encoding="UTF-8"?>
<Image TileSize="%(tile_width)d" 
         Overlap="1" 
         Format="jpg" 
         xmlns="http://schemas.microsoft.com/deepzoom/2008">
         <Size Width="%(image_width_orig)d" Height="%(image_length_orig)d"/>
</Image>
""" % imageinfo
  dname= dzi_template % dict(outdir = outloc, dzi_name=dzi_name)
  f = open('%s' % dname, 'w')
  f.write(dzi_descriptor)
  f.close

  imageinfo['image_lowest_level']=lowest_level
  imageinfo['data_location']=outloc;
  
  image_descriptor = """\
<?xml version="1.0" encoding="UTF-8"?>
<IMAGE_PROPERTIES
                    WIDTH="%(image_width_orig)d" 
                    HEIGHT="%(image_length_orig)d" 
                    NUMTILES="%(total_tile_count)d" 
                    NUMIMAGES="1" 
                    VERSION="1.8" 
                    TILESIZE="%(tile_width)d" 
                    MINLEVEL="%(image_lowest_level)d" 
                    MAXLEVEL="%(image_level)d" 
                    DATA="%(data_location)s"
/>
""" % imageinfo
  
  iname= image_template % dict(outdir = outloc)
  f = open('%s' % iname, 'w')
  f.write(image_descriptor)
  f.close
    
###############################################

tiff_files = []
tiff_outpages = []
tiff_tifffile = []
tiff_infile = []
tiff_maxval = []

redColors = ['Rhodamine', 'RFP', 'Alexa Fluor 555', 'Alexa Fluor 594', 'tdTomato', 'Alexa Fluor 633', 'Alexa Fluor 647']
greenColors = ['FITC', 'Alexa 488', 'EGFP', 'Alexa Fluor 488']
blueColors = ['DAPI']

tiff_colors = [redColors, greenColors, blueColors]

def getFileColor(file):
    colorMatched = None
    for colors in tiff_colors:
        for color in colors:
            if re.match('.*[-]%s([-]Z[0-9]+)*[.]tif' % color, file):
                colorMatched = True
                return color
    if not colorMatched:
        sys.stderr.write('Unknown color for file "%s" \n' % file)
        sys.exit(1)

def checkFileColors(files):
    for file in files:
        colorMatched = None
        for colors in tiff_colors:
            for color in colors:
                if re.match('.*[-]%s[-]Z1[.]tif' % color, file):
                    colorMatched = True
                    break
            if colorMatched:
                break
        if not colorMatched:
            sys.stderr.write('Unknown color for file "%s" \n' % file)
            sys.exit(1)
    
def colorFile(files, colors, pattern):
    tifFiles = []
    for color in colors:
        colorFiles = [ f for f in files if re.match('.*[-]%s%s' % (color, pattern), f) ]
        if len(colorFiles) == 1:
            tifFiles.append(colorFiles[0])
    if len(tifFiles) > 0:
        return tifFiles
    else:
        return None
    
def getTiffFiles(dname):
    global tiff_files
    files = os.listdir(dname)
    z1 = [f for f in files if re.match('.*[-]Z1[.]tif', f)]
    if len(z1) > 0:
        checkFileColors(z1)
        stacks = len(files) / len(z1)
        stackNo = stacks / 2
        if stackNo * 2 < stacks:
            stackNo += 1
        stackPattern = '[-]Z%d[.]tif' % stackNo
    else:
        stackPattern = '[.]tif'
    for colors in tiff_colors:
        colorFiles = colorFile(files, colors, stackPattern)
        if colorFiles:
            for file in colorFiles:
                tiff_files.append(file)
    if len(tiff_files) == 0:
        tiff_files = [ '%s' % (f) for f in files if re.match('.*%s' % stackPattern, f) ]
    

####### Main body ######
try:
    getTiffFiles(srcloc)
except SystemExit:
    raise

if len(tiff_files) == 0:
    print 'Nothing to do'
    sys.exit()

if not os.path.exists(outloc):
    os.makedirs(outloc)

processTiff()

