# -*- coding: utf-8 -*-
"""Shared test helpers.

The modules under test are loaded straight off disk rather than imported
through the `TwitchChannelPointsMiner` package. Importing the package runs its
`__init__`, which drags in irc, flask and pandas — none of which these tests
touch, and all of which would make a CI run slow and flaky for no benefit.
"""

import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_module(module_name, relative_path):
    """Load `relative_path` as `module_name`, once.

    Reusing whatever is already in sys.modules is the whole point: executing
    constants.py a second time would build a *second* GQLOperations class, and
    a healer patching one of them would look like it had patched nothing.
    """
    cached = sys.modules.get(module_name)
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location(
        module_name, os.path.join(ROOT, relative_path)
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


# constants must be in sys.modules before GQLHealer imports from it.
load_module("TwitchChannelPointsMiner.constants", "TwitchChannelPointsMiner/constants.py")
