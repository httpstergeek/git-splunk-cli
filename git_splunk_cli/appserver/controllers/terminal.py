import os
import cherrypy
import logging
import logging.handlers
import json
import subprocess
import shlex
from shutil import rmtree

import splunk, splunk.util
import splunk.appserver.mrsparkle.controllers as controllers
from splunk.appserver.mrsparkle.lib.decorators import expose_page
from splunk.appserver.mrsparkle.lib.routes import route
from splunk.appserver.mrsparkle.lib import jsonresponse, util, cached

def setup_logger(level):
    logger = logging.getLogger('webcli_app')
    logger.propagate = False  # Prevent the log messages from being duplicated in the python.log file
    logger.setLevel(level)

    file_handler = logging.handlers.RotatingFileHandler(
        os.path.join(os.environ.get("SPLUNK_HOME"), 'var', 'log', 'splunk', 'webcli_app.log'), maxBytes=25000000,
        backupCount=5)
    formatter = logging.Formatter('%(asctime)s %(levelname)s %(message)s')
    file_handler.setFormatter(formatter)

    logger.addHandler(file_handler)

    return logger


logger = setup_logger(logging.INFO)


def which(name, flags=os.X_OK):
    result = []
    path = os.environ.get('PATH', None)
    if path is None:
        return []
    paths = os.environ.get('PATH').split(os.pathsep)
    for path in paths:
        path = os.path.join(path,name)
        if os.access(path, flags):
            result.append(path)
    return result[0]

def create_symblink(symlink):
    exec_path = which(symlink)
    symlink_path = os.path.join(os.environ['SPLUNK_HOME'], 'etc', 'apps', symlink)
    if not os.path.islink(os.path.join(symlink_path)):
        os.symlink(exec_path.lstrip('.'), symlink_path)

def find_repo(dir_path):
    return [dir for dir in os.listdir(dir_path) \
            if os.path.isdir(os.path.join(dir_path, dir)) \
            if os.path.exists(os.path.join(dir_path, dir, '.git'))]

class TerminalController(controllers.BaseController):
    def render_template(self, template_path, template_args={}):
        template_args['appList'] = self.get_app_manifest()
        return super(TerminalController, self).render_template(template_path, template_args)

    def get_app_manifest(self):
        output = cached.getEntities('apps/local', search=['disabled=false', 'visible=true'], count=-1)
        return output

    @expose_page(must_login=True, methods=['GET'])
    @route('/', methods=['GET'])
    def view(self, **kwargs):

        app = cherrypy.request.path_info.split('/')[3]

        return self.render_template('/%s:/templates/terminal.html' % app, dict(app=app))


    @expose_page(must_login=True, methods=['POST'])
    @route('/', methods=['POST'])
    def process(self, command=None, **kwargs):
        cmds = ['git', 'ls', 'rm', 'curl']
        rlist = cmds + ['web-cli', 'user-prefs', 'search',
                        'django-git', 'django', 'default', 'launcher', 'Splunk',
                        'framework', 'learned', 'gettingstarted', 'legacy',
                        'SplunkForwarder', 'SplunkLightForwarder', 'splunk_datapreview']
        user = cherrypy.session['user']['name']
        command = command
        splitCommand = shlex.split(command) if os.name == 'posix' else command.split(' ')
        isRestartCommand = False
        if not command:
            error = "No command"
            return self.render_json(dict(success=False, payload=error))
        logger.info('user=' + str(user) + ' command=' + str(command))
        for cmd in cmds:
            create_symblink(cmd)
        cmds.append('help')
        if splitCommand[0] and splitCommand[0].lower() == 'cmd':
            if splitCommand[1] and not splitCommand[1].lower() in ['btool', 'splunkd', 'exporttool', 'btprobe',
                                                                   'classify']:
                payload = 'For security purposes this command is disabled'
                return self.render_json(dict(success=False, payload=payload))
        try:
            os.environ['SPLUNK_TOK'] = str(cherrypy.session['sessionKey'])
            if (splitCommand[0] not in cmds):
                payload = '%s %s' % (splitCommand[0], 'is not a valid command. type help for more information')
            if splitCommand[0] == 'help':
                payload = ("command\t  description\n"
                           "git    \t- allows clone, pull, and manage git repos within the splk project.\n"
                           "\t\t\tExample:\n"
                           "\t\t\t\tgit clone <repo_name>\n"
                           "\t\t\t\tgit <repo_name> pull origin master\n"
                           "\t\t\t\tgit <repo_name> checkout -b test\n"
                           "\t\t\t\tgit <repo_name> push origin test\n"
                           "repos  \t- lists all repose\n"
                           "rm     \t- removes repo\n"
                           "\t\t\tExample:\n"
                           "\t\t\t\trm <repo_name>\n"
                       #    "restart\t- restart Splunk services.  this will force all session to terminate.\n"
                       #    "\t\t\tExample:\n"
                       #    "\t\t\t\trestart\n"
                       #    "reload \t- attempts to reload and refresh views. If view does not appear use restart\n"
                       #    "\t\t\tExample:\n"
                       #    "\t\t\t\treload\n"
                            )
            else:
                splunkPath = os.path.join(os.environ['SPLUNK_HOME'], 'etc', 'apps')
                if splitCommand[0] == 'repos':
                    gitrepo = '\n'.join(find_repo(splunkPath))
                    return self.render_json(dict(success=True, payload=gitrepo))
                    #fullCommand = [os.path.join(splunkPath, splitCommand.pop(0))] + [splunkPath]
                elif command.startswith('restart'):
                    splunkPath = os.path.join(os.environ['SPLUNK_HOME'],'bin','splunk')
                    fullCommand = [splunkPath] + splitCommand
                    isRestartCommand = True
                elif splitCommand[0] == 'rm':
                    if splitCommand[1] in find_repo(splunkPath):
                        rmtree(os.path.join(splunkPath, splitCommand[1]))
                        payload = '\'%s\' %s' % (splitCommand[1], 'removed')
                        return self.render_json(dict(success=True, payload=payload))
                        #fullCommand = [os.path.join(splunkPath, splitCommand.pop(0))] + ['-rf', os.path.join(splunkPath, splitCommand.pop(0))]
                    else:
                        payload = '%s \'%s\' %s' % ('Repo', splitCommand[1], 'does not exist.')
                        return self.render_json(dict(success=False, payload=payload))
                elif splitCommand[0] == 'reload':
                    cmdArgs = ['-v', '-H', '"Authorization: OAuth ' + os.environ['SPLUNK_TOK'] + '"',
                               '-k', 'https://localhost:8089/services/apps/local/']
                    fullCommand = [os.path.join(splunkPath, 'curl')] + cmdArgs
                else:
                    if splitCommand[1] == 'clone':
                        fullCommand = [os.path.join(splunkPath, splitCommand.pop(0))] + [splitCommand[0],
                                                                                         'ssh://git@git.nordstrom.net/splnk/' + splitCommand[1] + '.git'] + [splunkPath+ '/' +splitCommand[1]]
                    else:
                        fullCommand = [os.path.join(splunkPath, splitCommand.pop(0))] + ['-C',
                                                                                     os.path.join(splunkPath, splitCommand.pop(0))] + splitCommand
                        cmdlen = len(splitCommand)
                        if cmdlen > 3:
                            cmdArgs = splitCommand[cmdlen-1:]
                            fullCommand = fullCommand + cmdArgs

                logger.info('user=' + str(user) + ' command=' + str(fullCommand))
                p = subprocess.Popen(fullCommand, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                if isRestartCommand:
                    payload = 'Restart is in progress and may take up too 5 minutes, please wait...'
                else:
                    stdout, stderr = p.communicate()
                    if stderr:
                        return self.render_json(dict(success=False, payload=str(stderr)))
                    for item in rlist:
                        stdout = stdout.replace(str(item+"\n"), "")
                    payload = str(stdout)
                del os.environ['SPLUNK_TOK']
        except Exception, e:
            return self.render_json(dict(success=False, payload=str(e)))

        return self.render_json(dict(success=True, payload=payload))
