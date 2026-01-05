import urllib.request
from urllib.error import HTTPError
import json
import dill
import base64
import types
import ast
import yaml
from yaml import Loader, Dumper

# Request types
STUDENT_GRADE_REQUEST = 'STUDENT_GRADE'

class PennGrader:
    
    def __init__(self, config_filename, homework_id, student_id, secret, course_name):
        if '_' in str(student_id):
            raise Exception("Student ID cannot contain '_'")

        with open(config_filename) as config_file:
            config = yaml.safe_load(config_file)

            self.grader_api_url = config['grader_api_url']
            self.token_generator_url = config['token_generator_url']
            self.grader_api_key = config['grader_api_key']
            self.token_generator_api_key = config.get('token_generator_api_key')

            self.homework_id = course_name + "_HW" + str(homework_id)
            self.student_id = str(student_id)
            self.student_secret = str(secret)
            self.course_name = course_name
            print('PennGrader initialized with Student ID: {}'.format(self.student_id))
            print('\nMake sure this correct or we will not be able to store your grade')

        
    def grade(self, test_case_id, answer):
        # Step 1: Get tokens from token_generator
        tokens = self._get_tokens(test_case_id)
        token_test = tokens.get('token1')
        token_save = tokens.get('token2')
        
        # Step 2: Build grading request with tokens
        request = { 
            'homework_id' : self.homework_id, 
            'student_id' : self.student_id, 
            'secret': self.student_secret,
            'test_case_id' : test_case_id,
            'answer' : self._serialize(answer),
            'token_test' : token_test,
            'token_save' : token_save
        }
        response = self._send_request(request, self.grader_api_url, self.grader_api_key)
        
        return response

    def _send_request(self, request, api_url, api_key):
        params = json.dumps(request).encode('utf-8')
        headers = {'content-type': 'application/json', 'x-api-key': api_key}
        try:
            response = urllib.request.urlopen(
                urllib.request.Request(api_url, data=params, headers=headers)
            )
            return response.read().decode('utf-8')
        except HTTPError as error:
            raise SystemExit(error)
    
    def _get_tokens(self, test_case_id):
        """Request tokens from token_generator for grading this test case."""
        token_request = {
            'student_id': self.student_id,
            'student_secret': self.student_secret,
            'test_case': test_case_id,
            'course_name': self.course_name
        }
        params = json.dumps(token_request).encode('utf-8')
        headers = {'content-type': 'application/json'}
        if self.token_generator_api_key:
            headers['x-api-key'] = self.token_generator_api_key
            
        try:
            response = urllib.request.urlopen(
                urllib.request.Request(self.token_generator_url, data=params, headers=headers)
            )
            tokens = json.loads(response.read().decode('utf-8'))
            
            return json.loads(tokens['body'])
        except HTTPError as error:
            raise SystemExit('Token generation error: {}'.format(error.read().decode('utf-8')))
        
    def _serialize(self, obj):
        byte_serialized = dill.dumps(obj, recurse = True)
        return base64.b64encode(byte_serialized).decode("utf-8")
