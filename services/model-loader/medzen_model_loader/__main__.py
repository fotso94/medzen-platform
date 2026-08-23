import json
import os

# B6v2 round 5 (Codex): the container ALWAYS started the legacy v0
# Whisper loader — nothing could ever invoke the v2 init path. The
# deployment selects the mode explicitly; default stays the v0 proof.
if os.environ.get("MEDZEN_LOADER_MODE") == "b6v2":
    from .loader_v2 import run_b6v2_init

    print(json.dumps(run_b6v2_init()))
    raise SystemExit(0)

from .loader import main

raise SystemExit(main())
