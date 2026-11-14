"""Aufruf: python -m local_agents "Frage" [Verzeichnis]"""

import sys
from pathlib import Path

from local_agents.agent import Deps, agent, ollama_model


def main(argv: list[str]) -> int:
    if not 2 <= len(argv) <= 3:
        print(__doc__, file=sys.stderr)
        return 2
    root = Path(argv[2]) if len(argv) == 3 else Path.cwd()
    result = agent.run_sync(argv[1], model=ollama_model(), deps=Deps(root=root))
    print(result.output)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
