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
Raw network client for HTTP(S) communication with ERMREST service.
"""

import os
import subprocess
import hashlib
import json
import base64
import urlparse
from httplib import HTTPConnection, HTTPSConnection, HTTPException, OK, CREATED, ACCEPTED, NO_CONTENT, CONFLICT, FORBIDDEN, INTERNAL_SERVER_ERROR, SERVICE_UNAVAILABLE, BadStatusLine, CannotSendRequest, GATEWAY_TIMEOUT, METHOD_NOT_ALLOWED, NOT_FOUND
import sys
import traceback
import time
import shutil
import smtplib
import urllib
import re
import mimetypes
from email.mime.text import MIMEText
import socket
from dateutil.parser import parse

import httplib
import httplib2
import random

from apiclient.discovery import build
from apiclient.errors import HttpError
from apiclient.http import MediaFileUpload
from oauth2client.client import flow_from_clientsecrets
from oauth2client.file import Storage
from oauth2client.tools import argparser, run_flow

from client_delete import ClientDeleteYouTubeVideo

mail_footer = 'Do not reply to this message.  This is an automated message generated by the system, which does not receive email messages.'

YOUTUBE_UPLOAD_SCOPE = set(["https://www.googleapis.com/auth/youtube", "https://www.googleapis.com/auth/youtube.upload"])
YOUTUBE_API_SERVICE_NAME = "youtube"
YOUTUBE_API_VERSION = "v3"
    
httplib2.RETRIES = 1
MAX_RETRIES = 10            
RETRIABLE_EXCEPTIONS = (    
    httplib2.HttpLib2Error, 
    IOError, 
    httplib.NotConnected,
    httplib.IncompleteRead, 
    httplib.ImproperConnectionState,
    httplib.CannotSendRequest, 
    httplib.CannotSendHeader,
    httplib.ResponseNotReady, 
    httplib.BadStatusLine)

RETRIABLE_STATUS_CODES = [500, 502, 503, 504]

MISSING_CLIENT_SECRETS_MESSAGE = "WARNING: Please configure OAuth 2.0"

class ErmrestHTTPException(Exception):
    def __init__(self, value, status, retry=False):
        super(ErmrestHTTPException, self).__init__(value)
        self.value = value
        self.status = status
        self.retry = retry
        
    def __str__(self):
        message = "%s." % self.value
        return message

class ErmrestException(Exception):
    def __init__(self, value, cause=None):
        super(ErmrestException, self).__init__(value)
        self.value = value
        self.cause = cause
        
    def __str__(self):
        message = "%s." % self.value
        if self.cause:
            message += " Caused by: %s." % self.cause
        return message

class MalformedURL(ErmrestException):
    """MalformedURL indicates a malformed URL.
    """
    def __init__(self, cause=None):
        super(MalformedURL, self).__init__("URL was malformed", cause)

class UnresolvedAddress(ErmrestException):
    """UnresolvedAddress indicates a failure to resolve the network address of
    the Ermrest service.
    
    This error is raised when a low-level socket.gaierror is caught.
    """
    def __init__(self, cause=None):
        super(UnresolvedAddress, self).__init__("Could not resolve address of host", cause)

class NetworkError(ErmrestException):
    """NetworkError wraps a socket.error exception.
    
    This error is raised when a low-level socket.error is caught.
    """
    def __init__(self, cause=None):
        super(NetworkError, self).__init__("Network I/O failure", cause)

class ProtocolError(ErmrestException):
    """ProtocolError indicates a protocol-level failure.
    
    In other words, you may have tried to add a tag for which no tagdef exists.
    """
    def __init__(self, message='Network protocol failure', errorno=-1, response=None, cause=None):
        super(ProtocolError, self).__init__("Ermrest protocol failure", cause)
        self._errorno = errorno
        self._response = response
        
    def __str__(self):
        message = "%s." % self.value
        if self._errorno >= 0:
            message += " HTTP ERROR %d: %s" % (self._errorno, self._response)
        return message
    
class NotFoundError(ErmrestException):
    """Raised for HTTP NOT_FOUND (i.e., ERROR 404) responses."""
    pass


class ErmrestClient (object):
    """Network client for ERMREST.
    """
    ## Derived from the ermrest iobox service client

    def __init__(self, **kwargs):
        self.baseuri = kwargs.get("baseuri")
        o = urlparse.urlparse(self.baseuri)
        self.scheme = o[0]
        host_port = o[1].split(":")
        self.host = host_port[0]
        self.path = o.path
        self.port = None
        if len(host_port) > 1:
            self.port = host_port[1]
        self.cookie = kwargs.get("cookie")
        self.client_secrets_file = kwargs.get("client_secrets_file")
        self.client_oauth2_file = kwargs.get("client_oauth2_file")
        self.data_scratch = kwargs.get("data_scratch")
        self.category = kwargs.get("category")
        self.keywords = kwargs.get("keywords")
        self.privacyStatus = kwargs.get("privacyStatus")
        self.timeout = kwargs.get("timeout") * 60
        self.limit = kwargs.get("limit")
        self.chunk_size = kwargs.get("chunk_size")
        self.mail_server = kwargs.get("mail_server")
        self.mail_sender = kwargs.get("mail_sender")
        self.mail_receiver = kwargs.get("mail_receiver")
        self.logger = kwargs.get("logger")
        self.header = None
        self.webconn = None
        argparser.add_argument("--config", required=True, help="YouTube configuration file")
        self.args = argparser.parse_args()
        self.args.category = self.category
        self.args.keywords = self.keywords
        self.args.privacyStatus = self.privacyStatus
        self.args.noauth_local_webserver = True
        self.logger.debug('Client initialized.')

    """
    Send requests to the Ermrest service.
    """
    def send_request(self, method, url, body='', headers={}, sendData=False, ignoreErrorCodes=[]):
        try:
            request_headers = headers.copy()
            url = self.url_cid(url)
            if self.header:
                headers.update(self.header)
            self.logger.debug('Sending request: method="%s", url="%s://%s%s", headers="%s"' % (method, self.scheme, self.host, url, request_headers))
            retry = False
            try:
                if sendData == False:
                    self.webconn.request(method, url, body, headers)
                else:
                    """ 
                    For file upload send the request step by step 
                    """
                    self.webconn.putrequest(method, url)
                    for key,value in headers.iteritems():
                        self.webconn.putheader(key,value)
                    self.webconn.endheaders()
                    self.webconn.send(body)
                resp = self.webconn.getresponse()
                self.logger.debug('Response: %d' % resp.status)
            except socket.error, e:
                retry = True
                self.logger.debug('Socket error: %d' % (e.errno))
            except (BadStatusLine, CannotSendRequest):
                retry = True
            except:
                raise
            if retry:
                """ 
                Resend the request 
                """
                self.close()
                self.connect()
                self.sendMail('WARNING Video: The HTTPSConnection has been restarted', 'The HTTPSConnection has been restarted on "%s://%s".\n' % (self.scheme, self.host))
                self.logger.debug('Resending request: method="%s", url="%s://%s%s"' % (method, self.scheme, self.host, url))
                if sendData == False:
                    self.webconn.request(method, url, body, headers)
                else:
                     self.webconn.putrequest(method, url)
                     for key,value in headers.iteritems():
                         self.webconn.putheader(key,value)
                     self.webconn.endheaders()
                     self.webconn.send(body)
                resp = self.webconn.getresponse()
                self.logger.debug('Response: %d' % resp.status)
            if resp.status in [INTERNAL_SERVER_ERROR, SERVICE_UNAVAILABLE, GATEWAY_TIMEOUT]:
                """ 
                Resend the request 
                """
                self.close()
                self.connect()
                self.sendMail('WARNING Video: The HTTPSConnection has been restarted', 'HTTP exception: %d.\nThe HTTPSConnection has been restarted on "%s://%s".\n' % (resp.status, self.scheme, self.host))
                self.logger.debug('Resending request: method="%s", url="%s://%s%s", headers="%s"' % (method, self.scheme, self.host, url, request_headers))
                if sendData == False:
                    self.webconn.request(method, url, body, headers)
                else:
                     self.webconn.putrequest(method, url)
                     for key,value in headers.iteritems():
                         self.webconn.putheader(key,value)
                     self.webconn.endheaders()
                     self.webconn.send(body)
                resp = self.webconn.getresponse()
                self.logger.debug('Response: %d' % resp.status)
            if resp.status not in [OK, CREATED, ACCEPTED, NO_CONTENT]:
                errmsg = resp.read()
                if resp.status not in ignoreErrorCodes:
                    self.logger.error('Error response: method="%s", url="%s://%s%s", status=%i, error: %s' % (method, self.scheme, self.host, url, resp.status, errmsg))
                else:
                    self.logger.error('Error response: %s' % (errmsg))
                raise ErmrestHTTPException("Error response (%i) received: %s" % (resp.status, errmsg), resp.status, retry)
            return resp
        except ErmrestHTTPException:
            raise
        except:
            et, ev, tb = sys.exc_info()
            self.logger.error('got HTTP exception: method="%s", url="%s://%s%s", error="%s"' % (method, self.scheme, self.host, url, str(ev)))
            self.logger.error('%s' % str(traceback.format_exception(et, ev, tb)))
            self.sendMail('FAILURE Video: Unexpected Exception', 'Error generated during the HTTP request: method="%s", url="%s://%s%s", error="\n%s\n%s"' % (method, self.scheme, self.host, url, str(ev), ''.join(traceback.format_exception(et, ev, tb))))
            raise

    """
    Open the connection to the Ermrest service.
    """
    def connect(self, reconnect=False):
        if self.scheme == 'https':
            self.webconn = HTTPSConnection(host=self.host, port=self.port)
        elif self.scheme == 'http':
            self.webconn = HTTPConnection(host=self.host, port=self.port)
        else:
            raise ValueError('Scheme %s is not supported.' % self.scheme)

        """
        if self.use_goauth:
            auth = base64.encodestring('%s:%s' % (self.username, self.password)).replace('\n', '')
            headers = dict(Authorization='Basic %s' % auth)
            resp = self.send_request('GET', '/service/nexus/goauth/token?grant_type=client_credentials', '', headers, reconnect)
            goauth = json.loads(resp.read())
            self.access_token = goauth['access_token']
            self.header = dict(Authorization='Globus-Goauthtoken %s' % self.access_token)
        else:
            #headers = {}
            #headers["Content-Type"] = "application/x-www-form-urlencoded"
            #resp = self.send_request("POST", "/ermrest/authn/session", "username=%s&password=%s" % (self.username, self.password), headers, reconnect)
            #self.header = dict(Cookie=resp.getheader("set-cookie"))
        """
        self.header = {'Cookie': self.cookie}
        
    """
    Close the connection to the Ermrest service.
    The underlying python documentation is not very helpful but it would
    appear that the HTTP[S]Connection.close() could raise a socket.error.
    Thus, this method potentially raises a 'NetworkError'.
    """
    def close(self):
        assert self.webconn
        try:
            self.webconn.close()
        except socket.error as e:
            raise NetworkError(e)
        finally:
            self.webconn = None

    """
    Send email notification
    """
    def sendMail(self, subject, text):
        if self.mail_server and self.mail_sender and self.mail_receiver:
            retry = 0
            ready = False
            while not ready:
                try:
                    msg = MIMEText('%s\n\n%s' % (text, mail_footer), 'plain')
                    msg['Subject'] = subject
                    msg['From'] = self.mail_sender
                    msg['To'] = self.mail_receiver
                    s = smtplib.SMTP(self.mail_server)
                    s.sendmail(self.mail_sender, self.mail_receiver.split(','), msg.as_string())
                    s.quit()
                    self.logger.debug('Sent email notification.')
                    ready = True
                except socket.gaierror as e:
                    if e.errno == socket.EAI_AGAIN:
                        time.sleep(100)
                        retry = retry + 1
                        ready = retry > 10
                    else:
                        ready = True
                    if ready:
                        et, ev, tb = sys.exc_info()
                        self.logger.error('got exception "%s"' % str(ev))
                        self.logger.error('%s' % str(traceback.format_exception(et, ev, tb)))
                except:
                    et, ev, tb = sys.exc_info()
                    self.logger.error('got exception "%s"' % str(ev))
                    self.logger.error('%s' % str(traceback.format_exception(et, ev, tb)))
                    ready = True

    """
    Start the process for uploading videos to YouTube
    """
    def start(self):
        self.connect()
        try:
            self.youtube_authenticated_service()
            if self.youtube is not None:
                self.logger.debug('Authenticated to the YouTube service.')
                self.uploadVideo()
                self.clearYouTube()
            self.deleteVideo()
        except:
            et, ev, tb = sys.exc_info()
            self.logger.error('got unexpected exception "%s"' % str(ev))
            self.logger.error('%s' % str(traceback.format_exception(et, ev, tb)))
            self.sendMail('FAILURE Video Processing: unexpected exception', '%s\nThe process might have been stopped\n' % str(traceback.format_exception(et, ev, tb)))
            raise
        
    """
    Get the YouTube credentials
    """
    def youtube_authenticated_service(self):
        flow = flow_from_clientsecrets(self.client_secrets_file, scope=YOUTUBE_UPLOAD_SCOPE, message=MISSING_CLIENT_SECRETS_MESSAGE)
        storage = Storage(self.client_oauth2_file)
        credentials = storage.get()
        if credentials is None or credentials.invalid:
            credentials = run_flow(flow, storage, self.args)
        self.youtube = build(YOUTUBE_API_SERVICE_NAME, YOUTUBE_API_VERSION, http=credentials.authorize(httplib2.Http()))
        
    """
    Create a request to upload a video to YouTube
    """
    def youtube_request(self):
        tags = None
        if self.args.keywords:
            tags = self.args.keywords.split(",")
        body=dict(
            snippet=dict(
                title=self.args.title,
                description=self.args.description,
                tags=tags,
                categoryId=self.args.category
            ),
            status=dict(
                privacyStatus=self.args.privacyStatus
            )
        )
        request = self.youtube.videos().insert(
            part=",".join(body.keys()),
            body=body,
            media_body=MediaFileUpload(self.args.file, chunksize=-1, resumable=True)
        )
        return request

    """
    Upload a video to YouTube
    """
    def youtube_upload(self, request):
        response = None
        retry = 0
        id = None
        while response is None:
            error = None
            try:
                self.logger.debug('Uploading file...')
                status, response = request.next_chunk()
                if 'id' in response:
                    id = response['id']
                    self.logger.debug("Video id '%s' was successfully uploaded." % id)
                else:
                    self.logger.error("The upload failed with an unexpected response: %s" % response)
            except HttpError, e:
                if e.resp.status in RETRIABLE_STATUS_CODES:
                    error = "A retriable HTTP error %d occurred:\n%s" % (e.resp.status, e.content)
                else:
                    raise
            except RETRIABLE_EXCEPTIONS, e:
                error = "A retriable error occurred: %s" % e
            if error is not None:
                self.logger.error(error)
                retry += 1
                if retry > MAX_RETRIES:
                    self.logger.error("No longer attempting to retry.")
                    break
                max_sleep = 2 ** retry
                sleep_seconds = random.random() * max_sleep  
                self.logger.debug("Sleeping %f seconds and then retrying..." % sleep_seconds)
                time.sleep(sleep_seconds)
        return id
                
    """
    Delete videos from YouTube
    """
    def clearYouTube(self):
        url = '%s/entity/Common:Delete_Youtube/Youtube_Deleted=FALSE&Record_Type=Immunofluorescence%%3ASlide_Video&!YouTube_MD5::null::&!YouTube_URI::null::' % (self.path)
        headers = {'Content-Type': 'application/json', 'Accept': 'application/json'}
        resp = self.send_request('GET', url, '', headers, False)
        videos = json.loads(resp.read())
        videoids = []
        for video in videos:
            videoids.append((video['YouTube_URI'], video['RID']))
                
        self.logger.debug('Deleting from YouTube %d video(s).' % (len(videoids))) 
        if len(videoids) > 0:
            client_delete = ClientDeleteYouTubeVideo(client_secrets_file=self.client_secrets_file,
                                                     client_oauth2_file=self.client_oauth2_file,
                                                     logger=self.logger)
            for youtube_uri,rid in videoids:
                youtube_deleted = client_delete.youtube_delete(youtube_uri)
                if youtube_deleted == True:
                    columns = ["Youtube_Deleted"]
                    columns = ','.join([urllib.quote(col, safe='') for col in columns])
                    url = '%s/attributegroup/Common:Delete_Youtube/RID;%s' % (self.path, columns)
                    body = []
                    obj = {'RID': rid,
                           "Youtube_Deleted": True
                           }
                    body.append(obj)
                    headers = {'Content-Type': 'application/json'}
                    resp = self.send_request('PUT', url, json.dumps(body), headers, False)
                    resp.read()
                    self.logger.debug('SUCCEEDED updated the Common:Delete_Youtube table entry for the YouTube URL: "%s".' % (youtube_uri)) 
        
    """
    Delete videos from hatrac
    """
    def deleteVideo(self):
        url = '%s/entity/Common:Delete_Hatrac/Hatrac_Deleted=FALSE' % (self.path)
        headers = {'Content-Type': 'application/json', 'Accept': 'application/json'}
        resp = self.send_request('GET', url, '', headers, False)
        files = json.loads(resp.read())
        fileids = []
        for f in files:
            fileids.append((f['Hatrac_URI'], f['RID']))
                
        self.logger.debug('Deleting from hatrac %d files(s).' % (len(fileids))) 
        for hatrac_uri,rid in fileids:
            parts = []
            for part in hatrac_uri.split('/'):
                parts.append(urllib.quote(part, safe=''))
            url = '%s' % ('/'.join(parts))
            headers = {}
            resp = self.send_request('DELETE', url, '', headers, False)
            resp.read()
            self.logger.debug('SUCCEEDED deleted from hatrac the "%s" file.' % (hatrac_uri)) 
            columns = ["Hatrac_Deleted"]
            columns = ','.join([urllib.quote(col, safe='') for col in columns])
            url = '%s/attributegroup/Common:Delete_Hatrac/RID;%s' % (self.path, columns)
            body = []
            obj = {'RID': rid,
                   "Hatrac_Deleted": True
                   }
            body.append(obj)
            headers = {'Content-Type': 'application/json'}
            resp = self.send_request('PUT', url, json.dumps(body), headers, False)
            resp.read()
            self.logger.debug('SUCCEEDED updated the Common:Delete_Hatrac table entry for the Hatrac URL: "%s".' % (hatrac_uri)) 
        
    """
    Upload videos to YouTube
    """
    def uploadVideo(self):
        url = '%s/entity/Immunofluorescence:Slide_Video/!Identifier::null::&!Name::null::&!Bytes::null::&Media_Type=video%%2Fmp4&Processing_Status::null::' % (self.path)
        headers = {'Content-Type': 'application/json', 'Accept': 'application/json'}
        resp = self.send_request('GET', url, '', headers, False)
        videos = json.loads(resp.read())
        videoids = []
        for video in videos:
            videoids.append((video['Accession_ID'], video['Name'], video['Title'], video['Description'], video['Identifier'], video['MD5'], video['YouTube_MD5'], video['YouTube_URI'], video['RID']))
                
        self.logger.debug('Processing %d video(s).' % (len(videoids))) 
        for movieId,fileName,title,description,uri,md5,youtube_md5,youtube_uri,rid in videoids:
            if description == None:
                description = ''
            f = self.getVideoFile(fileName, uri)
            if f == None:
                self.reportFailure(movieId, 'error')
                continue
                
            if youtube_uri != None:
                """
                We have an update.
                Delete the video from YouTube
                """
                client_delete = ClientDeleteYouTubeVideo(client_secrets_file=self.client_secrets_file,
                                                         client_oauth2_file=self.client_oauth2_file,
                                                         logger=self.logger)
                youtube_deleted = client_delete.youtube_delete(youtube_uri)
                """
                We have an update.
                Insert the old video into the Delete_Youtube table
                """
                self.logger.debug('Inserting the old video "%s" file into the Delete_Youtube table.' % (fileName))
                url = '%s/entity/Common:Delete_Youtube' % (self.path)
                body = []
                obj = {'YouTube_MD5': youtube_md5,
                       'YouTube_URI': youtube_uri,
                       'Youtube_Deleted': youtube_deleted,
                       'Record_Type': 'Immunofluorescence:Slide_Video',
                       'Record_RID': rid,
                       'Record_Deleted': False
                       }
                body.append(obj)
                headers = {'Content-Type': 'application/json'}
                resp = self.send_request('PUT', url, json.dumps(body), headers, False)
                resp.read()
                self.logger.debug('SUCCEEDED updated the entry for the "%s" file in the Common:Delete_Youtube table.' % (fileName)) 
            
            self.logger.debug('Uploading the video "%s" to YouTube' % (fileName))
            
            """
            Initialize YouTube video parameters
            """
            self.args.file = f
            self.args.title = ('gudmap.org:\n%s' % title)[:64]
            self.args.description = description
            
            """
            Upload video to YouTube
            """
            try:
                request = self.youtube_request()
                if request is not None:
                    id = self.youtube_upload(request)
                    returncode = 0
                else:
                    returncode = 1
            except:
                et, ev, tb = sys.exc_info()
                self.logger.error('got unexpected exception "%s"' % str(ev))
                self.logger.error('%s' % str(traceback.format_exception(et, ev, tb)))
                self.sendMail('FAILURE Video: YouTube ERROR', '%s\n' % str(traceback.format_exception(et, ev, tb)))
                returncode = 1
            
            if returncode != 0:
                self.logger.error('Can not upload to YouTube the "%s" file.' % (fileName)) 
                self.sendMail('FAILURE YouTube', 'Can not upload to YouTube the "%s" file.' % (fileName))
                os.remove(f)
                """
                Update the Slide_Video table with the failure result.
                """
                self.reportFailure(movieId, 'error')
                continue
                
            """
            Upload the Slide_Video table with the SUCCESS status
            """
            columns = ["YouTube_MD5", "YouTube_URI", "Processing_Status"]
            #youtube_uri = "https://www.youtube.com/embed/%s?showinfo=0&rel=0" % id
            youtube_uri = "https://www.youtube.com/embed/%s?rel=0" % id
            os.remove(f)
            columns = ','.join([urllib.quote(col, safe='') for col in columns])
            url = '%s/attributegroup/Immunofluorescence:Slide_Video/Accession_ID;%s' % (self.path, columns)
            body = []
            obj = {'Accession_ID': movieId,
                   'YouTube_URI': youtube_uri,
                   'YouTube_MD5': md5,
                   "Processing_Status": 'success'
                   }
            body.append(obj)
            headers = {'Content-Type': 'application/json'}
            resp = self.send_request('PUT', url, json.dumps(body), headers, False)
            resp.read()
            self.logger.debug('SUCCEEDED updated the entry for the "%s" file.' % (fileName)) 
        self.logger.debug('Ended uploading videos to YouTube.') 
        
    """
    Update the Slide_Video table with the ERROR status
    """
    def reportFailure(self, movieId, error_message):
        """
            Update the Slide_Video table with the YouTube Upload failure result.
        """
        try:
            columns = ["Processing_Status"]
            columns = ','.join([urllib.quote(col, safe='') for col in columns])
            url = '%s/attributegroup/Immunofluorescence:Slide_Video/Accession_ID;%s' % (self.path, columns)
            body = []
            obj = {'Accession_ID': movieId,
                   "Processing_Status": '%s' % error_message
                   }
            body.append(obj)
            headers = {'Content-Type': 'application/json'}
            resp = self.send_request('PUT', url, json.dumps(body), headers, False)
            resp.read()
            self.logger.debug('SUCCEEDED updated the Slide_Video table for the video Accession_ID "%s"  with the Processing_Status result "%s".' % (movieId, error_message)) 
        except:
            et, ev, tb = sys.exc_info()
            self.logger.error('got unexpected exception "%s"' % str(ev))
            self.logger.error('%s' % str(traceback.format_exception(et, ev, tb)))
            self.sendMail('FAILURE Video: reportFailure ERROR', '%s\n' % str(traceback.format_exception(et, ev, tb)))
            
        
    """
    Get the video file from hatrac
    """
    def getVideoFile(self, fileName, uri):
        try:
            self.logger.debug('Processing file: "%s".' % (fileName)) 
            movieFile = '%s/%s' % (self.data_scratch, fileName)
            url = '%s' % (uri)
            headers = {'Accept': '*'}
            resp = self.send_request('GET', url, '', headers, False)
            self.logger.debug('content-length: %s.' % (resp.getheader('content-length'))) 
            #self.logger.debug('response headers: %s.' % (resp.getheaders())) 
            block_sz = 8192
            f = open(movieFile, 'wb')
            while True:
                buffer = resp.read(block_sz)
                if not buffer:
                    break
                f.write(buffer)
            f.close()
            self.logger.debug('File "%s", %d bytes.' % (movieFile, os.stat(movieFile).st_size)) 
            return movieFile
        except:
            et, ev, tb = sys.exc_info()
            self.logger.error('Can not get from hatrac the video file "%s"\n"%s"' % (fileName, str(ev)))
            self.logger.error('%s' % str(traceback.format_exception(et, ev, tb)))
            self.sendMail('FAILURE YouTube: getVideoFile ERROR', '%s\n' % str(traceback.format_exception(et, ev, tb)))
            return None

    """
    Append the cid=video string to the url query
    """
    def url_cid(self, url):
        """
        """
        ret = url
        o = urlparse.urlparse(url)
        if o.path.startswith('/ermrest/'):
            delimiter = '?'
            try:
                o = urlparse.urlparse(url)
                if o.query != '':
                    delimiter = '&'
                ret = '%s%scid=youtube' % (url, delimiter)
            except:
                pass
        return ret

        
