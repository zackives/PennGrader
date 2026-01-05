import json
import dill
import base64
import types
import ast
import types
import urllib.request
import pandas as pd
import numpy as np
from datetime import datetime
import inspect
from difflib import SequenceMatcher

from urllib.error import HTTPError
import yaml
from yaml import Loader, Dumper

# Request types
HOMEWORK_ID_REQUEST     = 'GET_HOMEWORK_ID'
UPDATE_METADATA_REQUEST = 'UPDATE_METADATA'
UPDATE_TESTS_REQUEST    = 'UPDATE_TESTS'

def is_function(val):
    return inspect.isfunction(val)

def is_module(val):
    return inspect.ismodule(val)

def is_class(val):
    return inspect.isclass(val)

def is_external(name):
    return name not in ['__builtin__','__builtins__', 'penngrader','_sh', '__main__'] and 'penngrader' not in name


class PennGraderBackend:
    
    def __init__(self, config_filename, homework_number):
        with open(config_filename) as config_file:
            config = yaml.safe_load(config_file)

            self.config_api_url = config['config_api_url']
            self.config_api_key = config['config_api_key']

            self.secret_key      = config['secret_id']

            self.homework_number = homework_number
            print('Fetching homework number...')
            self.homework_id = self._get_homework_id()
            if 'Error' not in self.homework_id:
                response  = 'Success! Teacher backend initialized.\n\n'
                response += 'Homework ID: {}'.format(self.homework_id)
                print(response)
            else:
                print(self.homework_id)
            
    def update_metadata(self, deadline, total_score, max_daily_submissions):
        request = { 
            'homework_number' : self.homework_number, 
            'secret_key' : self.secret_key, 
            'request_type' : UPDATE_METADATA_REQUEST,
            'payload' : self._serialize({
                'max_daily_submissions' : max_daily_submissions,
                'total_score' : total_score,
                'deadline' : deadline
            })
        }
        print(self._send_request(request, self.config_api_url, self.config_api_key))
    
            
    def update_test_cases(self, global_items):
        request = { 
            'homework_number' : self.homework_number, 
            'secret_key' : self.secret_key, 
            'request_type' : UPDATE_TESTS_REQUEST,
            'payload' : self._serialize({
                'libraries'  : self._get_imported_libraries(),
                'test_cases' : self._get_test_cases(global_items),
            })
        }
        print(self._send_request(request, self.config_api_url, self.config_api_key))
    
    def _get_homework_id(self):
        request = { 
            'homework_number' : self.homework_number,
            'secret_key' : self.secret_key,
            'request_type' : HOMEWORK_ID_REQUEST,
            'payload' : self._serialize(None)
        }
        return self._send_request(request, self.config_api_url, self.config_api_key)

        
    def _send_request(self, request, api_url, api_key):
        params = json.dumps(request).encode('utf-8')
        headers = {'content-type': 'application/json', 'x-api-key': api_key}
        try:
          request = urllib.request.Request(api_url, data=params, headers=headers)
        except err:
          return 'Request builder error: {}'.format(err.read().decode("utf-8")) 
        try:
            response = urllib.request.urlopen(request)
            return '{}'.format(response.read().decode('utf-8'))
        except HTTPError as error:
            return 'Http Error: {}'.format(error.read().decode("utf-8")) 
        
    
    def _get_imported_libraries(self):
        # Get all externally imported base packages
        packages = set() # (package, shortname)
        for shortname, val in list(globals().items()):
            if is_module(val) and is_external(shortname):
                base_package = val.__name__.split('.')[0]
                if base_package != 'google' and base_package != 'yaml':
                  packages.add(base_package)
            if (is_function(val) or is_class(val)) and is_external(val.__module__):
                base_package = val.__module__.split('.')[0]
                packages.add(base_package)
        print ('Importing packages ', packages)

        # Get all sub-imports i.e import sklearn.svm etc 
        imports = set() # (module path , shortname )
        for shortname, val in list(globals().items()):
            if is_module(val) and is_external(shortname):
                if val.__name__ in packages:
                    packages.remove(val.__name__)
                if shortname != 'drive' and shortname != 'yaml':
                  imports.add((val.__name__, shortname))

        print ('Importing libraries ', imports)
        # Get all function imports 
        functions = set() # (module path , function name)
        for shortname, val in list(globals().items()):
            if is_function(val)and is_external(val.__module__):
                functions.add((val.__module__, shortname))     
        print ('Importing functions ', functions)

        return {
            'packages' : list(packages), 
            'imports' : list(imports), 
            'functions' : list(functions)
        }

    
    def _get_test_cases(self, global_items):
        # Get all function imports 
        test_cases = {}
        if not global_items:
            global_items = globals().items()
        for shortname, val in list(global_items):
            try:
                if val and is_function(val) and not is_external(val.__module__) and \
                'penngrader' not in val.__module__:
                  test_cases[shortname] = inspect.getsource(val)   
                  print ('Adding case {}', shortname)
            except:
                print ('Skipping {}', shortname)
                pass
        return test_cases

    
    def _serialize(self, obj):
        '''Dill serializes Python object into a UTF-8 string'''
        byte_serialized = dill.dumps(obj, recurse = False)
        return base64.b64encode(byte_serialized).decode("utf-8")

    
    def _deserialize(self, obj):
        byte_decoded = base64.b64decode(obj)
        return dill.loads(byte_decoded)
