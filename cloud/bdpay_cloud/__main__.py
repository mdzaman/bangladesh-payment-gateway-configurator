"""``python -m bdpay_cloud`` is the same as the ``bdpay-cloud`` command."""

import sys

from .cli import main

sys.exit(main())
