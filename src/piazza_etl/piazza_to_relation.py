# Copyright (c) 2014, Stanford University
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without modification, are permitted provided that the following conditions are met:
# 1. Redistributions of source code must retain the above copyright notice, this list of conditions and the following disclaimer.
# 2. Redistributions in binary form must reproduce the above copyright notice, this list of conditions and the following disclaimer in the documentation and/or other materials provided with the distribution.
# 3. Neither the name of the copyright holder nor the names of its contributors may be used to endorse or promote products derived from this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

'''
Created on May 23, 2014

@author: paepcke

This class imports a Piazza dump, and then rebuilds
tables EdxPiazza.contents, and EdxPiazza.users. Piazza
is a company offering forum services. Some instructors
who use OpenEdX prefer that forum to the one built into
the platform.

A Piazza dump contains two files: class_content.json (post contents),
and users.json with info about individual users. 

PiazzaImporter is a singleton. It imports both the posts
and the users. Users turn into PiazzaUser instances, while
posts turn into PiazzaPost instances. References among instances
are constructed to mirror the relationships among JSON structures. 

Once created, PiazzaImporter class acts like a list of posts. The
first post pulled from the class_content.json file is PiazzaPost
object PiazzaImporter[0], and so on.  

Example JSON element within a Piazza class_content.json file. All
elements are held within one JSON array:

[
{
  "id": "h9wrqvexznt1zw",
  "type": "note",
  "children": [
    
  ],
  "nr": 0,
  "unique_views": 222,
  "tag_good_arr": [
    "hqyjmaplhAK"
  ],
  "history": [
    {
      "anon": "no",
      "uid": "gd6v7134AUa",
      "content": "\nPiazza is a Q&A platform designed to ...",
      "subject": "Welcome to Piazza!",
      "created": "2012-11-24T13:22:31Z"
    }
  ],
  "config": {
    "is_default": 1
  },
  "folders": [
    
  ],
  "no_answer_followup": 0,
  "tag_good": [
    {
      "id": "hqyjmaplhAK",
      "photo": null,
      "us": false,
      "facebook_id": null,
      "name": "Sergio Vogtschmidt",
      "admin": false,
      "role": "student",
      "email": "sergiushenrybatero@hotmail.com"
    }
  ],
  "change_log": [
    {
      "type": "create",
      "anon": "no",
      "uid": "gd6v7134AUa",
      "data": "h9wrqvez2qv1zx",
      "when": "2012-11-24T13:22:31Z"
    }
  ],
  "status": "active",
  "tags": [
    "student"
  ],
  "created": "2012-11-24T13:22:31Z"
}
]

Along with the above contents comes a file users.json. It
contains mappings from Piazza's internal uids to LTI names.
The records are in a JSON array:
Example entry:

[
{
    "email": "myemail@gmail.com",
    "asks": 0,
    "lti_ids": [
        "stanford.edu__aff1b14edf5054292a31e584b4749f42"
    ],
    "user_id": "hr7xjaytsC8",
    "views": 1,
    "days": 1,
    "name": "John Doe",
    "answers": 0,
    "posts": 0
 } ...
]  

Some leniency in column names:
    # Some columns mean the same in the OpenEdX
    # forum and the Piazza forum. Allow use of
    # both names here. Key: alternate name,
    # value: Piazza-native name: 
    SCHEMA_NAME_EQUIVALENCES = {'body' : 'content',
                                'creation_date' : 'create_date',
                                'created_at' : 'create_date', 
                                'good_tags' : 'tag_good_arr',
                                'endorse_tags' : 'tag_endorse_arr'
                                }

#******  ???
Endorsement:
   anon_screen_name, endorsement_type, endorsement_post

'''

#from UserDict import DictMixin
import argparse
import base64
import copy
import getpass
import hashlib
import json
import logging
import os
import sys
import zipfile

from pymysql_utils.pymysql_utils import MySQLDB


class PiazzaImporterMetaclass(type):
    
    def __init__(self, className, bases, namespace):

        super(PiazzaImporterMetaclass, self).__init__(className, bases, namespace)
        if not hasattr(self, 'piazzaImporterInstance'):
            self.singletonPiazzaImporter = None
    
    def __call__(self, *args, **kwdargs):

        if self.singletonPiazzaImporter is not None:
            return self.singletonPiazzaImporter
                # Call the PiazzaPost class' init method:

        # Call PiazzaImporter's __init__() method:
        newPiazzaImporterObj = super(PiazzaImporterMetaclass, self).__call__(*args, **kwdargs)
        self.singletonPiazzaImporter = newPiazzaImporterObj
        return newPiazzaImporterObj


class PiazzaImporter(object):
    '''
    classdocs
    '''
    __metaclass__ = PiazzaImporterMetaclass
    
    STANDARD_CONTENT_FILE_NAME = 'class_content.json'
    STANDARD_USERS_FILE_NAME   = 'users.json'    
    STANDARD_MAPPING_FILE_NAME = 'account_mapping.csv'
    
    # How many rows to skip at start of mapping file
    # (skip past header):
    MAPPING_FILE_ROW_SKIPS     = 1
    
    MYSQL_PIAZZA_DB = 'Edx_Piazza'
    
    # Db in MySQL that holds function
    # idExt2Anon()
    CONVERT_FUNCTIONS_DB = 'Edx'
    
    # Dict to hold map between Piazza 'id' field, and user_int_id:
    piazza2UserIntId = {}
    
    # Dict to retrieve a PiazzaUser object by the user's Piazza
    # company internal ID (they look like: 'hc19qkoyc9C'
    usersByPiazzaId  = {}

    logger = None
  
    def __init__(self, 
                 mysqlUser, 
                 mysqlPwd, 
                 dbname, 
                 tablename, 
                 jsonFileName,
                 usersFileName=None, 
                 loggingLevel=logging.INFO, 
                 logFile=None,
                 unittesting=False):
        '''
        Create an instance that will hold a dict between
        Piazza IDs and anon_screen_name ids:
        
        :param mysqlUser: MySQL UID to use for looking up mapping between external (LTI) IDs and anon_screen_names
        :type mysqlUser: String
        :param mysqlPwd: MySQL PWD to use for user mysqlUser
        :type mysqlPwd: String
        :param dbname: name of MySQL database to which the forum content tables will be written.
        :type dbname: String
        :param tablename: name of table within dbname that will hold the forum contents
        :type tablename: String
        :param jsonFileName: name of JSON formatted file that holds the forum contents. If jsonFileName
            is a zip file, then that zip archive must contain a file named STANDARD_CONTENT_FILE_NAME (a class variable)
        :type jsonFileName: String
        :param usersFileName: name of file containing Piazza's users.json JSON 
        :type usersFileName: String
        :param loggingLevel: detail of logging to do; default is INFO
        :type loggingLevel: logging
        :param logFile: file to send log into. If None: log to console
        :type String 
        '''
        
        self.mysqlUser = mysqlUser
        self.mysqlPwd = mysqlPwd
        self.dbname = dbname
        self.tablename = tablename
        self.jsonFileName = jsonFileName
        self.usersFile = usersFileName
              
        self.setupLogging(loggingLevel, logFile)
        
        if unittesting:
            # If unittesting is True, then testPiazzaToRelation.py
            # wants to call individual methods, so don't do any more init:
            return
        
        # Import JSON from Piazza content file, 
        if zipfile.is_zipfile(jsonFileName):
            # Grab and import content JSON file from zip archive:
            self.importJsonContentFromPiazzaZip(jsonFileName)
        else:
            # Caller did not provide a zip file from Piazza, but
            # a separate JSON file with the forum content:
            with open(jsonFileName, 'r') as jsonFd:
                self.jData = json.load(jsonFd)

            # Load user info:
            self.importJsonUsersFromPiazzaZip(usersFileName)

    def importJsonContentFromPiazzaZip(self, zipContentFileName):
        '''
        Given a JSON file with all of one class' Piazza forum data,
        import the JSON, creating a messy in-memory data structure
        called jData that is made sense of by making PiazzaImporter
        act as a list of content posts. Whenever an element in that
        list is accessed, it is materialized into a PiazzaPost instance.
        The given file may be a stand-alone JSON file, or it can be 
        inside a zip file, of which zipContentFileName is the name. In that
        case the JSON within the zip file must be named class_contents.json.
        
        :param zipContentFileName: name of file, or zip file with JSON encoded Piazz forum content
        :type zipFileName: String
        '''

        contentFd = None
        try:
            if zipfile.is_zipfile(zipContentFileName):
                zipObj = zipfile.ZipFile(zipContentFileName)
                contentFd = zipObj.open(PiazzaImporter.STANDARD_CONTENT_FILE_NAME)
                fileNameList = zipObj.namelist()
                if not PiazzaImporter.STANDARD_CONTENT_FILE_NAME in fileNameList:
                    raise ValueError('Zip file %s does not contain a file %s.' % (zipContentFileName, PiazzaImporter.STANDARD_CONTENT_FILE_NAME))
            else:
                contentFd = open(zipContentFileName, 'r')

            jsonArr = contentFd.readlines()
            jsonStr = ''.join([line.strip() for line in jsonArr])
            self.jData = json.loads(jsonStr)
            #print('Read')
        finally:
            if contentFd is not None:
                contentFd.close()
    
    def importJsonUsersFromPiazzaZip(self, zipUserFileName):
        '''
        Given a JSON file with user info, as distributed in Piazza's user.json,
        import the JSON, creating a memory structure of PiazzaUser
        instances. The structure will be a dict mapping Piazza uids
        to the corresponding PiazzaUser object.
        
        Every PiazzaUser instance itself acts as a dict, with the 
        user properties ('answers', 'question', etc.) as keys. Two
        properties are added for each PiazzaUser: anon_screen_name,
        which is initialized with the string 'anon_screen_name_redacted'.
        The second property, user_int_id is initialized with the 
        integer uids that are generated by the OpenEdX platform.
        
        The given JSON file may be a stand-alone JSON file, or it can be 
        inside a zip file, of which zipUserFileName is the name. In that
        case the JSON within the zip file must be named users.json.
        
        A dict: user info keyed on Piazza ID.
        
        :param zipUserFileName: name of file, or zip file with JSON encoded Piazza forum users
        :type zipFileName: String
        '''

        usersFd = None
        try:
            if zipfile.is_zipfile(zipUserFileName):
                zipObj = zipfile.ZipFile(zipUserFileName)
                usersFd = zipObj.open(PiazzaImporter.STANDARD_USERS_FILE_NAME)
                fileNameList = zipObj.namelist()
                if not PiazzaImporter.STANDARD_USERS_FILE_NAME in fileNameList:
                    raise ValueError('Zip file %s does not contain a file %s.' % (zipUserFileName, PiazzaImporter.STANDARD_USERS_FILE_NAME))
            else:
                usersFd = open(zipUserFileName, 'r')

            jsonArr = usersFd.readlines()
            jsonStr = ''.join([line.strip() for line in jsonArr])
            userInfoArray = json.loads(jsonStr)
            PiazzaImporter.usersByPiazzaId  = {}
            PiazzaImporter.usersByTrueUserName  = {}
            for userJsonStruct in userInfoArray:
                # Maintain a map of user's real name to its 
                # Piazza uid:
                try:
                    thePiazzaId = userJsonStruct['user_id']
                    theUserRealName = userJsonStruct['name']
                    PiazzaImporter.usersByTrueUserName[theUserRealName] = thePiazzaId
                except KeyError:
                    # If either 'user_id' or name' wasn't in the userJsonStruct,
                    # no entry are entering into PiazzaImporter.usersByTrueUserName
                    pass
                # Build a PiazzaUser instance:
                userObj = PiazzaUser(userJsonStruct)
                # Add the new PiazzaUser instance to the 
                # class variable usersByPiazzaId dict:
                PiazzaImporter.usersByPiazzaId[userObj['piazza_id']] = userObj
                 
            #print('Read')
        finally:
            if usersFd is not None:
                usersFd.close()
    
        # Now, try to give each user object a proper user_int_id,
        # which are used by the OpenEdx platform. Also, add key/value
        # pair anon_screen_name--->'anon_screen_name_redacted'
        try:
            db = None
            db = MySQLDB(user=self.mysqlUser, passwd=self.mysqlPwd,  db=PiazzaImporter.CONVERT_FUNCTIONS_DB)
        except Exception as e:
            raise(IOError('Could not open MySQL db for user %s to resolve LTI uids to user_int_ids: %s' % (self.mysqlUser, `e`)))
        
        for (userObj, lti) in [(userObj, userObj.get('ext_id')) for userObj in PiazzaImporter.usersByPiazzaId.values()]:
            queryIt = db.query("SELECT %s.idAnon2Int(idExt2Anon('%s'));" % (PiazzaImporter.CONVERT_FUNCTIONS_DB,lti))
            # Results come back as tuples, so the LTI will be as in (211516L,):
            userObj['user_int_id'] = queryIt.next()[0]
            # If no mapping from LTI to integer exists, set user_int_id to -1:
            if userObj['user_int_id'] is None:
                userObj['user_int_id'] = -1

            # Help quickly find a user_int_id from a Piazza id:
            PiazzaImporter.piazza2UserIntId[userObj['piazza_id']] = userObj['user_int_id']
        

        db.close()

    # ----------------------------------------  Getters ------------------------------------------
    
    def getChildArr(self, piazzaPostObj):
        '''
        Return an array that contains the 'children' field internalized
        JSON structures of the given JSON object structure.
        
        :param piazzaPostObj:
        :type piazzaPostObj:
        :return array of user_int_id that point to child objects. Any -1
            entries occur when a child object did not have an associated
            human associated with it.
        '''
        return piazzaPostObj.get('children', None)
                
    
    def getPosterUidAnon(self, oidOrDict):
        '''
        Return a anon_screen_name type UID, given
        either a PiazzaPost OID, or a dict that
        represents a raw JSON object. 
        
        If parameter is an OID, simply return
        that object's anon_screen_name attribute.
        Else look for field name 'id', get its 
        (Piazza UID type) value, and look up the
        equivalent anon_screen_name. If that mapping
        is unknown, return None. 
        
        :param oidOrDict: a PiazzaPost object id, or a JSON object dict
        :type oidOrDict: {PiazzaPost | dict}
        '''
        
        if type(oidOrDict) == basestring:
            try:
                oid = oidOrDict
                return PiazzaImporter[oid]['anon_screen_name']
            except KeyError:
                raise ValueError('Value %s is not a PiazzaPost instance identifier.' % oidOrDict)

        # Parameter is a dict, i.e. a JSON object.
        # Get its (Piazza) 'id' attribute, and look
        # up the equivalent anon_screen_name: 
        try:
            theDict = oidOrDict
            return self.piazza2UserIntId.get(theDict.get('id',None))
        except KeyError:
            return None
          
    def getSubject(self, oidOrDictOrPiazzaPostObj):

        if isinstance(oidOrDictOrPiazzaPostObj, basestring):
            oid = oidOrDictOrPiazzaPostObj
            try:
                piazzaObj = PiazzaImporter.singletonPiazzaImporter[oid]
            except KeyError:
                raise KeyError("No PiazzaPost object with OID %s is known." % piazzaObj)
            
        elif type(oidOrDictOrPiazzaPostObj) == dict or isinstance(oidOrDictOrPiazzaPostObj, PiazzaPost): 
            piazzaObj = oidOrDictOrPiazzaPostObj
            
        try:
            historyArr = piazzaObj['history']
        except (KeyError):
            # Does the object itself have a 'subject' key?:
            try:
                return(piazzaObj['subject'])
            except KeyError:
                raise KeyError("Dict parameter (%s) contains no 'history' array." % str(piazzaObj))
        try:
            # Return the last (i.e. oldest) entry in the history:
            return historyArr[-1]['subject']
        except KeyError:
            raise ValueError("History array's first dict element contains no 'subject' entry (%s)." % str(historyArr))
        except IndexError:
            raise ValueError("Dict parameter 'history' in an empty array (%s)." % str(piazzaObj))
            

    def getContent(self, oidOrDictOrPiazzaPostObj):

        if isinstance(oidOrDictOrPiazzaPostObj, basestring):
            oid = oidOrDictOrPiazzaPostObj
            try:
                piazzaObj = PiazzaImporter.singletonPiazzaImporter[oid]
            except KeyError:
                raise KeyError("No PiazzaPost object with OID %s is known." % piazzaObj)
            
        elif type(oidOrDictOrPiazzaPostObj) == dict or isinstance(oidOrDictOrPiazzaPostObj, PiazzaPost): 
            piazzaObj = oidOrDictOrPiazzaPostObj
            
        historyArr = piazzaObj['history']
        # Maybe this the object whose content we are
        # to get is itself one of the history objects:
        if historyArr is None:
                return(piazzaObj.getRaw('content'))
        try:
            resContentArr = []
            for historyObj in historyArr:
                resContentArr.append(historyObj.getRaw('content'))
            return(resContentArr)
        except KeyError:
            resContentArr.append(None)
            

    #*********** Continue testing here


#     def getTags(self, jsonObjArrOrObj, arrIndex=0):
#         '''
#         Return post's tags as a Python array of strings.
#         
#         :param arrIndex:
#         :type arrIndex:
#         '''
#         if jsonObjArrOrObj is None:
#             jsonObjArrOrObj = self.jData
#         if not type(jsonObjArrOrObj) == list:
#             jsonObjArrOrObj = [jsonObjArrOrObj]
#         try:
#             return jsonObjArrOrObj[arrIndex].get('tags', None)
#         except IndexError:
#             return None
#     
#     def getPiazzaId(self, jsonObjArrOrObj, arrIndex=0):
#         if jsonObjArrOrObj is None:
#             jsonObjArrOrObj = self.jData
#         if not type(jsonObjArrOrObj) == list:
#             jsonObjArrOrObj = [jsonObjArrOrObj]
#         try:
#             return jsonObjArrOrObj[arrIndex].get('id', None)
#         except IndexError:
#             return None
#           
#     def getStatus(self, jsonObjArrOrObj, arrIndex=0):
#         if jsonObjArrOrObj is None:
#             jsonObjArrOrObj = self.jData
#         if not type(jsonObjArrOrObj) == list:
#             jsonObjArrOrObj = [jsonObjArrOrObj]
#         try:
#             return jsonObjArrOrObj[arrIndex].get('status', None)
#         except IndexError:
#             return None
#     
#     def getNoAnswerFollowup(self, jsonObjArrOrObj, arrIndex=0):
#         if jsonObjArrOrObj is None:
#             jsonObjArrOrObj = self.jData
#         if not type(jsonObjArrOrObj) == list:
#             jsonObjArrOrObj = [jsonObjArrOrObj]
#         try:
#             return jsonObjArrOrObj[arrIndex].get('no_answer_followup', None)
#         except IndexError:
#             return None       
#     
#     def getCreationDate(self, jsonObjArrOrObj, arrIndex=0):
#         if jsonObjArrOrObj is None:
#             jsonObjArrOrObj = self.jData
#         if not type(jsonObjArrOrObj) == list:
#             jsonObjArrOrObj = [jsonObjArrOrObj]
#         try:
#             return jsonObjArrOrObj[arrIndex].get('created', None)
#         except IndexError:
#             return None
#     
#     def getPostType(self, jsonObjArrOrObj, arrIndex=0):
#         if jsonObjArrOrObj is None:
#             jsonObjArrOrObj = self.jData
#         if not type(jsonObjArrOrObj) == list:
#             jsonObjArrOrObj = [jsonObjArrOrObj]
#         try:
#             return jsonObjArrOrObj[arrIndex].get('type', None)
#         except IndexError:
#             return None
    
    def getTagGoodAnons(self, oidOrDictOrPiazzaPostObj):
        if isinstance(oidOrDictOrPiazzaPostObj, basestring):
            oid = oidOrDictOrPiazzaPostObj
            try:
                piazzaObj = PiazzaImporter.singletonPiazzaImporter[oid]
            except KeyError:
                raise KeyError("No PiazzaPost object with OID %s is known." % piazzaObj)
            
        elif type(oidOrDictOrPiazzaPostObj) == dict or isinstance(oidOrDictOrPiazzaPostObj, PiazzaPost): 
            piazzaObj = oidOrDictOrPiazzaPostObj

        anons = []

        for piazzaId in piazzaObj.get('tag_good_arr', None):
            if piazzaId is None:
                continue
            anons.append(self.idPiazza2UserIntId(piazzaId))

        return anons
            
    
    def getTagEndorseAnons(self, oidOrDictOrPiazzaPostObj):
        if isinstance(oidOrDictOrPiazzaPostObj, basestring):
            oid = oidOrDictOrPiazzaPostObj
            try:
                piazzaObj = PiazzaImporter.singletonPiazzaImporter[oid]
            except KeyError:
                raise KeyError("No PiazzaPost object with OID %s is known." % piazzaObj)
            
        elif type(oidOrDictOrPiazzaPostObj) == dict or isinstance(oidOrDictOrPiazzaPostObj, PiazzaPost): 
            piazzaObj = oidOrDictOrPiazzaPostObj

        anons = []

        for piazzaId in piazzaObj.get('tag_endorse_arr', None):
            if piazzaId is None:
                continue
            anons.append(self.idPiazza2UserIntId(piazzaId))

        return anons
            
    
#     def getNumUpVotes(self, jsonObjArrOrObj, arrIndex=0):
#         if jsonObjArrOrObj is None:
#             jsonObjArrOrObj = self.jData
#         if not type(jsonObjArrOrObj) == list:
#             jsonObjArrOrObj = [jsonObjArrOrObj]
#         try:
#             return jsonObjArrOrObj[arrIndex].get('no_upvotes', None)
#         except IndexError:
#             return None
#             
#     def getNumAnswers(self, jsonObjArrOrObj, arrIndex=0):
#         if jsonObjArrOrObj is None:
#             jsonObjArrOrObj = self.jData
#         try:
#             return jsonObjArrOrObj[arrIndex].get('no_answer', None)
#         except IndexError:
#             return None        
#     
#     def getIsAnonPost(self, jsonObjArrOrObj, arrIndex=0):
#         if jsonObjArrOrObj is None:
#             jsonObjArrOrObj = self.jData
#         if not type(jsonObjArrOrObj) == list:
#             jsonObjArrOrObj = [jsonObjArrOrObj]
#         try:
#             return jsonObjArrOrObj[arrIndex].get('anon', 'no')
#         except IndexError:
#             return None
#             
#     def getBucketName(self, jsonObjArrOrObj, arrIndex=0):
#         if jsonObjArrOrObj is None:
#             jsonObjArrOrObj = self.jData
#         if not type(jsonObjArrOrObj) == list:
#             jsonObjArrOrObj = [jsonObjArrOrObj]
#         try:
#             return jsonObjArrOrObj[arrIndex].get('bucket_name', None)
#         except IndexError:
#             return None
#     
#     def getUpdated(self, jsonObjArrOrObj, arrIndex=0):
#         if jsonObjArrOrObj is None:
#             jsonObjArrOrObj = self.jData
#         if not type(jsonObjArrOrObj) == list:
#             jsonObjArrOrObj = [jsonObjArrOrObj]
#         try:
#             return jsonObjArrOrObj[arrIndex].get('updated', None)
#         except IndexError:
#             return None
#     
#     def getFolders(self, jsonObjArrOrObj, arrIndex=0):
#         if jsonObjArrOrObj is None:
#             jsonObjArrOrObj = self.jData
#         if not type(jsonObjArrOrObj) == list:
#             jsonObjArrOrObj = [jsonObjArrOrObj]
#         try:
#             return ','.join(jsonObjArrOrObj[arrIndex].get('folders', None))
#         except IndexError:
#             return None
    

    # ----------------------------------------  Make  PiazzaImporter Act Like a Read-Only List of Dicts ------------------------------------------
    
    # Want ability to retrieve a PiazzaPost obj from an
    # instance of PiazzaImporter by providing the OID of one of
    # the PiazzaPost objects. We provide that facility via a
    # dict behavior.
    #
    # Finally: want ability to act as iterator over a list
    # of objects
    
    # Dict functionality
    def __getitem__(self, offsetOrObjId):
        '''
        Given a list index or a JSON object dictionary,
        or a PiazzaPost oid, return a PiazzaPost instance
        if possible. List index identifies one of the 
        top level JSON dicts in the JSON array. 
        
        :param offsetOrObjId: list index, JSON dict, or oid that describe desired PiazzaPost instance
        :type offsetOrObjId: {int | dict | String}
        :return desired PiazzaPost instance, if found
        :rtype PiazzaPos
        :raise KeyError
        '''
        
        if type(offsetOrObjId) == int:
            # Behave like a list.
            # offsetOrObjId is an offset into the original JSON list:
            objDict = list.__getitem__(self.jData, offsetOrObjId)
            return self.findOrCreatePostObj(objDict)
        
        elif type(offsetOrObjId) == dict:
            # Behave like a PiazzaPost instance factory:
            return self.findOrCreatePostObj(offsetOrObjId)
        
        else: # offsetOrObjId is the obj ID of an already materialized obj
            # Behave like a dict: return the obj with that ID,
            # or raise KeyError:
            return PiazzaPost.getPiazzaPostObj(offsetOrObjId)
    
    def __len__(self):
        return len(self.usersByPiazzaId)
    
    # The iterator functionality:
    class PiazzaImporterIterator(object):
        def __init__(self, piazzaImporterObj):
            self.piazzaImporterObj = piazzaImporterObj
            self.currObjIndex = -1
            
        def next(self):
            self.currObjIndex += 1
            try:
                return self.piazzaImporterObj[self.currObjIndex]
            except IndexError:
                raise StopIteration
            
    def  __iter__(self):
        return PiazzaImporter.PiazzaImporterIterator(self)

    def findOrCreatePostObj(self, jsonDict):
        
        # Find existing instance for this JSON obj (dict),
        # or have a new one made:

        oid = PiazzaImporter.makeHashFromJsonDict(jsonDict)

        # Try to find this OID among the already
        # created instances: caller may make multiple
        # PiazzaPost instantiations with the same JSON
        # object; we'll just find the respective object
        # and return it:
        try:
            return PiazzaPost.piazzaPostInstances[oid]
        except KeyError:
            pass

        piazzaPostObj = PiazzaPost(jsonDict)

        return piazzaPostObj
        
      
    # ----------------------------------------  Utilities ------------------------------------------

    @classmethod
    def makeHashFromJsonDict(cls, jsonDict):
        return base64.urlsafe_b64encode(hashlib.md5(str(jsonDict)).digest())
    
    @classmethod
    def idPiazza2UserIntId(cls, piazzaId):
        try:
            return cls.piazza2UserIntId[piazzaId]
        except KeyError:
            return -1
            

    def setupLogging(self, loggingLevel, logFile):
        '''
        Set up the standard Python logger. 
        TODO: have the logger add the script name as Sef's original
        @param loggingLevel:
        @type loggingLevel:
        @param logFile:
        @type logFile:
        '''
        # Set up logging:
        #self.logger = logging.getLogger('pullTackLogs')
        PiazzaImporter.logger = logging.getLogger(os.path.basename(__file__))

        # Create file handler if requested:
        if logFile is not None:
            handler = logging.FileHandler(logFile)
            print('Logging of control flow will go to %s' % logFile)            
        else:
            # Create console handler:
            handler = logging.StreamHandler()
        handler.setLevel(loggingLevel)

        # Create formatter
        formatter = logging.Formatter("%(name)s: %(asctime)s;%(levelname)s: %(message)s")       
        handler.setFormatter(formatter)
        
        # Add the handler to the logger
        PiazzaImporter.logger.addHandler(handler)
        PiazzaImporter.logger.setLevel(loggingLevel)
         
    @classmethod
    def logDebug(cls, msg):
        PiazzaImporter.logger.debug(msg)

    @classmethod
    def logWarn(cls, msg):
        PiazzaImporter.logger.warn(msg)

    @classmethod
    def logInfo(cls, msg):
        PiazzaImporter.logger.info(msg)

    @classmethod
    def logErr(cls, msg):
        PiazzaImporter.logger.error(msg)
            
class PiazzaPostMetaclass(type):
    '''
    Metaclass that governs creation of PiazzaPost instances.
    Imposes a singleton pattern, with existing objects held
    in a class level dict called piazzaPostInstances. Keys
    are OIDs, which are computed from the JSON dict that is
    passed into the __call__() method. 
    '''
    
    def __init__(self, className, bases, namespace):
        super(PiazzaPostMetaclass, self).__init__(className, bases, dict)
        if not hasattr(self, 'piazzePostInstances'):
            self.piazzaPostInstances = {}

    def __call__(self, objIdOrObjOrJsonDict, buildingChangeEventObj=False, buildingHistoryEventObj=False):
        '''
        Invoked whenever a PiazzaPost instance is created.
        Checks whether object with given OID or JSON object
        already exists; if so, returns it. If caller passed
        in an already extant PiazzaPost instance, just return it. 
        Else creates instance,
        initializes its jsonDict instance variable, and
        adds oid and anon_screen_name as separate instance 
        variables.
        
        :param objIdOrObjOrJsonDict: either an oid, or a JSON structure
            or an already existing PiazzaPost instance from the Piazza forum contents file.
        :type anonScreenNameOrJsonDict: String
        '''
        # For readability: figure out which
        # type of parm was passed in, and assign
        # to appropriate var:
        if type(objIdOrObjOrJsonDict) == dict:
            jsonDict = objIdOrObjOrJsonDict
            oid = None
        elif isinstance(objIdOrObjOrJsonDict, basestring):
            oid = objIdOrObjOrJsonDict
            jsonDict = None
        elif isinstance(objIdOrObjOrJsonDict, PiazzaPost):
            return objIdOrObjOrJsonDict
        else:
            raise ValueError("Must pass either an OID or a JSON dictionary; oid was None, jsonDict was %s" % str(objIdOrObjOrJsonDict))
        
        # If caller provided an oid, try to find it.
        # NameError if doesn't exist:
        if oid is not None:
            try:
                return self.piazzaPostInstances[oid]
            except KeyError:
                raise NameError("Object with oid '%s' does not exist." % oid)
        
        # No OID provided. Compute OID from the JSON dict;
        oid = PiazzaImporter.makeHashFromJsonDict(jsonDict)

        # Try to find this OID among the already
        # created instances: caller may make multiple
        # PiazzaPost instantiations with the same JSON
        # object; we'll just find the respective object
        # and return it:
        try:
            return self.piazzaPostInstances[oid]
        except KeyError:
            pass

        # Really don't have this instance yet:
        
        # Call the PiazzaPost class' init method:
        resObj = super(PiazzaPostMetaclass, self).__call__(jsonDict, buildingChangeEventObj=buildingChangeEventObj)

        # The JSON dict will become an instance level
        # variable called nameValueDict. Add OID

        resObj['oid'] = oid
        
        # Remember this object by oid at the
        # class level (i.e. in a class var):
        self.piazzaPostInstances[oid] = resObj

        return resObj

    def __str__(self):
        return self.__name__
    
    def __len__(self):
        return len(self.piazzaPostInstances)
    
class PiazzaPost(object):    
    '''
    Wraps one dict that represents a Piazza Post
    cashes objects.
    '''
    
    # Make PiazzaPost instantiation work as
    # defined in PiazzaPostMetaclass. I.e.
    # return object if already exists. Only
    # otherwise create a new one (Singleton pattern).
    # Also: compute and initialize oid, store it in
    # instance level new object's JSON dict.
    # It is named nameValueDict, and is an instance
    # level member.
    # Init anon_screen_name in the JSON dict.
    __metaclass__ = PiazzaPostMetaclass

    
    def __init__(self, jsonDict, buildingChangeEventObj=False, buildingHistoryEventObj=False):
        '''
        Note: because PiazzaPostMetaclass is this class'
        metaclass, instantiation of PiazzaPost will 
        call the metaclass' __class__() method, which
        will in turn call this __init__() method. 
        So to add any additional args, that __call__()
        method must also take those args and do the 
        call to this __init__() method with the args. 
        '''
        self.nameValueDict = copy.deepcopy(jsonDict)
        
        # Add anon_screen_name to this instance's attribute:
        # Three cases: if we are building from a main content post
        # JSON struct, the Piazza ID is in field 'id'.
        # But if we are building from an entry in a change_log
        # field of a JSON post struct, then the Piazza ID
        # may be in one of several fields instead, or may be 
        # absent. Similarly, if we are building a history event
        # object, the Piazza ID is in 'uid': 
        
        if buildingChangeEventObj:
            piazzaId = self.getPiazzaIdFromChangeEvent(jsonDict)
        elif buildingHistoryEventObj:
            piazzaId = self.getPiazzaIdFromHistoryEvent(jsonDict)
        else:
            piazzaId = jsonDict.get('id', None)

        # Find the anon_screen_name that corresponds
        # to the Piazza id in the JSON dict:
        if piazzaId is None:
            user_int_id = -1
        else:
            user_int_id = PiazzaImporter.idPiazza2UserIntId(piazzaId)
        self['anon_screen_name'] = 'anon_screen_name_redacted' 
        self['user_int_id'] = user_int_id
        
        # Replace the Piazza 'id' field with piazza_id
        # to distinguish among all the damn uids floating
        # around:
        self['piazza_id'] = piazzaId
        
        # The following (commented) code enters anon ids
        # for each Piazza id in fields tag_good_arr and
        # tag_endorse_arr. But right now we do that conversion
        # in the __getitem__()
#         # Lists tag_good_arr and tag_endorse_arr are Piazza
#         # Ids: replace those with anon_screen_names:
#         try:
#             goodTaggers = []
#             for goodTagger in self['tag_good_arr']:
#                 goodTaggerAnon = PiazzaImporter.resolveLTIToAnon(goodTagger)
#                 goodTaggers.append(goodTaggerAnon)
#             self['tag_good_arr'] = goodTaggers
#         except KeyError:
#             # This JSON post struct doesn't have a good tagger array
#             pass
#         
#         try:
#             goodEndorsers = []
#             for goodEndorser in self['tag_endorse_arr']:
#                 goodEndorserAnon = PiazzaImporter.resolveLTIToAnon(goodEndorser)
#                 goodTaggers.append(goodEndorserAnon)
#             self['tag_endorse_arr'] = goodEndorsers
#         except KeyError:
#             # This JSON post struct doesn't have a good tagger array
#             pass
        
        # If there is a change log, turn each
        # entry into its own PiazzaPost instance.
        # Also replace name by anonymous name:
        changeLogField = self['change_log']
        if changeLogField is not None:
            # New object does have a change_log field:
            changeLogObjs = []
            for oneChangeJson in changeLogField:
                oneChangeObj = PiazzaPost(oneChangeJson, buildingChangeEventObj=True)
                changeLogObjs.append(oneChangeObj)
            self['change_log'] = changeLogObjs

        # If there is a history array, turn each
        # entry into its own PiazzaPost instance.
        # Also replace Piazza uid by anonymous name:
        historyField = self['history']
        if historyField is not None:
            # New object does have a history field, which is
            # a JSON array of history structs (subject, content, created, anon, and uid):
            historyObjs = []
            for oneHistoryJson in historyField:
                oneHistoryObj = PiazzaPost(oneHistoryJson, buildingHistoryEventObj=True)
                piazzaId = oneHistoryObj.get('uid')
                if piazzaId is not None:
                    oneHistoryObj['user_int_id'] = PiazzaImporter.idPiazza2UserIntId(piazzaId)
                historyObjs.append(oneHistoryObj)
            self['history'] = historyObjs

    def values(self):
        return self.nameValueDict.values()
    
    def items(self):
        return self.nameValueDict.items()
    
    def has_key(self, key):
        return(self.nameValueDict.has_key(key))
    
    def getPiazzaIdFromChangeEvent(self, jsonDict):
        '''
        Pull a Piazza ID out of a change log entry, if it's
        there. The id maybe be in one of several fields:
          - to
          - data
        Return None if no Piazza ID is present in the JSON obj.
        
        :param jsonDict:
        :type jsonDict:
        '''
        piazzaId = jsonDict.get('to')
        if piazzaId is None:
            piazzaId = jsonDict.get('data')
        return piazzaId
            
    
    def getPiazzaIdFromHistoryEvent(self, jsonDict):
        '''
        Pull a Piazza ID out of a history event, if it's
        there. The Piazza id there is in field 'uid'. However,
         
        Return None if no Piazza ID is present in the JSON obj.
        
        :param jsonDict:
        :type jsonDict:
        '''
        piazzaId = jsonDict.get('uid')
        return piazzaId
            
    
                
    @classmethod
    def getPiazzaPostObj(cls, oid):
        '''
        Return instance with given oid if such an
        instance exists. Else raise KeyError.
        
        :param oid: object identifier to check
        :type oid: String
        :return the previously existing PiazzaPost object
        :rtype PiazzaPost
        :raise KeyError if instance with given oid does not exist
        '''
        return PiazzaPost.piazzaPostInstances[oid]

    def __repr__(self):
        return '<PiazzaPost oid=%s>' % self.oid

    def __getitem__(self, key):
        '''
        Called when a PiazzaPost instance is treated
        like a dictionary: myPost['anon']
        
        :param key: the instance variable name
        :type key: String
        :return: the instance variable's value
        :rtype: <any>
        :raise KeyError: when given instance variable does not exist.
        '''

        # Oid is stored
        # in an instance variable (not in the 
        # JSON dict we keep in each instance:
        if key == 'oid':
            return self.oid

        # Some values are buried in the lower levels
        # of the JSON dict; pick those out, and call
        # the proper retrieval methods:         
        if key == 'subject':
            # Subjects are properies of the history objects.
            # Those ojbs are in an array within the 'history'
            # field, if it exists. Method getSubject() runs
            # a recursive descend into the history ojbs to find
            # the first subject. Eventually the history obj
            # is found, and will have a 'subject' property
            if not self.nameValueDict.has_key('subject'):
                return PiazzaImporter.singletonPiazzaImporter.getSubject(self)
        
        # Allow 'body' instead of content for compatibility
        # with OpenEdX forum:
        if key == 'content' or key == 'body':
            return PiazzaImporter.singletonPiazzaImporter.getContent(self)
        
        if key == 'tag_good_arr' or key == 'good_tags':
            return PiazzaImporter.singletonPiazzaImporter.getTagGoodAnons(self)

        if key == 'tag_endorse_arr' or key == 'endorse_tags':
            return PiazzaImporter.singletonPiazzaImporter.getTagEndorseAnons(self)

        # Allow create_date and creation_date instead of 'created':
        if key == 'create_date' or key == 'creation_date':
            key = 'created' 
        
        # Allow 'piazza_id' in place of 'id':
        if key == 'piazza_id':
            if self.nameValueDict.has_key('id'):
                key = 'id'
            elif self.nameValueDict.has_key('uid'):
                # Sometimes 'uid' fields in Piazza's json structs
                # contain a Piazza user id, other times, the value
                # of the 'uid' field is a user's real name. During
                # method importJsonUsersFromPiazzaZip() we build
                # a map from user real names to Piazza uids, try
                # that map:
                try:
                    return PiazzaImporter.usersByTrueUserName[self.nameValueDict['uid']]
                except KeyError:
                    return(self.nameValueDict.get('uid', ''))
                
        try:
            # We never fail when a property doesn't
            # exist; just return None. Client can
            # use has_key(), or keys() on a PiazzaPost object:
            jsonValue = self.nameValueDict[key]
        except KeyError:
            if key == 'children' or\
                key == 'change_log':
                return []
            else:
                return(None)
    
        if key == 'children':
            jsonValueArr = []
            for jsonValueEl in jsonValue:
                jsonValueArr.append(PiazzaImporter.singletonPiazzaImporter[jsonValueEl]) 
            return jsonValueArr
        else:
            return jsonValue
    
    def __setitem__(self, key, value):
        
        if key == 'oid':
            self.oid = value
            return
        self.nameValueDict[key] = value
    
    def __delitem__(self, key):
        if key == 'anon_screen_name' or key == 'oid':
            raise ValueError('Cannot delete anon_screen_name or oid from PiazzaPost instances')
        del self.nameValueDict[key]
    
    def keys(self):
        theKeys = self.nameValueDict.keys()
        theKeys.append('oid')
        return theKeys
    
    def get(self, key, default=None):
        # Using self.nameValueDict.get(key, default)
        # as the body of this msg would return the 
        # array of JSON that makes up the value of
        # the 'children' field in posts. To match
        # Python semantics, we instead return the
        # same as __getitem__(). To get the JSON,
        # use getRaw().
        try:
            return self.nameValueDict[key]
        except KeyError:
            return(default)

    def getRaw(self, key, default=None):
        return self.nameValueDict.get(key, default)

    def toTuple(self):
        '''
        Returns a tuple that is ready for insertion into table
        PiazzaPost. Schema:
			'anon_screen_name',
			'oid', 
			created', 
			updated', 
			type',
			anon', 
			tags', 
			unique_views',
			status', 
			folders', 
			num_upvotes', 
			num_answer',
			num_answer_followup',
			nr', 
			lk',  
			good_tags',
			endorse_tags'
			history',
			children', 
			bucket_name',
			change_log',  
			config',
			piazzaId',
			        
        '''
        

class PiazzaUserMetaclass(type):
    '''
    Metaclass that governs creation of PiazzaUser instances.
    Imposes a singleton pattern, with existing objects held
    in a class level dict called piazzaUserInstances. Keys
    are anon_screen_name strings:
    '''
    
    def __init__(self, className, bases, namespace):
        super(PiazzaUserMetaclass, self).__init__(className, bases, dict)
        if not hasattr(self, 'piazzaUserInstances'):
            self.piazzaUserInstances = {}

    def __call__(self, ltiOrJsonDict):
        '''
        Invoked whenever a PiazzaUser instance is created.
        Checks whether object with given lit or JSON object
        already exists; if so, returns it. Else creates instance,
        initializes its jsonDict instance variable, and
        adds oid as separate instance variables.
        
        :param ltiOrJsonDict: either an lti uid, or a JSON structure
            from the Piazza users.json file.
        :type ltiOrJsonDict: String
        '''
        # For readability: figure out which
        # type of parm was passed in, and assign
        # to appropriate var:
        if type(ltiOrJsonDict) == dict:
            jsonDict = ltiOrJsonDict
            lti = None
        elif isinstance(ltiOrJsonDict, basestring):
            lti = ltiOrJsonDict
            jsonDict = None
        else:
            raise ValueError("Must pass either an LTI uid or a JSON dictionary; oid was None, jsonDict was %s" % str(ltiOrJsonDict))
        
        # If caller provided an lti, try to find it.
        # NameError if doesn't exist:
        if  lti is not None:
            try:
                return self.piazzaUserInstances[lti]
            except KeyError:
                raise NameError("User object with lti '%s' does not exist." % lti)
        
        # No lti provided. Compute OID from the JSON dict;
        oid = PiazzaImporter.makeHashFromJsonDict(jsonDict)

        # Try to find this OID among the already
        # created instances: caller may make multiple
        # PiazzaPost instantiations with the same JSON
        # object; we'll just find the respective object
        # and return it:
        try:
            return self.piazzaUserInstances[oid]
        except KeyError:
            pass

        # Really don't have this instance yet:
        
        # Call the PiazzaPost class' init method:
        resObj = super(PiazzaUserMetaclass, self).__call__(jsonDict)

        # The JSON dict will become an instance level
        # variable called nameValueDict. Add OID
        # to that dict:

        resObj['oid'] = oid
        
        # Remember this object by oid at the
        # class level (i.e. in a class var):
        self.piazzaUserInstances[oid] = resObj

        return resObj

    def __str__(self):
        return self.__name__

class PiazzaUser(object):
    
    __metaclass__ = PiazzaUserMetaclass
    
    def __init__(self, jsonDict):
        '''
        Note: because PiazzaUserMetaclass is this class'
        metaclass, instantiation of PiazzaUser will 
        call the metaclass' __class__() method, which
        will in turn call this __init__() method. 
        So to add any additional args, that __call__()
        method must also take those args and do the 
        call to this __init__() method with the args. 
        
        :param jsonDict: dict holding the properties of the PiazzaUser 
                instance-to-be. 
        :type jsonDict: {String : <any>}
        
        '''
        self.nameValueDict = jsonDict
        # Ensure this Piazza json entry has a Piazza
        # uid:
        piazzaId = jsonDict.get('user_id', None)
        if piazzaId is None:
            raise ValueError("The JSON dict that is to be a PiazzaUser object does not have the required Piazza uid 'user_id' attribute (%s)" % str(jsonDict))

        # Make a new field: 'ext_id' (for 'external id):
        ltiArr = jsonDict.get('lti_ids', [])
        # Replace the lti_ids field of the user json
        # entry with a non-array:
        if len(ltiArr) > 0:
            # Get Piazza's entry for Stanford's LTIs;
            # they look like this: stanford.edu__47bf69315b7391dace7ccbc344690969
            stanfordEduLTI = ltiArr[0]
            ltiSpecComponents = stanfordEduLTI.split('_')
            jsonDict['ext_id'] = ltiSpecComponents[-1]
        else:
            jsonDict['ext_id'] = None
        
        jsonDict['piazza_id'] = jsonDict['user_id']
        
        # Add the two new key/values user_int_id (default -1),
        # and anon_screen_name (default 'anon_screen_name_redacted')
        jsonDict['user_int_id'] = -1
        jsonDict['anon_screen_name'] = 'anon_screen_name_redacted'
        
        # Remove all PII and other unneeded fields from this new PiazzaUser instance:
        fieldsToDelete = ['name','email', 'lti_ids', 'user_id']
        for fldName in fieldsToDelete:
            if fldName in jsonDict:
                del jsonDict[fldName]


        #CHANGED: we no longer find the anon_screen_name
        # for each poster. Instead, importJsonUsersFromPiazzaZip()
        # creates PiazzaUser instances with info from Piazza's
        # users.json file (calling this method each time). 
        # Then, the method looks up all the user_int_id from
        # the LTIs in the PiazzaUser instance, and looks up
        # corresponding user_int_id entries in the DB.  
        # Find the anon_screen_name that corresponds
        # to the Piazza id in the JSON dict:
        # anonId = PiazzaImporter.resolveLTIToAnon(piazzaId)
        #self.anon_screen_name = anonId

    def __repr__(self):
        return '<PiazzaUser oid=%s>' % self.oid

    def __getitem__(self, key):
        '''
        Called when a PiazzaUser instance is treated
        like a dictionary: myUser['anon']
        
        :param key: the instance variable name
        :type key: String
        :return: the instance variable's value
        :rtype: <any>
        :raise KeyError: when given instance variable does not exist.
        '''

        # Oid and anon_screen_name are stored
        # in an instance variable (not in the 
        # JSON dict we keep in each instance:
        if key == 'oid':
            return self.oid
        if key == 'anon_screen_name':
            return self.anon_screen_name

        jsonValue = self.nameValueDict[key]
        return jsonValue
    
    def __setitem__(self, key, value):
        
        # Anon_screen_name and oid
        # are kept in instance variables.
        # All others are kept in nameValueDict 
        if key == 'anon_screen_name':
            self.anon_screen_name = value
            return
        elif key == 'oid':
            self.oid = value
            return
        self.nameValueDict[key] = value
    
    def __delitem__(self, key):
        if key == 'anon_screen_name' or key == 'oid':
            raise ValueError('Cannot delete anon_screen_name or oid from PiazzaPost instances')
        del self.nameValueDict[key]
    
    def keys(self):
        theKeys = self.nameValueDict.keys()
        theKeys.extend(['anon_screen_name', 'oid'])
        return theKeys

    def has_key(self, key):
        return(self.nameValueDict.has_key(key))
    
    def values(self):
        return self.nameValueDict.values()
    
    def items(self):
        return self.nameValueDict.items()

    def get(self, key, default=None):
        return self.nameValueDict.get(key, default)

class ForumComputer(object):
    
    def __init__(self):
        pass
    
    def prettyPrint(self, postObj, *fieldNames):
        pass
    
    def getAllFieldsFromX(self, piazzaPostObj, fieldName):
        '''
        Given a PiazzaPost instance, and a field name,
        return an array of the instance and, recursively,
        all its children as an array.
        
        :param piazzaPostObj:
        :type piazzaPostObj:
        :param fieldName:
        :type fieldName:
        '''
        fieldValues = [piazzaPostObj[fieldName]]
        children = piazzaPostObj['children']
        for child in children:
            fieldValues.extend(self.getAllFieldsFromX(child, fieldName))
        return fieldValues
    

    
if __name__ == '__main__':
    
    # -------------- Manage Input Parameters ---------------
    
    #usage = 'Usage: pizza_to_relation.py mysql_db_name {<courseFile>.zip | <class_content>.json}\n'

    parser = argparse.ArgumentParser(prog=os.path.basename(sys.argv[0]), formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument('-u', '--mySQLUser',
                        action='store',
                        help='User ID that is to log into MySQL. Default: the mySQLUser who is invoking this script.')
    parser.add_argument('-p', '--mySQLPwd',
                        action='store_true',
                        help='Request to be asked for mySQLPwd for operating MySQL;\n' +\
                             '    default: content of scriptInvokingUser$Home/.ssh/mysql if --mySQLUser is unspecified,\n' +\
                             '    or, if specified mySQLUser is root, then the content of scriptInvokingUser$Home/.ssh/mysql_root.'
                        )
    parser.add_argument('-w', '--password',
                        action='store',
                        help='User explicitly provided password to log into MySQL.\n' +\
                             '    default: content of scriptInvokingUser$Home/.ssh/mysql if --mySQLUser is unspecified,\n' +\
                             '    or, if specified mySQLUser is root, then the content of scriptInvokingUser$Home/.ssh/mysql_root.'
                        )

    parser.add_argument('-m', '--mappingFile',
                        action='store',
                        help='Path to file that maps Piazza IDs to LTI IDs. If not present, then the zip file\n' +\
                             '    must contain a file called account_mapping.csv'
                        )
    
    parser.add_argument('dbname',
                        action='store',
                        help='Name of MySQL database into which forum data is to be placed.' 
                        ) 
    
    parser.add_argument('tablename',
                        action='store',
                        help='Name of MySQL database into which forum data is to be placed.' 
                        ) 
    
    parser.add_argument('jsonFileName',
                        action='store',
                        help='Either a zipped Piazza dump, which contains a file named class_content.json, or ' +\
                        'a .json file that contains the class content in JSON format.'
                        ) 
    
    args = parser.parse_args();
    if args.mySQLUser is None:
        mySQLUser = getpass.getuser()
    else:
        mySQLUser = args.mySQLUser

    if args.password and args.pwd:
        raise ValueError('Use either -p, or -w, but not both.')
        
    if args.mySQLPwd:
        mySQLPwd = getpass.getpass("Enter %s's MySQL password on localhost: " % mySQLUser)
    elif args.password:
        mySQLPwd = args.password
    else:
        # Try to find mySQLPwd in specified mySQLUser's $HOME/.ssh/mysql
        currUserHomeDir = os.getenv('HOME')
        if currUserHomeDir is None:
            mySQLPwd = None
        else:
            # Don't really want the *current* mySQLUser's homedir,
            # but the one specified in the -u cli arg:
            userHomeDir = os.path.join(os.path.dirname(currUserHomeDir), mySQLUser)
            try:
                if mySQLUser == 'root':
                    with open(os.path.join(currUserHomeDir, '.ssh/mysql_root')) as fd:
                        mySQLPwd = fd.readline().strip()
                else:
                    with open(os.path.join(userHomeDir, '.ssh/mysql')) as fd:
                        mySQLPwd = fd.readline().strip()
            except IOError:
                # No .ssh subdir of mySQLUser's home, or no mysql inside .ssh:
                mySQLPwd = ''

    # -------------- Run the Loading ---------------

    piazzaImporter = PiazzaImporter(args.dbname, 
                                    mySQLUser,
                                    mySQLPwd,
                                    args.tablename, 
                                    args.jsonFileName 
                                    )
    piazzaImporter.doImport()