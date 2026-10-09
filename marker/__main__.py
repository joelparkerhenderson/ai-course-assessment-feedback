"""Allow ``python -m marker ...`` as an alternative to the ``marker`` console script."""

import sys

from .cli import main

sys.exit(main())
