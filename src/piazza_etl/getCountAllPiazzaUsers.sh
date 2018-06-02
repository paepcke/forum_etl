#!/usr/bin/env bash

###################################################
# 
# Count the number of Piazza users without unzipping
# files. The number maybe be written to stdout, or
# an outfile may be specified. Also, only the number
# of users may be output for each Piazza zip file,
# or csv rows may be obtained. Column 0 will be the
# name of a course; column 1 will be the number of
# users.
#
# Options:
#    -v/--verbose:   write files being processed to stdout
#    -n/--nums_only: suppress column 0 of the output. I.e.
#                    only one column of numbers is written
#    -o/--outfile:   write output to given file, which must 
#                    be an absolute path. Without this option,
#                    output is written to stdout
#    -d/--dir:       normally all Piazza files are included
#                    under ~/Data/Piazza. This option has the
#                    script only find .zip files below the given
#                    directory.
#
# To change the assumption that Piazza zip files are
# below ~/Data/Piazza, change the constant ${PIAZZA_ROOT_DIR}
# below.
# 
# 
###################################################

USAGE="Usage: `basename $0` [-v | --verbose][-n | --nums_only][{-o | --outfile} outfile][{-d|--dir} directory"]

NUM_STUDENTS_ONLY=0
VERBOSE=0

# Use leading ':' in options list to have
# erroneous optons end up in the \? clause
# below:

OPTS=$(getopt -o vno:d:h --long verbose,nums_only,outfile,dir,help)
while true
do
  case $1 in
    -o|--outfile)
      OUTFILE=$2
      shift
      shift
      ;;
    -d|--dir)
      DIRECTORY=$2
      shift
      shift
      ;;
    -n|--nums_only)
      NUM_STUDENTS_ONLY=1
      shift
      ;;
    -v|--verbose)
      VERBOSE=1
      shift
      ;;
    -h|--help)
      echo $USAGE
      exit
      ;;
    *) break;;
  esac
done

PIAZZA_ROOT_DIR=/home/dataman/Data/Piazza
cd ${PIAZZA_ROOT_DIR}

# Ensure that outfile is an absolute path, if it was
# specified:

if [[ $OUTFILE != /* ]]
then
    echo "If outfile is specified, it must be an absolute path; exiting"
    exit
fi

# If single directory was specified, use that,
# else collect directory immediately below PWD

if [[ -z ${DIRECTORY} ]]
then
    dirlist=$(find `pwd` -mindepth 1 -maxdepth 1 -type d)
else
    dirlist=$DIRECTORY
fi

#*************
# echo "nums_only: '${NUM_STUDENTS_ONLY}'"
# echo "outfile:   '${OUTFILE}'"
# echo "dir:       '${DIRECTORY}'"
# echo "dirlist:   '${dirlist}'"
# exit
#*************

# Clear the file into which we will accumulate number
# of users:

function extract_num_students() {
    zipfile_path=$1
    
    NUM_STUDS=$(unzip -c "$zipfile_path" users.json | \
        sed -n 's/user_id/user_id\n/gp' |\
        grep -c "user_id")
    echo ${NUM_STUDS}
}

# The following test should return 36:
#extract_num_students ~/Data/Piazza/Fall2011/Fall2011-SURG_203_Human_Anatomy.zip

if [[ -e ${OUTFILE} ]]
then
    rm ${OUTFILE}
fi

for dir in ${dirlist}
do
    # /dev/null: to suppress the info written
    # to stdout by the pushd command:
    pushd $dir > /dev/null
    for zipfile in *.zip
    do
        if [[ ${VERBOSE} == 1 ]]
        then
            echo "Processing ${dir}/${zipfile}..."
        fi
        # User specified an outfile? (-n is 'var not empty'):
        if [[ -n ${OUTFILE} ]]
        then
            if [[ ${NUM_STUDENTS_ONLY} == 1 ]]
            then
                # Output just the num of students to outfile:
                extract_num_students "${zipfile}" >> ${OUTFILE}
            else
                # Output class name, follow by comma, followed
                # by num of students to outfile:
                printf "${zipfile}," >> ${OUTFILE}
                extract_num_students "${zipfile}" >> ${OUTFILE}
            fi
        else
            if [[ ${NUM_STUDENTS_ONLY} == 1 ]]
            then
                # Output just the num of students to STDOUT:
                extract_num_students "${zipfile}"
            else
                # Output class name, follow by comma, followed
                # by num of students to STDOUT:
                printf "${zipfile},"
                extract_num_students "${zipfile}"
            fi
        fi
    done
    # /dev/null: to suppress the info written
    # to stdout by the popd command:
    popd > /dev/null
done    
