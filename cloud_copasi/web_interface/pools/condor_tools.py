#-------------------------------------------------------------------------------
# Cloud-COPASI
# Copyright (c) 2013 Edward Kent.
# All rights reserved. This program and the accompanying materials
# are made available under the terms of the GNU Public License v3.0
# which accompanies this distribution, and is available at
# http://www.gnu.org/licenses/gpl.html
#-------------------------------------------------------------------------------
import subprocess, re, os
import os.path, time
from cloud_copasi import settings
import logging
from cloud_copasi.web_interface.models import EC2Pool, Subtask, CondorJob

log = logging.getLogger(__name__)

CONDOR_Q = 'condor_q'
CONDOR_SUBMIT = 'condor_submit'
CONDOR_RM = 'condor_rm'
BOSCO_CLUSTER = 'bosco_cluster'

#Set up the Bosco environment variables (equivalent to bosco_setenv)
os_env = os.environ.copy()

env={}
bosco_path = os.path.join(settings.BOSCO_DIR, 'bin') + ':' + os.path.join(settings.BOSCO_DIR, 'sbin')
env['PATH'] = bosco_path + ':' + os_env.get('PATH', '')
env['CONDOR_CONFIG'] = os.path.join(settings.BOSCO_DIR, 'etc/condor_config')
env['HOME'] = os_env.get('HOME', '')


###Custom env options
if hasattr(settings, 'BOSCO_CUSTOM_ENV'):
    env = dict(env.items() + settings.BOSCO_CUSTOM_ENV.items())



def run_bosco_command(command, error=False, cwd=None, shell=False):
    
    process = subprocess.Popen(command, shell=shell, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=cwd)
    
    output = process.communicate()

    if not error: return output[0].splitlines()
    else: return (output[0].splitlines(), output[1].splitlines(), process.returncode)

def add_bosco_pool(platform, address, keypair, pool_type='condor'):
    

    command = 'eval `ssh-agent`; ssh-add ' + keypair + '; '
    
    command += BOSCO_CLUSTER + ' --platform %s --add %s %s;' % (platform, address, pool_type)
    
    command += 'kill $SSH_AGENT_PID;'
        
    
    output = run_bosco_command(command, error=True, shell=True)
    
    log.debug(output)
    
    return output

def remove_bosco_pool(address):
    
    log.debug('Removing pool %s' %address)
    output = run_bosco_command([BOSCO_CLUSTER, '--remove', address], error=True)
    log.debug('Response:')
    log.debug(output)
    
    #log.debug('Removing pool from ssh known_hosts')
    #process = subprocess.Popen(['ssh-keygen', '-R', address], stderr=subprocess.PIPE, stdout=subprocess.PIPE)
    #output = process.communicate()
    #log.debug(output)
    
    return output

def test_bosco_pool(address):
    log.debug('Testing bosco cluster %s' % address)
    
    command = [BOSCO_CLUSTER, '--test', address]

    output =  run_bosco_command(command, error=True)
    
    log.debug('Test response:')
    log.debug(output[0])
    log.debug('Errors:')
    log.debug(output[1])
    log.debug('Exit status')
    log.debug(output[2])
    
    return output


def add_ec2_pool(ec2_pool):
    """Add an EC2 pool to bosco
    """
    assert isinstance(ec2_pool, EC2Pool)
    
    address = str(ec2_pool.address)
    
    platform = 'DEB6' #Ubuntu-based server
    pool_type = 'condor' #Condor scheduler
    keypair = ec2_pool.key_pair.path
    
    log.debug('Adding EC2 pool to bosco')
    
    output = add_bosco_pool(platform, address, keypair, pool_type)
    return output

def remove_ec2_pool(ec2_pool):
    address = str(ec2_pool.address)
    return remove_bosco_pool(address)
def condor_submit(condor_file):
    """Submit the .job file condor_file to the condor system using the condor_submit command"""
    #condor_file must be an absolute path to the condor job filename
    (directory, filename) = os.path.split(condor_file)
    
    output, error, exit_status = run_bosco_command([CONDOR_SUBMIT, condor_file], error=True, cwd=directory)
    log.debug('Submitting to condor. Output: ')
    log.debug(output)
    #Get condor_process number...
#    process_id = int(process_output.splitlines()[2].split()[5].strip('.'))
    #use a regular expression to parse the process output
    process_output = output[-1] #We're only interested in the last line
    
    try:
        assert exit_status == 0
        r=re.compile(r'.*(?P<n>\d+) job\(s\) submitted to cluster (?P<cluster>\d+).*', re.DOTALL)
        number_of_jobs = int(r.match(process_output).group('n'))
        cluster_id = int(r.match(process_output).group('cluster'))

    except:
        logging.exception('Failed to submit job')
        raise Exception('Failed to submit job')
    return (cluster_id, number_of_jobs)

def condor_rm(queue_id):
    
    p = subprocess.Popen([CONDOR_RM, str(queue_id)])
    p.communicate()
    time.sleep(0.5)
    
    
    
    
    
def submit_task(subtask):
    """Submit the subtask to the pool. Create all necessary CondorJobs, and update their status.
    """
    
    assert isinstance(subtask, Subtask)
    
    spec_file_path = os.path.join(subtask.task.directory, subtask.spec_file)
    
    cluster_id, number_of_jobs = condor_submit(spec_file_path)
    
    log.debug('cluster id %d' % cluster_id)
    log.debug('number_of_jobs %d' % number_of_jobs)
    
    subtask.cluster_id=cluster_id
    for n in range(number_of_jobs):
        copasi_model_filename = 'auto_copasi_%d.%d.cps' % (subtask.index, n)
        job = CondorJob(subtask=subtask,
                        std_output_file = copasi_model_filename + '.out',
                        std_error_file = copasi_model_filename + '.err',
                        log_file = copasi_model_filename + '.log',
                        job_output = 'output_%d.%d.txt' % (subtask.index, n),
                        status = 'I',
                        process_id = n,
                        run_time = 0.0,
                        copasi_file = copasi_model_filename,
                        )
        job.save()
        
    subtask.status='running'
    subtask.save()

def remove_task(subtask):
    """Call condor_rm on the condor jobs belonging to a subtask
    """
    assert isinstance(subtask, Subtask)
    
    output, error, exit_status = run_bosco_command([CONDOR_RM, subtask.cluster_id], error=True)
    
    assert exit_status == 0
    
    return True
    
def read_condor_q():
    
    """Execute the condor_q command and process the output
    Returns a list of tuples of the form (cluster_id, process_id, status)
    where status is a single lettter, e.g. I, R, H, X
    """
    
    
    
    condor_q_output, error, exit_status = run_bosco_command([CONDOR_Q], error=True)
    
    assert exit_status == 0
    
    #Process the output using regexps. Example line is as follows:
    # ID      OWNER            SUBMITTED     RUN_TIME ST PRI SIZE CMD               
    #18756.0   ed              1/7  11:45   0+03:19:53 R  0   22.0 CopasiSE.$$(OpSys)
    condor_q=[]
    no_of_jobs = len(condor_q_output) - 6
    if no_of_jobs > 0:
        job_string = r'\s*(?P<cluster_id>\d+)\.(?P<process_id>\d+)\s+(?P<owner>\S+)\s+(?P<sub_date>\S+)\s+(?P<sub_time>\S+)\s+(?P<run_time>\S+)\s+(?P<status>\w)\s+(?P<pri>\d+)\s+(?P<size>\S+)\s+(?P<cmd>\S+)'
        job_re = re.compile(job_string)
        for job_listing in condor_q_output:
            match = job_re.match(job_listing)
            if match:
                cluster_id = int(match.group('cluster_id'))
                process_id = int(match.group('process_id'))

                owner = match.group('owner')
                sub_date = match.group('sub_date')
                sub_time = match.group('sub_time')
                run_time = match.group('run_time')
                status = match.group('status')
                pri = match.group('pri')
                size=match.group('size')
                cmd=match.group('cmd')
                
                condor_q.append((cluster_id, process_id,status))

    
    
    return condor_q

def process_condor_q(user=None, subtask=None):
    """Process the output of the condor q and updates the status of condor jobs as necessary
    If specified we can narrow down to a specific user or subtask
    
    Note: this method only updates the status of CondorJob objects. It does not update any upstream subtask or task changes. this is performed in task_tools
    """
    
    #Next, get a list of all condor jobs we think are still running
    #Status will be 'I', 'R', 'H'
    
    condor_jobs = CondorJob.objects.filter(status='I') | CondorJob.objects.filter(status='R') | CondorJob.objects.filter(status='H')
    
    if user:
        condor_jobs = condor_jobs.filter(subtask__condor_pool__user=user)
    if subtask:
        condor_jobs = condor_jobs.filter(subtask=subtask)
    
    
    if len(condor_jobs) == 0:
        log.debug('No jobs marked as running. Not checking condor_q')
        
    else:
        log.debug('Reading condor_q')
        condor_q = read_condor_q()
        
        
        for job in condor_jobs:
            in_q = False
            for cluster_id, process_id, status in condor_q:
                if status != 'C' and process_id == job.process_id and cluster_id == job.subtask.cluster_id:
                    #Skip if state == 'C' -- means complete, so just assume not in the queue
                    in_q = True
                    job.status = status
                    job.save()
            if not in_q:
                #If not in the queue, then the job must have finished running. Change the status accordingly
                #TODO: At some point we need to validate the job based on the log file
                log.debug('Job %d.%d (Task %s) not in queue. Marking status as finished' % (job.subtask.cluster_id, job.process_id, job.subtask.task.name))
                job.status = 'F'
                job.save()

def cancel_task(task):
    #TODO: implement this method
    return