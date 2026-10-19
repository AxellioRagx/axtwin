#!/bin/sh
# axRange config-slice demo script (run-script / OP-APP-030). Idempotent:
# writes a fixed marker, so re-running converges to the same state.
set -e
mkdir -p /var/lib/axrange
echo "web app provisioned by run-script" > /var/lib/axrange/web-setup.done
