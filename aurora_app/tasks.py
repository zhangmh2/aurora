import os
import imp
import sys
from datetime import datetime
from multiprocessing import Process

from git import Repo
from fabric.api import local, execute

from aurora_app.database import db
from aurora_app.decorators import notify_result, task
from aurora_app.models import Deployment
from aurora_app.constants import STATUSES


@task
@notify_result
def clone_repository(project, user_id=None):
    """Clones project's repository to Aurora folder."""
    result = {
        'action': 'clone_repository',
        'user_id': user_id,
        'category': 'error'
    }

    if project.repository_path == '':
        result['message'] = """Can't clone "{}" repository without path.""" \
            .format(project.name)
        return result

    project_path = project.get_path()
    if os.path.exists(project_path):
        result['message'] = """Can't clone "{}" repository. "{}" is exists."""\
            .format(project.name, project_path)
        return result

    local('git clone {} {}'.format(project.repository_path, project_path))

    if not os.path.exists(project_path):
        result['message'] = """Can't clone "{}" repository.\n""" \
            .format(project.name) + "Something gone wrong."
        return result

    result['category'] = 'success'
    result['message'] = 'Cloning "{}" repository' \
        .format(project.name) + " has finished successfully."

    return result


@task
@notify_result
def remove_repository(project, user_id=None):
    """Removes project's repository in Aurora folder."""
    result = {
        'action': 'remove_repository',
        'category': 'error',
        'user_id': user_id
    }
    project_path = project.get_path()
    if not os.path.exists(project_path):
        result['message'] = """Can't remove "{}" repository.""" \
            .format(project.name) + " It's not exists."
        return result

    local('rm -rf {}'.format(project_path))

    if os.path.exists(project_path):
        result['message'] = """Can't remove "{}" repository.""" \
            .format(project.name) + " Something gone wrong."
        return result

    result['category'] = 'success'
    result['message'] = """"{}" repository has removed successfully.""" \
        .format(project.name, project_path)
    return result


@task
@notify_result
def deploy(deployment_id):
    """Run's given deployment."""
    deployment = Deployment.query.filter_by(id=deployment_id).first()

    result = {
        'action': 'deploy_stage',
        'category': 'error',
        'user_id': deployment.user_id
    }

    # Create deployment dir
    deployment_tmp_path = deployment.get_tmp_path()
    os.makedirs(deployment_tmp_path)

    deployment_project_tmp_path = os.path.join(
        deployment_tmp_path, deployment.stage.project.name.lower())

    # Copy project's repo if exists, else create an empty folder
    if deployment.stage.project.repository_folder_exists():
        os.system('cp -rf {} {}'.format(deployment.stage.project.get_path(),
                                        deployment_project_tmp_path))
    else:
        os.makedirs(deployment_project_tmp_path)

    # Change dir
    os.chdir(deployment_project_tmp_path)

    # Checkout to commit if repo exists
    if deployment.stage.project.repository_folder_exists():
        deployment_repo = Repo.init(deployment_project_tmp_path)
        deployment_repo.git.checkout(deployment.commit)

    # Create module
    module = imp.new_module("deployment_{}".format(deployment.id))

    # Replace stdout and stderr
    log_path = os.path.join(deployment_tmp_path, 'log')

    old_stdout = sys.stdout
    old_stderr = sys.stderr
    sys.stdout = sys.stderr = open(log_path, 'w', 0)

    # Update status
    deployment.status = STATUSES['RUNNING']
    db.session.add(deployment)
    db.session.commit()

    try:
        print 'Deployment has started.'

        exec deployment.code in module.__dict__

        for task in deployment.tasks:
            # Execute task
            process = Process(target=execute,
                              args=(eval('module.' +
                                    task.get_function_name()),))
            process.start()
            process.join()

            if process.exitcode != 0:
                raise Exception('Process has died')
        print 'Deployment has finished.'
    except Exception as e:
        deployment.status = STATUSES['FAILED']
        print 'Deployment has failed.'
        print 'Error: {}'.format(e.message)

        result['message'] = """"{}" deployement has failed.""" \
            .format(deployment.stage)

    finally:
        # Return stdout and stderr
        sys.stdout.close()
        sys.stdout = old_stdout
        sys.stderr = old_stderr

        log_file = open(log_path)

        # If status has not changed
        if deployment.status == STATUSES['RUNNING']:
            deployment.status = STATUSES['COMPLETED']

    deployment.log = '\n'.join(log_file.readlines())
    deployment.finished_at = datetime.now()
    db.session.add(deployment)
    db.session.commit()

    result['category'] = 'success'
    result['message'] = """"{}" has been deployed successfully.""" \
        .format(deployment.stage)

    return result
