#!/bin/sh
# macOS: double-click in Finder. Linux: run from a terminal. Arguments are passed to install.py.
cd "$(dirname "$0")" || exit 1
python3 install.py "$@"
status=$?
printf '\nPress Return to close.'
read -r _
exit $status
