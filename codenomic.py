#!/usr/bin/env python3

import psutil
import pyinotify
import subprocess
import sys


def hypervisor():
  # The hypervisor watches the `win condition` file and also restarts the
  # gameserver fork of the process.
  manager = pyinotify.WatchManager()

  # The process_IN_CLOSE_WRITE method is called when a file is closed after
  # having been opened in write mode.
  class EventHandler(pyinotify.ProcessEvent):
    def process_IN_CLOSE_WRITE(self, event):
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


def gameserver():
  # When the game server is started, it must first always ensure the
  # hypervisor is fresh. So we kill it and restart it.
  kill_codenomic_process('hypervisor')
  fork_and_abandon_child('hypervisor')



def main(args):
  if len(args) != 2:
    raise Exception('GAME OVER')

  if args[1] == '--gameserver':
    gameserver()
  elif args[1] == '--hypervisor':
    hypervisor()


if __name__ == '__main__':
  main(sys.argv)