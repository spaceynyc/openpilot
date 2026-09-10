#!/usr/bin/env python3

import time
from multiprocessing import Process

from openpilot.common.params import Params
from openpilot.system.manager.process import launcher
from openpilot.common.swaglog import cloudlog
from openpilot.system.hardware import HARDWARE
from openpilot.system.version import get_build_metadata
from openpilot.starpilot.insight.backend import use_konik_server
from openpilot.starpilot.insight.konik import bounded_registration, normalize

ATHENA_MGR_PID_PARAM = "AthenadPid"


def main():
  params = Params()
  dongle_id = params.get("DongleId")
  build_metadata = get_build_metadata()

  cloudlog.bind_global(dongle_id=dongle_id,
                       version=build_metadata.openpilot.version,
                       origin=build_metadata.openpilot.git_normalized_origin,
                       branch=build_metadata.channel,
                       commit=build_metadata.openpilot.git_commit,
                       dirty=build_metadata.openpilot.is_dirty,
                       device=HARDWARE.get_device_type())

  try:
    while 1:
      if use_konik_server() and normalize(params.get("DongleId")) is None:
        if params.get_bool("IsOffroad"):
          bounded_registration(params)
        if normalize(params.get("DongleId")) is None:
          time.sleep(30)
          continue
      cloudlog.info("starting athena daemon")
      proc = Process(name='athenad', target=launcher, args=('system.athena.athenad', 'athenad'))
      proc.start()
      proc.join()
      cloudlog.event("athenad exited", exitcode=proc.exitcode)
      time.sleep(5)
  except Exception:
    cloudlog.exception("manage_athenad.exception")
  finally:
    params.remove(ATHENA_MGR_PID_PARAM)


if __name__ == '__main__':
  main()
