'''
Created on Jun 26, 2018

@author: paepcke
'''

import argparse
import json
import os
import sys
import zipfile

from jsonpath_ng import parse


class ProfEmailGetter(object):
    '''
    Given a directory root with subdirectories of 
    Piazza zip files: try to find the name and email
    of professors or TAs. Print them to an outfile, or
    to stdout. 
    '''

    def __init__(self, piazza_zip_files_parent_dir, outfile=None):
        '''
        Constructor
        '''
        if outfile is None:
            outfile_fd = sys.stdout
        else:
            # Truncate outfile:
            outfile_fd = open(outfile, 'w')
                
        zipFiles = []
        for (dirpath, _, filenames) in os.walk(piazza_zip_files_parent_dir):
            zipFiles.extend(filter(lambda the_file: the_file.endswith('.zip'), 
                                   [os.path.join(dirpath,the_file) for the_file in filenames]))
            
        for the_file in zipFiles:
            [instructor_name, instructor_email, instructor_rank] = self.extract_prof_emails(the_file) #@UnusedVariable
            if instructor_name is None:
                outfile_fd.write('%s: no instructor found\n' % os.path.basename(the_file))
            else:
                outfile_fd.write('%s: %s, %s, %s\n' % (os.path.basename(the_file),
                                                       instructor_name, 
                                                       instructor_email,
                                                       instructor_rank)
                )
            
        
    def extract_prof_emails(self, zip_file):
        '''
        Given the name of one Piazza zip file, try to 
        find a message from a professor, and return 
        a result triplet: name, email, and whether a professor,
        or a TA.
        
        @param zip_file: Piazza zip file name
        @type zip_file: string
        @return: Triplet: [name, email, {'professor' | 'ta'}
        @rtype: [string, string, string]
        '''
        zip_obj = zipfile.ZipFile(zip_file)
        #zip_obj.namelist()  # ['users.json', 'class_content.js]
        contentFd = zip_obj.open('class_content.json')
        content   = json.load(contentFd)

        # Get something like:
        # [u'professor', u'student', u'student', u'ta', u'professor', u'student',... ]:
        msg_roles = [match.value for match in parse('$[*].children[*].tag_endorse[0].role').find(content)]
     
        # Find the position of a professor:
        instructor_pos = None
        try:
            instructor_pos = msg_roles.index('professor')
            instructor = 'professor'
        except ValueError:
            try:
                # Didn't find a prof:
                instructor_pos = msg_roles.index('ta')
                instructor = 'ta'
            except ValueError:
                return [None, None, None]

        # Found an instructor (prof or ta). Get the name:
        msg_names = [match.value for match in parse('$[*].children[*].tag_endorse[0].name').find(content)]
        instructor_name = msg_names[instructor_pos] 
        
        msg_emails = [match.value for match in parse('$[*].children[*].tag_endorse[0].email').find(content)]
        instructor_email = msg_emails[instructor_pos]
            
        # Return name, email, and whether prof or TA:
        return [instructor_name, instructor_email, instructor]
        
if __name__ == '__main__':
    parser = argparse.ArgumentParser(prog=os.path.basename(sys.argv[0]), formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument('-o', '--outfile',
                        help='fully qualified outfile name. Default: stdout.',
                        dest='outfile',
                        default=None);
    parser.add_argument('root_dir',
                        help='Root whose subdirectories are the Piazza .zip files.'
                        )

    args = parser.parse_args();
    
    ProfEmailGetter(args.root_dir, args.outfile)             
        
        
        

        