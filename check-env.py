#!/usr/bin/env python3

"""Check script imports without running synchronization or token refresh."""

from pathlib import Path
import runpy
import sys


def main():
    root = Path(__file__).resolve().parent
    scripts = sorted(root.glob('feed2*.py'))
    scripts.append(root / 'threads-refresh-access-token.py')

    print(f'Python {sys.version.split()[0]} ({sys.executable})')
    for script in scripts:
        runpy.run_path(str(script), run_name='feed2social_env_check')
        print(f'{script.name}: OK')


if __name__ == '__main__':
    main()
