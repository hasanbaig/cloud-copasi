#-------------------------------------------------------------------------------
# Cloud-COPASI
# Copyright (c) 2013 Edward Kent.
# All rights reserved. This program and the accompanying materials
# are made available under the terms of the GNU Public License v3.0
# which accompanies this distribution, and is available at
# http://www.gnu.org/licenses/gpl.html
#-------------------------------------------------------------------------------
from boto import s3, sqs
import json, s3_tools, os, sys
from cloud_copasi.web_interface.models import Task, CondorJob, Subtask
from boto.s3.key import Key
from cloud_copasi.web_interface.aws import aws_tools
from boto.sqs.message import Message

def copy_to_bucket(filename, bucket, delete):
    key = Key(bucket)
    path, name = os.path.split(filename)
    key.name = name
    key.set_contents_from_filename(filename)
    if delete:
        os.remove(filename)
    return name

def store_to_outgoing_bucket(task, directory, file_list, delete=True):
    """Copy the files in the list to the outgoing bucket of the task. By default
    files will be deleted from the local drive after they have been copied over
    """
    assert isinstance(task, Task)
    
    s3_connection = s3_tools.create_s3_connection(task.condor_pool.vpc.access_key)
    bucket_name = task.get_outgoing_bucket_name()
    
    
    #Create the bucket if it doesn't already exist?
    
    bucket = s3_connection.create_bucket(bucket_name)
    
    for file in file_list:
        full_path = os.path.join(directory, file)
        copy_to_bucket(full_path, bucket, delete)
        
    if delete:
        #Delete the parent folder of the last file
        try:
            os.rmdir(directory)
        except:
            #Most likely dir not empty
            pass

    
    
#    for condor_job in condor_jobs:
#        model_key_name = copy_to_bucket(condor_job.copasi_file, bucket, delete)
#        condor_job.copasi_file = model_key_name
#        file_keys.append(model_key_name)
#        
#        spec_key_name = copy_to_bucket(condor_job.spec_file, bucket, delete)
#        condor_job.spec_file = spec_key_name
#        spec_keys.append(spec_key_name)
#        
#        condor_job.save()

    
    #Delete the folder that contained the files
    if delete:
        #Delete the parent folder of the last file
        try:
            os.rmdir(directory)
        except:
            #Most likely dir not empty
            pass
    

def notify_new_condor_task(task, file_key_names, spec_key_names):
    """Notify the pool queue that there is a new task to run
    """
    
    assert isinstance(task, Task)
    queue_name = task.condor_pool.get_queue_name()
    sqs_connection = aws_tools.create_sqs_connection(task.condor_pool.vpc.access_key)
    queue = sqs_connection.get_queue(queue_name)
    assert queue != None
    
    output = {}
    output['notify_type'] = 'new_task'
    output['bucket_name'] = task.get_outgoing_bucket_name()
    output['task_id'] = str(task.uuid)
    output['file_keys'] = file_key_names
    output['spec_keys'] = spec_key_names
    
    condor_jobs = CondorJob.objects.filter(subtask__task=task).filter(queue_status='C')
    output_jobs = []
    for job in condor_jobs:
        output_jobs.append((job.id, job.spec_file))
    
    output['jobs'] = output_jobs

    json_output = json.dumps(output)
    message = Message()
    message.set_body(json_output)
    
    print >>sys.stderr, queue.write(message)
