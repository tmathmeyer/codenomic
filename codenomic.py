#!/usr/bin/env python3

import bottle
import os
import psutil
import pyinotify
import shutil
import stat
import subprocess
import sys
import tempfile


PENDING_PROPOSAL_FILE = '/opt/codenomic/pending-proposal'
PROPOSAL_FILE = '/opt/codenomic/proposal'
SOURCE_FILE = '/opt/codenomic/codenomic.py'
ACTIVE_PLAYER = '/opt/codenomic/playerindex'

ACTIVE_SIGNATURES = set()
PLAYERS = [
  ('Ted', '3E430BA8997F61554719E47A7854BF294E6DBC84'),
]

NAME_LOOKUP = {key:name for name,key in PLAYERS}


def check_signature(file, expected_key=None):
  result = subprocess.run(f'gpg --verify {file}',
                          encoding='utf-8',
                          shell=True,
                          stderr=subprocess.PIPE,
                          stdout=subprocess.PIPE)
  if result.returncode:
    return False, result.stderr + result.stdout
  if 'Good signature' not in result.stderr:
    return False, result.stderr + result.stdout
  key = result.stderr.strip().split('\n')[1].split(' ')[-1].strip()
  if expected_key is not None:
    return (expected_key == key), key
  for name,pub in PLAYERS:
    if pub == key:
      return True, key
  return False, key


def get_active_player():
  if not PLAYERS:
    return None, None
  if not os.path.exists(ACTIVE_PLAYER):
    return PLAYERS[0]
  with open(ACTIVE_PLAYER, 'r') as f:
    try:
      index = int(f.read().strip())
      if index >= len(PLAYERS):
        return PLAYERS[0]
      return PLAYERS[index]
    except:
      return PLAYERS[0]


def update_next_active_player():
  current = get_active_player() + 1
  if current >= len(PLAYERS):
    current = 0
  with open(ACTIVE_PLAYER, 'w') as f:
    f.write(str(current))


def check_required_signatures():
  if len(ACTIVE_SIGNATURES) < len(PLAYERS):
    return
  for sig in ACTIVE_SIGNATURES:
    if sig not in NAME_LOOKUP:
      return
  update_next_active_player()
  with open(SOURCE_FILE, 'w') as dest:
    with open(PROPOSAL_FILE, 'r') as src:
      dest.write(src.read())
  os.remove(PROPOSAL_FILE)


def strip_signature(source, destination):
  result = subprocess.run(f'gpg --output {destination} {source}',
                          encoding='utf-8',
                          shell=True,
                          stderr=subprocess.PIPE,
                          stdout=subprocess.PIPE)


def kill_codenomic_process(mode:str):
  for proc in psutil.process_iter():
    if proc.name() != 'python3':
      continue
    commandline = proc.cmdline()
    if len(commandline) != 3:
      continue
    if 'codenomic.py' not in commandline[1]:
      continue
    if mode not in commandline[2]:
      continue
    proc.kill()


def fork_and_abandon_child(mode:str):
  subprocess.Popen(['./codenomic.py', f'--{mode}'], start_new_session=True)


def hypervisor():
  # The hypervisor watches the `win condition` file and also restarts the
  # gameserver fork of the process.
  manager = pyinotify.WatchManager()

  # The process_IN_CLOSE_WRITE method is called when a file is closed after
  # having been opened in write mode.
  class EventHandler(pyinotify.ProcessEvent):
    def process_IN_CLOSE_WRITE(self, event):
      if event.pathname == SOURCE_FILE:
        print('RESTARTING SERVER')
        kill_codenomic_process('gameserver')
        fork_and_abandon_child('gameserver')
      else:
        print(event.pathname)

  # All changes take place in the /opt/codenomic directory, including database
  # updates and the win condition.
  manager.add_watch('/opt/codenomic', pyinotify.IN_CLOSE_WRITE, rec=True)

  # TODO: How does notifier handle a shutdown when receiving a signal?
  notifier = pyinotify.Notifier(manager, EventHandler())
  try:
    notifier.loop()
  finally:
    notifier.stop()


def gameserver():
  # When the game server is started, it must first always ensure the
  # hypervisor is fresh. So we kill it and restart it.
  print('RESTARTING HYPERVISOR')
  kill_codenomic_process('hypervisor')
  fork_and_abandon_child('hypervisor')
  print(f'There are {len(PLAYERS)} players')

  # Start hosting the webserver
  @bottle.route('/')
  def index():
    name, _ = get_active_player()
    signatures = len(ACTIVE_SIGNATURES)
    if not os.path.exists(PROPOSAL_FILE):
      signatures = -1
    signatories = [NAME_LOOKUP[k] for k in ACTIVE_SIGNATURES]
    return bottle.template('''
      <html><body>
      <h2> It's {{turn}}'s turn.</h2><br />
      <h3> The proposal has {{signatures}} of {{required}} signatures.</h3><br />
      <h4> Signed-offs by:</h4><br /> {{signatories}} <br /><hr />
      <a href="/source">Current Source Code</a><br />
      <a href="/submit">Submit A Patch</a><br />
      <a href="/proposal">View Proposal</a><br />
      <a href="/sign">Signoff Current Proposal</a><br />
      <hr />
      You can sign snippits of code using GPG: <br />
      <pre>
      $ gpg --clearsign -o signed_code.txt codenomic.py
      </pre>
      </body></html>
      ''', turn=name, signatures=signatures,
           required=len(PLAYERS),
           signatories='<br />'.join(signatories))

  @bottle.route('/source')
  def source():
    bottle.response.content_type = 'text/plain; charset=utf8'
    with open('/opt/codenomic/codenomic.py', 'r') as f:
      return f.read()

  @bottle.route('/proposal')
  def proposal():
    if not os.path.exists(PROPOSAL_FILE):
      return '<html><body><h1>No Active Proposal</h1></body></html>'
    bottle.response.content_type = 'text/plain; charset=utf8'
    with open(PROPOSAL_FILE, 'r') as f:
      return f.read()

  @bottle.route('/sign')
  def sign():
    if not os.path.exists(PROPOSAL_FILE):
      return '<html><body><h1>No Active Proposal</h1></body></html>'
    return bottle.template('''
      <html><body>
      <form action="/upload/sign"
            method="post"
            enctype="multipart/form-data">
        Upload a signed copy of the proposed code to sign off.
        <input type="file" name="upload" /> <br />
        <input type="submit" value="Upload signature" />
      </form>
      </body></html>
      ''')

  @bottle.route('/submit')
  def submit():
    return bottle.template('''
      <html><body>
      <form action="/upload/submit"
            method="post"
            enctype="multipart/form-data">
        Upload a signed file to replace the codenomic server:
        <input type="file" name="upload" /> <br />
        <input type="submit" value="Upload new code" />
      </form>
      </body></html>
      ''')

  @bottle.route('/upload/sign', method='POST')
  def upload_signature():
    global ACTIVE_SIGNATURES
    upload = bottle.request.files.get('upload')
    _, tempfile = tempfile.mkstemp()
    upload.save(tempfile)
    ok, key = check_signature(tempfile)
    os.remove(tempfile)
    if not ok:
      return f'<html><body><h1>Unauthorized: {key}</h1></body></html>'
    ACTIVE_SIGNATURES.add(key)
    check_required_signatures()
    return '<html><body><h1>Signature Approved</h1><br /><a href="/">Home</a></body></html>'

  @bottle.route('/upload/submit', method='POST')
  def upload_submit():
    global ACTIVE_SIGNATURES
    upload = bottle.request.files.get('upload')
    if os.path.exists(PENDING_PROPOSAL_FILE):
      os.remove(PENDING_PROPOSAL_FILE)
    upload.save(PENDING_PROPOSAL_FILE)
    name, pub = get_active_player()
    ok, key = check_signature(PENDING_PROPOSAL_FILE, pub)
    if not ok:
      os.remove(PENDING_PROPOSAL_FILE)
      return f'<html><body><h1>Unauthorized: {key}</h1></body></html>'
    strip_signature(PENDING_PROPOSAL_FILE, PROPOSAL_FILE)
    os.remove(PENDING_PROPOSAL_FILE)
    ACTIVE_SIGNATURES = set([key])
    check_required_signatures()
    return '<html><body><h1>Accepted</h1><br /><a href="/">Acquire approvals</a></body></html>'

  bottle.run(host='localhost', port=8080)


def main(args):
  if len(args) != 2:
    raise Exception('GAME OVER')
  if args[1] == '--gameserver':
    gameserver()
  elif args[1] == '--hypervisor':
    hypervisor()
  elif args[1] == '--cleanup':
    kill_codenomic_process('hypervisor')
    kill_codenomic_process('gameserver')


if __name__ == '__main__':
  main(sys.argv)
