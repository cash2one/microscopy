#!/usr/bin/python

# 
# Copyright 2014 University of Southern California
# 
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
# 
#    http://www.apache.org/licenses/LICENSE-2.0
# 
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
"""
Load configuration for the Scan Tiler.
Check with ermrest for images that need tiles.
Generate the tiles directory.
Generate the thumbnail and the HTML zoomify file.
Update ermrest.
"""

import os
import logging
import json
import sys
import traceback

from client import ErmrestClient, UnresolvedAddress, NetworkError, ProtocolError, MalformedURL
from logging.handlers import RotatingFileHandler    

FORMAT = '%(asctime)s: %(levelname)s <%(module)s>: %(message)s'
logger = logging.getLogger(__name__)

# Exit return codes
__EXIT_SUCCESS = 0
__EXIT_FAILURE = 1

# Loglevel dictionary
__LOGLEVEL = {'error': logging.ERROR,
              'warning': logging.WARNING,
              'info': logging.INFO,
              'debug': logging.DEBUG}

def load(config_filename):
    """
    Read the configuration file.
    """
    
    # Load configuration file, or create configuration based on arguments
    cfg = {}
    if os.path.exists(config_filename):
        f = open(config_filename, 'r')
        try:
            cfg = json.load(f)
            loglevel = cfg.get('loglevel', None)
            logfile = cfg.get('log', None)
            if loglevel and logfile:
                rotatingFileHandler = RotatingFileHandler(logfile, maxBytes=1000000, backupCount=7)
                rotatingFileHandler.setFormatter(logging.Formatter(FORMAT))
                logger.addHandler(rotatingFileHandler)
                logger.setLevel(__LOGLEVEL.get(loglevel))
            else:
                logging.getLogger().addHandler(logging.NullHandler())
            logger.debug("config: %s" % cfg)
        except ValueError as e:
            logger.error('Malformed configuration file: %s' % e)
            return None
        else:
            f.close()
    else:
        logger.error('Configuration file: "%s" does not exist.' % config_filename)
        return None
    
    # Ermrest settings
    url = cfg.get('url', None)
    if not url:
        logger.error('Ermrest URL must be given.')
        return None
    
    goauthtoken = cfg.get('goauthtoken', None)
    
    tiles = cfg.get('tiles', None)
    if not tiles or not os.path.isdir(tiles):
        logger.error('Tiles directory must be given and exist.')
        return None

    thumbnails = cfg.get('thumbnails', None)
    if not thumbnails or not os.path.isdir(thumbnails):
        logger.error('Thumbnails directory must be given and exist.')
        return None

    tiff = cfg.get('tiff', None)
    if not tiff or not os.path.isdir(tiff):
        logger.error('Tiff directory must be given and exist.')
        return None

    html = cfg.get('html', None)
    if not html or not os.path.isdir(html):
        logger.error('HTML directory must be given and exist.')
        return None

    cirm_path = cfg.get('cirm_path', None)
    if not cirm_path:
        logger.error('CIRM PATH must be given.')
        return None

    http_storage = cfg.get('http_storage', None)
    if not cirm_path:
        logger.error('HTTP Storage must be given.')
        return None

    extract = cfg.get('extract', None)
    if not extract or not os.path.isfile(extract):
        logger.error('Extract application must be given and exist.')
        return None

    username = cfg.get('username', None)
    if not username:
        logger.error('Ermrest username must be given.')
        return None
        
    password = cfg.get('password', None)
    if not password:
        logger.error('Ermrest password must be given.')
        return None

    mail_server = cfg.get('mail_server', None)
    mail_sender = cfg.get('mail_sender', None)
    mail_receiver = cfg.get('mail_receiver', None)
    timeout = cfg.get('timeout', 30)

    # Establish Ermrest client connection
    try:
        client = ErmrestClient(baseuri=url, \
                               username=username, \
                               password=password, \
                               tiles=tiles, \
                               thumbnails=thumbnails, \
                               tiff=tiff, \
                               html=html, \
                               extract=extract, \
                               cirm_path=cirm_path, \
                               http_storage=http_storage, \
                               timeout=timeout, \
                               mail_server=mail_server, \
                               mail_sender=mail_sender, \
                               mail_receiver=mail_receiver,
                               logger=logger,
                               use_goauth=goauthtoken)
        client.connect()
    except MalformedURL as err:
        logger.error(err)
        return None
    except UnresolvedAddress as err:
        logger.error(err)
        return None
    except NetworkError as err:
        logger.error(err)
        return None
    except ProtocolError as err:
        logger.error(err)
        return None
    except:
        et, ev, tb = sys.exc_info()
        logger.error('got INIT exception "%s"' % str(ev))
        logger.error('%s' % str(traceback.format_exception(et, ev, tb)))
        return None
    
    return client

try:
    config_filename = sys.argv[1]
    client = load(config_filename)
    if client:
        client.start()
except:
    sys.stderr.write('\nusage: scanTiler.py config-file\n\n')
    raise
