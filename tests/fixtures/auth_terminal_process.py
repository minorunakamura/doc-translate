"""Give a CLI test process a controlling PTY without a threaded preexec_fn."""

import fcntl
import os
import sys
import termios

fcntl.ioctl(0, termios.TIOCSCTTY, 0)
os.execv(sys.argv[1], sys.argv[1:])
